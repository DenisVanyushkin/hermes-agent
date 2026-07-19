"""Contract tests for the normalized career preference model (Step 1).

Covers schema validation, structural invariants and the 13 golden policy
scenarios from the Step 1 agent task. These tests exercise the CONTRACT via a
minimal deterministic matcher — they are not an evaluator and prove no
production integration exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from job_intel.preference_model.model import (
    DEFAULT_MODEL_PATH,
    SCHEMA_PATH,
    Confidence,
    CountryGroup,
    FeasibilityVerdict,
    Lane,
    RuleStatus,
    Scenario,
    SponsorshipStated,
    Strength,
    WorkFormat,
    applicable_anti_preferences,
    applicable_positive_preferences,
    evaluate_feasibility,
    export_json_schema,
    load_model,
    role_level_vetoes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def model():
    return load_model()


# ---------------------------------------------------------------------------
# Schema / structural invariants
# ---------------------------------------------------------------------------

def test_model_file_parses_and_validates(model):
    assert model.metadata.model_version == "1.0.0"
    assert model.metadata.production_integration is False


def test_schema_artifact_is_up_to_date():
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == export_json_schema(), (
        "career-preference-model.schema.json is stale; regenerate via "
        "python -m job_intel.preference_model.model or write_json_schema()"
    )


def test_no_unknown_enum_values(model):
    # Pydantic already rejects unknown enums; assert normalized values only.
    all_rules = (
        model.mandate_preferences
        + model.company_preferences
        + model.anti_preferences
        + model.feasibility_constraints.constraints
    )
    for r in all_rules:
        assert r.strength in set(Strength)
        assert r.confidence in set(Confidence)
        assert "medium_high" not in (r.strength.value, r.confidence.value)


def test_every_active_rule_has_provenance_and_evidence(model):
    rules = (
        model.motivations
        + model.feasibility_constraints.constraints
        + model.mandate_preferences
        + model.company_preferences
        + model.anti_preferences
        + model.interaction_rules
    )
    for r in rules:
        if r.status == RuleStatus.active:
            assert r.provenance.evidence, f"{r.id} lacks evidence"
            assert r.provenance.last_validated is not None


def test_domains_physically_separated(model):
    # feasibility, mandate and company signals live in distinct sections.
    mandate_ids = {p.id for p in model.mandate_preferences}
    company_ids = {p.id for p in model.company_preferences}
    feas_ids = {c.id for c in model.feasibility_constraints.constraints}
    assert not (mandate_ids & company_ids)
    assert not (mandate_ids | company_ids) & feas_ids
    # anti-preferences split company vs role level explicitly.
    levels = {a.level.value for a in model.anti_preferences}
    assert levels == {"company", "role"}


def test_compensation_inactive_and_effect_free(model):
    comp = model.feasibility_constraints.compensation_policy
    assert comp.status == RuleStatus.inactive
    assert not comp.gating_effect and not comp.ranking_effect
    assert not comp.missing_salary_is_negative


def test_timezone_not_a_hard_gate(model):
    assert model.feasibility_constraints.timezone_policy.hard_gate is False


def test_no_standalone_industry_country_title_preferences(model):
    for p in model.mandate_preferences + model.company_preferences:
        if p.status == RuleStatus.active:
            assert p.axis not in {"industry", "country", "title"}


def test_kz_fallback_separate_from_core(model):
    lane_rule = next(
        c for c in model.feasibility_constraints.constraints if c.id == "fc_kz_local_lane"
    )
    assert lane_rule.lane == Lane.fallback_local
    assert model.local_market_fallback_policy.activation == "manual_by_user"


def test_no_production_integration():
    """No production job_intel module imports the preference model package."""
    pkg_dir = REPO_ROOT / "job_intel"
    offenders = []
    for py in pkg_dir.rglob("*.py"):
        # shadow_evaluator is the sanctioned Step 3 consumer of this SoT
        if "preference_model" in py.parts or "shadow_evaluator" in py.parts                 or py.name.startswith("test_"):
            continue
        if re.search(r"^\s*(from|import)\s+job_intel\.preference_model",
                     py.read_text(encoding="utf-8", errors="ignore"), re.M):
            offenders.append(str(py))
    assert not offenders, f"production modules import preference_model: {offenders}"


# ---------------------------------------------------------------------------
# 13 golden policy scenarios (agent task «Tests / golden policy cases»)
# ---------------------------------------------------------------------------

def test_1_remote_us_director_not_rejected_on_geography(model):
    s = Scenario(work_format=WorkFormat.remote, country_group=CountryGroup.usa)
    res = evaluate_feasibility(model, s)
    assert res.verdict == FeasibilityVerdict.feasible
    assert res.matched_constraint_ids == []


def test_2_us_onsite_without_explicit_sponsorship_infeasible(model):
    for sponsorship in (SponsorshipStated.no, SponsorshipStated.unknown):
        s = Scenario(
            work_format=WorkFormat.onsite,
            country_group=CountryGroup.usa,
            sponsorship_stated=sponsorship,
        )
        res = evaluate_feasibility(model, s)
        assert res.verdict == FeasibilityVerdict.infeasible
        assert "fc_usa_onsite_requires_explicit_sponsorship" in res.matched_constraint_ids


def test_3_us_onsite_with_explicit_sponsorship_eligible(model):
    s = Scenario(
        work_format=WorkFormat.onsite,
        country_group=CountryGroup.usa,
        sponsorship_stated=SponsorshipStated.yes,
    )
    assert evaluate_feasibility(model, s).verdict == FeasibilityVerdict.feasible


def test_4_relocation_hub_with_sponsorship_feasible_unless_flagged(model):
    # Berlin/Dubai/Singapore resolve to country_group=other.
    s = Scenario(
        work_format=WorkFormat.onsite,
        country_group=CountryGroup.other,
        sponsorship_stated=SponsorshipStated.yes,
    )
    assert evaluate_feasibility(model, s).verdict == FeasibilityVerdict.feasible
    # ...but the sanctioned/unstable rules still apply to any geography.
    for group in (CountryGroup.sanctioned, CountryGroup.unstable):
        s2 = Scenario(
            work_format=WorkFormat.onsite,
            country_group=group,
            sponsorship_stated=SponsorshipStated.yes,
        )
        assert evaluate_feasibility(model, s2).verdict == FeasibilityVerdict.infeasible


def test_5_africa_relocation_currently_excluded(model):
    s = Scenario(
        work_format=WorkFormat.onsite,
        country_group=CountryGroup.africa,
        sponsorship_stated=SponsorshipStated.yes,
    )
    res = evaluate_feasibility(model, s)
    assert res.verdict == FeasibilityVerdict.infeasible
    assert "fc_africa_current_stage" in res.matched_constraint_ids


def test_6_kz_local_strong_role_goes_to_fallback_lane_only(model):
    # Realistic case: a local KZ vacancy naturally states no sponsorship.
    s = Scenario(
        work_format=WorkFormat.onsite,
        country_group=CountryGroup.kazakhstan,
        local_market=True,
        sponsorship_stated=SponsorshipStated.unknown,
    )
    res = evaluate_feasibility(model, s)
    assert res.verdict == FeasibilityVerdict.feasible
    assert res.lane == Lane.fallback_local  # never the global core lane


def test_6b_kz_feasibility_independent_of_sponsorship(model):
    """Regression: KZ local roles never become uncertain/infeasible solely
    because sponsorship is unknown or absent — no visa path is needed to
    work in Kazakhstan."""
    for sponsorship in SponsorshipStated:
        for fmt in (WorkFormat.onsite, WorkFormat.hybrid, WorkFormat.remote):
            s = Scenario(
                work_format=fmt,
                country_group=CountryGroup.kazakhstan,
                local_market=True,
                sponsorship_stated=sponsorship,
            )
            res = evaluate_feasibility(model, s)
            assert res.verdict == FeasibilityVerdict.feasible, (fmt, sponsorship)
            assert res.lane == Lane.fallback_local
    # Schema-level guard: no non-feasible constraint may combine KZ with a
    # sponsorship condition.
    for c in model.feasibility_constraints.constraints:
        if (
            c.status == RuleStatus.active
            and c.verdict != FeasibilityVerdict.feasible
            and c.when.country_group is not None
            and CountryGroup.kazakhstan in c.when.country_group
        ):
            assert c.when.sponsorship_stated is None, c.id


def test_7_narrow_pricing_role_monetization_override(model):
    s = Scenario(flags={"narrow_feature_scope", "monetization_core"})
    assert "narrow_feature_scope" not in applicable_anti_preferences(model, s)
    # without monetization the penalty applies
    s2 = Scenario(flags={"narrow_feature_scope"})
    assert "narrow_feature_scope" in applicable_anti_preferences(model, s2)


def test_8_b2b_platform_as_business_no_generic_rejection(model):
    s = Scenario(flags={"b2b_enterprise_context", "platform_as_the_business"})
    assert "b2b_enterprise_context" not in applicable_anti_preferences(model, s)


def test_9_crypto_employer_company_concern_not_role_veto(model):
    s = Scenario(flags={"crypto_exchange_employer"})
    anti = applicable_anti_preferences(model, s)
    assert "crypto_exchange_employer" in anti          # concern is recorded...
    assert anti["crypto_exchange_employer"].level.value == "company"
    assert role_level_vetoes(model, s) == []           # ...but no role veto
    assert evaluate_feasibility(model, s).verdict == FeasibilityVerdict.feasible


def test_10_platform_engineering_does_not_inherit_platform_business_positive(model):
    s = Scenario(flags={"platform_engineering", "platform_as_the_business"})
    assert "platform_as_the_business" not in applicable_positive_preferences(model, s)
    assert "pure_infrastructure_devex" in applicable_anti_preferences(model, s)


def test_11_gm_digital_with_pnl_ownership_not_rejected(model):
    s = Scenario(flags={"non_product_function", "digital_business_ownership"})
    assert evaluate_feasibility(model, s).verdict == FeasibilityVerdict.feasible
    # pure non-product function without ownership stays infeasible
    s2 = Scenario(flags={"non_product_function"})
    res = evaluate_feasibility(model, s2)
    assert res.verdict == FeasibilityVerdict.infeasible
    assert "fc_function_digital_business_ownership" in res.matched_constraint_ids


def test_12_missing_compensation_no_penalty(model):
    s = Scenario(flags={"compensation_missing"})
    res = evaluate_feasibility(model, s)
    assert res.verdict == FeasibilityVerdict.feasible
    assert res.matched_constraint_ids == []
    assert res.risks == []


def test_13_large_timezone_gap_risk_only(model):
    s = Scenario(work_format=WorkFormat.remote, flags={"timezone_gap_large"})
    res = evaluate_feasibility(model, s)
    assert res.verdict == FeasibilityVerdict.feasible
    assert res.risks == ["timezone_gap"]
