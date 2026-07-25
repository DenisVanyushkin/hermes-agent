"""The selector's constants must match the sanctioned lineup in the policy file.

``hermes_cli/model_selection.py`` is deliberately "pure and import-light", so it
hardcodes model names instead of reading ``config/hermes-model-policy.yaml`` at
import time. That is a reasonable trade, but it means the two drift silently:
the config moved to the 5.6 lineup while the selector still named gpt-5.4-mini /
gpt-5.4 / gpt-5.5, so every ``model selection:`` log line advertised models that
were not in the sanctioned lineup at all.

These tests are the guard the trade-off requires: the config file stays the
single source of truth, and the next lineup move fails here instead of quietly
producing a misleading log.
"""
from pathlib import Path

import pytest
import yaml

from hermes_cli import model_selection

POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "hermes-model-policy.yaml"


@pytest.fixture(scope="module")
def tiers() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text())["tiers"]


@pytest.mark.parametrize(
    ("constant", "tier"),
    [
        ("_DEFAULT_MODEL", "standard"),
        ("_REASONING_MODEL", "reasoning"),
        ("_CODING_MODEL", "coding"),
        ("_CRITICAL_MODEL", "critical"),
    ],
)
def test_constant_matches_policy_tier(tiers: dict, constant: str, tier: str):
    assert getattr(model_selection, constant) == tiers[tier]["model"], (
        f"{constant} disagrees with tiers.{tier}.model in {POLICY_PATH.name}"
    )


@pytest.mark.parametrize(
    ("constant", "tier"),
    [("_DEFAULT_PROVIDER", "standard"), ("_CODING_PROVIDER", "coding")],
)
def test_provider_constant_matches_policy_tier(tiers: dict, constant: str, tier: str):
    assert getattr(model_selection, constant) == tiers[tier]["provider"]


def test_every_selectable_model_is_in_the_sanctioned_lineup(tiers: dict):
    """No policy may name a model the tier block does not sanction."""
    sanctioned = {tier["model"] for tier in tiers.values()}
    named = {
        model_selection._DEFAULT_MODEL,
        model_selection._REASONING_MODEL,
        model_selection._CODING_MODEL,
        model_selection._CRITICAL_MODEL,
    }
    assert named <= sanctioned, f"outside the lineup: {sorted(named - sanctioned)}"


# ── Everything else that names a model from the lineup ──────────────────────
# The selector is not the only place that hardcodes model names. The router runs
# on its own model, and the controlled smoke harness carries an allowlist of the
# models it will let the engineer/reviewer really invoke. Both were left on the
# 5.4/5.5 lineup: the allowlist in particular would have refused the very models
# the runtime now uses, so the smoke path was latently broken.


def test_router_model_is_the_standard_tier(tiers: dict):
    from hermes_cli.pipeline_router import DEFAULT_ROUTER_LLM_MODEL

    assert DEFAULT_ROUTER_LLM_MODEL == tiers["standard"]["model"]


@pytest.mark.parametrize(
    ("constant", "tier"),
    [
        ("SMOKE_ENGINEER_MODEL", "coding"),
        ("SMOKE_REVIEWER_MODEL", "code_review"),
        ("SMOKE_ROUTER_MODEL", "standard"),
    ],
)
def test_smoke_harness_model_matches_policy_tier(tiers: dict, constant: str, tier: str):
    from hermes_cli import pipeline_controlled_dry_run as dry_run

    assert getattr(dry_run, constant) == tiers[tier]["model"]


def test_smoke_allowlist_admits_exactly_the_engineer_and_reviewer_tiers(tiers: dict):
    """An allowlist narrower than the lineup silently vetoes real runs."""
    from hermes_cli import pipeline_controlled_dry_run as dry_run

    assert set(dry_run.SMOKE_ALLOWED_REAL_MODELS) == {
        tiers["coding"]["model"],
        tiers["code_review"]["model"],
    }
