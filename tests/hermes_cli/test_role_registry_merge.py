"""Slice 1: fail-soft role package registry merge seam tests.

Tests that:
- zero packages → merged registry == built-in registry (identity/deep-equal)
- missing role-packages dir → no failure, built-ins load normally
- valid additive package → discovered, visible through API, built-ins unchanged
- broken package (invalid YAML / missing fields) → skipped, marked broken, built-ins intact
- duplicate built-in id → rejected, built-in role unchanged
- route_task() survives broken packages without exception

All tests use an isolated tmp HERMES_HOME so they never touch real ~/.hermes/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.profile_routing import (
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    load_profile_registry,
    route_task,
)
from hermes_cli.role_packages import (
    PackageLoadStatus,
    RegistryMergeResult,
    discover_package_dirs,
    load_installed_package,
    load_merged_registry,
)

_REGISTRY_PATH = DEFAULT_PROFILE_REGISTRY_PATH
_POLICY_PATH = DEFAULT_MODEL_POLICY_PATH

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_manifest(
    role_id: str = "sample_role",
    canonical_id: str = "sample_role",
    name: str = "sample-role",
    version: str = "0.1.0",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "package": {"name": name, "version": version},
        "role": {
            "id": role_id,
            "canonical_id": canonical_id,
            "display_name": "Sample Role",
        },
        "boundary_mode": "advisory",
    }


def _write_manifest(pkg_dir: Path, data: dict[str, Any]) -> Path:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    manifest = pkg_dir / "role-package.yaml"
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Test 1: zero packages → parity with built-in registry
# ---------------------------------------------------------------------------

def test_zero_packages_registry_parity(tmp_path: Path) -> None:
    """With no role-packages dir, merged registry is identical to built-in."""
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    # No role-packages dir exists at all.

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)

    builtin = load_profile_registry(_REGISTRY_PATH)
    assert result.merged_registry == builtin
    assert result.merged_registry is result.builtin_registry  # same object on fast path
    assert result.packages == []
    assert result.broken == []


# ---------------------------------------------------------------------------
# Test 2: missing role-packages dir → no failure
# ---------------------------------------------------------------------------

def test_missing_role_packages_dir_no_failure(tmp_path: Path) -> None:
    """Loader must not fail when ~/.hermes/role-packages/ does not exist."""
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    packages_dir = hermes_home / "role-packages"
    assert not packages_dir.exists()

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)

    builtin = load_profile_registry(_REGISTRY_PATH)
    assert result.merged_registry == builtin
    assert result.packages == []
    assert result.broken == []


# ---------------------------------------------------------------------------
# Test 3: valid additive package discovered
# ---------------------------------------------------------------------------

def test_valid_additive_package_discovered(tmp_path: Path) -> None:
    """A valid package is discovered and visible through the merge API."""
    hermes_home = tmp_path / "hermes_home"
    packages_dir = hermes_home / "role-packages"
    pkg_dir = packages_dir / "sample-role"
    _write_manifest(pkg_dir, _make_valid_manifest())

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)

    # Package is accessible via the new API.
    assert len(result.packages) == 1
    pkg = result.packages[0]
    assert pkg.status == PackageLoadStatus.OK
    assert pkg.role_id == "sample_role"
    assert pkg.name == "sample-role"
    assert pkg.error is None

    # Built-in registry is unchanged.
    builtin = load_profile_registry(_REGISTRY_PATH)
    assert result.builtin_registry == builtin

    # Broken list is empty.
    assert result.broken == []

    # Slice 1: merged_registry == builtin (package roles not yet injected).
    assert result.merged_registry == builtin


# ---------------------------------------------------------------------------
# Test 4: broken package skipped
# ---------------------------------------------------------------------------

def test_broken_package_skipped(tmp_path: Path) -> None:
    """A malformed manifest is skipped; built-ins are unchanged and routing works."""
    hermes_home = tmp_path / "hermes_home"
    packages_dir = hermes_home / "role-packages"
    pkg_dir = packages_dir / "broken-role"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "role-package.yaml").write_text(": this is: not valid yaml:", encoding="utf-8")

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)

    assert len(result.broken) == 1
    broken = result.broken[0]
    assert broken.status == PackageLoadStatus.BROKEN
    assert broken.error is not None

    assert result.packages == []

    builtin = load_profile_registry(_REGISTRY_PATH)
    assert result.merged_registry == builtin

    # route_task must not raise.
    decision = route_task(
        "deploy this to production",
        registry_path=_REGISTRY_PATH,
        policy_path=_POLICY_PATH,
    )
    assert decision.primary_profile in {"engineer", "security_auditor", "general_operator"}


def test_broken_package_missing_required_fields(tmp_path: Path) -> None:
    """A manifest missing required fields is BROKEN, not silent."""
    hermes_home = tmp_path / "hermes_home"
    packages_dir = hermes_home / "role-packages"
    pkg_dir = packages_dir / "incomplete-role"
    bad_manifest = {"schema_version": 1, "package": {"name": "foo"}}  # missing 'role', missing 'version'
    _write_manifest(pkg_dir, bad_manifest)

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)

    assert len(result.broken) == 1
    assert result.broken[0].status == PackageLoadStatus.BROKEN
    assert result.packages == []


# ---------------------------------------------------------------------------
# Test 5: duplicate built-in id rejected
# ---------------------------------------------------------------------------

def test_duplicate_builtin_id_rejected(tmp_path: Path) -> None:
    """A package whose role.id collides with a built-in is DUPLICATE / skipped."""
    hermes_home = tmp_path / "hermes_home"
    packages_dir = hermes_home / "role-packages"
    pkg_dir = packages_dir / "bad-engineer"
    _write_manifest(pkg_dir, _make_valid_manifest(role_id="engineer", canonical_id="engineer"))

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)

    assert len(result.broken) == 1
    assert result.broken[0].status == PackageLoadStatus.DUPLICATE
    assert result.packages == []

    # Built-in engineer profile is untouched.
    builtin = load_profile_registry(_REGISTRY_PATH)
    engineer = next(p for p in builtin["profiles"] if p["id"] == "engineer")
    assert engineer is not None
    assert result.merged_registry == builtin


# ---------------------------------------------------------------------------
# Test 6: route_task survives broken packages
# ---------------------------------------------------------------------------

def test_route_task_survives_broken_packages(tmp_path: Path) -> None:
    """route_task() built-in routing is unaffected when broken packages exist."""
    hermes_home = tmp_path / "hermes_home"
    packages_dir = hermes_home / "role-packages"

    # One valid-ish additive package.
    valid_dir = packages_dir / "sample-role"
    _write_manifest(valid_dir, _make_valid_manifest())

    # One broken package.
    broken_dir = packages_dir / "broken-role"
    broken_dir.mkdir(parents=True)
    (broken_dir / "role-package.yaml").write_text("not: a: valid: manifest", encoding="utf-8")

    result = load_merged_registry(registry_path=_REGISTRY_PATH, hermes_home=hermes_home)
    assert len(result.packages) == 1
    assert len(result.broken) == 1

    # Each built-in routing decision must still work and return expected profiles.
    probes = [
        ("deploy docker systemd rollback", "engineer"),
        ("auth secrets exposure cloudflare firewall", "security_auditor"),
        ("vacancy CV cover letter recruiter", "career_strategist"),
    ]
    for prompt, expected_primary in probes:
        decision = route_task(prompt, registry_path=_REGISTRY_PATH, policy_path=_POLICY_PATH)
        assert decision.primary_profile == expected_primary, (
            f"prompt={prompt!r}: expected {expected_primary!r}, got {decision.primary_profile!r}"
        )


# ---------------------------------------------------------------------------
# Test: discover_package_dirs helper
# ---------------------------------------------------------------------------

def test_discover_package_dirs_empty_when_no_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    assert discover_package_dirs(missing) == []


def test_discover_package_dirs_skips_non_manifest_dirs(tmp_path: Path) -> None:
    (tmp_path / "no-manifest").mkdir()
    with_manifest = tmp_path / "has-manifest"
    with_manifest.mkdir()
    (with_manifest / "role-package.yaml").write_text("x: 1", encoding="utf-8")

    dirs = discover_package_dirs(tmp_path)
    assert dirs == [with_manifest]


# ---------------------------------------------------------------------------
# Test: load_installed_package low-level
# ---------------------------------------------------------------------------

def test_load_installed_package_ok(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "good-pkg"
    _write_manifest(pkg_dir, _make_valid_manifest())

    known: set[str] = set()
    pkg = load_installed_package(pkg_dir, known)

    assert pkg.status == PackageLoadStatus.OK
    assert pkg.role_id == "sample_role"
    # IDs were claimed.
    assert "sample_role" in known


def test_load_installed_package_duplicate(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "dup-pkg"
    _write_manifest(pkg_dir, _make_valid_manifest(role_id="engineer", canonical_id="engineer"))

    known: set[str] = {"engineer"}
    pkg = load_installed_package(pkg_dir, known)

    assert pkg.status == PackageLoadStatus.DUPLICATE
    assert pkg.error is not None
