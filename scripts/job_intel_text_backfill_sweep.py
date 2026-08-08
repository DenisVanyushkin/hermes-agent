#!/usr/bin/env python3
"""Recover description text for vacancies the daily listing no longer returns.

The daily collection only re-surfaces ~17% of history (874 of 5267
smartrecruiters rows seen in the last two days), so the live branch can never
reach the rest. This sweep reads them from the database and fills their text.

It exists to improve the corpus that §7.2 is measured on. It writes nothing but
`description` and the backfill state -- no scoring, no notifications, no
observability. That is enforced by what this file imports, not by a flag: a
shared function with a boolean would eventually be called with the wrong value
and would announce a half-year-old vacancy to the operator.
"""
from __future__ import annotations

import logging
import os
import sys

from job_intel.runtime import assert_runtime_contract, resolve_db_path
from job_intel.store import JobIntelStore
from job_intel.text_backfill import BACKFILL_SOURCES, backfill

logger = logging.getLogger(__name__)

# Rows still appearing in the daily listing are handled by the live branch;
# only what the listing no longer returns belongs to the sweep.
SEEN_RECENTLY_DAYS = 2


def sweep(store: JobIntelStore, *, budget: int, fetchers=None):
    """Fetch and persist text for up to `budget` eligible rows.

    Asks for one row beyond the budget purely to learn whether the backlog
    outgrows this pass -- that extra row is sliced off before `backfill()`
    ever sees it, so it is never fetched and never written. The answer is
    attached to the returned report as `more_eligible`, a plain presence
    check rather than an exact count: one probe row proves "at least one
    more", nothing about how many.
    """
    rows = store.rows_needing_text(sorted(BACKFILL_SOURCES), limit=budget + 1,
                                   exclude_seen_since_days=SEEN_RECENTLY_DAYS)
    more_eligible = len(rows) > budget
    rows = rows[:budget]
    report = backfill(rows, budget=budget, fetchers=fetchers)
    report.more_eligible = more_eligible
    for result in report.results:
        try:
            store.record_text_backfill(int(result.row["id"]), result.state, result.text)
        except Exception:
            # A row that fails to persist keeps its prior state, so it stays
            # eligible for the next pass -- containing this failure here must
            # not cost the rows that come after it in this same loop.
            logger.exception(
                "text backfill: failed to persist row id=%s", result.row.get("id"))
    return report


def main() -> int:
    assert_runtime_contract()
    store = JobIntelStore(resolve_db_path())
    store.bootstrap()
    budget = int(os.getenv("JOB_INTEL_TEXT_BACKFILL_SWEEP_BUDGET", "400") or "400")
    report = sweep(store, budget=budget)
    print(f"attempted={report.attempted} filled={report.filled} "
          f"failed={report.failed} unavailable={report.unavailable} "
          f"more_eligible={report.more_eligible}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
