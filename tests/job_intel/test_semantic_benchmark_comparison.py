"""Step 5B Slice 5B-5: cross-provider comparison metrics. All offline."""
from __future__ import annotations

import json

import pytest

from job_intel.vacancy_understanding.semantic.benchmark.comparison import (
    classify_recommendation_pair,
    decision_divergence,
    evidence_metrics_for_run,
    micro_macro,
)


# --- recommendation pair classification (compatible-match consumption) ------

def test_recommendation_pair_exact():
    assert classify_recommendation_pair("promising", "promising") == "exact_match"


def test_recommendation_pair_compatible_within_equivalence_class():
    # both map to the same recommendation -> exact; different recommendations
    # are compatible only if the derived equivalence classes say so; the
    # 36-cell matrix groups by recommendation, so distinct recommendations
    # are never compatible under this derivation
    assert classify_recommendation_pair("promising", "not_recommended") == "mismatch"
    assert classify_recommendation_pair("strong", "exceptional") == "mismatch"


def test_recommendation_pair_uses_derived_classes_not_hardcode():
    from job_intel.vacancy_understanding.semantic.benchmark.compatible_match import (
        derive_recommendation_equivalences,
    )
    classes = derive_recommendation_equivalences()["equivalence_classes"]
    any_rec = next(iter(classes))
    assert classify_recommendation_pair(any_rec, any_rec) == "exact_match"


# --- micro/macro aggregation -------------------------------------------------

def test_micro_macro_aggregation():
    per_fact = {
        "a": {"value_matches": 8, "emitted": 10, "gold_known": 10},
        "b": {"value_matches": 1, "emitted": 1, "gold_known": 4},
        "c": {"value_matches": 0, "emitted": 0, "gold_known": 0},  # excluded
    }
    result = micro_macro(per_fact)
    assert result["micro_precision"] == pytest.approx(9 / 11)
    assert result["micro_recall"] == pytest.approx(9 / 14)
    assert result["macro_precision"] == pytest.approx((0.8 + 1.0) / 2)
    assert result["macro_recall"] == pytest.approx((0.8 + 0.25) / 2)
    assert result["facts_counted"] == 2


# --- evidence metrics --------------------------------------------------------

def _dump(observations, rejected, fragment=None):
    return {
        "observations": observations, "rejected_observations": rejected,
        "fragment": fragment or {}, "conflicts": [], "clarifications": [],
        "diagnostics": {"observations_total": len(observations) + len(rejected),
                        "observations_rejected": len(rejected)},
    }


def test_evidence_metrics_counts_verbatim_and_rejections(tmp_path):
    run = tmp_path / "run"
    dumps = run / "semantic_dumps"
    dumps.mkdir(parents=True)
    obs = [{"observation_id": "o1", "excerpt": "Own the growth roadmap",
            "maps_to": ["mandate.growth_mandate"], "signal_type": "growth_mandate=true",
            "location": "description", "basis": "direct", "interpretation": "x"}]
    rej = [{"observation": {"observation_id": "o2", "excerpt": "NOT IN TEXT",
                            "maps_to": ["mandate.growth_mandate"]},
            "reason": "excerpt_not_verbatim"},
           {"observation": {"observation_id": "o3", "excerpt": "Own the growth roadmap",
                            "maps_to": ["nonexistent.fact"]},
            "reason": "unknown_fact_reference"}]
    (dumps / "c1.semantic.json").write_text(json.dumps(_dump(obs, rej)))
    cases = [{"case_id": "c1", "vacancy_key": "v1", "title": "Head of Growth",
              "text": "Own the growth roadmap end to end."}]
    m = evidence_metrics_for_run(run, cases)
    assert m["observations_emitted"] == 3
    assert m["observations_accepted"] == 1
    assert m["verbatim_evidence_rate"]["value"] == pytest.approx(2 / 3)
    assert m["unsupported_evidence_rate"]["value"] == pytest.approx(2 / 3)
    assert m["accepted_verbatim_rate"]["value"] == pytest.approx(1.0)


def test_evidence_metrics_empty_run_not_applicable(tmp_path):
    run = tmp_path / "run"
    (run / "semantic_dumps").mkdir(parents=True)
    m = evidence_metrics_for_run(run, [])
    assert m["verbatim_evidence_rate"]["state"] == "not_applicable"


# --- decision divergence -----------------------------------------------------

def _decision(rec):
    return {"overall": {"recommendation": rec, "action": "save", "lane": "core",
                        "confidence": "low", "applied_caps": [],
                        "exploration_axis": None}}


def test_decision_divergence_flags_mismatches(tmp_path):
    det, llm = tmp_path / "det", tmp_path / "llm"
    for root, recs in ((det, {"c1": "promising", "c2": "unclear"}),
                       (llm, {"c1": "promising", "c2": "not_recommended"})):
        (root / "decisions").mkdir(parents=True)
        for cid, rec in recs.items():
            (root / "decisions" / f"{cid}.decision.json").write_text(
                json.dumps(_decision(rec)))
    result = decision_divergence(det, llm)
    assert result["cases_compared"] == 2
    assert result["exact"] == 1
    assert result["divergent"] == [
        {"case_id": "c2", "deterministic": "unclear", "llm": "not_recommended",
         "category": "mismatch"}]


def test_decision_divergence_reports_missing_counterpart(tmp_path):
    det, llm = tmp_path / "det", tmp_path / "llm"
    (det / "decisions").mkdir(parents=True)
    (llm / "decisions").mkdir(parents=True)
    (det / "decisions" / "c1.decision.json").write_text(json.dumps(_decision("strong")))
    result = decision_divergence(det, llm)
    assert result["cases_compared"] == 0
    assert result["missing_in_llm"] == ["c1"]
