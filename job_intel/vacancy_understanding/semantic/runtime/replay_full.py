"""Closure Part 2: full historical replay with semantic extraction.

Read-only over the live DB (mode=ro). Classifies the ENTIRE corpus, runs the
Step 4B pipeline on every eligible record, evaluates via the UNCHANGED Step 3
engine with and without semantic facts, and reports before/after decision
transitions. Artifacts only; no writes, no policy changes.

Usage:
    venv/bin/python -m job_intel.vacancy_understanding.semantic.runtime.replay_full
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from job_intel.shadow_evaluator.engine import evaluate
from job_intel.shadow_evaluator.policy import load_policy
from job_intel.vacancy_understanding.extractor import RawVacancy, extract as det_extract
from job_intel.vacancy_understanding.model import VacancyUnderstanding
from job_intel.vacancy_understanding.semantic.runtime.models import RUNTIME_VERSION
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic
from job_intel.vacancy_understanding.semantic.runtime.provider import DeterministicPhraseProvider

DB_PATH = "/var/lib/job-intel/state/job_intel.sqlite3"
FIXED_TS = datetime(2026, 7, 19, tzinfo=timezone.utc)
POSITIVE = {"interesting", "applied", "exceptional", "save_for_later"}
FULL_MIN, PARTIAL_MIN = 600, 200


def _clean(text: str) -> str:
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_corpus(db_path: str = DB_PATH):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, vacancy_key, source, source_id, company, title, location, "
        "description FROM vacancies").fetchall()
    fb = dict(conn.execute(
        "SELECT vacancy_key, feedback_type FROM vacancy_feedback_state "
        "WHERE active = 1 AND user_id NOT IN "
        "('U_TEST','U_AUDIT','U_SMOKE_REPLAY','U_VALIDATION')").fetchall())
    conn.close()

    classified, eligible = [], []
    seen: set[str] = set()
    for r in rows:
        rec = dict(r)
        if (r["source"] or "").startswith(("synthetic", "smoke", "test")):
            cls = "excluded_smoke_test"
        elif not r["title"] or not r["vacancy_key"]:
            cls = "malformed_unreadable"
        else:
            key = (r["company"] or "").lower() + "|" + (r["title"] or "").lower()
            if key in seen:
                cls = "duplicate_vacancy"
            else:
                seen.add(key)
                text = _clean(r["description"])
                if len(text) >= FULL_MIN:
                    cls = "full_text_usable"
                elif len(text) >= PARTIAL_MIN:
                    cls = "partial_text_usable"
                else:
                    cls = "title_only_source_incomplete"
                rec["clean_text"] = text
        rec["class"] = cls
        rec["feedback"] = fb.get(r["vacancy_key"])
        classified.append(rec)
        if cls in ("full_text_usable", "partial_text_usable"):
            eligible.append(rec)
    return classified, eligible


def run_full_replay(out_dir: Path = Path("artifacts/shadow-evaluator/semantic-full-replay"),
                    provider=None):
    provider = provider or DeterministicPhraseProvider()
    policy = load_policy()
    classified, eligible = classify_corpus()
    out_dir.mkdir(parents=True, exist_ok=True)

    class_dist = Counter(r["class"] for r in classified)
    before_dist, after_dist = Counter(), Counter()
    transitions = Counter()
    per_fact_emitted = Counter()
    evidence_cov = Counter()
    extraction_fail = 0
    fn_candidates, fp_candidates, reannotation = [], [], []
    contract_gaps = []

    fh = (out_dir / "case-results.jsonl").open("w")
    for rec in eligible:
        try:
            raw = RawVacancy(
                vacancy_key=rec["vacancy_key"], source_system=rec["source"],
                source_record_id=str(rec["source_id"]) if rec["source_id"] else None,
                company=rec["company"], title=rec["title"],
                location=rec["location"], description=rec["clean_text"])
            base = det_extract(raw, created_at=FIXED_TS)
            before = evaluate(base, policy=policy, evaluated_at=FIXED_TS)
            sem = extract_semantic(base, title=rec["title"], text=rec["clean_text"],
                                   provider=provider)
            enriched = VacancyUnderstanding.model_validate(sem.fragment)
            after = evaluate(enriched, policy=policy, evaluated_at=FIXED_TS)
        except Exception as exc:  # classified, never silent
            extraction_fail += 1
            contract_gaps.append({"vacancy_key": rec["vacancy_key"], "error": str(exc)[:200]})
            continue

        b, a = before.overall.recommendation.value, after.overall.recommendation.value
        before_dist[b] += 1
        after_dist[a] += 1
        transitions[f"{b}->{a}"] += 1
        for fid in sem.provenance:
            per_fact_emitted[fid] += 1
        cov_bucket = ("none" if sem.diagnostics.facts_emitted == 0 else
                      "low" if sem.diagnostics.facts_emitted <= 2 else
                      "medium" if sem.diagnostics.facts_emitted <= 5 else "high")
        evidence_cov[cov_bucket] += 1

        feedback = rec.get("feedback")
        row = {
            "vacancy_key": rec["vacancy_key"], "company": rec["company"],
            "title": rec["title"], "class": rec["class"],
            "provider": provider.provider_id, "prompt_version": provider.prompt_version,
            "runtime_version": RUNTIME_VERSION,
            "observations": sem.diagnostics.observations_total,
            "facts_emitted": sorted(sem.provenance),
            "facts_unknown": sem.diagnostics.facts_unknown,
            "clarifications": len(sem.clarifications),
            "evidence_coverage": cov_bucket,
            "feedback": feedback,
            "before": {"recommendation": b, "mandate": before.mandate_fit.band.value,
                       "feasibility": before.feasibility.verdict.value},
            "after": {"recommendation": a, "mandate": after.mandate_fit.band.value,
                      "feasibility": after.feasibility.verdict.value,
                      "confidence": after.overall.confidence.value,
                      "caps": after.overall.applied_caps},
        }
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if feedback in POSITIVE and a == "not_recommended":
            fn_candidates.append(row)
        if feedback == "not_interesting" and a in ("strong", "exceptional"):
            fp_candidates.append(row)
        if sem.diagnostics.facts_emitted >= 3 and feedback:
            reannotation.append({"vacancy_key": rec["vacancy_key"],
                                 "company": rec["company"], "title": rec["title"],
                                 "facts": sorted(sem.provenance), "feedback": feedback})
    fh.close()

    summary = {
        "run": "semantic-full-historical-replay",
        "provider": provider.provider_id, "prompt_version": provider.prompt_version,
        "runtime_version": RUNTIME_VERSION,
        "total_historical_records": len(classified),
        "corpus_classification": dict(class_dist),
        "eligible_records": len(eligible),
        "extraction_success": len(eligible) - extraction_fail,
        "extraction_failures": extraction_fail,
        "evidence_coverage_distribution": dict(evidence_cov),
        "per_fact_emission": dict(per_fact_emitted.most_common()),
        "recommendation_before": dict(before_dist),
        "recommendation_after": dict(after_dist),
        "transitions": dict(transitions.most_common()),
        "changed_cases": sum(v for k, v in transitions.items()
                             if k.split("->")[0] != k.split("->")[1]),
        "critical_false_negative_candidates": len(fn_candidates),
        "critical_false_positive_candidates": len(fp_candidates),
        "contract_gaps": contract_gaps,
        "reannotation_candidates": reannotation[:40],
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "semantic-full-historical-replay.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    (out_dir / "fn-candidates.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in fn_candidates))
    (out_dir / "fp-candidates.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in fp_candidates))
    return summary


if __name__ == "__main__":
    s = run_full_replay()
    print(json.dumps({k: s[k] for k in (
        "total_historical_records", "corpus_classification", "eligible_records",
        "extraction_failures", "evidence_coverage_distribution",
        "recommendation_before", "recommendation_after", "changed_cases",
        "critical_false_negative_candidates", "critical_false_positive_candidates")},
        indent=1, ensure_ascii=False))
