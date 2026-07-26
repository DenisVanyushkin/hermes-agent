"""Phase III — shadow-vs-production drift report (B1 evidence gate).

Compares the observe-only semantic shadow verdict against the production
job-intel recommendation, and — the decision-relevant part — against the
owner's actual reactions. The two providers use DIFFERENT recommendation
taxonomies, so both are projected onto a common coarse ordinal band before
any comparison; a naive string match would be meaningless.

Pure production module: reads only persisted rows via the store; imports no
semantic/shadow_evaluator code, so it never crosses the production import
boundary. Run it at the 2-week checkpoint (scripts/job_intel_shadow_drift_
report.py).
"""
from __future__ import annotations

from typing import Any

# Coarse ordinal bands (best -> worst); the only axis on which the two
# taxonomies are comparable.
BANDS = ("top", "mid", "review", "reject", "unknown")

# Semantic shadow evaluator vocabulary -> band.
_SHADOW_BAND = {
    "exceptional": "top", "strong": "top",
    "promising": "mid",
    "unclear": "review",
    "not_recommended": "reject",
}
# Production job-intel recommendation vocabulary -> band.
_PROD_BAND = {
    "exceptional_fit": "top", "strong_fit": "top",
    "potential_fit": "mid", "possible_fit": "mid",
    "near_miss": "review", "needs_review": "review",
    "weak_fit": "reject", "reject": "reject",
}

# Reaction polarity. save_for_later = attractive-but-blocked -> weak positive.
_POSITIVE = {"applied", "exceptional", "interesting"}
_WEAK_POSITIVE = {"save_for_later"}
_NEGATIVE = {"not_interesting"}


def shadow_band(recommendation: str | None) -> str:
    return _SHADOW_BAND.get((recommendation or "").strip().lower(), "unknown")


def prod_band(recommendation: str | None) -> str:
    return _PROD_BAND.get((recommendation or "").strip().lower(), "unknown")


def reaction_polarity(feedback_type: str | None) -> str:
    f = (feedback_type or "").strip().lower()
    if f in _POSITIVE:
        return "positive"
    if f in _WEAK_POSITIVE:
        return "weak_positive"
    if f in _NEGATIVE:
        return "negative"
    return "other"


def agreement_matrix(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """pairs of (shadow_band, prod_band) -> nested count matrix."""
    matrix: dict[str, dict[str, int]] = {b: {b2: 0 for b2 in BANDS} for b in BANDS}
    for sb, pb in pairs:
        matrix[sb][pb] += 1
    return matrix


def agreement_rate(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    same = sum(1 for sb, pb in pairs if sb == pb)
    return same / len(pairs)


def _aligned(band: str, polarity: str) -> str:
    """Did a provider's band align with a real reaction?

    positive/weak_positive: aligned if it recommended (top/mid), missed if it
    rejected, hedged if review.
    negative: aligned if it rejected, false_positive if it recommended,
    hedged if review.
    """
    recommended = band in ("top", "mid")
    rejected = band == "reject"
    if polarity in ("positive", "weak_positive"):
        if recommended:
            return "aligned"
        if rejected:
            return "missed"
        return "hedged"
    if polarity == "negative":
        if rejected:
            return "aligned"
        if recommended:
            return "false_positive"
        return "hedged"
    return "n/a"


def reaction_alignment(
    reacted: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """reacted rows carry: polarity, shadow_band, prod_band. Returns per
    provider a tally of aligned/missed/false_positive/hedged."""
    out = {
        "shadow": {"aligned": 0, "missed": 0, "false_positive": 0, "hedged": 0, "n/a": 0},
        "prod": {"aligned": 0, "missed": 0, "false_positive": 0, "hedged": 0, "n/a": 0},
    }
    for r in reacted:
        pol = r["polarity"]
        out["shadow"][_aligned(r["shadow_band"], pol)] += 1
        out["prod"][_aligned(r["prod_band"], pol)] += 1
    return out


def build_drift_report(store: Any, *, lookback_days: int = 14) -> dict[str, Any]:
    """Aggregate shadow-vs-prod drift and reaction alignment over runs whose
    shadow rows fall within the lookback window. Reads only; no mutation."""
    pairs: list[tuple[str, str]] = []
    reacted: list[dict[str, Any]] = []
    shadow_dist: dict[str, int] = {b: 0 for b in BANDS}
    prod_dist: dict[str, int] = {b: 0 for b in BANDS}

    rows = store.fetch_shadow_vs_prod(lookback_days=lookback_days)
    for r in rows:
        sb = shadow_band(r.get("shadow_recommendation"))
        pb = prod_band(r.get("prod_recommendation"))
        pairs.append((sb, pb))
        shadow_dist[sb] += 1
        prod_dist[pb] += 1
        if r.get("feedback_type"):
            reacted.append({
                "vacancy_key": r.get("vacancy_key"),
                "polarity": reaction_polarity(r.get("feedback_type")),
                "shadow_band": sb, "prod_band": pb,
            })

    return {
        "lookback_days": lookback_days,
        "vacancies_compared": len(pairs),
        "shadow_band_distribution": shadow_dist,
        "prod_band_distribution": prod_dist,
        "coarse_agreement_rate": agreement_rate(pairs),
        "agreement_matrix": agreement_matrix(pairs),
        "reactions_compared": len(reacted),
        "reaction_alignment": reaction_alignment(reacted),
    }
