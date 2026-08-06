"""§7.2 T1+T2 — corpus split and real-corpus extraction coverage.

Why this module exists BEFORE any new rule is written:

The provider's phrase rules were authored against the contract's own
synthetic control phrases, so 158/158 controls pass while a real 7000-char
vacancy yields a median of ONE observation. The control suite is
structurally incapable of detecting that failure — it validates the rules
against the very phrases they were written from. This module supplies the
missing, non-circular gate: what fraction of REAL vacancies actually yield a
mandate fact. A rule that lifts controls but not this number is not an
improvement.

The DEV/HOLDOUT split exists so rules mined from real text cannot be
silently overfitted to the current corpus: mining reads DEV only, acceptance
is measured on HOLDOUT.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

FIXED_TS = datetime(2026, 7, 19, tzinfo=timezone.utc)
DEV_RATIO = 0.70
_BUCKETS = 1000

# Mandate facts that were unknown on 40/40 live shown roles — the target set.
MANDATE_FACTS = (
    "mandate.scope_breadth",
    "mandate.revenue_proximity",
    "mandate.expansion_mandate",
    "mandate.monetization_core",
    "mandate.pricing_core",
    "mandate.acquiring_core",
    "mandate.strategy_ownership",
    "mandate.org_design_mandate",
    "mandate.team_build_mandate",
    "mandate.executive_exposure",
    "mandate.board_exposure",
    "mandate.pnl_ownership",
    "mandate.growth_mandate",
)


def assign_split(vacancy_key: str, dev_ratio: float = DEV_RATIO) -> str:
    """Stable DEV/HOLDOUT assignment derived from the key alone.

    Hash-based rather than index/order-based on purpose: the corpus grows
    every day, and an order-dependent split would reassign existing rows
    between rounds, leaking holdout into DEV.
    """
    digest = hashlib.sha256(vacancy_key.encode()).hexdigest()
    bucket = int(digest[:8], 16) % _BUCKETS
    return "dev" if bucket < int(dev_ratio * _BUCKETS) else "holdout"


def split_corpus(rows: list[dict[str, Any]], dev_ratio: float = DEV_RATIO
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dev, holdout = [], []
    for r in rows:
        target = dev if assign_split(r["vacancy_key"], dev_ratio) == "dev" else holdout
        target.append(r)
    return dev, holdout


def _extract_mandate_facts(row: dict[str, Any]) -> set[str]:
    """Fact ids with a known (non-unknown) value for one vacancy."""
    from job_intel.vacancy_understanding.extractor import RawVacancy
    from job_intel.vacancy_understanding.extractor import extract as det_extract
    from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
    from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic
    from job_intel.vacancy_understanding.semantic.runtime.provider import (
        DeterministicPhraseProvider,
    )

    title = (row.get("title") or "").strip() or "Unknown"
    text = row.get("text") or row.get("description") or ""
    vu = det_extract(
        RawVacancy(
            vacancy_key=row.get("vacancy_key") or "k",
            source_system=row.get("source") or "unknown",
            company=row.get("company") or "Unknown",
            title=title,
            location=row.get("location") or "Unknown",
            description=text,
        ),
        created_at=FIXED_TS,
    )
    sem = extract_semantic(vu, title=title, text=text,
                           provider=DeterministicPhraseProvider(),
                           contract=load_semantic_contract())
    mandate = (sem.fragment or {}).get("mandate") or {}
    known = set()
    for fid in MANDATE_FACTS:
        leaf = fid.split(".", 1)[1]
        node = mandate.get(leaf)
        value = node.get("value") if isinstance(node, dict) else None
        if value not in (None, "unknown", [], ["unknown"]):
            known.add(fid)
    return known


def coverage_report(rows: list[dict[str, Any]], *, label: Optional[str] = None
                    ) -> dict[str, Any]:
    """Extraction coverage over a REAL corpus slice — the §7.2 gate metric.

    An empty slice reports state=not_applicable with rates of None: a 0.0
    rate would read as "we extract nothing", which is a different claim from
    "there was nothing to measure".
    """
    if not rows:
        return {"label": label, "state": "not_applicable", "roles_total": 0,
                "roles_with_any_mandate": 0, "roles_with_any_mandate_rate": None,
                "per_fact": {fid: 0 for fid in MANDATE_FACTS}}

    per_fact = {fid: 0 for fid in MANDATE_FACTS}
    with_any = 0
    for row in rows:
        try:
            known = _extract_mandate_facts(row)
        except Exception:
            known = set()  # a broken row measures as zero coverage, never crashes the gate
        if known:
            with_any += 1
        for fid in known:
            per_fact[fid] += 1

    total = len(rows)
    return {
        "label": label,
        "state": "known_value",
        "roles_total": total,
        "roles_with_any_mandate": with_any,
        "roles_with_any_mandate_rate": with_any / total,
        "per_fact": per_fact,
        "per_fact_rate": {fid: n / total for fid, n in per_fact.items()},
    }
