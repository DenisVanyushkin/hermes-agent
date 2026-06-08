"""Tests for the machine-readable Hermes profile registry."""

from pathlib import Path

import yaml

from hermes_cli.profile_validation import (
    DEFAULT_PROFILE_REGISTRY_PATH,
    DEFAULT_MODEL_POLICY_PATH,
    validate_profile_architecture,
    validate_profile_registry,
    validate_model_policy,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_REGISTRY = REPO_ROOT / "config" / "hermes-profiles.yaml"
CANONICAL_POLICY = REPO_ROOT / "config" / "hermes-model-policy.yaml"


def test_valid_canonical_registry_passes():
    issues = validate_profile_architecture(CANONICAL_REGISTRY, CANONICAL_POLICY)
    errors = [issue for issue in issues if issue.severity == "error"]
    assert errors == []


def test_missing_required_field_fails(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    registry["profiles"][0].pop("may_write_paths")

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(CANONICAL_POLICY.read_text(encoding="utf-8"), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any(issue.severity == "error" and "may_write_paths" in issue.message for issue in issues)


def test_invalid_enum_fails(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    registry["profiles"][0]["default_model"] = "ultra_reasoning"

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(CANONICAL_POLICY.read_text(encoding="utf-8"), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("default_model" in issue.message and "ultra_reasoning" in issue.message for issue in issues)


def test_docs_job_intel_future_only_not_required_now():
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    doc_policy = registry["documentation_policy"]

    assert "docs/job-intel/" in doc_policy["future_only_paths"]
    assert "docs/job-intel/" not in doc_policy["runtime_required_paths"]


def test_engineer_production_mutation_approval_requirement_present():
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    engineer = next(profile for profile in registry["profiles"] if profile["id"] == "engineer")

    assert "any_production_host_mutation" in engineer["requires_approval_for"]


def test_malformed_yaml_fails_closed(tmp_path):
    registry_path = tmp_path / "broken-registry.yaml"
    policy_path = tmp_path / "broken-policy.yaml"
    registry_path.write_text("profiles: [\n  - id: engineer\n", encoding="utf-8")
    policy_path.write_text(CANONICAL_POLICY.read_text(encoding="utf-8"), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("YAML" in issue.message or "parse" in issue.message.lower() for issue in issues)


def test_policy_references_unknown_profile_fails(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    policy["profile_tiers"]["ghost_profile"] = "standard"

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("ghost_profile" in issue.message for issue in issues)


def test_unknown_model_tier_fails(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    registry["profiles"][0]["default_model"] = "turbo"

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    assert any("unknown model tier" in issue.message.lower() or "turbo" in issue.message for issue in issues)


def test_critical_fallback_fails_unless_explicit_fields_are_safe(tmp_path):
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    policy = yaml.safe_load(CANONICAL_POLICY.read_text(encoding="utf-8"))
    critical = policy["tiers"]["critical"]
    critical["allow_fallback"] = True
    critical["unavailable_behavior"] = "fallback_allowed"
    critical["fallback_models"] = ["anthropic/claude-sonnet-4.6"]

    registry_path = tmp_path / "hermes-profiles.yaml"
    policy_path = tmp_path / "hermes-model-policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    issues = validate_profile_architecture(registry_path, policy_path)
    messages = "\n".join(issue.message for issue in issues)
    assert "allow_fallback" in messages
    assert "unavailable_behavior" in messages
    assert "fallback_models" in messages


def test_scribe_output_artifacts_distinguish_complete_and_incomplete():
    registry = yaml.safe_load(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    scribe = next(profile for profile in registry["profiles"] if profile["id"] == "scribe")
    artifacts = set(scribe["output_artifacts"])

    assert "handoff_complete" in artifacts
    assert "handoff_incomplete" in artifacts
    assert not any(artifact in {"handoff", "handoff_report"} for artifact in artifacts)
