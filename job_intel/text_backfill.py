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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from job_intel.ats_sources import (
    fetch_headhunter_detail,
    fetch_smartrecruiters_detail,
    fetch_teamtailor_detail,
)
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


FETCHERS: dict[str, Callable[[str], "str | None"]] = {
    "smartrecruiters": fetch_smartrecruiters_detail,
    "headhunter": fetch_headhunter_detail,
    "teamtailor": fetch_teamtailor_detail,
}


@dataclass(frozen=True)
class BackfillResult:
    row: Mapping
    state: str          # "ok" | "failed" | "unavailable"
    text: "str | None"


@dataclass
class BackfillReport:
    attempted: int = 0
    filled: int = 0
    failed: int = 0
    unavailable: int = 0
    skipped_budget: int = 0
    per_source: dict = field(default_factory=dict)
    results: list = field(default_factory=list)

    def _bump(self, source: str, key: str) -> None:
        bucket = self.per_source.setdefault(
            source, {"attempted": 0, "filled": 0, "failed": 0, "unavailable": 0})
        bucket[key] += 1


def backfill(rows, *, budget: int, fetchers=None) -> BackfillReport:
    """Fetch missing text for as many rows as the budget allows.

    Writes nothing. The caller decides what to persist and whether the result
    may reach a user — which is the whole reason the live branch and the sweep
    can share this function without being able to share consequences.
    """
    fetchers = FETCHERS if fetchers is None else fetchers
    chosen = [r for r in select(rows, budget=budget)
              if (r.get("source") or "") in fetchers]
    all_candidates = [r for r in select(rows, budget=len(rows) + 1)
                      if (r.get("source") or "") in fetchers]
    report = BackfillReport(skipped_budget=max(len(all_candidates) - len(chosen), 0))

    for row in chosen:
        source = row.get("source") or ""
        report.attempted += 1
        report._bump(source, "attempted")
        try:
            text = fetchers[source](row.get("url") or "")
        except Exception:
            report.failed += 1
            report._bump(source, "failed")
            report.results.append(BackfillResult(row, "failed", None))
            continue
        if not text or len(text.strip()) < PARTIAL_MIN:
            report.unavailable += 1
            report._bump(source, "unavailable")
            report.results.append(BackfillResult(row, "unavailable", None))
            continue
        report.filled += 1
        report._bump(source, "filled")
        report.results.append(BackfillResult(row, "ok", text.strip()))
    return report
