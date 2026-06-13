"""Tests for selected-role package activation."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from hermes_cli.config import DEFAULT_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[2]
SHADOW_DIR = REPO_ROOT / "tests" / "fixtures" / "role_packages" / "core-shadow"

# Stable golden-corpus prompts — one per role, deterministic routing confirmed.
# Source: tests/fixtures/role_packages/golden_routing_corpus.yaml
_GOLDEN = {
    "engineer":          "The database migration failed — prepare a rollback plan",
    "scribe":            "Capture durable memory of today's work and capture the outcome",
    "researcher":        "Compare bitcoin fees on binance and coinbase",
    "security_auditor":  "Review the auth tokens and document the security findings",
    "career_strategist": "Help me update my CV and cover letter for this application",
}

from hermes_cli.role_package_activation import (
    RolePackageRoutingConfig,
    PackageActivationResult,
    load_role_package_routing_config,
    render_package_role_context,
    activate_package_for_role,
)

ROLE_TO_PKG = {
    "scribe": "hermes-scribe-core",
    "researcher": "hermes-researcher-core",
    "engineer": "hermes-engineer-core",
    "security_auditor": "hermes-security-auditor-core",
    "career_strategist": "hermes-career-strategist-core",
}


def _load_manifest(pkg_dir: Path) -> dict:
    return yaml.safe_load((pkg_dir / "role-package.yaml").read_text(encoding="utf-8"))


class TestDefaultConfig:
    def test_role_packages_key_present(self):
        assert "role_packages" in DEFAULT_CONFIG

    def test_role_packages_routing_disabled_by_default(self):
        routing = DEFAULT_CONFIG["role_packages"]["routing"]
        assert routing["enabled"] is False

    def test_role_packages_routing_has_required_keys(self):
        routing = DEFAULT_CONFIG["role_packages"]["routing"]
        assert "activation_mode" in routing
        assert "active_roles" in routing
        assert "package_path" in routing
        assert "fallback_to_builtin" in routing

    def test_role_packages_active_roles_empty_by_default(self):
        routing = DEFAULT_CONFIG["role_packages"]["routing"]
        assert routing["active_roles"] == []

    def test_role_packages_fallback_true_by_default(self):
        routing = DEFAULT_CONFIG["role_packages"]["routing"]
        assert routing["fallback_to_builtin"] is True


class TestLoadRolePackageRoutingConfig:
    def test_disabled_by_default_when_missing(self):
        cfg = load_role_package_routing_config({})
        assert cfg.enabled is False
        assert cfg.activation_mode == "selected_roles"
        assert cfg.active_roles == []
        assert cfg.package_path is None
        assert cfg.fallback_to_builtin is True

    def test_parses_enabled_config(self):
        raw = {
            "role_packages": {
                "routing": {
                    "enabled": True,
                    "activation_mode": "selected_roles",
                    "active_roles": ["scribe", "engineer"],
                    "package_path": "/some/path",
                    "fallback_to_builtin": True,
                }
            }
        }
        cfg = load_role_package_routing_config(raw)
        assert cfg.enabled is True
        assert cfg.active_roles == ["scribe", "engineer"]
        assert cfg.package_path == "/some/path"

    def test_garbage_routing_section_safe_defaults(self):
        raw = {"role_packages": {"routing": "not-a-dict"}}
        cfg = load_role_package_routing_config(raw)
        assert cfg.enabled is False

    def test_null_package_path_remains_none(self):
        raw = {"role_packages": {"routing": {"enabled": True, "package_path": None}}}
        cfg = load_role_package_routing_config(raw)
        assert cfg.package_path is None


class TestRenderPackageRoleContext:
    def test_contains_package_activation_marker(self):
        manifest = _load_manifest(SHADOW_DIR / "hermes-scribe-core")
        ctx = render_package_role_context(manifest, "scribe")
        assert "[Package role context active via selected-role activation]" in ctx

    def test_does_not_claim_enforced_tools_active(self):
        for builtin_role, pkg_dir in ROLE_TO_PKG.items():
            manifest = _load_manifest(SHADOW_DIR / pkg_dir)
            ctx = render_package_role_context(manifest, builtin_role)
            assert "enforced_tools is NOT active" in ctx, f"missing disclaimer in {pkg_dir}"

    def test_does_not_claim_package_triggers_active(self):
        for builtin_role, pkg_dir in ROLE_TO_PKG.items():
            manifest = _load_manifest(SHADOW_DIR / pkg_dir)
            ctx = render_package_role_context(manifest, builtin_role)
            assert "Package triggers are NOT active" in ctx

    def test_does_not_claim_secrets_read_allowed(self):
        for builtin_role, pkg_dir in ROLE_TO_PKG.items():
            manifest = _load_manifest(SHADOW_DIR / pkg_dir)
            ctx = render_package_role_context(manifest, builtin_role)
            assert "secrets_read is NOT allowed" in ctx

    def test_does_not_claim_production_deploy_without_approval(self):
        for builtin_role, pkg_dir in ROLE_TO_PKG.items():
            manifest = _load_manifest(SHADOW_DIR / pkg_dir)
            ctx = render_package_role_context(manifest, builtin_role)
            assert "explicit approval" in ctx

    def test_model_tier_metadata_only(self):
        manifest = _load_manifest(SHADOW_DIR / "hermes-engineer-core")
        ctx = render_package_role_context(manifest, "engineer")
        assert "model_tier_request" in ctx
        assert "metadata only" in ctx

    def test_engineer_approval_boundary_present(self):
        manifest = _load_manifest(SHADOW_DIR / "hermes-engineer-core")
        ctx = render_package_role_context(manifest, "engineer")
        assert "Production/runtime mutation requires explicit approval" in ctx

    def test_security_auditor_reviewer_disclaimer(self):
        manifest = _load_manifest(SHADOW_DIR / "hermes-security-auditor-core")
        ctx = render_package_role_context(manifest, "security_auditor")
        assert "reviewer, not a universal blocker" in ctx


def test_engineer_package_activation_keeps_builtin_coding_model_resolution():
    from hermes_cli.profile_routing import route_task

    cfg = RolePackageRoutingConfig(
        enabled=True,
        activation_mode="selected_roles",
        active_roles=["engineer"],
        package_path=str(SHADOW_DIR),
        fallback_to_builtin=True,
    )
    task = "check docker logs for errors"
    decision = route_task(task)
    result = activate_package_for_role("engineer", cfg, prompt_text=task)

    assert result.activated is True
    assert result.package_name == "hermes-engineer-core"
    assert decision.primary_profile == "engineer"
    assert decision.route_chain[0].model_tier == "coding"
    assert decision.route_chain[0].provider == "openrouter"
    assert decision.route_chain[0].model == "xiaomi/mimo-v2.5-pro"


class TestActivatePackageForRole:

    def _cfg(self, **overrides) -> RolePackageRoutingConfig:
        defaults = dict(
            enabled=True,
            activation_mode="selected_roles",
            active_roles=["scribe", "researcher", "engineer", "security_auditor", "career_strategist"],
            package_path=str(SHADOW_DIR),
            fallback_to_builtin=True,
        )
        defaults.update(overrides)
        return RolePackageRoutingConfig(**defaults)

    def test_disabled_all_roles_fallback(self):
        cfg = self._cfg(enabled=False)
        for role in ["scribe", "researcher", "engineer", "security_auditor", "career_strategist"]:
            result = activate_package_for_role(role, cfg)
            assert result.activated is False, f"expected fallback for {role}"
            assert result.fallback is True
            assert result.reason == "disabled"

    @pytest.mark.parametrize("builtin_role,expected_pkg_role_id", [
        ("scribe", "hermes_scribe_core"),
        ("researcher", "hermes_researcher_core"),
        ("engineer", "hermes_engineer_core"),
        ("security_auditor", "hermes_security_auditor_core"),
        ("career_strategist", "hermes_career_strategist_core"),
    ])
    def test_all_five_roles_activate(self, builtin_role, expected_pkg_role_id):
        cfg = self._cfg()
        result = activate_package_for_role(builtin_role, cfg, prompt_text="test prompt")
        assert result.activated is True, f"{builtin_role}: expected activated, reason={result.reason}"
        assert result.fallback is False
        assert result.reason == "activated"
        assert result.package_role_id == expected_pkg_role_id
        assert result.context_text != ""

    def test_general_operator_not_activated(self):
        cfg = self._cfg(active_roles=["scribe", "engineer", "general_operator"])
        result = activate_package_for_role("general_operator", cfg)
        assert result.activated is False
        assert result.reason == "package_missing"

    def test_role_not_in_active_roles_fallback(self):
        cfg = self._cfg(active_roles=["scribe"])
        result = activate_package_for_role("engineer", cfg)
        assert result.activated is False
        assert result.reason == "role_not_enabled"
        assert result.fallback is True

    def test_missing_package_path_fallback(self):
        cfg = self._cfg(package_path=None)
        result = activate_package_for_role("scribe", cfg)
        assert result.activated is False
        assert result.reason == "package_missing"

    def test_nonexistent_package_path_fallback(self, tmp_path):
        cfg = self._cfg(package_path=str(tmp_path / "no-such-dir"))
        result = activate_package_for_role("scribe", cfg)
        assert result.activated is False
        assert result.reason == "package_missing"

    def test_invalid_manifest_fallback(self, tmp_path):
        pkg_dir = tmp_path / "hermes-scribe-core"
        pkg_dir.mkdir()
        (pkg_dir / "role-package.yaml").write_text(": bad yaml:\n", encoding="utf-8")
        cfg = self._cfg(package_path=str(tmp_path))
        result = activate_package_for_role("scribe", cfg)
        assert result.activated is False
        assert result.reason == "validation_error"

    def test_role_id_mismatch_fallback(self, tmp_path):
        pkg_dir = tmp_path / "hermes-scribe-core"
        pkg_dir.mkdir()
        manifest_data = {
            "schema_version": 1,
            "package": {"name": "hermes-scribe-core", "version": "0.1.0"},
            "role": {
                "id": "wrong_id",
                "canonical_id": "wrong_id",
                "display_name": "Wrong",
            },
            "boundary_mode": "advisory",
        }
        (pkg_dir / "role-package.yaml").write_text(
            yaml.safe_dump(manifest_data), encoding="utf-8"
        )
        cfg = self._cfg(package_path=str(tmp_path))
        result = activate_package_for_role("scribe", cfg)
        assert result.activated is False
        assert result.reason == "validation_error"

    def test_activation_event_is_jsonl(self, caplog):
        cfg = self._cfg()
        with caplog.at_level(logging.INFO, logger="hermes_cli.role_package_activation"):
            activate_package_for_role("scribe", cfg, prompt_text="hello world")
        activation_records = [
            r for r in caplog.records
            if "PACKAGE_ROLE_ACTIVATION_EVENT" in r.message
        ]
        assert activation_records, "no PACKAGE_ROLE_ACTIVATION_EVENT log record found"
        msg = activation_records[-1].message
        assert msg.startswith("PACKAGE_ROLE_ACTIVATION_EVENT ")
        payload = json.loads(msg[len("PACKAGE_ROLE_ACTIVATION_EVENT "):])
        assert payload["event"] == "package_role_activation"
        assert payload["activation_mode"] == "selected_roles"
        assert payload["builtin_primary_role"] == "scribe"
        assert payload["package_role_id"] == "hermes_scribe_core"
        assert payload["activated"] is True
        assert payload["fallback"] is False
        assert payload["reason"] == "activated"
        assert payload["prompt_hash"].startswith("sha256:")
        assert len(payload["prompt_hash"]) == len("sha256:") + 12
        assert isinstance(payload["ts"], int)
        # Must NOT contain full prompt text
        assert "hello world" not in msg

    def test_no_event_logged_when_disabled(self, caplog):
        cfg = self._cfg(enabled=False)
        with caplog.at_level(logging.INFO, logger="hermes_cli.role_package_activation"):
            activate_package_for_role("scribe", cfg, prompt_text="hello")
        activation_records = [
            r for r in caplog.records
            if "PACKAGE_ROLE_ACTIVATION_EVENT" in r.message
        ]
        assert activation_records == [], "disabled path must not emit activation events"

    @pytest.mark.parametrize("builtin_role", [
        "scribe", "researcher", "engineer", "security_auditor", "career_strategist"
    ])
    def test_activated_context_does_not_claim_enforced_tools(self, builtin_role):
        cfg = self._cfg()
        result = activate_package_for_role(builtin_role, cfg)
        assert result.activated is True
        assert "enforced_tools is NOT active" in result.context_text

    @pytest.mark.parametrize("builtin_role", [
        "scribe", "researcher", "engineer", "security_auditor", "career_strategist"
    ])
    def test_activated_context_does_not_allow_secrets_read(self, builtin_role):
        cfg = self._cfg()
        result = activate_package_for_role(builtin_role, cfg)
        assert "secrets_read is NOT allowed" in result.context_text

    @pytest.mark.parametrize("builtin_role", [
        "scribe", "researcher", "engineer", "security_auditor", "career_strategist"
    ])
    def test_activated_context_requires_approval_for_production(self, builtin_role):
        cfg = self._cfg()
        result = activate_package_for_role(builtin_role, cfg)
        assert "explicit approval" in result.context_text


from hermes_cli.profile_context import RoleContextResult
from hermes_cli.profile_routing import route_task
from hermes_cli.profile_context import build_role_context_for_task

_REGISTRY_PATH = REPO_ROOT / "config" / "hermes-profiles.yaml"
_POLICY_PATH = REPO_ROOT / "config" / "hermes-model-policy.yaml"


def _active_cfg() -> RolePackageRoutingConfig:
    return RolePackageRoutingConfig(
        enabled=True,
        activation_mode="selected_roles",
        active_roles=["scribe", "researcher", "engineer", "security_auditor", "career_strategist"],
        package_path=str(SHADOW_DIR),
        fallback_to_builtin=True,
    )


def _disabled_cfg() -> RolePackageRoutingConfig:
    return RolePackageRoutingConfig(
        enabled=False,
        activation_mode="selected_roles",
        active_roles=[],
        package_path=str(SHADOW_DIR),
        fallback_to_builtin=True,
    )


class TestRoleContextResultContextSource:
    def test_default_context_source_is_builtin(self):
        result = RoleContextResult(
            task="test",
            selected_role="scribe",
            canonical_role="scribe",
            context_text="hello",
            profile_context_used=True,
        )
        assert result.context_source == "builtin"

    def test_context_source_can_be_package(self):
        result = RoleContextResult(
            task="test",
            selected_role="scribe",
            canonical_role="scribe",
            context_text="hello",
            profile_context_used=True,
            context_source="package",
        )
        assert result.context_source == "package"


class TestBuildRoleContextForTaskWithPackages:

    def test_disabled_config_uses_builtin_context(self):
        route = route_task(
            _GOLDEN["scribe"],
            registry_path=_REGISTRY_PATH,
            policy_path=_POLICY_PATH,
        )
        result = build_role_context_for_task(
            _GOLDEN["scribe"],
            route_decision=route,
            role_package_routing_config=_disabled_cfg(),
        )
        assert result.selected_role == "scribe"
        assert result.context_source == "builtin"
        assert "[Package role context active" not in result.context_text

    @pytest.mark.parametrize("role", [
        "scribe", "researcher", "engineer", "security_auditor", "career_strategist"
    ])
    def test_all_five_roles_activate_via_build_role_context(self, role):
        route = route_task(
            _GOLDEN[role],
            registry_path=_REGISTRY_PATH,
            policy_path=_POLICY_PATH,
        )
        assert route.primary_profile == role, (
            f"golden corpus routing changed: expected {role!r}, got {route.primary_profile!r}"
        )
        result = build_role_context_for_task(
            _GOLDEN[role],
            route_decision=route,
            role_package_routing_config=_active_cfg(),
        )
        assert result.selected_role == role
        assert result.context_source == "package", (
            f"expected package context for {role}, got {result.context_source!r}"
        )
        assert "[Package role context active" in result.context_text
        assert result.profile_context_used is True

    def test_package_context_preserves_approval_fields(self):
        from hermes_cli.profile_execution import build_role_execution_plan
        from hermes_cli.profile_routing import route_task as _route_task

        route = _route_task(
            _GOLDEN["engineer"],
            registry_path=_REGISTRY_PATH,
            policy_path=_POLICY_PATH,
        )
        expected_plan = build_role_execution_plan(_GOLDEN["engineer"], route_decision=route)

        result = build_role_context_for_task(
            _GOLDEN["engineer"],
            route_decision=route,
            role_package_routing_config=_active_cfg(),
        )
        assert result.context_source == "package"
        # Approval fields must come from the execution plan, not be lost or defaulted
        assert result.requires_explicit_approval == expected_plan.requires_explicit_approval
        assert result.critical_approval_required == expected_plan.critical_approval_required
        assert result.requires_reviewer == expected_plan.requires_reviewer
        assert result.operation_category == expected_plan.operation_category
        assert result.approval_reason == expected_plan.approval_reason

    def test_missing_package_path_falls_back_to_builtin(self):
        bad_cfg = RolePackageRoutingConfig(
            enabled=True,
            activation_mode="selected_roles",
            active_roles=["scribe"],
            package_path="/nonexistent/path/to/packages",
            fallback_to_builtin=True,
        )
        route = route_task(
            _GOLDEN["scribe"],
            registry_path=_REGISTRY_PATH,
            policy_path=_POLICY_PATH,
        )
        result = build_role_context_for_task(
            _GOLDEN["scribe"],
            route_decision=route,
            role_package_routing_config=bad_cfg,
        )
        assert result.context_source == "builtin"
        assert result.selected_role == "scribe"
