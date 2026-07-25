#!/usr/bin/env python3
"""Aggregate controlled-run reports to see where the engineering pipeline is lost.

Usage:
    venv/bin/python scripts/pipeline_eligibility_report.py [--days 30]
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path


def _runs_dir() -> Path:
    """Where ``controlled_execution_report.json`` files actually land.

    Deliberately NOT ``Path.home() / ".hermes" / "controlled-runs"``: the
    durable root is an absolute path owned by the ``hermes`` user, and this
    script is frequently run over ``ssh hermes`` (which lands as root, whose
    home is ``/root``). Deriving it from ``$HOME`` silently reports zero runs.
    We reuse the writer's own constant so the two cannot drift apart.
    """
    try:
        from hermes_cli.pipeline_report_artifacts import DEFAULT_DURABLE_ROOT
    except Exception:  # pragma: no cover - import guard for standalone use
        return Path("/home/hermes/.hermes/controlled-runs")
    return DEFAULT_DURABLE_ROOT


RUNS_DIR = _runs_dir()


def summarize_runs(reports: list[dict]) -> dict:
    reasons: Counter[str] = Counter()
    pipelines: Counter[str] = Counter()
    router_statuses: Counter[str] = Counter()
    blocked_dirty = 0
    reviewer_invoked = 0
    commits_without_review = 0

    for report in reports:
        per = report.get("pipeline_execution_report") or {}
        gate = per.get("gate") or {}
        reasons[gate.get("preflight_reason_code") or "unknown"] += 1

        # The gate reports "router_not_selected" both when the router declined an
        # ordinary chat turn and when routing genuinely failed. Only the router's
        # own status tells them apart, and reading the reason code alone makes a
        # working router look like the biggest source of lost runs.
        routing = report.get("routing") or {}
        router_statuses[routing.get("router_status") or "unknown"] += 1

        execution = report.get("execution") or {}
        pipelines[execution.get("effective_pipeline_id") or "unknown"] += 1
        reviewed = bool(execution.get("reviewer_invoked"))
        if reviewed:
            reviewer_invoked += 1

        completion = per.get("completion") or {}
        if completion.get("blocked_reason") == "workspace_dirty_baseline":
            blocked_dirty += 1

        # A commit actually landed only when HEAD moved. ``execution.commit_status``
        # is not that signal -- it reports whether the git gate was armed
        # ("enabled"/"unavailable"), not whether anything was committed.
        git_gate = per.get("git_gate") or {}
        if git_gate.get("head_changed") and not reviewed:
            commits_without_review += 1

    return {
        "total": len(reports),
        "by_reason_code": dict(reasons),
        "by_router_status": dict(router_statuses),
        "router_declined": router_statuses.get("no_specialized_pipeline", 0),
        "router_needs_clarification": router_statuses.get("needs_clarification", 0),
        "by_pipeline": dict(pipelines),
        "blocked_dirty": blocked_dirty,
        "reviewer_invoked": reviewer_invoked,
        "commits_without_review": commits_without_review,
    }


def load_reports(days: int) -> list[dict]:
    cutoff = time.time() - days * 86400
    reports = []
    for path in RUNS_DIR.glob("*/controlled_execution_report.json"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
            reports.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return reports


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    summary = summarize_runs(load_reports(args.days))
    summary["runs_dir"] = str(RUNS_DIR)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
