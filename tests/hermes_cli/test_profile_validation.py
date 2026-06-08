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
