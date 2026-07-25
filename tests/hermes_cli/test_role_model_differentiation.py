"""Per-role base models are allowed, but only from the sanctioned lineup.

The old contract was equality with a single DEFAULT_BASE_MODEL, and it existed
for a reason: the blast radius of getting it wrong is total. route_task() calls
_validate_loaded_architecture(), any issue at all raises RoutingError, and
profile_context.py catches it and returns selected_role="general_operator" for
*every* task. One typo in one role's base_model collapses all role routing.

So this relaxes the contract rather than removing it -- membership in a lineup
instead of equality with a constant -- and adds the per-role view the old
all-or-nothing check could not express.
"""
from pathlib import Path

import pytest
import yaml

from hermes_cli.profile_validation import (
    DEFAULT_MODEL_POLICY_PATH,
    SUPPORTED_BASE_MODELS,
    validate_model_policy,
    validate_role_policies,
)

POLICY = Path(DEFAULT_MODEL_POLICY_PATH)


def test_lineup_is_explicit():
    assert SUPPORTED_BASE_MODELS == {
        "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
    }


def test_the_lineup_matches_the_policy_file():
    """The constant and the tier block must not drift apart."""
    tiers = yaml.safe_load(POLICY.read_text())["tiers"]
    assert {tier["model"] for tier in tiers.values()} == SUPPORTED_BASE_MODELS


def test_differentiated_policies_are_valid():
    policies = {
        "engineer": {"base_model": "gpt-5.6-terra"},
        "scribe": {"base_model": "gpt-5.6-luna"},
    }
    result = validate_role_policies(policies)
    assert result.valid
    assert result.errors == []


def test_unknown_model_is_rejected_loudly():
    policies = {"engineer": {"base_model": "gpt-4o-mini"}}
    result = validate_role_policies(policies)
    assert not result.valid
    assert "engineer" in result.errors[0]


def test_invalid_policy_does_not_silently_downgrade_all_roles():
    """The historical failure mode: one bad entry soft-fell-back every role."""
    policies = {
        "engineer": {"base_model": "gpt-4o-mini"},
        "scribe": {"base_model": "gpt-5.6-luna"},
    }
    result = validate_role_policies(policies)
    assert result.invalid_roles == {"engineer"}
    assert "scribe" not in result.invalid_roles


def test_a_deferred_role_is_not_held_to_the_lineup():
    policies = {"trading_observer_trader_deferred": {
        "status": "deferred", "base_model": "deferred",
    }}
    result = validate_role_policies(policies)
    assert result.valid


def test_the_shipped_policy_file_validates_clean():
    """The guard that matters in practice: ship a bad file and all routing dies."""
    issues = validate_model_policy(yaml.safe_load(POLICY.read_text()))
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], "\n".join(i.message for i in errors)


def test_the_shipped_policy_actually_differentiates_the_roles():
    """The operator's decision, asserted against the file rather than assumed."""
    policy = yaml.safe_load(POLICY.read_text())
    tiers, profile_tiers = policy["tiers"], policy["profile_tiers"]

    def model_for(role):
        return tiers[profile_tiers[role]]["model"]

    assert model_for("engineer") == "gpt-5.6-terra"
    assert model_for("security_auditor") == "gpt-5.6-sol"
    assert model_for("scribe") == "gpt-5.6-luna"
    assert model_for("general_operator") == "gpt-5.6-luna"
