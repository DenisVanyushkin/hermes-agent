"""Weekly Company Discovery Report formatter (buckets + reasons, no scores)."""
from __future__ import annotations

from collections import Counter

from .anchors import EXPLORATION_QUOTA
from .models import CandidateCompany

_ORDER = ["strong_candidate", "candidate", "maybe", "hold", "reject"]
_ICONS = {"strong_candidate": "\U0001f7e2", "candidate": "\U0001f535",
          "maybe": "⚪", "hold": "⏸"}


def _line(c: CandidateCompany, *, full: bool) -> str:
    head = f"• {c.name}"
    if c.ats_type:
        head += f" — {c.ats_type} ✓"
    if c.dry_run_vacancies >= 0:
        sample = f' (e.g. "{c.dry_run_sample_titles[0]}")' if c.dry_run_sample_titles else ""
        head += f", dry-run: {c.dry_run_vacancies} vacancies{sample}"
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
           f"Exploration: {non_anchor}/{total} non-anchor-sourced ({pct}%){quota_flag}", ""]
    by_bucket: dict[str, list[CandidateCompany]] = {}
    for c in candidates:
        by_bucket.setdefault(c.bucket or "reject", []).append(c)
    for bucket in _ORDER[:4]:
        group = by_bucket.get(bucket, [])
        if not group:
            continue
        out.append(f"{_ICONS[bucket]} {bucket} ({len(group)})")
        full = bucket in ("strong_candidate", "candidate")
        out.extend(_line(c, full=full) for c in group)
        out.append("")
    rejected = by_bucket.get("reject", [])
    if rejected:
        reasons = Counter(r for c in rejected for r in c.reasons
                          if r in ("low_relevance", "reputation_risk"))
        detail = ", ".join(f"{k}: {v}" for k, v in reasons.most_common())
        out.append(f"Rejected this run: {len(rejected)} ({detail})")
    return "\n".join(out).strip()
