"""Tests for role-package.yaml manifest parsing and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.role_packages import (
    VALID_BOUNDARY_MODES,
    VALID_MODEL_TIERS,
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

# ---------------------------------------------------------------------------
# env_requires validation (Slice 5)
# ---------------------------------------------------------------------------


class TestEnvRequiresValidation:
    def _pkg(self, env_requires=None, **extra):
        data = {
            "schema_version": 1,
            "package": {"name": "env-test-role", "version": "0.1.0"},
            "role": {
                "id": "env_test_role",
                "canonical_id": "env_test_role",
                "display_name": "Env Test Role",
            },
            "boundary_mode": "advisory",
        }
        if env_requires is not None:
            data["env_requires"] = env_requires
        data.update(extra)
        return data

    def test_valid_env_requires_accepted(self, tmp_path):
        data = self._pkg(env_requires=[
            {"name": "SAMPLE_FAKE_TOKEN", "description": "Fake token for tests", "required": False}
        ])
        _write_manifest(tmp_path / "pkg", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "pkg")
        assert errors == []
        assert manifest is not None

    def test_wildcard_env_name_rejected(self, tmp_path):
        for bad_name in ["*", "FOO_*", "*_TOKEN", "FOO*"]:
            data = self._pkg(env_requires=[{"name": bad_name}])
            _write_manifest(tmp_path / bad_name.replace("*", "STAR"), data)
            _, errors, _ = validate_manifest_path(tmp_path / bad_name.replace("*", "STAR"))
            assert any("wildcard" in e.lower() or "invalid" in e.lower() for e in errors), (
                f"Expected error for wildcard env name {bad_name!r}, got: {errors}"
            )

    def test_invalid_env_var_name_rejected(self, tmp_path):
        bad_names = ["123STARTS_WITH_DIGIT", "has-hyphen", "has space", ""]
        for idx, bad_name in enumerate(bad_names):
            data = self._pkg(env_requires=[{"name": bad_name}])
            pkg_dir = tmp_path / f"bad_name_{idx}"
            _write_manifest(pkg_dir, data)
            _, errors, _ = validate_manifest_path(pkg_dir)
            assert errors, f"Expected error for invalid env name {bad_name!r}"

    def test_missing_name_field_rejected(self, tmp_path):
        data = self._pkg(env_requires=[{"description": "no name field"}])
        _write_manifest(tmp_path / "noname", data)
        _, errors, _ = validate_manifest_path(tmp_path / "noname")
        assert errors

    def test_env_requires_not_a_list_rejected(self, tmp_path):
        data = self._pkg()
        data["env_requires"] = "SINGLE_STRING"
        _write_manifest(tmp_path / "notlist", data)
        _, errors, _ = validate_manifest_path(tmp_path / "notlist")
        assert errors

    def test_default_value_field_rejected(self, tmp_path):
        data = self._pkg(env_requires=[
            {"name": "SAMPLE_FAKE_TOKEN", "default": "some_default_value"}
        ])
        _write_manifest(tmp_path / "withdefault", data)
        _, errors, _ = validate_manifest_path(tmp_path / "withdefault")
        assert any("default" in e.lower() or "value" in e.lower() for e in errors)

    def test_secret_looking_description_warns(self, tmp_path):
        # sk-* pattern in description produces a warning, not a hard error.
        data = self._pkg(env_requires=[
            {"name": "SAMPLE_FAKE_TOKEN", "description": "sk-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"}
        ])
        _write_manifest(tmp_path / "secretdesc", data)
        _, errors, warnings = validate_manifest_path(tmp_path / "secretdesc")
        assert errors == [], "sk-* in description should be a warning, not a hard error"
        assert any("secret" in w.lower() or "sk-" in w for w in warnings), (
            f"Expected warning for sk-* in description, got warnings={warnings}"
        )

    def test_absent_env_requires_is_ok(self, tmp_path):
        data = self._pkg()  # no env_requires key
        _write_manifest(tmp_path / "noenv", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "noenv")
        assert errors == []
        assert manifest is not None


# ---------------------------------------------------------------------------
# role.tools validation (Slice 6)
# ---------------------------------------------------------------------------


class TestRoleToolsValidation:
    def _pkg(self, role_tools=None, boundary_mode="observe_warn"):
        data = {
            "schema_version": 1,
            "package": {"name": "policy-role", "version": "0.1.0"},
            "role": {
                "id": "policy_role",
                "canonical_id": "policy_role",
                "display_name": "Policy Role",
            },
            "boundary_mode": boundary_mode,
        }
        if role_tools is not None:
            data["role"]["tools"] = role_tools
        return data

    def test_valid_allowed_categories_accepted(self, tmp_path):
        data = self._pkg(role_tools={"allowed_categories": ["read_only_inspection", "repo_edit"]})
        _write_manifest(tmp_path / "valid_allowed", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "valid_allowed")
        assert errors == []
        assert manifest is not None

    def test_valid_denied_categories_accepted(self, tmp_path):
        data = self._pkg(role_tools={"denied_categories": ["production_deploy", "secrets_read"]})
        _write_manifest(tmp_path / "valid_denied", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "valid_denied")
        assert errors == []
        assert manifest is not None

    def test_unknown_category_in_allowed_rejected(self, tmp_path):
        data = self._pkg(role_tools={"allowed_categories": ["read_only_inspection", "totally_made_up"]})
        _write_manifest(tmp_path / "bad_allowed", data)
        _, errors, _ = validate_manifest_path(tmp_path / "bad_allowed")
        assert any("totally_made_up" in e or "unknown" in e.lower() for e in errors), errors

    def test_unknown_category_in_denied_rejected(self, tmp_path):
        data = self._pkg(role_tools={"denied_categories": ["nonexistent_cat"]})
        _write_manifest(tmp_path / "bad_denied", data)
        _, errors, _ = validate_manifest_path(tmp_path / "bad_denied")
        assert any("nonexistent_cat" in e or "unknown" in e.lower() for e in errors), errors

    def test_non_list_allowed_categories_rejected(self, tmp_path):
        data = self._pkg(role_tools={"allowed_categories": "read_only_inspection"})
        _write_manifest(tmp_path / "non_list", data)
        _, errors, _ = validate_manifest_path(tmp_path / "non_list")
        assert errors

    def test_duplicate_category_in_allowed_rejected(self, tmp_path):
        data = self._pkg(role_tools={"allowed_categories": ["read_only_inspection", "read_only_inspection"]})
        _write_manifest(tmp_path / "dup_allowed", data)
        _, errors, _ = validate_manifest_path(tmp_path / "dup_allowed")
        assert errors

    def test_duplicate_category_in_denied_rejected(self, tmp_path):
        data = self._pkg(role_tools={"denied_categories": ["production_deploy", "production_deploy"]})
        _write_manifest(tmp_path / "dup_denied", data)
        _, errors, _ = validate_manifest_path(tmp_path / "dup_denied")
        assert errors

    def test_no_role_tools_is_valid(self, tmp_path):
        data = self._pkg()  # no role.tools
        _write_manifest(tmp_path / "no_tools", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "no_tools")
        assert errors == []
        assert manifest is not None

    def test_advisory_mode_no_role_tools_is_valid(self, tmp_path):
        data = self._pkg(boundary_mode="advisory")
        _write_manifest(tmp_path / "advisory_no_tools", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "advisory_no_tools")
        assert errors == []


# ---------------------------------------------------------------------------
# model_tier_request validation
# ---------------------------------------------------------------------------


class TestModelTierValidation:
    """Tests for role.model_tier_request field validation."""

    def _pkg(self, model_tier=None, role_family=None):
        data = {
            "schema_version": 1,
            "package": {"name": "tier-test-role", "version": "0.1.0"},
            "role": {
                "id": "tier_test_role",
                "canonical_id": "tier_test_role",
                "display_name": "Tier Test Role",
            },
            "boundary_mode": "advisory",
        }
        if model_tier is not None:
            data["role"]["model_tier_request"] = model_tier
        if role_family is not None:
            data["role"]["role_family"] = role_family
        return data

    def test_valid_model_tiers_constant(self):
        assert VALID_MODEL_TIERS == frozenset({"standard", "reasoning", "critical"})

    def test_standard_accepted(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="standard"))
        manifest, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert errors == []

    def test_reasoning_accepted(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="reasoning"))
        manifest, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert errors == []

    def test_critical_accepted_for_security_family(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="critical", role_family="security"))
        manifest, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert errors == []

    def test_critical_accepted_for_security_auditor_family(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="critical", role_family="security_auditor"))
        manifest, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert errors == []

    def test_critical_rejected_for_non_security_family(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="critical", role_family="engineering"))
        _, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert any("critical" in e for e in errors), f"expected critical rejection error, got: {errors}"

    def test_critical_rejected_for_advisor_family(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="critical", role_family="advisor"))
        _, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert any("critical" in e for e in errors)

    def test_unknown_tier_rejected(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="experimental"))
        _, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert any("Invalid model_tier_request" in e or "experimental" in e for e in errors), (
            f"expected unknown tier rejection, got: {errors}"
        )

    def test_unknown_tier_error_message_is_clear(self, tmp_path):
        _write_manifest(tmp_path / "p", self._pkg(model_tier="unknown_value"))
        _, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        msg = " ".join(errors)
        assert "Invalid model_tier_request" in msg
        assert "standard" in msg
        assert "reasoning" in msg
        assert "critical" in msg

    def test_omitted_model_tier_defaults_to_standard(self, tmp_path):
        # Omitting model_tier_request is valid; defaults to standard behaviour.
        data = self._pkg()  # no model_tier
        _write_manifest(tmp_path / "p", data)
        manifest, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert errors == []

    def test_critical_without_role_family_rejected(self, tmp_path):
        # critical with no role_family (empty string treated as unknown family)
        _write_manifest(tmp_path / "p", self._pkg(model_tier="critical"))
        _, errors, _ = validate_manifest_path(tmp_path / "p", check_builtin_collision=False)
        assert any("critical" in e for e in errors)
