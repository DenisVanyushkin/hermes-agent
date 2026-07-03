from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from .recruiter_decision_flow import (
    DecisionSupportRequest,
    run_recruiter_decision_support_flow,
)
from .recruiter_decision_modules import DecisionBundleStatus


Runner = Callable[..., object]


def register_recruiter_decision_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "recruiter-decision",
        help="Run the modular company/vacancy decision-support bundle (draft-only)",
        description=(
            "Run one or more decision-support analysis modules (vacancy assessment, company assessment, "
            "risk register, recommendation, positioning, claims, questions) with per-module statuses. "
            "Draft-only: no outbound, no submission, no CRM/job-intel writes."
        ),
    )
    nested = parser.add_subparsers(dest="recruiter_decision_command")
    run = nested.add_parser("run", help="Run requested decision-support modules")
    run.add_argument("--prompt", default="", help="User prompt used to infer requested modules")
    run.add_argument(
        "--requested-outputs",
        default=None,
        help="Comma-separated module ids (overrides prompt-based module selection)",
    )
    run.add_argument("--input-json", default=None, help="Path to a JSON file with the full request payload")
    run.add_argument("--vacancy-url", default=None, help="Approved vacancy URL")
    run.add_argument("--company", default=None, help="Company identity for company research modules")
    run.add_argument(
        "--allow-provider-execution",
        action="store_true",
        help="Explicitly open the provider fuse for module execution",
    )
    run.add_argument("--provider", default=None, help="Provider override for module execution")
    run.add_argument("--model", default=None, help="Model override for module execution")
    run.add_argument("--json", action="store_true", help="Print JSON output")
    run.set_defaults(func=cmd_recruiter_decision_run)
    return parser


def cmd_recruiter_decision_run(
    args: argparse.Namespace,
    *,
    runner: Runner = run_recruiter_decision_support_flow,
    executor_factory: Callable[..., Any] | None = None,
) -> None:
    if not getattr(args, "json", False):
        sys.stderr.write("recruiter-decision run: --json is required\n")
        raise SystemExit(2)

    payload: dict[str, Any] = {}
    input_json = getattr(args, "input_json", None)
    if input_json:
        with open(input_json, encoding="utf-8") as handle:
            payload = json.load(handle)

    requested_raw = getattr(args, "requested_outputs", None)
    if requested_raw:
        payload["requested_outputs"] = [item.strip() for item in requested_raw.split(",") if item.strip()]
    if getattr(args, "prompt", ""):
        payload["prompt"] = args.prompt
    if getattr(args, "vacancy_url", None):
        payload.setdefault(
            "vacancy_source",
            {"source_type": "vacancy_url", "source_id": args.vacancy_url, "approved": True},
        )
    if getattr(args, "company", None):
        payload["company_identity"] = args.company

    # Hard safety rails: these can never be enabled from the CLI.
    for forbidden in ("outbound_enabled", "crm_writes_enabled", "job_intel_writes_enabled"):
        if payload.get(forbidden):
            sys.stderr.write(f"recruiter-decision run: {forbidden} is not allowed\n")
            raise SystemExit(2)

    request = DecisionSupportRequest(
        **{key: value for key, value in payload.items() if key in _REQUEST_FIELDS}
    )

    module_executor = None
    if getattr(args, "allow_provider_execution", False):
        factory = executor_factory or _default_executor_factory
        module_executor = factory(
            provider=getattr(args, "provider", None),
            model=getattr(args, "model", None),
        )

    report = runner(request, module_executor=module_executor)
    sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    raise SystemExit(0 if report.status is DecisionBundleStatus.READY else 1)


_REQUEST_FIELDS = {
    "prompt",
    "requested_outputs",
    "vacancy_source",
    "career_fact_sources",
    "company_identity",
    "company_research_claims",
    "role_context",
    "permitted_source_types",
    "output_mode",
    "private_file_access_requested",
    "private_file_access_approved",
}


def _default_executor_factory(*, provider: str | None, model: str | None) -> Any:
    from .recruiter_decision_provider_executor import build_recruiter_decision_provider_executor

    return build_recruiter_decision_provider_executor(provider=provider, model=model)
