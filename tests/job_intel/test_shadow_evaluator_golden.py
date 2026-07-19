"""Executable golden decision suite: the real engine over golden cases.

Fixture-backed cases run on the Step 2 canonical fixtures; policy_only cases
run on synthetic canonical records (explicitly marked synthetic) — no live
vacancy evidence is fabricated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from job_intel.shadow_evaluator.engine import evaluate
from job_intel.shadow_evaluator.models import ShadowEvaluation
from job_intel.vacancy_understanding.model import VacancyUnderstanding

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "vacancy_understanding"
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "shadow_evaluator" / "golden-decision-cases.yaml"
CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)

SRC_GOLD = "src_gold_annotation"
REGISTRY = [
    {"id": SRC_GOLD, "source_type": "manual_gold_annotation",
     "description": "synthetic policy-control gold annotation"},
    {"id": "src_structured_fields", "source_type": "structured_source_field",
     "description": "structured fields"},
]


def _fact(value, conf="high"):
    return {"value": value, "confidence": conf, "method": "manual_gold_annotation",
            "evidence": [{"source_id": SRC_GOLD, "source_type": "manual_gold_annotation",
                          "rationale": "synthetic policy control"}]}


def _synthetic(key: str, *, mandate=None, company=None, feasibility=None) -> VacancyUnderstanding:
    doc = {
        "metadata": {
            "schema_version": "1.0.0", "extractor_version": "0.1.1",
            "created_at": "2026-07-19T00:00:00Z", "vacancy_key": f"synthetic:{key}",
            "source_system": "synthetic_fixture", "is_synthetic_fixture": True,
        },
        "role_identity": {"raw_title": key, "normalized_title": key,
                          "title_families": ["product"], "function_families": ["product"]},
        "mandate": mandate or {},
        "company": {"name": f"{key} Co (synthetic)", **(company or {})},
        "feasibility_facts": feasibility or {},
        "evidence_registry": REGISTRY,
    }
    return VacancyUnderstanding.model_validate(doc)


def _feas(work="remote", group="other", country=None, sponsorship=None):
    f = {"work_format": _fact(work), "country_group": _fact(group)}
    if country:
        f["country"] = _fact(country)
    if sponsorship:
        f["sponsorship_stated"] = _fact(sponsorship)
    return f


SYNTHETIC_BUILDERS = {
    "gd_wise_apac_fulltext": lambda: _synthetic(
        "wise-apac-fulltext",
        mandate={"scope_breadth": _fact("region"), "growth_mandate": _fact("true"),
                 "expansion_mandate": _fact("true"), "revenue_proximity": _fact("direct_revenue")},
        company={"scale": _fact("global"), "brand_recognition": _fact("tier1_scaleup"),
                 "is_crypto_exchange": _fact("false")},
        feasibility=_feas(work="onsite", country="Singapore", sponsorship="yes")),
    "gd_airwallex_gpni_relocation_variant": lambda: _synthetic(
        "gpni-relocation-variant",
        mandate={"scope_breadth": _fact("business_line"),
                 "platform_as_business": _fact("true"),
                 "platform_engineering": _fact("false"),
                 "zero_to_one_mandate": _fact("true", "medium"),
                 "revenue_proximity": _fact("direct_revenue", "medium")},
        company={"scale": _fact("global"), "brand_recognition": _fact("tier1_scaleup"),
                 "is_crypto_exchange": _fact("false")},
        feasibility=_feas(work="onsite", country="Singapore", sponsorship="yes")),
    "gd_sanctioned_location": lambda: _synthetic(
        "sanctioned-location",
        feasibility=_feas(work="onsite", group="sanctioned", sponsorship="yes")),
    "gd_africa_location": lambda: _synthetic(
        "africa-location",
        feasibility=_feas(work="onsite", group="africa", sponsorship="yes")),
    "gd_crypto_broad_mandate": lambda: _synthetic(
        "crypto-broad-mandate",
        mandate={"scope_breadth": _fact("business_line"), "growth_mandate": _fact("true")},
        company={"is_crypto_exchange": _fact("true")},
        feasibility=_feas(work="remote")),
    "gd_strong_role_small_local_company": lambda: _synthetic(
        "small-local-company",
        mandate={"scope_breadth": _fact("business_line"), "growth_mandate": _fact("true")},
        company={"local_only": _fact("true"), "scale": _fact("local")},
        feasibility=_feas(work="remote")),
    "gd_strong_role_company_unknown": lambda: _synthetic(
        "company-unknown",
        mandate={"scope_breadth": _fact("business_line"), "pnl_ownership": _fact("true")},
        feasibility=_feas(work="remote", group="usa", country="United States")),
}


@pytest.fixture(scope="module")
def golden_cases() -> list[dict]:
    return yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))["golden_decision_cases"]["cases"]


@pytest.fixture(scope="module")
def results(golden_cases) -> dict[str, ShadowEvaluation]:
    out = {}
    for case in golden_cases:
        ref = case.get("fixture_ref")
        if ref:
            doc = yaml.safe_load((FIXTURE_DIR / f"{ref}.yaml").read_text(encoding="utf-8"))
            vu = VacancyUnderstanding.model_validate(doc["vacancy_understanding"])
        else:
            vu = SYNTHETIC_BUILDERS[case["id"]]()
        out[case["id"]] = evaluate(vu, evaluated_at=CREATED)
    return out


def _all_unknown_paths(r: ShadowEvaluation) -> set[str]:
    paths = set(r.feasibility.unknowns) | set(r.mandate_fit.unknowns) | set(r.company_fit.unknowns)
    paths |= {u.field for u in r.unknown_ledger}
    return paths


def _rule_ids(r: ShadowEvaluation, kinds, active_only=True) -> set[str]:
    return {
        i.preference_rule_id for i in r.items
        if i.kind.value in kinds and i.preference_rule_id
        and (i.active or not active_only)
    }


def test_all_golden_cases_pass(golden_cases, results):
    failures = []
    for case in golden_cases:
        r = results[case["id"]]
        checks = [
            ("lane", r.feasibility.lane.value, case["expected_lane"]),
            ("feasibility", r.feasibility.verdict.value, case["expected_feasibility"]),
            ("mandate", r.mandate_fit.band.value, case["expected_mandate_fit"]),
            ("company", r.company_fit.band.value, case["expected_company_fit"]),
            ("recommendation", r.overall.recommendation.value, case["expected_recommendation"]),
            ("action", r.overall.action.value, case["expected_action"]),
            ("confidence", r.overall.confidence.value, case["expected_confidence"]),
        ]
        for name, actual, expected in checks:
            if actual != expected:
                failures.append(f"{case['id']}: {name} = {actual!r} != {expected!r}")
        if not set(case["expected_caps"]) <= set(r.overall.applied_caps):
            failures.append(f"{case['id']}: caps {r.overall.applied_caps} missing {case['expected_caps']}")
        supports = _rule_ids(r, {"support"})
        for s in case["required_supports"]:
            if s not in supports:
                failures.append(f"{case['id']}: missing support {s} (have {sorted(supports)})")
        concerns = _rule_ids(r, {"concern"})
        for c in case["required_concerns"]:
            if c not in concerns:
                failures.append(f"{case['id']}: missing concern {c} (have {sorted(concerns)})")
        blockers = _rule_ids(r, {"blocker"})
        for b in case["required_blockers"]:
            if b not in blockers:
                failures.append(f"{case['id']}: missing blocker {b} (have {sorted(blockers)})")
        unknowns = _all_unknown_paths(r)
        for u in case["required_unknowns"]:
            if u not in unknowns:
                failures.append(f"{case['id']}: missing unknown {u} (have {sorted(unknowns)})")
        trace_ids = {t.rule_id for t in r.interaction_trace}
        for ir in case["expected_interactions"]:
            if ir not in trace_ids:
                failures.append(f"{case['id']}: missing interaction {ir} (have {sorted(trace_ids)})")
        if case["expected_recommendation"] == "unclear" and not r.clarifications:
            failures.append(f"{case['id']}: unclear without clarifications")
    assert not failures, "\n".join(failures)


def test_fallback_case_is_marked_standby(results):
    kz = results["gd_kz_local_fallback"]
    assert kz.feasibility.lane.value == "fallback_local"
    assert kz.feasibility.fallback_state == "standby"


def test_terminal_infeasible_bands_are_diagnostic(results):
    r = results["gd_block_sales_only"]
    assert r.feasibility.verdict.value == "infeasible"
    assert r.mandate_fit.decisioning is False
    assert r.company_fit.decisioning is False


def test_every_applied_cap_is_explained(results):
    for cid, r in results.items():
        joined = " ".join(r.explanations.why_may_not_work)
        for cap in r.overall.applied_caps:
            assert cap in joined, f"{cid}: cap {cap} not explained"


def test_every_active_item_has_evidence(results):
    for cid, r in results.items():
        for item in r.items:
            if item.active and item.kind.value != "interaction":
                assert item.evidence_refs, f"{cid}: {item.id} lacks evidence refs"


def test_semantic_determinism(results, golden_cases):
    case = golden_cases[0]
    ref = case["fixture_ref"]
    doc = yaml.safe_load((FIXTURE_DIR / f"{ref}.yaml").read_text(encoding="utf-8"))
    vu = VacancyUnderstanding.model_validate(doc["vacancy_understanding"])
    a = evaluate(vu, evaluated_at=CREATED)
    b = evaluate(vu, evaluated_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert a.semantic_hash() == b.semantic_hash()
