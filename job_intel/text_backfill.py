"""Select vacancies whose description was never fetched, and bound the cost.

The gate is deliberately wide: only an explicit blocklist excludes a vacancy,
and volume is controlled by a per-run budget. A title gate is a verdict — it
decides a role does not deserve text, and once decided the role can never be
reclassified, because classification is what needed the text. It is also a
language filter nobody asked for: an English-token gate scores zero on
teamtailor and drops "Директор по продукту" for its language rather than its
content. A budget's mistake is reversible tomorrow; a gate's is not.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from job_intel.evaluator import _title_function_blocker
from job_intel.text_thresholds import PARTIAL_MIN

# Sources whose listing endpoint carries no description and which expose a
# detail endpoint we can call. linkedin and duckduckgo are excluded on purpose:
# no open detail API (login wall / search results).
BACKFILL_SOURCES = frozenset({"smartrecruiters", "headhunter", "teamtailor"})

_NON_PRODUCT_EXEC = re.compile(
    r"\b(account|sales|customer success|marketing|business development)\s+executive\b",
    re.I)
# Priority only — never exclusion. Absence of these tokens must not cost a
# vacancy its text.
_SENIORITY = re.compile(
    r"\b(head|director|vp|vice president|chief|cpo|gm|general manager|lead|"
    r"principal|group|svp|evp|owner|managing)\b", re.I)
_DOMAIN = re.compile(
    r"\b(product|platform|growth|monetization|monetisation|pricing|payments?|"
    r"commercial|portfolio)\b", re.I)


def needs_text(row: Mapping) -> bool:
    """Whether this row has no usable description.

    PARTIAL_MIN is imported from job_intel.text_thresholds rather than
    restated: it is the same constant classify_corpus uses to call a vacancy
    title_only_source_incomplete, and the two answers to "does this row have
    usable text" must not drift apart.
    """
    if (row.get("source") or "") not in BACKFILL_SOURCES:
        return False
    description = (row.get("description") or "").strip()
    if not description:
        return True
    if description == (row.get("title") or "").strip():
        return True
    return len(description) < PARTIAL_MIN


def _blocked(title: str) -> bool:
    t = title or ""
    return bool(_NON_PRODUCT_EXEC.search(t)) or bool(_title_function_blocker(t))


def _priority(title: str) -> int:
    """Lower sorts first. Priority spends a constrained budget well; it never
    excludes anyone."""
    t = title or ""
    if _SENIORITY.search(t) and _DOMAIN.search(t):
        return 0
    if _SENIORITY.search(t) or _DOMAIN.search(t):
        return 1
    return 2


def select(rows: Sequence[Mapping], *, budget: int) -> list[Mapping]:
    candidates = [r for r in rows if needs_text(r) and not _blocked(r.get("title") or "")]
    candidates.sort(key=lambda r: _priority(r.get("title") or ""))
    return candidates[:max(budget, 0)]
