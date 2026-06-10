"""Fail-soft role package registry merge seam + lifecycle operations.

Slice 1: load_merged_registry(), InstalledPackage, RegistryMergeResult
Slice 3: manifest validation, install/remove, lockfile, list/info helpers

Invariants:
- Zero installed packages → merged_registry is the identical built-in dict.
- One broken package → skipped, recorded; built-ins unaffected.
- Duplicate built-in ID → package rejected, built-ins unaffected.
- route_task() is not altered — package roles are NOT routable until Slice 2+.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ROLE_PACKAGES_DIRNAME = "role-packages"
MANIFEST_FILENAME = "role-package.yaml"
LOCKFILE_FILENAME = "lock.yaml"
SUPPORTED_SCHEMA_VERSION = 1

VALID_BOUNDARY_MODES: frozenset[str] = frozenset({"advisory", "observe_warn", "enforced_tools"})

# Package names: lowercase, start with letter, alphanumeric + hyphens, max 64 chars.
_PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# Valid env var name: must start with letter or underscore; alphanumeric + underscore only.
# Wildcards (*, FOO_*, *_TOKEN) are explicitly rejected.
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WILDCARD_CHARS = frozenset({"*", "?"})

# Hard-error patterns: clearly secret-shaped, minimal false positives.
_SECRET_HARD_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                          # OpenAI-style API key
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                          # GitHub PAT
    re.compile(r"(?i)(password|passwd|secret)\s*[:=]\s*\S+"),    # password=...
]
# Warning-only patterns: broad heuristics with meaningful false-positive rate.
_SECRET_WARN_PATTERNS = [
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),  # Base64-length (too broad for hard error)
]


_REQUIRED_TOP_FIELDS: frozenset[str] = frozenset({"schema_version", "package", "role"})
_REQUIRED_PACKAGE_FIELDS: frozenset[str] = frozenset({"name", "version"})
_REQUIRED_ROLE_FIELDS: frozenset[str] = frozenset({"id", "canonical_id", "display_name"})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class PackageLoadStatus(str, Enum):
    OK = "ok"
    BROKEN = "broken"       # malformed YAML, missing fields, unreadable
    DUPLICATE = "duplicate"  # id/canonical_id/alias collision with built-in or earlier package


@dataclass(frozen=True)
class InstalledPackage:
    """Represents one discovered and loaded (or failed) role package."""

    name: str
    version: str
    role_id: str
    canonical_id: str
    display_name: str
    boundary_mode: str
    source_dir: Path
    manifest: dict[str, Any]
    status: PackageLoadStatus
    error: str | None = None


@dataclass
class RegistryMergeResult:
    """Result of merging the built-in registry with installed packages."""

    # Raw built-in registry dict — never mutated.
    builtin_registry: dict[str, Any]
    # Merged view: Slice 1/3 — same object as builtin_registry (packages not routable yet).
    merged_registry: dict[str, Any]
    # Successfully loaded (OK) packages.
    packages: list[InstalledPackage] = field(default_factory=list)
    # Packages that failed validation (BROKEN or DUPLICATE).
    broken: list[InstalledPackage] = field(default_factory=list)


@dataclass
class LockfileEntry:
    """One entry in lock.yaml."""

    name: str
    version: str
    status: str           # active | disabled
    installed_at: str     # ISO-8601
    source_type: str      # local (git in future)
    source_path: str      # original source path
    role_id: str
    canonical_id: str
    accepted_env: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def default_hermes_home() -> Path:
    """Return HERMES_HOME path, respecting the env var."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


def get_role_packages_dir(hermes_home: Path | None = None) -> Path:
    """Return the role-packages directory (may not exist)."""
    return (hermes_home or default_hermes_home()) / ROLE_PACKAGES_DIRNAME


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


def read_lockfile(packages_dir: Path) -> dict[str, Any]:
    """Return lockfile data dict; empty if file absent or unreadable."""
    lock_path = packages_dir / LOCKFILE_FILENAME
    if not lock_path.exists():
        return {"packages": {}}
    try:
        raw = lock_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return {"packages": {}}
        if "packages" not in data or not isinstance(data["packages"], dict):
            data["packages"] = {}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("lock.yaml unreadable: %s", exc)
        return {"packages": {}}


def _write_lockfile_atomic(packages_dir: Path, data: dict[str, Any]) -> None:
    """Write lock.yaml atomically (write to tmp, rename)."""
    packages_dir.mkdir(parents=True, exist_ok=True)
    lock_path = packages_dir / LOCKFILE_FILENAME
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=packages_dir, suffix=".lock.tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=True)
        tmp_path.replace(lock_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _add_lockfile_entry(packages_dir: Path, entry: LockfileEntry) -> None:
    data = read_lockfile(packages_dir)
    data["packages"][entry.name] = entry.to_dict()
    _write_lockfile_atomic(packages_dir, data)


def _remove_lockfile_entry(packages_dir: Path, name: str) -> bool:
    """Remove entry from lockfile. Returns True if entry existed."""
    data = read_lockfile(packages_dir)
    if name not in data.get("packages", {}):
        return False
    del data["packages"][name]
    _write_lockfile_atomic(packages_dir, data)
    return True


# ---------------------------------------------------------------------------
# Manifest parsing and validation
# ---------------------------------------------------------------------------


def _builtin_reserved_ids() -> frozenset[str]:
    """Return all IDs/aliases that packages must not collide with."""
    from hermes_cli.profile_validation import (  # late import avoids circular
        ACTIVE_PROFILE_IDS,
        CANONICAL_PROFILE_IDS,
        DEFERRED_PROFILE_IDS,
        PROFILE_ID_ALIASES,
    )

    aliases: set[str] = set(PROFILE_ID_ALIASES.keys()) | set(PROFILE_ID_ALIASES.values())
    return frozenset(ACTIVE_PROFILE_IDS | DEFERRED_PROFILE_IDS | CANONICAL_PROFILE_IDS | aliases)


def _validate_env_requires(
    env_requires: list[Any],
) -> tuple[list[str], list[str]]:
    """Validate manifest env_requires list.

    Returns (errors, warnings). Errors block install; warnings are surfaced only.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(env_requires, list):
        errors.append("env_requires must be a list")
        return errors, warnings
    for idx, entry in enumerate(env_requires):
        if not isinstance(entry, dict):
            errors.append(f"env_requires[{idx}]: must be a mapping, got {type(entry).__name__}")
            continue
        name = entry.get("name")
        if name is None:
            errors.append(f"env_requires[{idx}]: missing 'name' field")
            continue
        if not isinstance(name, str):
            errors.append(f"env_requires[{idx}]: 'name' must be a string")
            continue
        if any(c in name for c in _WILDCARD_CHARS):
            errors.append(
                f"env_requires[{idx}]: name {name!r} contains wildcard character"
                " — only exact var names allowed"
            )
            continue
        if not _ENV_VAR_NAME_RE.match(name):
            errors.append(
                f"env_requires[{idx}]: name {name!r} is not a valid env-var name"
                " (must match [A-Za-z_][A-Za-z0-9_]*)"
            )
            continue
        if "default" in entry:
            errors.append(
                f"env_requires[{idx}]: 'default' field is not allowed"
                " — manifests must not supply default values for env vars"
            )
        description = entry.get("description", "")
        if isinstance(description, str):
            desc_text = description
            for pat in (*_SECRET_HARD_PATTERNS, *_SECRET_WARN_PATTERNS):
                if pat.search(desc_text):
                    warnings.append(
                        f"env_requires[{idx}]: description for {name!r} may contain"
                        " a secret-looking value"
                    )
                    break
    return errors, warnings


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load and parse a role-package.yaml. Raises ValueError on any problem."""
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read manifest: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("manifest must be a YAML mapping")

    missing_top = _REQUIRED_TOP_FIELDS - data.keys()
    if missing_top:
        raise ValueError(f"missing top-level fields: {sorted(missing_top)}")

    schema_ver = data.get("schema_version")
    if schema_ver != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {schema_ver!r}; expected {SUPPORTED_SCHEMA_VERSION}"
        )

    pkg = data.get("package")
    if not isinstance(pkg, dict):
        raise ValueError("'package' must be a mapping")
    missing_pkg = _REQUIRED_PACKAGE_FIELDS - pkg.keys()
    if missing_pkg:
        raise ValueError(f"missing package fields: {sorted(missing_pkg)}")

    pkg_name = str(pkg.get("name", ""))
    if not _PACKAGE_NAME_RE.match(pkg_name):
        raise ValueError(
            f"invalid package name {pkg_name!r}: must match [a-z][a-z0-9-]{{0,63}}"
        )

    role = data.get("role")
    if not isinstance(role, dict):
        raise ValueError("'role' must be a mapping")
    missing_role = _REQUIRED_ROLE_FIELDS - role.keys()
    if missing_role:
        raise ValueError(f"missing role fields: {sorted(missing_role)}")

    boundary_mode = data.get("boundary_mode", "advisory")
    if boundary_mode not in VALID_BOUNDARY_MODES:
        raise ValueError(
            f"invalid boundary_mode {boundary_mode!r}; valid: {sorted(VALID_BOUNDARY_MODES)}"
        )

    env_requires = data.get("env_requires")
    if env_requires is not None:
        env_errors, _ = _validate_env_requires(env_requires)
        if env_errors:
            raise ValueError("env_requires validation failed: " + "; ".join(env_errors))

    return data


def validate_manifest_path(
    source_path: Path,
    *,
    check_builtin_collision: bool = True,
    check_overlap: bool = False,
    known_ids: set[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Validate a role-package.yaml at source_path/role-package.yaml.

    Returns (manifest_or_None, errors, warnings).
    Errors are hard failures; warnings are surfaced but do not block.
    """
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = source_path / MANIFEST_FILENAME
    if not manifest_path.exists():
        errors.append(f"no {MANIFEST_FILENAME} found in {source_path}")
        return None, errors, warnings

    try:
        manifest = _load_manifest(manifest_path)
    except ValueError as exc:
        errors.append(str(exc))
        return None, errors, warnings

    role = manifest["role"]
    role_id = str(role.get("id", ""))
    canonical_id = str(role.get("canonical_id", ""))

    if check_builtin_collision:
        reserved = _builtin_reserved_ids()
        colliding = reserved & {role_id, canonical_id}
        if colliding:
            errors.append(
                f"role id/canonical_id {sorted(colliding)} collides with a built-in role"
            )

    if known_ids is not None:
        colliding = known_ids & {role_id, canonical_id}
        if colliding:
            errors.append(
                f"role id/canonical_id {sorted(colliding)} collides with an already-installed package"
            )

    role_family = role.get("role_family")
    if not role_family:
        warnings.append("role.role_family is missing; recommended for role categorization")

    purpose = role.get("purpose_summary")
    if not purpose:
        warnings.append("role.purpose_summary is missing; recommended for operator docs")

    # Overlap / routing-flip validation (Slice 4, opt-in)
    if check_overlap and manifest is not None and not errors:
        from hermes_cli.role_overlap import validate_package_overlap  # noqa: PLC0415
        pkg_name = str(manifest.get("package", {}).get("name", source_path.name))
        for f in validate_package_overlap(manifest, pkg_name):
            if f.severity == "ERROR":
                errors.append(f"[{f.code}] {f.message}")
            else:
                warnings.append(f"[{f.code}] {f.message}")

    env_requires = manifest.get("env_requires") if manifest else None
    if env_requires is not None:
        _, env_warnings = _validate_env_requires(env_requires)
        warnings.extend(env_warnings)

    return manifest, errors, warnings


# ---------------------------------------------------------------------------
# Load helpers (Slice 1 — unchanged behaviour)
# ---------------------------------------------------------------------------


def load_installed_package(
    pkg_dir: Path,
    known_ids: set[str],
) -> InstalledPackage:
    """Load one installed package directory. Never raises — returns BROKEN on any error."""
    manifest_path = pkg_dir / MANIFEST_FILENAME

    try:
        manifest = _load_manifest(manifest_path)
    except ValueError as exc:
        logger.warning("role-package %s: skipped (broken): %s", pkg_dir.name, exc)
        return InstalledPackage(
            name=pkg_dir.name,
            version="unknown",
            role_id="",
            canonical_id="",
            display_name="",
            boundary_mode="advisory",
            source_dir=pkg_dir,
            manifest={},
            status=PackageLoadStatus.BROKEN,
            error=str(exc),
        )

    pkg_meta = manifest["package"]
    role_meta = manifest["role"]
    name = str(pkg_meta.get("name", pkg_dir.name))
    version = str(pkg_meta.get("version", "unknown"))
    role_id = str(role_meta.get("id", ""))
    canonical_id = str(role_meta.get("canonical_id", ""))
    display_name = str(role_meta.get("display_name", ""))
    boundary_mode = str(manifest.get("boundary_mode", "advisory"))

    colliding = known_ids & {role_id, canonical_id}
    if colliding:
        msg = f"role id/canonical_id {sorted(colliding)} collides with a built-in or earlier package"
        logger.warning("role-package %s: rejected (duplicate): %s", name, msg)
        return InstalledPackage(
            name=name,
            version=version,
            role_id=role_id,
            canonical_id=canonical_id,
            display_name=display_name,
            boundary_mode=boundary_mode,
            source_dir=pkg_dir,
            manifest=manifest,
            status=PackageLoadStatus.DUPLICATE,
            error=msg,
        )

    known_ids.add(role_id)
    known_ids.add(canonical_id)

    logger.debug("role-package %s v%s loaded (role_id=%s)", name, version, role_id)
    return InstalledPackage(
        name=name,
        version=version,
        role_id=role_id,
        canonical_id=canonical_id,
        display_name=display_name,
        boundary_mode=boundary_mode,
        source_dir=pkg_dir,
        manifest=manifest,
        status=PackageLoadStatus.OK,
        error=None,
    )


def discover_package_dirs(packages_dir: Path) -> list[Path]:
    """Return sorted list of package subdirs that contain a manifest file."""
    if not packages_dir.exists() or not packages_dir.is_dir():
        return []
    return sorted(
        d for d in packages_dir.iterdir()
        if d.is_dir() and (d / MANIFEST_FILENAME).exists()
    )


def load_merged_registry(
    registry_path: Path | str | None = None,
    hermes_home: Path | None = None,
) -> RegistryMergeResult:
    """Load the built-in registry then fail-softly discover and merge packages.

    With zero packages installed, merged_registry is the identical built-in
    dict — no copies, no mutations, same object.
    """
    from hermes_cli.profile_routing import (  # late import avoids circular
        DEFAULT_PROFILE_REGISTRY_PATH,
        load_profile_registry,
    )

    path = Path(registry_path) if registry_path is not None else DEFAULT_PROFILE_REGISTRY_PATH
    builtin_registry = load_profile_registry(path)

    packages_dir = get_role_packages_dir(hermes_home)
    pkg_dirs = discover_package_dirs(packages_dir)

    if not pkg_dirs:
        return RegistryMergeResult(
            builtin_registry=builtin_registry,
            merged_registry=builtin_registry,
        )

    known_ids: set[str] = set(_builtin_reserved_ids())

    ok_packages: list[InstalledPackage] = []
    broken_packages: list[InstalledPackage] = []

    for pkg_dir in pkg_dirs:
        pkg = load_installed_package(pkg_dir, known_ids)
        if pkg.status == PackageLoadStatus.OK:
            ok_packages.append(pkg)
        else:
            broken_packages.append(pkg)

    return RegistryMergeResult(
        builtin_registry=builtin_registry,
        merged_registry=builtin_registry,
        packages=ok_packages,
        broken=broken_packages,
    )


# ---------------------------------------------------------------------------
# Install / Remove (Slice 3)
# ---------------------------------------------------------------------------


class RolePackageError(Exception):
    """Raised by install/remove when operation cannot proceed."""


def install_package(
    source_path: Path,
    hermes_home: Path | None = None,
    *,
    force: bool = False,
    accept_env: list[str] | None = None,
) -> InstalledPackage:
    """Install a role package from a local directory.

    Validates the manifest, checks for built-in ID collision, copies the
    directory into ~/.hermes/role-packages/<name>/, and writes lock.yaml.

    Raises RolePackageError on any hard validation failure.
    Returns the InstalledPackage on success.
    """
    source_path = Path(source_path).resolve()
    if not source_path.is_dir():
        raise RolePackageError(f"source path is not a directory: {source_path}")

    manifest, errors, warnings = validate_manifest_path(source_path, check_overlap=True)
    if errors:
        raise RolePackageError("manifest validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    assert manifest is not None
    pkg_meta = manifest["package"]
    role_meta = manifest["role"]
    name = str(pkg_meta["name"])
    version = str(pkg_meta.get("version", "0.0.0"))
    role_id = str(role_meta["id"])
    canonical_id = str(role_meta.get("canonical_id", role_id))
    display_name = str(role_meta.get("display_name", name))
    boundary_mode = str(manifest.get("boundary_mode", "advisory"))

    packages_dir = get_role_packages_dir(hermes_home)
    dest_dir = packages_dir / name

    if dest_dir.exists() and not force:
        raise RolePackageError(
            f"package {name!r} is already installed at {dest_dir}; use --force to reinstall"
        )

    # Copy payload.
    packages_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(source_path, dest_dir)

    # Validate accept_env against declared env_requires.
    declared_names: set[str] = {
        str(e["name"])
        for e in (manifest.get("env_requires") or [])
        if isinstance(e, dict) and "name" in e
    }
    normalized_accept_env: list[str] = []
    if accept_env:
        undeclared = sorted(set(accept_env) - declared_names)
        if undeclared:
            raise RolePackageError(
                f"accept_env contains names not declared in env_requires: {undeclared}"
            )
        normalized_accept_env = sorted(set(accept_env) & declared_names)

    # Write lockfile entry.
    entry = LockfileEntry(
        name=name,
        version=version,
        status="active",
        installed_at=datetime.now(tz=timezone.utc).isoformat(),
        source_type="local",
        source_path=str(source_path),
        role_id=role_id,
        canonical_id=canonical_id,
        accepted_env=normalized_accept_env,
    )
    _add_lockfile_entry(packages_dir, entry)

    if warnings:
        for w in warnings:
            logger.warning("role-package %s: %s", name, w)

    return InstalledPackage(
        name=name,
        version=version,
        role_id=role_id,
        canonical_id=canonical_id,
        display_name=display_name,
        boundary_mode=boundary_mode,
        source_dir=dest_dir,
        manifest=manifest,
        status=PackageLoadStatus.OK,
        error=None,
    )


def remove_package(name: str, hermes_home: Path | None = None) -> None:
    """Remove an installed role package by name.

    Removes the payload directory and lockfile entry.
    Raises RolePackageError if the package is not installed.
    """
    packages_dir = get_role_packages_dir(hermes_home)
    dest_dir = packages_dir / name

    found_dir = dest_dir.exists()
    found_lock = name in read_lockfile(packages_dir).get("packages", {})

    if not found_dir and not found_lock:
        raise RolePackageError(f"package {name!r} is not installed")

    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    _remove_lockfile_entry(packages_dir, name)
    logger.info("role-package %s: removed", name)


# ---------------------------------------------------------------------------
# List / Info helpers (Slice 3)
# ---------------------------------------------------------------------------


def list_packages(hermes_home: Path | None = None) -> list[dict[str, Any]]:
    """Return a list of dicts describing all known packages.

    Combines lockfile data (authoritative for status/metadata) with
    discovery results (detects on-disk breakage not recorded in lockfile).
    """
    packages_dir = get_role_packages_dir(hermes_home)
    lock_data = read_lockfile(packages_dir).get("packages", {})

    # Also discover on-disk packages (may include broken ones not in lockfile).
    result: dict[str, dict[str, Any]] = {}

    # Start from lockfile entries.
    for name, entry in lock_data.items():
        result[name] = dict(entry)
        # Check if payload dir is intact.
        dest_dir = packages_dir / name
        if not dest_dir.exists() or not (dest_dir / MANIFEST_FILENAME).exists():
            result[name]["status"] = "broken"
            result[name]["_note"] = "payload directory missing"

    # Add any on-disk packages not in lockfile (e.g. manually placed).
    known_ids: set[str] = set(_builtin_reserved_ids())
    for pkg_dir in discover_package_dirs(packages_dir):
        pkg_name = pkg_dir.name
        if pkg_name not in result:
            pkg = load_installed_package(pkg_dir, known_ids)
            result[pkg_name] = {
                "name": pkg.name,
                "version": pkg.version,
                "status": pkg.status.value,
                "role_id": pkg.role_id,
                "canonical_id": pkg.canonical_id,
                "display_name": pkg.display_name,
                "boundary_mode": pkg.boundary_mode,
                "_note": "not in lockfile",
            }
            if pkg.error:
                result[pkg_name]["_error"] = pkg.error
        else:
            # Already in result from lockfile; mark known_ids to prevent false duplicates.
            known_ids.discard(result[pkg_name].get("role_id", ""))
            known_ids.discard(result[pkg_name].get("canonical_id", ""))

    return list(result.values())


def get_package_info(name: str, hermes_home: Path | None = None) -> dict[str, Any] | None:
    """Return info dict for one installed package, or None if not found."""
    packages_dir = get_role_packages_dir(hermes_home)
    lock_data = read_lockfile(packages_dir).get("packages", {})

    info: dict[str, Any] = {}
    if name in lock_data:
        info.update(lock_data[name])

    dest_dir = packages_dir / name
    if dest_dir.exists() and (dest_dir / MANIFEST_FILENAME).exists():
        info["install_path"] = str(dest_dir)
        # Re-validate to surface any current warnings/errors.
        _, errors, warnings = validate_manifest_path(dest_dir)
        if errors:
            info["validation_errors"] = errors
        if warnings:
            info["validation_warnings"] = warnings
    elif name in lock_data:
        info["status"] = "broken"
        info["_note"] = "payload directory missing"
        info["install_path"] = str(dest_dir)
    else:
        return None

    return info or None


# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------


def clear_registry_cache() -> None:
    """Clear the profile_context LRU cache — call in tests after modifying packages."""
    try:
        from hermes_cli.profile_context import _load_profile_registry_cached
        _load_profile_registry_cached.cache_clear()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Skill directory helpers (Slice 5)
# ---------------------------------------------------------------------------

_SKILLS_SUBDIR = "skills"


def get_package_skill_dirs(hermes_home: Path | None = None) -> list[Path]:
    """Return a list of skill directories contributed by installed packages.

    Only directories that actually exist on disk are returned. The list
    order is deterministic (sorted by package name).
    """
    packages_dir = get_role_packages_dir(hermes_home)
    if not packages_dir.is_dir():
        return []
    result: list[Path] = []
    for pkg_dir in sorted(packages_dir.iterdir()):
        if not pkg_dir.is_dir():
            continue
        skills_dir = pkg_dir / _SKILLS_SUBDIR
        if skills_dir.is_dir():
            result.append(skills_dir)
    return result


def get_package_for_skill_path(
    skill_path: Path,
    hermes_home: Path | None = None,
) -> str | None:
    """Return the package name that owns *skill_path*, or None.

    A path is "owned" by a package when it lives under
    ``<packages_dir>/<pkg_name>/`` (at any depth).
    """
    packages_dir = get_role_packages_dir(hermes_home).resolve()
    try:
        relative = skill_path.resolve().relative_to(packages_dir)
    except ValueError:
        return None
    # First component of the relative path is the package name.
    parts = relative.parts
    if not parts:
        return None
    return parts[0]


def cap_env_passthrough_for_skill(
    skill_path: Path,
    skill_env_names: set[str],
    hermes_home: Path | None = None,
) -> list[str] | None:
    """Apply the three-gate env passthrough cap for a package-owned skill.

    Returns:
        - ``None`` if the skill is not owned by any installed package
          (caller should treat it as a built-in skill and apply no cap).
        - A sorted list of env var names that pass all three gates:
          ``skill_required_env ∩ manifest.env_requires ∩ accepted_consents``
          (may be empty).
    """
    pkg_name = get_package_for_skill_path(skill_path, hermes_home)
    if pkg_name is None:
        return None

    packages_dir = get_role_packages_dir(hermes_home)
    pkg_dir = packages_dir / pkg_name

    # Gate 1: manifest.env_requires
    manifest_path = pkg_dir / MANIFEST_FILENAME
    try:
        manifest = _load_manifest(manifest_path)
    except ValueError:
        logger.warning(
            "cap_env_passthrough_for_skill: could not load manifest for %s, "
            "returning empty allowlist",
            pkg_name,
        )
        return []

    declared: set[str] = {
        str(e["name"])
        for e in (manifest.get("env_requires") or [])
        if isinstance(e, dict) and "name" in e
    }

    # Gate 2: accepted_env from lockfile
    lock = read_lockfile(packages_dir)
    pkg_entry = lock.get("packages", {}).get(pkg_name, {})
    accepted: set[str] = set(pkg_entry.get("accepted_env", []))

    # Three-gate intersection
    allowed = skill_env_names & declared & accepted
    return sorted(allowed)
