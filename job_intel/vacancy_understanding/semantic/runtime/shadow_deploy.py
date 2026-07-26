"""Phase III — Shadow Deployment of the semantic-preference evaluator.

Lives UNDER vacancy_understanding/ on purpose: the production/​semantic import
boundary (test_no_production_import / test_no_production_imports) forbids any
production job_intel module from importing the semantic or shadow_evaluator
stack. This exempt location lets the sanctioned Phase III bridge import both,
and it is driven as a DECOUPLED post-run job (scripts/job_intel_semantic_
shadow.py) rather than wired into the production scoring hot loop — so the
boundary stays intact and a shadow bug can never slow or break scoring.

Provider Selection (Step 5C) chose the deterministic-phrase provider as
canonical for decision-critical facts. For each live vacancy it runs the
full observe-only shadow chain:

    Vacancy -> RawVacancy -> deterministic Step-2 extract
            -> semantic runtime (DeterministicPhraseProvider)
            -> shadow evaluator (unchanged Decision SoT)
            -> {recommendation, action, ...}

It is OBSERVE-ONLY by construction: it returns a plain dict for the caller
to persist alongside the production evaluation and NEVER influences the
user-facing recommendation, notifications, or scoring. It also never raises
into the pipeline — any failure is captured as an ``error`` field so a
shadow bug can never take down a production run (§5 rollout: feasibility
gates only, no user-facing change).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from job_intel.dedup import canonical_vacancy_key
from job_intel.models import Vacancy

# Heavy semantic imports are done lazily inside the function so importing this
# module (e.g. at cli startup) never pays the cost unless the shadow runs.

SEMANTIC_SHADOW_VERSION = "phase3-shadow-1.0.0"
SEMANTIC_SHADOW_ENV = "SEMANTIC_SHADOW_ENABLED"


def semantic_shadow_enabled() -> bool:
    """Observe-only shadow defaults ON once deployed (mirrors the existing
    SCORING_V3_SHADOW_ENABLED precedent) — it cannot change user-facing
    output, so the safe default is to collect shadow evidence. Set
    SEMANTIC_SHADOW_ENABLED=0 to silence it."""
    return (os.getenv(SEMANTIC_SHADOW_ENV, "1") or "1").strip().lower() not in {
        "0", "false", "no"}


def evaluate_semantic_shadow(
    vacancy: Vacancy, *, evaluated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Run the observe-only semantic shadow chain for one vacancy.

    Returns a dict always containing ``status`` ("ok" | "error") and
    ``shadow_version``. On success it carries the shadow decision; on any
    failure it carries ``error`` and nothing else decision-shaped. Never
    raises.
    """
    result: dict[str, Any] = {"shadow_version": SEMANTIC_SHADOW_VERSION}
    try:
        from job_intel.shadow_evaluator.engine import evaluate as evaluate_decision
        from job_intel.shadow_evaluator.models import EvaluationError
        from job_intel.shadow_evaluator.policy import load_policy
        from job_intel.vacancy_understanding.extractor import RawVacancy
        from job_intel.vacancy_understanding.extractor import extract as det_extract
        from job_intel.vacancy_understanding.model import VacancyUnderstanding
        from job_intel.vacancy_understanding.semantic.contract import (
            load_semantic_contract,
        )
        from job_intel.vacancy_understanding.semantic.runtime.pipeline import (
            extract_semantic,
        )
        from job_intel.vacancy_understanding.semantic.runtime.provider import (
            DeterministicPhraseProvider,
        )

        at = evaluated_at or datetime.now(timezone.utc)
        vacancy_key = canonical_vacancy_key(vacancy)
        title = (vacancy.title or "").strip()
        text = vacancy.description or ""

        vu = det_extract(
            RawVacancy(
                vacancy_key=vacancy_key,
                source_system=(vacancy.source or "unknown"),
                source_record_id=(str(vacancy.source_id) if vacancy.source_id else None),
                company=(vacancy.company or "Unknown"),
                title=title or "Unknown",
                location=(vacancy.location or "Unknown"),
                description=text,
            ),
            created_at=at,
        )
        contract = load_semantic_contract()
        sem = extract_semantic(vu, title=title, text=text,
                               provider=DeterministicPhraseProvider(), contract=contract)
        enriched = VacancyUnderstanding.model_validate(sem.fragment)
        decision = evaluate_decision(enriched, policy=load_policy(), evaluated_at=at)

        if isinstance(decision, EvaluationError):
            result["status"] = "error"
            result["error"] = f"evaluation_error: {decision.error}"
            return result

        overall = decision.semantic_dump()["overall"]
        result.update({
            "status": "ok",
            "vacancy_key": vacancy_key,
            "recommendation": overall.get("recommendation"),
            "action": overall.get("action"),
            "lane": overall.get("lane"),
            "confidence": overall.get("confidence"),
            "applied_caps": overall.get("applied_caps") or [],
            "semantic_hash": decision.semantic_hash(),
            "observations_total": sem.diagnostics.observations_total,
        })
        return result
    except Exception as exc:  # observe-only: a shadow failure never propagates
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def _vacancy_from_row(row: dict[str, Any]) -> Vacancy:
    return Vacancy(
        source=row.get("source") or "unknown",
        source_id=str(row.get("source_id") or ""),
        company=row.get("company") or "Unknown",
        title=row.get("title") or "",
        location=row.get("location") or "Unknown",
        url=row.get("url") or "",
        description=row.get("description") or "",
    )


def run_semantic_shadow(store: Any, run_id: int) -> dict[str, int]:
    """Decoupled post-run driver: evaluate every vacancy of `run_id` in
    observe-only shadow and persist to semantic_shadow_evaluation. Returns a
    tally by recommendation/status. Reads and writes only — never mutates any
    production evaluation. Per-vacancy failures are recorded, not raised."""
    from collections import Counter

    tally: Counter[str] = Counter()
    for row in store.fetch_vacancies_for_run(run_id):
        vac = _vacancy_from_row(row)
        res = evaluate_semantic_shadow(vac)
        store.upsert_semantic_shadow_evaluation(
            run_id=run_id,
            vacancy_key=res.get("vacancy_key") or row.get("vacancy_key") or "",
            source=vac.source,
            recommendation=res.get("recommendation"),
            action=res.get("action"),
            lane=res.get("lane"),
            confidence=res.get("confidence"),
            applied_caps=res.get("applied_caps") or [],
            semantic_hash=res.get("semantic_hash"),
            observations_total=res.get("observations_total"),
            shadow_version=res.get("shadow_version", SEMANTIC_SHADOW_VERSION),
            error=res.get("error"),
        )
        tally[res.get("recommendation") or res.get("status", "error")] += 1
    return dict(tally)
