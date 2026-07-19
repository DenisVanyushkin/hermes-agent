"""Validation tests for the Shadow Evaluator Decision SoT (Step 3A).

These tests validate the CONTRACT — they do not implement evaluator
behaviour. Golden decision cases are checked for structural completeness and
consistency with the contract vocabularies and matrix, not executed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from job_intel.shadow_evaluator.contract import (
    CONTRACT_PATH,
    SCHEMA_PATH,
    DecisionContract,
    FitBand,
    Recommendation,
    export_json_schema,
    load_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "shadow_evaluator" / "golden-decision-cases.yaml"


@pytest.fixture(scope="module")
def contract() -> DecisionContract:
    return load_contract()


@pytest.fixture(scope="module")
def golden() -> dict:
    return yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))["golden_decision_cases"]


# ---------------------------------------------------------------------------
# Contract structure
# ---------------------------------------------------------------------------

def test_contract_validates(contract):
    assert contract.metadata.contract_version == "1.1.0"
    assert contract.metadata.production_integration is False
    assert contract.metadata.no_silent_learning is True


def test_o1_action_vocabulary_two_level(contract):
    mapping = {m.recommendation.value: m.action for m in contract.action_vocabulary.mapping}
    assert mapping == {
        "exceptional": "apply", "strong": "apply", "promising": "investigate",
        "unclear": "investigate", "not_recommended": "reject",
    }
    promising = next(m for m in contract.action_vocabulary.mapping
                     if m.recommendation.value == "promising")
    assert promising.low_confidence_or_uncertain_action == "save"
    unclear = next(m for m in contract.action_vocabulary.mapping
                   if m.recommendation.value == "unclear")
    assert unclear.requires_clarification is True


def test_o6_no_concern_counting(contract):
    concern = next(rt for rt in contract.result_types if rt.kind.value == "concern")
    assert not re.search(r">=?\s*\d|\d\s*concerns", concern.aggregation)
    for band_def in contract.mandate_fit_bands + contract.company_fit_bands:
        assert "<3" not in band_def.criteria and ">=3" not in band_def.criteria


def test_o4_crypto_cap_is_provisional(contract):
    crypto = next(c for c in contract.caps if c.id == "cap_crypto_employer")
    assert crypto.status == "provisional_shadow_policy"
    assert crypto.review_after


def test_schema_artifact_in_sync():
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == export_json_schema(), (
        "decision-contract.schema.json stale; regenerate via "
        "python -m job_intel.shadow_evaluator.contract"
    )


def test_unknown_fields_rejected():
    data = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    data["shadow_evaluator_decision_contract"]["surprise"] = 1
    with pytest.raises(ValidationError):
        DecisionContract.model_validate(data["shadow_evaluator_decision_contract"])


def test_matrix_full_coverage_and_invariants(contract):
    cells = {(c.mandate, c.company): c.recommendation
             for c in contract.recommendation_matrix.feasible_matrix}
    assert len(cells) == 36  # every mandate x company combination
    # mandate mismatch/weak can never become positive through company fit
    for company in FitBand:
        assert cells[(FitBand.mismatch, company)] == Recommendation.not_recommended
        assert cells[(FitBand.weak, company)] == Recommendation.not_recommended
    # company must not rescue a moderate mandate above promising
    for company in (FitBand.exceptional, FitBand.strong):
        assert cells[(FitBand.moderate, company)] == Recommendation.promising
    # infeasible is terminal
    assert any("infeasible" in r and "not_recommended" in r
               for r in contract.recommendation_matrix.terminal_rules)


def test_no_numeric_weights_anywhere():
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert not re.search(r"\bweight\b\s*:", text, re.I)
    assert not re.search(r"\bscore\b\s*:", text, re.I)
    # the only numerals allowed are versions, ids, dates and the documented
    # ">=3 concerns" / ">=2 preferences" qualitative thresholds
    for m in re.finditer(r":\s*(-?\d+(?:\.\d+)?)\s*$", text, re.M):
        pytest.fail(f"bare numeric value in contract: {m.group(0)!r}")


def test_every_interaction_effect_has_semantics(contract):
    effects = {e.effect.value for e in contract.interaction_effects}
    assert effects == {"suppress", "limit_to_company_fit", "gate",
                       "route_to_fallback", "exclude_from", "allow"}
    for e in contract.interaction_effects:
        assert e.semantics and e.trace_visibility and e.idempotent


def test_unknown_policy_never_maps_unknown_to_false(contract):
    for entry in contract.unknown_policy:
        effect = entry.verdict_effect.lower()
        assert not re.search(r"(->|as|to|becomes)\s+false", effect), entry.id


def test_fallback_distinct_from_core(contract):
    fb = contract.fallback_policy
    assert fb.lane.value == "fallback_local"
    assert fb.excluded_from_core_metrics
    assert fb.production_delivery == "disabled"
    assert fb.feedback_never_recalibrates_core


def test_exploration_gates_and_ineligible_axes(contract):
    ex = contract.exploration_policy
    assert ex.hard_gates_always_apply and ex.one_axis_at_a_time
    assert "big_tech_attitude" in ex.ineligible_axes
    assert "early_startup_attitude" in ex.ineligible_axes


def test_explanation_and_clarification_contracts(contract):
    assert contract.explanation_contract.evidence_required
    assert contract.explanation_contract.numeric_scores_forbidden
    assert "evidence_refs" in contract.explanation_contract.item_fields
    assert set(contract.clarification_policy.required_fields) >= {
        "question", "reason", "affected_section", "required_fact", "priority"}


def test_no_production_imports():
    pkg = REPO_ROOT / "job_intel"
    offenders = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "shadow_evaluator" in py.parts:
            # contract package must not import the other SoTs at runtime
            if re.search(r"^\s*(from|import)\s+job_intel\.(preference_model|vacancy_understanding)",
                         text, re.M):
                offenders.append(f"{py} imports another SoT package")
            continue
        if re.search(r"^\s*(from|import)\s+job_intel\.shadow_evaluator", text, re.M):
            offenders.append(str(py))
    assert not offenders, offenders


def test_no_runtime_evaluator_implemented():
    pkg = REPO_ROOT / "job_intel" / "shadow_evaluator"
    files = sorted(p.name for p in pkg.glob("*.py"))
    assert files == ["__init__.py", "contract.py"], (
        "Step 3A must not contain evaluator implementation modules"
    )
    src = (pkg / "contract.py").read_text(encoding="utf-8")
    assert "def evaluate" not in src


# ---------------------------------------------------------------------------
# Golden decision cases (structural review, not execution)
# ---------------------------------------------------------------------------

REQUIRED_CASE_FIELDS = {
    "id", "expected_lane", "expected_feasibility", "expected_mandate_fit",
    "expected_company_fit", "expected_recommendation", "expected_confidence",
    "required_supports", "required_concerns", "required_blockers",
    "required_unknowns", "expected_interactions", "expected_caps", "rationale",
}


def test_golden_cases_complete_and_vocabulary_valid(golden, contract):
    cases = golden["cases"]
    assert len(cases) >= 20
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    lanes = {"core", "fallback_local"}
    feas = {"feasible", "uncertain", "infeasible"}
    bands = {b.value for b in FitBand}
    recs = {r.value for r in Recommendation}
    caps = {c.id for c in contract.caps}
    for c in cases:
        missing = REQUIRED_CASE_FIELDS - set(c)
        assert not missing, f"{c['id']}: missing {missing}"
        assert c["expected_lane"] in lanes
        assert c["expected_feasibility"] in feas
        assert c["expected_mandate_fit"] in bands
        assert c["expected_company_fit"] in bands
        assert c["expected_recommendation"] in recs
        assert set(c["expected_caps"]) <= caps, c["id"]
        assert c["rationale"]


def test_golden_cases_consistent_with_matrix(golden, contract):
    """Expected outcomes must be derivable: matrix + uncertain cap + declared
    caps — no case may contradict the approved policy."""
    cells = {(c.mandate.value, c.company.value): c.recommendation.value
             for c in contract.recommendation_matrix.feasible_matrix}
    order = ["not_recommended", "unclear", "promising", "strong", "exceptional"]

    for c in golden["cases"]:
        expected = c["expected_recommendation"]
        if c["expected_feasibility"] == "infeasible":
            assert expected == "not_recommended", c["id"]
            continue
        base = cells[(c["expected_mandate_fit"], c["expected_company_fit"])]
        result = base
        if c["expected_feasibility"] == "uncertain" and base in ("strong", "exceptional"):
            result = "promising"
        for cap_id in c["expected_caps"]:
            ceiling = next(x.ceiling.value for x in contract.caps if x.id == cap_id)
            if order.index(result) > order.index(ceiling) and result not in ("unclear", "not_recommended"):
                result = ceiling
        assert expected == result, (
            f"{c['id']}: expected {expected} but policy derives {result} "
            f"(base {base}, caps {c['expected_caps']})"
        )


def test_golden_covers_required_contrast_groups(golden):
    ids = {c["id"] for c in golden["cases"]}
    required = {
        "gd_airwallex_gpni", "gd_airwallex_payment_fraud",           # mandate primacy pair
        "gd_wise_apac_titleonly", "gd_wise_pricing",                 # flagship + exception
        "gd_wise_financial_crime", "gd_coinbase_core_infra",
        "gd_okx_internal_tools", "gd_block_sales_only", "gd_canva_fpna",
        "gd_us_onsite_no_sponsorship", "gd_us_onsite_with_sponsorship",
        "gd_affirm_remote_us_leadership", "gd_payoneer_israel_unknown_sponsorship",
        "gd_kz_local_fallback", "gd_sanctioned_location", "gd_africa_location",
        "gd_language_domain_barrier", "gd_crypto_broad_mandate",
        "gd_strong_role_small_local_company", "gd_strong_role_company_unknown",
    }
    assert required <= ids, f"missing: {required - ids}"


def test_golden_fixture_refs_resolve(golden):
    fx_dir = REPO_ROOT / "tests" / "fixtures" / "vacancy_understanding"
    for c in golden["cases"]:
        ref = c.get("fixture_ref")
        if ref:
            assert (fx_dir / f"{ref}.yaml").exists(), f"{c['id']} -> {ref}"
        else:
            assert c.get("policy_only") is True, f"{c['id']} lacks fixture and policy_only flag"
