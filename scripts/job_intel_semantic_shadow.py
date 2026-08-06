#!/usr/bin/env python3
"""Phase III shadow deployment entrypoint (decoupled post-run job).

Runs the observe-only semantic shadow over one job-intel run's vacancies and
writes semantic_shadow_evaluation rows. Invoked AFTER the daily pipeline, as
its own step — it never touches the production scoring path.

Lives in scripts/ (outside the job_intel package) so it may freely import the
semantic bridge without crossing the production import boundary. Resolves the
repo itself (script-mode cron runs with cwd=script dir, outside the repo —
see CLAUDE.md namespace-package trap) and the live DB via the same env the
pipeline uses.

Usage:
    job_intel_semantic_shadow.py [--run-id N]   # default: latest run
Env:
    SEMANTIC_SHADOW_ENABLED=0   disables (no-op, exit 0)
    JOB_INTEL_DB_PATH           live DB (else default resolution)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _resolve_repo() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    repo = Path(home) / "hermes-agent"
    marker = repo / "job_intel" / "__main__.py"
    # The marker probe guards the PEP-420 namespace-package trap (CLAUDE.md):
    # never test `[[ -d job_intel ]]`, which the data dir would satisfy.
    if not marker.exists():
        # fall back to the repo two levels up from this script
        repo = Path(__file__).resolve().parents[1]
    return repo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, default=None)
    args = ap.parse_args()

    repo = _resolve_repo()
    sys.path.insert(0, str(repo))

    from job_intel.runtime import resolve_db_path
    from job_intel.store import JobIntelStore
    from job_intel.vacancy_understanding.shadow_deploy import (
        run_semantic_shadow,
        semantic_shadow_enabled,
    )

    if not semantic_shadow_enabled():
        print("[semantic-shadow] SEMANTIC_SHADOW_ENABLED=0 — skipping")
        return 0

    store = JobIntelStore(resolve_db_path())
    run_id = args.run_id if args.run_id is not None else store.latest_run_id()
    if run_id is None:
        print("[semantic-shadow] no runs found — nothing to shadow")
        return 0

    tally = run_semantic_shadow(store, run_id)
    total = sum(tally.values())
    print(f"[semantic-shadow] run_id={run_id} evaluated={total} tally={tally}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
