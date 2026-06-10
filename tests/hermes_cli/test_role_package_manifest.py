"""Tests for role-package.yaml manifest parsing and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.role_packages import (
    VALID_BOUNDARY_MODES,
    PackageLoadStatus,
    RolePackageError,
    _load_manifest,
    validate_manifest_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(pkg_dir: Path, data: dict[str, Any]) -> Path:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    manifest = pkg_dir / "role-package.yaml"
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return manifest


def _valid() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "package": {"name": "sample-role", "version": "0.1.0"},
        "role": {
            "id": "sample_role",
            "canonical_id": "sample_role",
            "display_name": "Sample Role",
        },
        "boundary_mode": "advisory",
    }


# ---------------------------------------------------------------------------
# Valid cases
# ---------------------------------------------------------------------------


def test_valid_minimal_manifest(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "valid"
    _write_manifest(pkg_dir, _valid())

    manifest, errors, warnings = validate_manifest_path(pkg_dir)

    assert manifest is not None
    assert errors == []
    # Warnings for missing optional fields are OK.


def test_valid_all_boundary_modes(tmp_path: Path) -> None:
    for mode in sorted(VALID_BOUNDARY_MODES):
        data = _valid()
        data["boundary_mode"] = mode
        pkg_dir = tmp_path / f"pkg-{mode}"
        _write_manifest(pkg_dir, data)
        manifest, errors, _ = validate_manifest_path(pkg_dir)
        assert errors == [], f"mode {mode!r} unexpectedly failed: {errors}"
        assert manifest is not None


# ---------------------------------------------------------------------------
# Missing manifest
# ---------------------------------------------------------------------------


def test_missing_manifest(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "empty"
    pkg_dir.mkdir()

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("no role-package.yaml" in e for e in errors)


# ---------------------------------------------------------------------------
# Malformed YAML
# ---------------------------------------------------------------------------


def test_malformed_yaml(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "bad-yaml"
    pkg_dir.mkdir()
    (pkg_dir / "role-package.yaml").write_text(": this is: {bad yaml:\n", encoding="utf-8")

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("invalid YAML" in e or "YAML" in e for e in errors)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_package_name(tmp_path: Path) -> None:
    data = _valid()
    del data["package"]["name"]
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("name" in e for e in errors)


def test_missing_package_version(tmp_path: Path) -> None:
    data = _valid()
    del data["package"]["version"]
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("version" in e for e in errors)


def test_missing_role_id(tmp_path: Path) -> None:
    data = _valid()
    del data["role"]["id"]
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("id" in e for e in errors)


def test_missing_role_top_level_field(tmp_path: Path) -> None:
    data = _valid()
    del data["role"]
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert errors


# ---------------------------------------------------------------------------
# Invalid package name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_name", [
    "UpperCase",
    "has spaces",
    "-starts-with-dash",
    "1starts-with-digit",
    "has_underscore",
    "",
])
def test_invalid_package_name(tmp_path: Path, bad_name: str) -> None:
    data = _valid()
    data["package"]["name"] = bad_name
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("name" in e for e in errors), f"expected name error for {bad_name!r}, got: {errors}"


# ---------------------------------------------------------------------------
# Duplicate built-in role id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builtin_id", ["engineer", "security_auditor", "chief_hermes"])
def test_duplicate_builtin_role_id(tmp_path: Path, builtin_id: str) -> None:
    data = _valid()
    data["role"]["id"] = builtin_id
    data["role"]["canonical_id"] = builtin_id
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir, check_builtin_collision=True)

    assert any("collides" in e for e in errors)


# ---------------------------------------------------------------------------
# Invalid boundary_mode
# ---------------------------------------------------------------------------


def test_invalid_boundary_mode(tmp_path: Path) -> None:
    data = _valid()
    data["boundary_mode"] = "totally_invalid"
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("boundary_mode" in e for e in errors)


# ---------------------------------------------------------------------------
# Unsupported schema_version
# ---------------------------------------------------------------------------


def test_unsupported_schema_version(tmp_path: Path) -> None:
    data = _valid()
    data["schema_version"] = 99
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("schema_version" in e for e in errors)


def test_schema_version_string_rejected(tmp_path: Path) -> None:
    data = _valid()
    data["schema_version"] = "1"  # string, not int
    pkg_dir = tmp_path / "pkg"
    _write_manifest(pkg_dir, data)

    _, errors, _ = validate_manifest_path(pkg_dir)

    assert any("schema_version" in e for e in errors)
