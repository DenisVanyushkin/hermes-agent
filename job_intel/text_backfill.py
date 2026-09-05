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
    is_rate_limited_detail,
    is_transient_detail,
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
    """One row's outcome. frozen=True stops reassigning these three fields,
    but not mutation of the referenced row mapping's contents -- backfill()
    itself never mutates row, but a caller holding the same reference can.
    """
    row: Mapping
    state: str          # "ok" | "failed" | "unavailable"
    text: "str | None"


@dataclass
class BackfillReport:
    attempted: int = 0
    filled: int = 0
    failed: int = 0
    unavailable: int = 0
    #: Rows not attempted because their source answered 429 earlier in this
    #: run. They produce no BackfillResult, so no state is persisted for them
    #: and they keep whatever eligibility they had.
    rate_limited: int = 0
    skipped_budget: int = 0
    per_source: dict = field(default_factory=dict)
    results: list = field(default_factory=list)

    def _bump(self, source: str, key: str) -> None:
        bucket = self.per_source.setdefault(
            source, {"attempted": 0, "filled": 0, "failed": 0, "unavailable": 0,
                     "rate_limited": 0})
        bucket[key] += 1


def backfill(rows, *, budget: int, fetchers=None) -> BackfillReport:
    """Fetch missing text for as many rows as the budget allows.

    Writes nothing. The caller decides what to persist and whether the result
    may reach a user — which is the whole reason the live branch and the sweep
    can share this function without being able to share consequences.

    Two states, and the difference is irreversible. `unavailable` is TERMINAL:
    JobIntelStore.rows_needing_text never offers such a row again, so it is
    reserved for positive evidence that there is no text to get — a 404/410, or
    a well-formed response carrying none. `failed` stays eligible and absorbs
    everything else, including anything unexpected: a bug must not be able to
    write terminal state.

    A source that answers 429 is closed for the rest of the call. Its remaining
    rows are counted in `rate_limited` and produce NO result, so a caller that
    persists results writes nothing for them and they stay eligible. Each
    Non-HH detail requests are preceded by a politeness delay
    (ats_sources.DETAIL_REQUEST_DELAY_SECONDS, 0.5s, overridable with
    JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS). HH detail pacing is owned by
    job_intel.hh_api and uses JOB_INTEL_HH_DELAY_SECONDS.

    Never raises.
    """
    fetchers = FETCHERS if fetchers is None else fetchers
    chosen = [r for r in select(rows, budget=budget)
              if (r.get("source") or "") in fetchers]
    all_candidates = [r for r in select(rows, budget=len(rows) + 1)
                      if (r.get("source") or "") in fetchers]
    report = BackfillReport(skipped_budget=max(len(all_candidates) - len(chosen), 0))
    closed_sources: set[str] = set()

    def _failed(row, source) -> None:
        report.failed += 1
        report._bump(source, "failed")
        report.results.append(BackfillResult(row, "failed", None))

    def _unavailable(row, source) -> None:
        report.unavailable += 1
        report._bump(source, "unavailable")
        report.results.append(BackfillResult(row, "unavailable", None))

    for row in chosen:
        source = row.get("source") or ""
        if source in closed_sources:
            report.rate_limited += 1
            report._bump(source, "rate_limited")
            continue
        report.attempted += 1
        report._bump(source, "attempted")
        try:
            text = fetchers[source](row.get("url") or "")
        except Exception:
            # A fetcher is not supposed to raise, but if one does the reason is
            # lost, and an unknown reason is never grounds for terminal state.
            _failed(row, source)
            continue
        # Signals are checked BEFORE any truthiness or length test: they are
        # truthy objects, so len(signal.strip()) would raise and a bare
        # truthiness check would store one as a description.
        if is_rate_limited_detail(text):
            closed_sources.add(source)
            _failed(row, source)
            continue
        if is_transient_detail(text):
            _failed(row, source)
            continue
        if text is None:
            _unavailable(row, source)
            continue
        if not isinstance(text, str):
            # Contract violation by a fetcher. Retryable, never terminal.
            _failed(row, source)
            continue
        stripped = text.strip()
        if len(stripped) < PARTIAL_MIN:
            # Terminal on purpose, and unlike the cases above this is a
            # content judgement on a SUCCESSFUL fetch: the posting's own text
            # was retrieved and is genuinely too short. A row whose real
            # description is 150 characters can never pass PARTIAL_MIN, so
            # retrying it every sweep burns budget for a guaranteed identical
            # result.
            _unavailable(row, source)
            continue
        report.filled += 1
        report._bump(source, "filled")
        report.results.append(BackfillResult(row, "ok", stripped))
    return report
