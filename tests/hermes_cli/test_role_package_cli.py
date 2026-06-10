"""Tests for the hermes role CLI lifecycle.

All tests use isolated HERMES_HOME via monkeypatch.setenv so they never
touch the real ~/.hermes/ directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.role_packages import (
    PackageLoadStatus,
    RolePackageError,
    default_hermes_home,
    get_package_info,
    get_role_packages_dir,
    install_package,
    list_packages,
    read_lockfile,
    remove_package,
    validate_manifest_path,
)
from hermes_cli.profile_routing import (
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    route_task,
)

_REGISTRY_PATH = DEFAULT_PROFILE_REGISTRY_PATH
_POLICY_PATH = DEFAULT_MODEL_POLICY_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    role_id: str = "test_role",
    name: str = "test-role",
    version: str = "0.1.0",
    boundary_mode: str = "advisory",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "package": {"name": name, "version": version, "description": "Test role"},
        "role": {
            "id": role_id,
            "canonical_id": role_id,
            "display_name": "Test Role",
            "role_family": "test",
            "purpose_summary": "A test role for CLI lifecycle tests",
        },
        "boundary_mode": boundary_mode,
    }


def _make_package_dir(tmp_path: Path, **kwargs) -> Path:
    pkg_dir = tmp_path / "source-pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "role-package.yaml").write_text(
        yaml.safe_dump(_make_manifest(**kwargs), sort_keys=False), encoding="utf-8"
    )
    return pkg_dir


@pytest.fixture()
def hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated HERMES_HOME for each test."""
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


# ---------------------------------------------------------------------------
# Install valid local package
# ---------------------------------------------------------------------------


def test_install_valid_package(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)

    pkg = install_package(source, hermetic_home)

    assert pkg.status == PackageLoadStatus.OK
    assert pkg.name == "test-role"
    assert pkg.version == "0.1.0"
    assert pkg.role_id == "test_role"

    # Payload copied.
    dest = get_role_packages_dir(hermetic_home) / "test-role"
    assert dest.is_dir()
    assert (dest / "role-package.yaml").exists()


def test_install_creates_lockfile_entry(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    packages_dir = get_role_packages_dir(hermetic_home)
    lock = read_lockfile(packages_dir)

    assert "test-role" in lock["packages"]
    entry = lock["packages"]["test-role"]
    assert entry["role_id"] == "test_role"
    assert entry["status"] == "active"
    assert entry["source_type"] == "local"
    assert entry["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# List shows installed package
# ---------------------------------------------------------------------------


def test_list_shows_installed_package(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    packages = list_packages(hermetic_home)

    assert len(packages) == 1
    p = packages[0]
    assert p["name"] == "test-role"
    assert p["status"] == "active"


def test_list_empty_when_nothing_installed(hermetic_home: Path) -> None:
    packages = list_packages(hermetic_home)
    assert packages == []


# ---------------------------------------------------------------------------
# Info shows package details
# ---------------------------------------------------------------------------


def test_info_shows_package_details(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    info = get_package_info("test-role", hermetic_home)

    assert info is not None
    assert info["name"] == "test-role"
    assert info["version"] == "0.1.0"
    assert info["role_id"] == "test_role"
    assert "install_path" in info


def test_info_returns_none_for_unknown(hermetic_home: Path) -> None:
    info = get_package_info("nonexistent", hermetic_home)
    assert info is None


# ---------------------------------------------------------------------------
# Validate installed package
# ---------------------------------------------------------------------------


def test_validate_installed_package_succeeds(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    dest = get_role_packages_dir(hermetic_home) / "test-role"
    manifest, errors, _ = validate_manifest_path(dest)

    assert errors == []
    assert manifest is not None


def test_validate_local_path_succeeds(tmp_path: Path) -> None:
    source = _make_package_dir(tmp_path)
    manifest, errors, _ = validate_manifest_path(source)
    assert errors == []
    assert manifest is not None


# ---------------------------------------------------------------------------
# Remove package
# ---------------------------------------------------------------------------


def test_remove_clears_payload_and_lockfile(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    remove_package("test-role", hermetic_home)

    packages_dir = get_role_packages_dir(hermetic_home)
    assert not (packages_dir / "test-role").exists()

    lock = read_lockfile(packages_dir)
    assert "test-role" not in lock.get("packages", {})


def test_remove_not_found_raises(hermetic_home: Path) -> None:
    with pytest.raises(RolePackageError, match="not installed"):
        remove_package("nonexistent", hermetic_home)


# ---------------------------------------------------------------------------
# Install failure cases
# ---------------------------------------------------------------------------


def test_install_invalid_manifest_fails_with_no_residue(tmp_path: Path, hermetic_home: Path) -> None:
    source = tmp_path / "bad-pkg"
    source.mkdir()
    (source / "role-package.yaml").write_text("not: valid: yaml:\n", encoding="utf-8")

    with pytest.raises(RolePackageError):
        install_package(source, hermetic_home)

    # No payload left behind.
    packages_dir = get_role_packages_dir(hermetic_home)
    assert not (packages_dir / "bad-pkg").exists()
    lock = read_lockfile(packages_dir)
    assert "bad-pkg" not in lock.get("packages", {})


def test_install_duplicate_builtin_id_fails(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path, role_id="engineer", name="bad-engineer")

    with pytest.raises(RolePackageError, match="collides"):
        install_package(source, hermetic_home)


def test_install_missing_source_dir_fails(tmp_path: Path, hermetic_home: Path) -> None:
    with pytest.raises(RolePackageError, match="not a directory"):
        install_package(tmp_path / "does-not-exist", hermetic_home)


def test_install_same_package_twice_fails(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    with pytest.raises(RolePackageError, match="already installed"):
        install_package(source, hermetic_home)


def test_install_force_overwrites(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    # Force reinstall with different version.
    updated_manifest = _make_manifest(version="0.2.0")
    (source / "role-package.yaml").write_text(
        yaml.safe_dump(updated_manifest, sort_keys=False), encoding="utf-8"
    )
    pkg = install_package(source, hermetic_home, force=True)

    assert pkg.version == "0.2.0"
    packages_dir = get_role_packages_dir(hermetic_home)
    lock = read_lockfile(packages_dir)
    assert lock["packages"]["test-role"]["version"] == "0.2.0"


# ---------------------------------------------------------------------------
# Parity: golden routing corpus and built-in probes after install/remove
# ---------------------------------------------------------------------------


def test_builtin_routing_unaffected_after_install(tmp_path: Path, hermetic_home: Path) -> None:
    """Installing a package must not change built-in routing decisions."""
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    probes = [
        ("deploy docker systemd rollback", "engineer"),
        ("auth secrets exposure cloudflare", "security_auditor"),
        ("vacancy CV cover letter recruiter", "career_strategist"),
    ]
    for prompt, expected in probes:
        decision = route_task(prompt, registry_path=_REGISTRY_PATH, policy_path=_POLICY_PATH)
        assert decision.primary_profile == expected, (
            f"routing changed after install: {prompt!r} → {decision.primary_profile!r} (expected {expected!r})"
        )


def test_builtin_routing_unaffected_after_remove(tmp_path: Path, hermetic_home: Path) -> None:
    """Removing a package must not change built-in routing decisions."""
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)
    remove_package("test-role", hermetic_home)

    decision = route_task(
        "deploy docker systemd rollback",
        registry_path=_REGISTRY_PATH,
        policy_path=_POLICY_PATH,
    )
    assert decision.primary_profile == "engineer"


def test_profile_validation_still_passes_after_install(tmp_path: Path, hermetic_home: Path) -> None:
    """Profile architecture validation must remain clean after package install."""
    from hermes_cli.profile_validation import validate_profile_architecture

    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    issues = validate_profile_architecture(_REGISTRY_PATH, _POLICY_PATH)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"validation errors after install: {errors}"


# ---------------------------------------------------------------------------
# Multiple packages
# ---------------------------------------------------------------------------


def test_install_multiple_packages(tmp_path: Path, hermetic_home: Path) -> None:
    for i in range(3):
        src = tmp_path / f"pkg-{i}"
        src.mkdir()
        data = _make_manifest(role_id=f"role_{i}", name=f"role-{i}")
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        install_package(src, hermetic_home)

    packages = list_packages(hermetic_home)
    assert len(packages) == 3
    names = {p["name"] for p in packages}
    assert names == {"role-0", "role-1", "role-2"}


def test_remove_one_of_two_leaves_other(tmp_path: Path, hermetic_home: Path) -> None:
    for i in range(2):
        src = tmp_path / f"pkg-{i}"
        src.mkdir()
        data = _make_manifest(role_id=f"role_{i}", name=f"role-{i}")
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        install_package(src, hermetic_home)

    remove_package("role-0", hermetic_home)

    packages = list_packages(hermetic_home)
    assert len(packages) == 1
    assert packages[0]["name"] == "role-1"
