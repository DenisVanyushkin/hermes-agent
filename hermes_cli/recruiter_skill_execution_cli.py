from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from .recruiter_skill_execution import (
    FLOW_EVALUATE_AND_POSITION,
    RecruiterSkillExecutionRequest,
    RecruiterSkillExecutionStatus,
    run_recruiter_skill_execution,
)


Runner = Callable[[RecruiterSkillExecutionRequest], object]
_SUCCESS_STATUSES = {RecruiterSkillExecutionStatus.EXECUTION_READY}


def register_recruiter_skill_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "recruiter-skill",
        help="Execute the controlled CLI-only recruiter skill bridge",
        description=(
            "Build recruiter context and deterministic skill input packets, then optionally execute the "
            "evaluate-and-position flow through an explicitly enabled executor without outbound actions."
        ),
    )
    nested = parser.add_subparsers(dest="recruiter_skill_command")
    execute = nested.add_parser("execute", help="Run recruiter evaluate-and-position flow")
    execute.add_argument("--vacancy-id", type=int, default=None, help="Job-intel vacancy id")
    execute.add_argument("--vacancy-url", default=None, help="Job-intel vacancy URL")
    execute.add_argument("--opportunity-id", type=int, default=None, help="CRM opportunity id")
    execute.add_argument("--job-intel-db-path", default=None, help="Override job-intel SQLite path")
    execute.add_argument("--private-career-dir", default=None, help="Override private career context directory")
    execute.add_argument("--repo-root", default=None, help="Override repo root used for recruiter package discovery")
    execute.add_argument("--stale-after-days", type=int, default=14, help="Age threshold for stale context warnings")
    execute.add_argument("--flow", default=FLOW_EVALUATE_AND_POSITION, help="Execution flow id")
    execute.add_argument(
        "--allow-provider-execution",
        action="store_true",
        help="Explicitly open the provider fuse for the CLI-only recruiter bridge",
    )
    execute.add_argument("--json", action="store_true", help="Print JSON output")
    execute.set_defaults(func=cmd_recruiter_skill_execute)
    return parser


def cmd_recruiter_skill_execute(
    args: argparse.Namespace,
    *,
    runner: Runner = run_recruiter_skill_execution,
) -> None:
    if not getattr(args, "json", False):
        sys.stderr.write("recruiter-skill execute: --json is required\n")
        raise SystemExit(2)

    request = RecruiterSkillExecutionRequest(
        vacancy_id=getattr(args, "vacancy_id", None),
        vacancy_url=getattr(args, "vacancy_url", None),
        opportunity_id=getattr(args, "opportunity_id", None),
        job_intel_db_path=getattr(args, "job_intel_db_path", None),
        private_career_dir=getattr(args, "private_career_dir", None),
        repo_root=_optional_path(getattr(args, "repo_root", None)),
        stale_after_days=getattr(args, "stale_after_days", 14),
        flow=getattr(args, "flow", FLOW_EVALUATE_AND_POSITION),
        allow_provider_execution=getattr(args, "allow_provider_execution", False),
    )
    report = runner(request)
    sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    raise SystemExit(0 if report.status in _SUCCESS_STATUSES else 1)


def _optional_path(value: str | None) -> Path | None:
    if value is None:
        return None
    return Path(value)
