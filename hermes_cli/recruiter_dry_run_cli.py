from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .recruiter_candidate_facts import load_candidate_facts_packet, run_candidate_facts_cli
from .recruiter_dry_run import (
    RecruiterDryRunReport,
    RecruiterDryRunRequest,
    RecruiterDryRunStatus,
    RecruiterPositioningSmokeReport,
    RecruiterPositioningSmokeStatus,
    _evaluation_downstream_gates,
    build_fake_positioning_packet_from_candidate_facts,
    run_recruiter_application_materials_flow_dry_run,
    run_recruiter_context_dry_run,
    run_recruiter_evaluation_flow_dry_run,
    run_recruiter_positioning_flow_dry_run,
    run_recruiter_positioning_smoke_harness,
)


_READY_STATUSES = {
    RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT,
    RecruiterDryRunStatus.EVALUATION_READY,
    RecruiterDryRunStatus.POSITIONING_READY,
    RecruiterDryRunStatus.APPLICATION_MATERIALS_READY,
}
_READY_SMOKE_STATUSES = {
    RecruiterPositioningSmokeStatus.READY,
}
_READY_PACKET_STATUSES = {"READY_PROVIDER_VISIBLE"}


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
    dry_run.add_argument("--flow", choices=["evaluate-vacancy", "positioning", "positioning-and-evidence", "application-materials"], default=None, help="Run a controlled recruiter flow dry-run")
    dry_run.add_argument("--prompt", default=None, help="Prompt text for prompt-driven recruiter flow dry-runs")
    dry_run.add_argument("--evaluation-packet-json", default=None, help="Read only this evaluation packet JSON file for positioning-and-evidence dry-runs")
    dry_run.add_argument("--candidate-facts-packet-json", default=None, help="Read only this candidate facts packet JSON file for positioning dry-runs")
    dry_run.add_argument("--fake-positioning-output", action="store_true", help="Build deterministic no-provider positioning output from validated candidate facts")
    dry_run.add_argument("--positioning-packet-json", default=None, help="Read only this positioning packet JSON file for application-materials dry-runs")
    dry_run.add_argument("--document-target", default=None, help="Optional application-materials document target")
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
    candidate_facts = nested.add_parser(
        "candidate-facts",
        help="Print a JSON safe candidate facts packet",
    )
    candidate_facts.add_argument(
        "--fixture-safe-facts-json",
        default=None,
        help="Read only this sanitized fixture JSON file for candidate-facts testing",
    )
    candidate_facts.add_argument("--json", action="store_true", help="Print JSON output (default behavior)")
    candidate_facts.set_defaults(func=cmd_recruiter_context)
    smoke_positioning = nested.add_parser(
        "smoke-positioning",
        help="Print a JSON provider-ready positioning smoke report",
    )
    smoke_positioning.add_argument("--evaluation-packet-json", required=True, help="Read only this evaluation packet JSON file")
    smoke_positioning.add_argument("--candidate-facts-packet-json", required=True, help="Read only this candidate facts packet JSON file")
    smoke_positioning.add_argument("--allow-provider-execution", action="store_true", help="Explicitly allow positioning executor invocation")
    smoke_positioning.add_argument(
        "--private-context-status",
        choices=["PRIVATE_CONTEXT_AVAILABLE", "PRIVATE_CONTEXT_MISSING", "PRIVATE_CONTEXT_NOT_INSPECTED"],
        default="PRIVATE_CONTEXT_NOT_INSPECTED",
        help="Optional private context readiness metadata forwarded to the executor seam",
    )
    smoke_positioning.add_argument("--repo-root", default=None, help="Override repo root used for recruiter package discovery")
    smoke_positioning.add_argument("--json", action="store_true", help="Print JSON output (default behavior)")
    smoke_positioning.set_defaults(func=cmd_recruiter_context)
    return parser


def cmd_recruiter_context(args: argparse.Namespace) -> None:
    repo_root = _optional_path(getattr(args, "repo_root", None))
    if getattr(args, "recruiter_context_command", None) == "candidate-facts":
        packet = run_candidate_facts_cli(
            fixture_safe_facts_json=getattr(args, "fixture_safe_facts_json", None),
        )
        sys.stdout.write(json.dumps(packet.to_dict(), sort_keys=True) + "\n")
        raise SystemExit(0 if packet.status in _READY_PACKET_STATUSES else 1)
    if getattr(args, "recruiter_context_command", None) == "smoke-positioning":
        report = _run_positioning_smoke_report(args, repo_root)
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        raise SystemExit(0 if report.status in _READY_SMOKE_STATUSES else 1)
    if getattr(args, "flow", None) == "evaluate-vacancy":
        report = run_recruiter_evaluation_flow_dry_run(
            prompt=getattr(args, "prompt", None) or "",
            repo_root=repo_root,
            private_context_status=getattr(args, "private_context_status", "PRIVATE_CONTEXT_NOT_INSPECTED"),
            allow_provider_execution=getattr(args, "allow_provider_execution", False),
        )
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        raise SystemExit(0 if report.status in _READY_STATUSES else 1)
    if getattr(args, "flow", None) in {"positioning", "positioning-and-evidence"}:
        evaluation_packet_path = getattr(args, "evaluation_packet_json", None)
        if not evaluation_packet_path:
            report = _cli_error_report("evaluation_packet_json_required")
        else:
            try:
                evaluation_packet = json.loads(Path(evaluation_packet_path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                report = _cli_error_report("evaluation_packet_json_missing")
            except json.JSONDecodeError:
                report = _cli_error_report("evaluation_packet_json_invalid")
            else:
                candidate_facts_packet_path = getattr(args, "candidate_facts_packet_json", None)
                try:
                    candidate_facts_packet = (
                        load_candidate_facts_packet(candidate_facts_packet_path)
                        if candidate_facts_packet_path
                        else None
                    )
                except ValueError as exc:
                    report = _cli_error_report(str(exc))
                else:
                    run_kwargs = dict(
                        evaluation_packet=evaluation_packet,
                        candidate_facts_packet=candidate_facts_packet,
                        repo_root=repo_root,
                        private_context_status=getattr(args, "private_context_status", "PRIVATE_CONTEXT_NOT_INSPECTED"),
                        allow_provider_execution=getattr(args, "allow_provider_execution", False),
                    )
                    if getattr(args, "fake_positioning_output", False):
                        run_kwargs["fake_positioning_result_factory"] = build_fake_positioning_packet_from_candidate_facts
                    report = run_recruiter_positioning_flow_dry_run(**run_kwargs)
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
        raise SystemExit(0 if report.status in _READY_STATUSES else 1)
    if getattr(args, "flow", None) == "application-materials":
        positioning_packet_path = getattr(args, "positioning_packet_json", None)
        if not positioning_packet_path:
            report = _cli_error_report("positioning_packet_json_required")
        else:
            try:
                positioning_packet = json.loads(Path(positioning_packet_path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                report = _cli_error_report("positioning_packet_json_missing")
            except json.JSONDecodeError:
                report = _cli_error_report("positioning_packet_json_invalid")
            else:
                report = run_recruiter_application_materials_flow_dry_run(
                    positioning_packet=positioning_packet,
                    repo_root=repo_root,
                    private_context_status=getattr(args, "private_context_status", "PRIVATE_CONTEXT_NOT_INSPECTED"),
                    allow_provider_execution=getattr(args, "allow_provider_execution", False),
                    document_target=getattr(args, "document_target", None),
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


def _cli_error_report(error: str) -> RecruiterDryRunReport:
    if error.startswith("evaluation_packet_") or error.startswith("candidate_facts_packet_"):
        status = RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
        context_status = "POSITIONING_INPUT_REQUIRED"
    else:
        status = RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED
        context_status = "APPLICATION_MATERIALS_INPUT_REQUIRED"
    return RecruiterDryRunReport(
        status=status,
        context_status=context_status,
        input={},
        readiness={"ready": False, "reason": error},
        context_packet=None,
        evaluation_flow=None,
        evaluation_result=None,
        positioning_result=None,
        missing_requirements=[],
        warnings=[],
        errors=[error],
        provenance={"writes_performed": False, "dry_run": True},
        next_allowed_actions=[],
        provider_called=False,
        provider_execution_enabled=False,
        executor_called=False,
        downstream_gates=_evaluation_downstream_gates(),
    )


def _run_positioning_smoke_report(
    args: argparse.Namespace,
    repo_root: Path | None,
) -> RecruiterPositioningSmokeReport:
    try:
        evaluation_packet = json.loads(Path(getattr(args, "evaluation_packet_json")).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _positioning_smoke_cli_error_report("evaluation_packet_json_missing")
    except json.JSONDecodeError:
        return _positioning_smoke_cli_error_report("evaluation_packet_json_invalid")

    try:
        candidate_facts_packet = load_candidate_facts_packet(getattr(args, "candidate_facts_packet_json"))
    except ValueError as exc:
        return _positioning_smoke_cli_error_report(str(exc))

    return run_recruiter_positioning_smoke_harness(
        evaluation_packet=evaluation_packet,
        candidate_facts_packet=candidate_facts_packet,
        repo_root=repo_root,
        private_context_status=getattr(args, "private_context_status", "PRIVATE_CONTEXT_NOT_INSPECTED"),
        allow_provider_execution=getattr(args, "allow_provider_execution", False),
    )


def _positioning_smoke_cli_error_report(error: str) -> RecruiterPositioningSmokeReport:
    return RecruiterPositioningSmokeReport(
        schema_version="recruiter_positioning_smoke_report_v1",
        status=RecruiterPositioningSmokeStatus.INPUT_BLOCKED,
        readiness_reason=error,
        provider_allowed=False,
        provider_called=False,
        executor_called=False,
        input_validation={
            "ready": False,
            "evaluation_packet_ready": False,
            "candidate_facts_packet_ready": False,
            "errors": [error],
        },
        output_validation={"ready": False, "status": "not_run", "errors": []},
        errors=[error],
        warnings=[],
        next_allowed_actions=[],
        provenance={"writes_performed": False, "dry_run": True, "flow": "positioning-smoke"},
    )
