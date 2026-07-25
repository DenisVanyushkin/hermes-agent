"""The policy file must not document mechanisms that do not exist.

Every role carried an `escalation` block -- engineer promoted to
`specialized_coding` / `xiaomi/mimo-v2.5-pro` on `complex_multi_file_changes`,
`difficult_bugfixes`, `failing_test_diagnosis`. Nothing implemented it.
`role_policies` is referenced only in profile_validation.py; the model escalation
that does exist in pipeline_rework_loop reads the *pipeline spec*, not this file.

So the validator was enforcing the shape of a fiction, in some detail -- down to
requiring an `example_model` that is not in the sanctioned lineup at all. Reading
the policy file, an operator would reasonably conclude the engineer escalates. It
does not.

Removing it needs both halves at once: the validator demanded the block, so
deleting it from the YAML alone would fail validation, and any validation issue
raises RoutingError and collapses every role onto general_operator.
"""
from pathlib import Path

import pytest
import yaml

from hermes_cli.profile_validation import DEFAULT_MODEL_POLICY_PATH, validate_model_policy

POLICY = Path(DEFAULT_MODEL_POLICY_PATH)


@pytest.fixture(scope="module")
def policy() -> dict:
    return yaml.safe_load(POLICY.read_text())


def test_no_unimplemented_escalation_blocks(policy: dict):
    for role, body in policy.get("role_policies", {}).items():
        assert "escalation" not in body, (
            f"{role} documents an escalation path that nothing implements"
        )


def test_the_stale_escalation_model_is_gone(policy: dict):
    """xiaomi/mimo-v2.5-pro was pinned by the validator and is not in the lineup."""
    assert "mimo" not in POLICY.read_text()


def test_a_policy_without_escalation_still_validates(policy: dict):
    """The other half: the validator must stop demanding the block."""
    issues = validate_model_policy(policy)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], "\n".join(i.message for i in errors)


def test_role_policies_still_carry_what_is_real(policy: dict):
    """Removing fiction must not remove the fields that do carry meaning."""
    for role, body in policy.get("role_policies", {}).items():
        assert "base_model" in body, f"{role} lost base_model"
        assert "free_fallback" in body, f"{role} lost free_fallback"
