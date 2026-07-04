"""Weekly Company Discovery Report formatter (buckets + reasons, no scores)."""
from __future__ import annotations

from collections import Counter

from .anchors import EXPLORATION_QUOTA
from .models import CandidateCompany

_ACTIONS = {
    "strong_candidate": "review manually; not auto-added",
    "candidate": "review manually; not auto-added",
    "maybe": "needs stronger evidence",
}
# Aggregator-derived fintech_payments_fit alone is weak evidence and does NOT
# qualify a hold for endpoint research — only anchor similarity or thesis fit do.
_RESEARCH_SIGNALS = ("positive_anchor_similarity", "thesis_fit")


def _hold_subgroup(c: CandidateCompany) -> str:
    if any(r in c.reasons for r in _RESEARCH_SIGNALS):
        return "needs_endpoint_research"
    return "low_quality_hold"


def _line(c: CandidateCompany, *, full: bool) -> str:
    head = f"• {c.name}"
    if c.ats_type:
        head += f" — {c.ats_type} ✓"
    if c.dry_run_vacancies >= 0:
        sample = f' (e.g. "{c.dry_run_sample_titles[0]}")' if c.dry_run_sample_titles else ""
        head += f", dry-run: {c.dry_run_vacancies} vacancies{sample}"
        if c.dry_run_vacancies > 0 and not c.dry_run_product_sample:
            head += " — no product-leadership sample found"
    if not full:
        return f"{head} [{', '.join(c.reasons)}]"
    return (f"{head}\n  reasons: {', '.join(c.reasons)}\n"
            f"  via: {' + '.join(c.sources)}")


def format_universe_report(candidates: list[CandidateCompany], *, week_label: str) -> str:
    total = len(candidates)
    non_anchor = sum(1 for c in candidates if c.sources != ["d1_anchor_similar"])
    pct = int(100 * non_anchor / total) if total else 100
    quota_flag = (" ⚠️ below 30% exploration quota"
                  if total and pct < EXPLORATION_QUOTA * 100 else "")

    out = [f"\U0001f9ed Company Discovery Report — {week_label} (read-only, no seeds changed)",
           "",
           "*What Denis should do*",
           "This is read-only discovery, not an approval queue.",
           "Suggested actions: approve for research / reject as irrelevant / hold.",
           "",
           f"Exploration: {non_anchor}/{total} non-anchor-sourced ({pct}%){quota_flag}", ""]

    by_bucket: dict[str, list[CandidateCompany]] = {}
    for c in candidates:
        by_bucket.setdefault(c.bucket or "reject", []).append(c)

    icons = {"strong_candidate": "\U0001f7e2", "candidate": "\U0001f535", "maybe": "⚪"}
    for bucket in ("strong_candidate", "candidate", "maybe"):
        group = by_bucket.get(bucket, [])
        if not group:
            continue
        out.append(f"{icons[bucket]} {bucket} ({len(group)}) — {_ACTIONS[bucket]}")
        full = bucket in ("strong_candidate", "candidate")
        out.extend(_line(c, full=full) for c in group)
        out.append("")

    holds = by_bucket.get("hold", [])
    for sub, icon in (("needs_endpoint_research", "\U0001f50d"), ("low_quality_hold", "⏸")):
        group = [c for c in holds if _hold_subgroup(c) == sub]
        if not group:
            continue
        out.append(f"{icon} hold — {sub} ({len(group)})")
        out.extend(_line(c, full=False) for c in group)
        out.append("")

    rejected = by_bucket.get("reject", [])
    if rejected:
        reasons = Counter(r for c in rejected for r in c.reasons
                          if r in ("low_relevance", "reputation_risk"))
        detail = ", ".join(f"{k}: {v}" for k, v in reasons.most_common())
        out.append(f"Rejected/suppressed this run: {len(rejected)} ({detail})")
    return "\n".join(out).strip()
