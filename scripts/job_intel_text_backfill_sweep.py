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

import os
import sys

from job_intel.runtime import assert_runtime_contract, resolve_db_path
from job_intel.store import JobIntelStore
from job_intel.text_backfill import BACKFILL_SOURCES, backfill

# Rows still appearing in the daily listing are handled by the live branch;
# only what the listing no longer returns belongs to the sweep.
SEEN_RECENTLY_DAYS = 2


def sweep(store: JobIntelStore, *, budget: int, fetchers=None):
    rows = store.rows_needing_text(sorted(BACKFILL_SOURCES), limit=budget,
                                   exclude_seen_since_days=SEEN_RECENTLY_DAYS)
    report = backfill(rows, budget=budget, fetchers=fetchers)
    for result in report.results:
        store.record_text_backfill(int(result.row["id"]), result.state, result.text)
    return report


def main() -> int:
    assert_runtime_contract()
    store = JobIntelStore(resolve_db_path())
    store.bootstrap()
    budget = int(os.getenv("JOB_INTEL_TEXT_BACKFILL_SWEEP_BUDGET", "400") or "400")
    report = sweep(store, budget=budget)
    print(f"attempted={report.attempted} filled={report.filled} "
          f"failed={report.failed} unavailable={report.unavailable} "
          f"remaining={report.skipped_budget}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
