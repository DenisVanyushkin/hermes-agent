"""Fail-soft role package registry merge seam (Slice 1).

Discovers installed role packages under ~/.hermes/role-packages/ and merges
them additively onto the built-in profile registry.

Invariants:
- Zero installed packages → merged_registry is the identical built-in dict.
- One broken package → skipped, recorded; built-ins unaffected.
- Duplicate built-in ID → package rejected, built-ins unaffected.
- route_task() is not altered by this module — package roles are NOT added to
  profiles[] in Slice 1 (routing expansion is Slice 2+).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ROLE_PACKAGES_DIRNAME = "role-packages"
MANIFEST_FILENAME = "role-package.yaml"
SUPPORTED_SCHEMA_VERSION = 1

_REQUIRED_TOP_FIELDS: frozenset[str] = frozenset({"schema_version", "package", "role"})
_REQUIRED_PACKAGE_FIELDS: frozenset[str] = frozenset({"name", "version"})
_REQUIRED_ROLE_FIELDS: frozenset[str] = frozenset({"id", "canonical_id", "display_name"})


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
    # Merged view: in Slice 1 this is the same object as builtin_registry
    # because package roles are not yet injected into profiles[].
    merged_registry: dict[str, Any]
    # Successfully loaded (OK) packages.
    packages: list[InstalledPackage] = field(default_factory=list)
    # Packages that failed validation (BROKEN or DUPLICATE).
    broken: list[InstalledPackage] = field(default_factory=list)


def default_hermes_home() -> Path:
    """Return HERMES_HOME path, respecting the env var (mirrors get_hermes_home())."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


def get_role_packages_dir(hermes_home: Path | None = None) -> Path:
    """Return the role-packages directory (may not exist)."""
    return (hermes_home or default_hermes_home()) / ROLE_PACKAGES_DIRNAME


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

    role = data.get("role")
    if not isinstance(role, dict):
        raise ValueError("'role' must be a mapping")
    missing_role = _REQUIRED_ROLE_FIELDS - role.keys()
    if missing_role:
        raise ValueError(f"missing role fields: {sorted(missing_role)}")

    return data


def load_installed_package(
    pkg_dir: Path,
    known_ids: set[str],
) -> InstalledPackage:
    """Load one installed package directory. Never raises — returns BROKEN on any error.

    Args:
        pkg_dir:   Directory containing role-package.yaml.
        known_ids: Set of already-claimed IDs (built-ins + earlier packages).
                   Mutated in-place when a valid package is accepted (OK status).
    """
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

    # Collision check against built-ins and previously loaded packages.
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

    # Claim the IDs so later packages cannot collide with this one.
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

    Package roles are discovered and validated but NOT injected into
    profiles[] in Slice 1 (routing is not altered until Slice 2+).
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
        # Fast path: zero packages → identical dict, no processing.
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

    # Slice 1: merged_registry == builtin_registry (packages not yet routable).
    # Slice 2+ will inject package roles into profiles[].
    return RegistryMergeResult(
        builtin_registry=builtin_registry,
        merged_registry=builtin_registry,
        packages=ok_packages,
        broken=broken_packages,
    )


def clear_registry_cache() -> None:
    """Clear the profile_context LRU cache — call in tests after modifying packages."""
    try:
        from hermes_cli.profile_context import _load_profile_registry_cached
        _load_profile_registry_cached.cache_clear()
    except Exception:  # noqa: BLE001
        pass
