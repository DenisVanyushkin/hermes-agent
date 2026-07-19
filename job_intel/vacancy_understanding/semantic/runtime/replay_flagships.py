"""Flagship golden replay with semantic extraction (Step 4B, read-only).

Runs: full recovered Wise texts + full DB texts of Airwallex/Monzo/Brex/
Affirm through deterministic extraction -> semantic extraction -> the Step 3
shadow evaluator (UNCHANGED), and compares against the Step 3 golden results.
Artifacts only; no DB writes, no evaluator changes.

Usage:
    venv/bin/python -m job_intel.vacancy_understanding.semantic.runtime.replay_flagships
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from job_intel.shadow_evaluator.engine import evaluate
from job_intel.vacancy_understanding.extractor import RawVacancy, extract as det_extract
from job_intel.vacancy_understanding.model import VacancyUnderstanding
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic
from job_intel.vacancy_understanding.semantic.runtime.provider import DeterministicPhraseProvider

FIXED_TS = datetime(2026, 7, 19, tzinfo=timezone.utc)
RECOVERED = Path("artifacts/shadow-evaluator/recovered-wise")
FIXTURES = Path("tests/fixtures/vacancy_understanding")

# fixture_id -> recovered artifact (Wise full texts); others use fixture
# replay_input which already carries the real (excerpted) DB text — for the
# four non-Wise flagships we re-read the FULL description from the stored
# source dump when present.
WISE = {
    "wise_apac_growth_expansion": "wise_5919.json",
    "wise_pricing": "wise_1728.json",
    "wise_acquiring": "wise_4510.json",
    "wise_financial_crime": "wise_1813.json",
    "wise_onboarding_experience": "wise_1773.json",
}
OTHERS = ["airwallex_gpni", "airwallex_payment_fraud", "monzo_business_banking",
          "brex_growth_ai_vancouver", "affirm_senior_director_pm_remote_us"]
SOURCE_DUMP = Path("/tmp/step2_fixture_source.json")


def _full_text_for(fixture: dict) -> str | None:
    if not SOURCE_DUMP.exists():
        return None
    rows = {r["id"]: r for r in json.loads(SOURCE_DUMP.read_text())}
    row = rows.get(fixture["vacancy_identity"].get("db_id"))
    if not row:
        return None
    import html, re
    text = html.unescape(html.unescape(row.get("description") or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def run(out_path: Path = Path("artifacts/shadow-evaluator/semantic-flagship-replay.json"),
        provider=None) -> dict:
    provider = provider or DeterministicPhraseProvider()
    step3_golden = {
        c["id"]: c for c in yaml.safe_load(
            Path("tests/fixtures/shadow_evaluator/golden-decision-cases.yaml")
            .read_text())["golden_decision_cases"]["cases"]}
    by_fixture = {c.get("fixture_ref"): c for c in step3_golden.values() if c.get("fixture_ref")}

    results = []
    for fid in list(WISE) + OTHERS:
        fx = yaml.safe_load((FIXTURES / f"{fid}.yaml").read_text())
        replay = dict(fx["replay_input"])
        if fid in WISE:
            rec = json.loads((RECOVERED / WISE[fid]).read_text())
            replay["description"] = rec["text"]
            text_status = "recovered_full_text"
        else:
            full = _full_text_for(fx)
            if full:
                replay["description"] = full
                text_status = "full_db_text"
            else:
                text_status = "fixture_excerpts_only"
        base = det_extract(RawVacancy(**replay), created_at=FIXED_TS)
        sem = extract_semantic(base, title=replay["title"],
                               text=replay["description"] or "", provider=provider)
        enriched = VacancyUnderstanding.model_validate(sem.fragment)
        ev = evaluate(enriched, evaluated_at=FIXED_TS)
        prior = by_fixture.get(fid, {})
        results.append({
            "fixture": fid, "text_status": text_status,
            "semantic_facts_emitted": sem.diagnostics.facts_emitted,
            "observations": sem.diagnostics.observations_total,
            "step3_result": {k: prior.get(k) for k in (
                "expected_feasibility", "expected_mandate_fit",
                "expected_company_fit", "expected_recommendation",
                "expected_confidence")},
            "step4b_result": {
                "feasibility": ev.feasibility.verdict.value,
                "mandate": ev.mandate_fit.band.value,
                "company": ev.company_fit.band.value,
                "recommendation": ev.overall.recommendation.value,
                "action": ev.overall.action.value,
                "confidence": ev.overall.confidence.value,
                "caps": ev.overall.applied_caps,
                "semantic_hash": ev.semantic_hash(),
            },
        })
    out = {"run": "semantic-flagship-replay", "provider": provider.provider_id,
           "prompt_version": provider.prompt_version, "cases": results}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    o = run()
    for c in o["cases"]:
        s3, s4 = c["step3_result"], c["step4b_result"]
        print(f"{c['fixture']:38s} {c['text_status']:22s} obs={c['observations']:2d} "
              f"facts={c['semantic_facts_emitted']:2d} "
              f"{(s3.get('expected_recommendation') or '?'):15s} -> {s4['recommendation']:15s} "
              f"m:{(s3.get('expected_mandate_fit') or '?'):11s}->{s4['mandate']:11s} conf={s4['confidence']}")
