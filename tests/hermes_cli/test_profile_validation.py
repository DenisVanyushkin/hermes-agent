"""Tests for the Hermes profile/model validator CLI entrypoint."""

from pathlib import Path
import subprocess
import sys

import yaml

from hermes_cli.profile_validation import validate_profile_architecture


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate_profile_architecture.py"
CANONICAL_REGISTRY = REPO_ROOT / "config" / "hermes-profiles.yaml"
CANONICAL_POLICY = REPO_ROOT / "config" / "hermes-model-policy.yaml"


def test_validator_script_exits_zero_on_canonical_configs():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "error" not in result.stdout.lower()


def test_validator_returns_issues_for_missing_tier(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    policy["tiers"].pop("reasoning")

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("reasoning" in issue.message.lower() for issue in issues)


def test_validation_rejects_unknown_canonical_role(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    registry["profiles"][0]["profile_contract"]["canonical_id"] = "ghost_coordinator"

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("ghost_coordinator" in issue.message or "canonical" in issue.message.lower() for issue in issues)


def test_validation_rejects_unknown_tool_category(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    registry["profiles"][1]["profile_contract"]["tool_contract"]["allowed_by_default"][0] = "alien_tool"

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("alien_tool" in issue.message for issue in issues)


def test_validation_rejects_missing_role_policy(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    policy["role_policies"].pop("general_operator")

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("missing required roles" in issue.message.lower() and "general_operator" in issue.message for issue in issues)


def test_validation_rejects_missing_fallback_policy(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    policy.pop("fallback_selection_policy")

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("fallback_selection_policy" in issue.message for issue in issues)


def test_validation_rejects_malformed_refresh_policy(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    policy["fallback_selection_policy"]["refresh"]["update_source_config"] = True

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("update_source_config" in issue.message or "fallback_selection_policy.refresh" in issue.message for issue in issues)


def test_validation_rejects_missing_critical_guards(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    policy["model_governance"]["critical_action_free_fallback_not_final_authority"] = []

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("critical_action_free_fallback_not_final_authority" in issue.message for issue in issues)
