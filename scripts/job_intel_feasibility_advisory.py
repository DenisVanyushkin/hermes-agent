#!/usr/bin/env python3
"""Phase III Stage 1 — feasibility advisory entrypoint (decoupled post-run).

Builds and (optionally) posts the observe→advisory feasibility message for a
run. Defaults to DRY-RUN and to the OFF flag: it prints the rendered message
and posts nothing unless SEMANTIC_SHADOW_ADVISORY_ENABLED=1 and --post are
BOTH set. Reads only; changes no production decision.

Usage:
  job_intel_feasibility_advisory.py [--run-id N] [--post]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _resolve_repo() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    repo = Path(home) / "hermes-agent"
    if not (repo / "job_intel" / "__main__.py").exists():
        repo = Path(__file__).resolve().parents[1]
    return repo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument("--post", action="store_true",
                    help="actually post to Slack (also needs the enable flag)")
    args = ap.parse_args()

    sys.path.insert(0, str(_resolve_repo()))
    from job_intel.runtime import resolve_db_path
    from job_intel.shadow_advisory import (
        advisory_enabled, build_feasibility_advisory, describe_post_result,
        format_advisory, post_advisory,
    )
    from job_intel.store import JobIntelStore

    store = JobIntelStore(resolve_db_path())
    run_id = args.run_id if args.run_id is not None else store.latest_run_id()
    if run_id is None:
        print("[advisory] no runs")
        return 0

    items = build_feasibility_advisory(store.fetch_shadow_advisory(run_id=run_id))
    message = format_advisory(items, run_label=f"run {run_id}")
    if message is None:
        print(f"[advisory] run {run_id}: no feasibility caveats on shown roles — nothing to post")
        return 0

    # posting requires BOTH the enable flag and --post; otherwise dry-run.
    do_post = args.post and advisory_enabled()
    result = post_advisory(message, dry_run=not do_post)
    print(describe_post_result(result, run_id=run_id, count=len(items)))
    if result.get("dry_run"):
        print("\n" + message)
    # a failed delivery is a real failure: exit non-zero so systemd/cron sees it
    return 0 if (result.get("posted") or result.get("dry_run")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
