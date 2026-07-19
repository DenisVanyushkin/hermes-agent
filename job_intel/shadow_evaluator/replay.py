"""Historical offline replay for the Shadow Evaluator (read-only).

Exports a bounded snapshot from the live DB (STRICTLY read-only, mode=ro),
runs the deterministic Step 2 extractor + shadow engine over the clean
cohort, compares against legacy results and user feedback, classifies
disagreements with the approved taxonomy and writes artifact-based
observability outputs. No production writes, no metrics writes, no sends.

Usage (offline CLI, never wired into production flows):
    venv/bin/python -m job_intel.shadow_evaluator.replay --run-id <id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from job_intel.shadow_evaluator.engine import evaluate
from job_intel.shadow_evaluator.models import ShadowEvaluation
from job_intel.shadow_evaluator.policy import EVALUATOR_VERSION, load_policy
from job_intel.vacancy_understanding.extractor import EXTRACTOR_VERSION, RawVacancy, extract

DB_PATH = "/var/lib/job-intel/state/job_intel.sqlite3"
TEST_USERS = {"U_TEST", "U_AUDIT", "U_SMOKE_REPLAY", "U_VALIDATION"}
POSITIVE = {"interesting", "applied", "exceptional", "save_for_later"}

TAXONOMY = [
    "expected_architecture_change", "legacy_false_positive", "legacy_false_negative",
    "shadow_possible_false_positive", "shadow_possible_false_negative",
    "insufficient_vacancy_evidence", "preference_model_gap",
    "vacancy_understanding_gap", "decision_contract_gap", "feedback_ambiguity",
]

FIXED_TS = datetime(2026, 7, 19, tzinfo=timezone.utc)  # semantic determinism


def export_snapshot(db_path: str = DB_PATH) -> list[dict]:
    """Bounded read-only cohort: unique vacancies that were sent and got a
    real-user reaction; test users, resends and data-quality noise excluded."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT v.id, v.vacancy_key, v.source, v.source_id, v.company, v.title,
               v.location, v.url, v.description,
               fs.feedback_type AS feedback, fs.user_id,
               (SELECT o.recommendation FROM vacancy_observability o
                 WHERE o.vacancy_key = v.vacancy_key AND o.recommendation IS NOT NULL
                 ORDER BY o.created_at DESC LIMIT 1) AS legacy_recommendation,
               (SELECT o.score_band FROM vacancy_observability o
                 WHERE o.vacancy_key = v.vacancy_key AND o.score_band IS NOT NULL
                 ORDER BY o.created_at DESC LIMIT 1) AS legacy_band
        FROM vacancy_feedback_state fs
        JOIN vacancies v ON v.vacancy_key = fs.vacancy_key
        WHERE fs.active = 1 AND fs.user_id NOT IN ({q})
        GROUP BY v.vacancy_key
        """.format(q=",".join(f"'{u}'" for u in TEST_USERS))
    ).fetchall()
    conn.close()
    seen: set[str] = set()
    cohort = []
    for r in rows:
        key = (r["company"] or "").lower() + "|" + (r["title"] or "").lower()
        if key in seen:  # duplicate req-id / resend collapse
            continue
        seen.add(key)
        cohort.append(dict(r))
    return cohort


def _classify(case: dict, ev: ShadowEvaluation) -> str:
    fb = case.get("feedback")
    positive = fb in POSITIVE
    negative = fb == "not_interesting"
    rec = ev.overall.recommendation.value
    incomplete = any(u.field == "source_text" for u in ev.unknown_ledger)
    mandate_unknown = ev.mandate_fit.band.value == "unknown"
    legacy = (case.get("legacy_recommendation") or case.get("legacy_band") or "").lower()
    legacy_positive = legacy in ("exceptional_fit", "strong_fit", "potential_fit", "possible_fit")

    if incomplete or mandate_unknown:
        return "insufficient_vacancy_evidence"
    if positive and rec == "not_recommended":
        return "shadow_possible_false_negative"
    if negative and rec in ("strong", "exceptional"):
        return "shadow_possible_false_positive"
    if negative and legacy_positive and rec in ("not_recommended", "promising", "unclear"):
        return "legacy_false_positive"
    if positive and not legacy_positive and rec in ("strong", "exceptional", "promising"):
        return "legacy_false_negative"
    if fb is None:
        return "feedback_ambiguity"
    return "expected_architecture_change"


def run_replay(run_id: str, out_root: Path, db_path: str = DB_PATH) -> dict:
    policy = load_policy()
    out_dir = out_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    cohort = export_snapshot(db_path)
    snapshot_hash = hashlib.sha256(
        json.dumps([c["vacancy_key"] for c in cohort], sort_keys=True).encode()
    ).hexdigest()[:16]

    results, disagreements = [], []
    rec_dist, action_dist, cause_dist = Counter(), Counter(), Counter()
    band_feedback: dict[str, Counter] = {}
    caps_top, blockers_top, supports_top, clar_top = Counter(), Counter(), Counter(), Counter()
    lane_dist = Counter()
    per_source: dict[str, Counter] = {}
    fn_list, fp_list = [], []
    explanation_covered = 0

    for case in cohort:
        raw = RawVacancy(
            vacancy_key=case["vacancy_key"], source_system=case["source"],
            source_record_id=str(case["source_id"]) if case["source_id"] else None,
            company=case["company"], title=case["title"],
            location=case["location"], description=case["description"] or "")
        vu = extract(raw, created_at=FIXED_TS)
        ev = evaluate(vu, policy=policy, evaluated_at=FIXED_TS)
        rec = ev.overall.recommendation.value
        cause = _classify(case, ev)
        record = {
            "vacancy_key": case["vacancy_key"], "source": case["source"],
            "company": case["company"], "title": case["title"],
            "legacy_result": {"recommendation": case.get("legacy_recommendation"),
                              "band": case.get("legacy_band")},
            "shadow_result": {
                "recommendation": rec, "action": ev.overall.action.value,
                "feasibility": ev.feasibility.verdict.value,
                "lane": ev.feasibility.lane.value,
                "mandate": ev.mandate_fit.band.value,
                "company_fit": ev.company_fit.band.value,
                "confidence": ev.overall.confidence.value,
                "caps": ev.overall.applied_caps,
                "semantic_hash": ev.semantic_hash()},
            "user_feedback": case.get("feedback"),
            "evidence_completeness": "incomplete" if any(
                u.field == "source_text" for u in ev.unknown_ledger) else "deterministic_only",
            "difference_classification": cause,
            "explanation_coverage": bool(ev.explanations.verdict_summary
                                         and (ev.explanations.why_attractive
                                              or ev.explanations.why_may_not_work
                                              or ev.explanations.unknowns)),
        }
        results.append(record)
        rec_dist[rec] += 1
        action_dist[ev.overall.action.value] += 1
        cause_dist[cause] += 1
        lane_dist[ev.feasibility.lane.value] += 1
        band_feedback.setdefault(rec, Counter())[case.get("feedback") or "none"] += 1
        per_source.setdefault(case["source"], Counter())[rec] += 1
        for c in ev.overall.applied_caps:
            caps_top[c] += 1
        for i in ev.items:
            if i.kind.value == "blocker" and i.active:
                blockers_top[i.preference_rule_id or i.id] += 1
            if i.kind.value == "support" and i.active:
                supports_top[i.preference_rule_id or i.id] += 1
        for cl in ev.clarifications:
            clar_top[cl.required_fact] += 1
        if record["explanation_coverage"]:
            explanation_covered += 1
        if cause == "shadow_possible_false_negative":
            fn_list.append(record)
        if cause == "shadow_possible_false_positive":
            fp_list.append(record)

    def band_precision(band: str) -> float | None:
        c = band_feedback.get(band)
        if not c:
            return None
        pos = sum(v for k, v in c.items() if k in POSITIVE)
        neg = c.get("not_interesting", 0)
        return round(pos / (pos + neg), 3) if (pos + neg) else None

    positives = [r for r in results if r["user_feedback"] in POSITIVE]
    recalled = [r for r in positives
                if r["shadow_result"]["recommendation"] in ("exceptional", "strong", "promising")]
    negatives = [r for r in results if r["user_feedback"] == "not_interesting"]
    neg_correct = [r for r in negatives
                   if r["shadow_result"]["recommendation"] in ("not_recommended", "unclear")]
    infeasible = [r for r in results if r["shadow_result"]["feasibility"] == "infeasible"]
    infeasible_neg = [r for r in infeasible if r["user_feedback"] == "not_interesting"]

    summary = {
        "run_id": run_id,
        "cohort_size": len(cohort),
        "snapshot_hash": snapshot_hash,
        "recommendation_distribution": dict(rec_dist),
        "action_distribution": dict(action_dist),
        "positive_precision_by_band": {b: band_precision(b) for b in rec_dist},
        "recall_positive_feedback": round(len(recalled) / len(positives), 3) if positives else None,
        "negative_precision": round(len(neg_correct) / len(negatives), 3) if negatives else None,
        "infeasible_precision": round(len(infeasible_neg) / len(infeasible), 3) if infeasible else None,
        "unclear_unknown_rate": round(rec_dist.get("unclear", 0) / len(results), 3) if results else None,
        "lane_distribution": dict(lane_dist),
        "per_source_recommendations": {k: dict(v) for k, v in per_source.items()},
        "explanation_coverage": round(explanation_covered / len(results), 3) if results else None,
        "top_applied_caps": caps_top.most_common(10),
        "top_blockers": blockers_top.most_common(10),
        "top_supports": supports_top.most_common(10),
        "top_clarifications": clar_top.most_common(10),
        "disagreement_causes": dict(cause_dist),
        "critical_false_negatives": len(fn_list),
        "critical_false_positives": len(fp_list),
        "versions": {
            "decision_contract": policy.contract.metadata.contract_version,
            "preference_model": policy.preference_model.metadata.model_version,
            "vacancy_schema": "1.0.0",
            "evaluator": EVALUATOR_VERSION,
            "extractor": EXTRACTOR_VERSION,
        },
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    (out_dir / "run-metadata.json").write_text(json.dumps(summary["versions"] | {
        "run_id": run_id, "snapshot_hash": snapshot_hash,
        "run_timestamp": summary["run_timestamp"]}, indent=2))
    with (out_dir / "case-results.jsonl").open("w") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    with (out_dir / "disagreements.jsonl").open("w") as fh:
        for r in results:
            if r["difference_classification"] != "expected_architecture_change":
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def review_md(title: str, rows: list[dict]) -> str:
        lines = [f"# {title} — manual review queue ({len(rows)})", ""]
        for r in rows:
            s = r["shadow_result"]
            lines += [
                f"## {r['company']} — {r['title']}",
                f"- vacancy: `{r['vacancy_key']}` ({r['source']})",
                f"- legacy: {r['legacy_result']} | shadow: {s['recommendation']}/{s['action']} "
                f"(feas {s['feasibility']}, m {s['mandate']}, c {s['company_fit']}, "
                f"conf {s['confidence']}, caps {s['caps']})",
                f"- feedback: {r['user_feedback']} | evidence: {r['evidence_completeness']}",
                f"- suggested class: {r['difference_classification']}", "",
            ]
        return "\n".join(lines)

    (out_dir / "critical-false-negatives.md").write_text(
        review_md("Shadow possible false negatives", fn_list), encoding="utf-8")
    (out_dir / "critical-false-positives.md").write_text(
        review_md("Shadow possible false positives", fp_list), encoding="utf-8")
    (out_dir / "clarification-summary.md").write_text(
        "# Clarification summary\n\n" + "\n".join(
            f"- {k}: {v}" for k, v in clar_top.most_common(20)), encoding="utf-8")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="replay-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--out", default="artifacts/shadow-evaluator/replay")
    args = ap.parse_args()
    s = run_replay(args.run_id, Path(args.out))
    print(json.dumps({k: s[k] for k in (
        "run_id", "cohort_size", "recommendation_distribution",
        "disagreement_causes", "recall_positive_feedback", "negative_precision")},
        indent=2, ensure_ascii=False))
