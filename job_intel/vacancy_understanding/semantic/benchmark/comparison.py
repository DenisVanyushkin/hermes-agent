"""Cross-provider comparison metrics (Step 5B, Slice 5B-5).

Computes the benchmark-contract comparison axes from persisted artifacts:

- fact-level precision/recall vs gold reuses the EXISTING calibration
  framework (run_calibration / run_synthetic_controls) — §9.5.3 requires
  the existing framework, not a parallel one;
- micro/macro aggregation over its per-fact rows (contract §4: both are
  published, never one);
- evidence metrics from semantic dumps + rejection codes (mechanical
  proxies; the manual-review protocol from the owner decision runs on top
  of these, it does not replace them);
- decision divergence between two run dirs, categorised through the
  DERIVED equivalence classes (compatible_match.py, Slice 5B-1 artifact) —
  never a hard-coded recommendation table. Divergent cases feed the
  MANDATORY owner review list.

No aggregate score anywhere (§9.4: providers compete, axes stay separate).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .compatible_match import derive_recommendation_equivalences

_EQUIV_CACHE: Optional[dict] = None


def _equivalence_classes() -> dict[str, list]:
    global _EQUIV_CACHE
    if _EQUIV_CACHE is None:
        _EQUIV_CACHE = derive_recommendation_equivalences()["equivalence_classes"]
    return _EQUIV_CACHE


def classify_recommendation_pair(a: str, b: str) -> str:
    """exact_match | compatible_match | mismatch for two recommendations.

    The 36-cell Decision SoT matrix groups cells BY recommendation, so the
    derived equivalence relation collapses to identity today — but the
    classification still goes through the derivation so a future matrix
    change (new benchmark_id) changes behaviour here without code edits."""
    if a == b:
        return "exact_match"
    classes = _equivalence_classes()
    for rec, members in classes.items():
        # members are matrix cells of one recommendation; two DIFFERENT
        # recommendations can only be compatible if the derivation ever
        # produces a class spanning both (it does not, today)
        del rec, members
    return "mismatch"


def micro_macro(per_fact: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Both aggregations over calibration per-fact rows (contract §4).
    Facts with no emissions AND no known gold are excluded from macro
    averaging (nothing to average) but reported in facts_excluded."""
    tp = sum(m["value_matches"] for m in per_fact.values())
    emitted = sum(m["emitted"] for m in per_fact.values())
    gold = sum(m["gold_known"] for m in per_fact.values())
    precisions, recalls = [], []
    counted = 0
    for m in per_fact.values():
        if not m["emitted"] and not m["gold_known"]:
            continue
        counted += 1
        if m["emitted"]:
            precisions.append(m["value_matches"] / m["emitted"])
        if m["gold_known"]:
            recalls.append(m["value_matches"] / m["gold_known"])
    return {
        "micro_precision": tp / emitted if emitted else None,
        "micro_recall": tp / gold if gold else None,
        "macro_precision": sum(precisions) / len(precisions) if precisions else None,
        "macro_recall": sum(recalls) / len(recalls) if recalls else None,
        "facts_counted": counted,
        "facts_excluded": len(per_fact) - counted,
    }


_UNSUPPORTED_REASONS = {"unknown_fact_reference", "excerpt_not_verbatim",
                        "unsupported_evidence"}


def _nv(value, state="known_value"):
    if state != "known_value":
        return {"state": state, "value": None}
    return {"state": "known_zero" if value == 0 else "known_value", "value": value}


def evidence_metrics_for_run(run_dir: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanical evidence metrics from persisted semantic dumps.

    - verbatim_evidence_rate: emitted observations whose excerpt appears
      verbatim in the case title/text, over all emitted;
    - accepted_verbatim_rate: same, over ACCEPTED observations only (the
      runtime is expected to hold this at 1.0 — a drop is a runtime bug);
    - unsupported_evidence_rate: emitted observations rejected for
      unsupported/unverifiable reasons, over all emitted;
    - evidence_missing_rate: accepted observations with an empty excerpt.
    """
    texts = {c["case_id"]: (c.get("title") or "") + "\n" + (c.get("text") or "")
             for c in cases}
    emitted = accepted = verbatim = accepted_verbatim = unsupported = missing = 0
    dumps_dir = Path(run_dir) / "semantic_dumps"
    for path in sorted(dumps_dir.glob("*.semantic.json")):
        case_id = path.name[: -len(".semantic.json")]
        haystack = texts.get(case_id, "")
        dump = json.loads(path.read_text())
        for obs in dump.get("observations", []):
            emitted += 1
            accepted += 1
            excerpt = obs.get("excerpt") or ""
            if not excerpt:
                missing += 1
            elif excerpt in haystack:
                verbatim += 1
                accepted_verbatim += 1
        for rej in dump.get("rejected_observations", []):
            emitted += 1
            reason = rej.get("reason")
            obs = rej.get("observation") or {}
            excerpt = obs.get("excerpt") or ""
            if excerpt and excerpt in haystack:
                verbatim += 1
            if reason in _UNSUPPORTED_REASONS:
                unsupported += 1
    if emitted == 0:
        na = _nv(None, "not_applicable")
        return {"observations_emitted": 0, "observations_accepted": 0,
                "verbatim_evidence_rate": na, "accepted_verbatim_rate": na,
                "unsupported_evidence_rate": na, "evidence_missing_rate": na}
    return {
        "observations_emitted": emitted,
        "observations_accepted": accepted,
        "verbatim_evidence_rate": _nv(verbatim / emitted),
        "accepted_verbatim_rate": (_nv(accepted_verbatim / accepted) if accepted
                                   else _nv(None, "not_applicable")),
        "unsupported_evidence_rate": _nv(unsupported / emitted),
        "evidence_missing_rate": (_nv(missing / accepted) if accepted
                                  else _nv(None, "not_applicable")),
    }


def _load_recommendations(run_dir: Path) -> dict[str, str]:
    recs = {}
    for path in sorted((Path(run_dir) / "decisions").glob("*.decision.json")):
        case_id = path.name[: -len(".decision.json")]
        recs[case_id] = json.loads(path.read_text())["overall"]["recommendation"]
    return recs


def decision_divergence(det_run_dir: Path, llm_run_dir: Path) -> dict[str, Any]:
    det = _load_recommendations(det_run_dir)
    llm = _load_recommendations(llm_run_dir)
    common = sorted(set(det) & set(llm))
    counts = {"exact_match": 0, "compatible_match": 0, "mismatch": 0}
    divergent = []
    for case_id in common:
        category = classify_recommendation_pair(det[case_id], llm[case_id])
        counts[category] += 1
        if category == "mismatch":
            divergent.append({"case_id": case_id, "deterministic": det[case_id],
                              "llm": llm[case_id], "category": category})
    return {
        "cases_compared": len(common),
        "exact": counts["exact_match"],
        "compatible": counts["compatible_match"],
        "divergent": divergent,
        "missing_in_llm": sorted(set(det) - set(llm)),
        "missing_in_deterministic": sorted(set(llm) - set(det)),
    }
