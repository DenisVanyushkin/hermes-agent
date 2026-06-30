from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .recruiter_dry_run import (
    RecruiterDryRunRequest,
    RecruiterDryRunStatus,
    run_recruiter_context_dry_run,
    run_recruiter_evaluation_flow_dry_run,
)


_READY_STATUSES = {
    RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT,
    RecruiterDryRunStatus.EVALUATION_READY,
}


def register_recruiter_context_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "recruiter-context",
        help="Inspect recruiter context through a safe read-only dry-run",
        description=(
            "Build a deterministic recruiter-context dry-run report without routing, provider calls, "
            "gateway integration, CRM writes, or outbound actions."
        ),
    )
    nested = parser.add_subparsers(dest="recruiter_context_command")
    dry_run = nested.add_parser(
        "dry-run",
        help="Print a JSON recruiter-context dry-run report",
    )
    dry_run.add_argument("--vacancy-id", type=int, default=None, help="Job-intel vacancy id")
    dry_run.add_argument("--vacancy-url", default=None, help="Job-intel vacancy URL")
    dry_run.add_argument("--opportunity-id", type=int, default=None, help="CRM opportunity id")
    dry_run.add_argument("--flow", choices=["evaluate-vacancy"], default=None, help="Run a prompt-based recruiter flow dry-run")
    dry_run.add_argument("--prompt", default=None, help="Prompt text for prompt-driven recruiter flow dry-runs")
    dry_run.add_argument("--allow-provider-execution", action="store_true", help="Explicitly allow provider-backed evaluation for READY evaluate-vacancy dry-runs")
    dry_run.add_argument(
        "--private-context-status",
        choices=["PRIVATE_CONTEXT_AVAILABLE", "PRIVATE_CONTEXT_MISSING", "PRIVATE_CONTEXT_NOT_INSPECTED"],
        default="PRIVATE_CONTEXT_NOT_INSPECTED",
        help="Explicit private context readiness for evaluate-vacancy dry-runs",
    )
    dry_run.add_argument("--job-intel-db-path", default=None, help="Override job-intel SQLite path")
    dry_run.add_argument("--private-career-dir", default=None, help="Override private career context directory")
    dry_run.add_argument("--repo-root", default=None, help="Override repo root used for recruiter package discovery")
    dry_run.add_argument("--stale-after-days", type=int, default=14, help="Age threshold for stale context warnings")
    dry_run.add_argument("--json", action="store_true", help="Print JSON output (default behavior)")
    dry_run.set_defaults(func=cmd_recruiter_context)
    return parser


def cmd_recruiter_context(args: argparse.Namespace) -> None:
    repo_root = _optional_path(getattr(args, "repo_root", None))
    if getattr(args, "flow", None) == "evaluate-vacancy":
        report = run_recruiter_evaluation_flow_dry_run(
            prompt=getattr(args, "prompt", None) or "",
            repo_root=repo_root,
            private_context_status=getattr(args, "private_context_status", "PRIVATE_CONTEXT_NOT_INSPECTED"),
            allow_provider_execution=getattr(args, "allow_provider_execution", False),
        )
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        raise SystemExit(0 if report.status in _READY_STATUSES else 1)

    request = RecruiterDryRunRequest(
        vacancy_id=getattr(args, "vacancy_id", None),
        vacancy_url=getattr(args, "vacancy_url", None),
        opportunity_id=getattr(args, "opportunity_id", None),
        job_intel_db_path=getattr(args, "job_intel_db_path", None),
        private_career_dir=getattr(args, "private_career_dir", None),
        repo_root=repo_root,
        stale_after_days=getattr(args, "stale_after_days", 14),
    )
    report = run_recruiter_context_dry_run(request)
    sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    raise SystemExit(0 if report.status in _READY_STATUSES else 1)


def _optional_path(value: str | None) -> Path | None:
    if value is None:
        return None
    return Path(value)
