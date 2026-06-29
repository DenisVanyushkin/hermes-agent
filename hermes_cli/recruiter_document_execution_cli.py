from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .recruiter_document_execution import FORBIDDEN_ACTIONS, run_recruiter_document_execution


_SUCCESS_STATUSES = {"DOCUMENT_REVIEW_APPROVED"}


def register_recruiter_document_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "recruiter-document",
        help="Inspect controlled recruiter document execution without provider calls",
        description=(
            "Load an existing recruiter execution report JSON, build the draft-only document execution "
            "packet, and return a fail-closed readiness report without provider/model execution."
        ),
    )
    nested = parser.add_subparsers(dest="recruiter_document_command")
    execute = nested.add_parser("execute", help="Run controlled recruiter document execution readiness")
    execute.add_argument(
        "--execution-report-json",
        required=True,
        help="Path to an existing recruiter skill execution report JSON file",
    )
    execute.add_argument("--document-type", required=True, help="Draft-only recruiter document type")
    execute.add_argument("--audience", default=None, help="Optional audience hint for the draft packet")
    execute.add_argument("--purpose", default=None, help="Optional purpose hint for the draft packet")
    execute.add_argument(
        "--allow-provider-execution",
        action="store_true",
        help="Explicitly open the provider fuse for manual draft-only document generation",
    )
    execute.add_argument("--provider", default=None, help="Optional provider override for manual execution")
    execute.add_argument("--model", default=None, help="Optional model override for manual execution")
    execute.add_argument("--json", action="store_true", help="Print JSON output")
    execute.set_defaults(func=cmd_recruiter_document_execute)
    return parser


def cmd_recruiter_document_execute(args: argparse.Namespace) -> None:
    if not getattr(args, "json", False):
        sys.stderr.write("recruiter-document execute: --json is required\n")
        raise SystemExit(2)

    report_path = Path(getattr(args, "execution_report_json"))
    load_result = _load_execution_report(report_path, document_type=getattr(args, "document_type"))
    if load_result["ok"] is not True:
        sys.stdout.write(json.dumps(load_result["payload"], sort_keys=True) + "\n")
        raise SystemExit(1)

    allow_provider_execution = bool(getattr(args, "allow_provider_execution", False))
    executor = None
    if allow_provider_execution:
        try:
            executor = create_provider_document_executor(
                provider=getattr(args, "provider", None),
                model=getattr(args, "model", None),
            )
        except Exception as exc:
            sys.stdout.write(
                json.dumps(
                    _cli_error_payload(
                        "DOCUMENT_EXECUTOR_NOT_WIRED",
                        document_type=getattr(args, "document_type"),
                        errors=[f"document_provider_executor_unavailable:{type(exc).__name__}"],
                    ),
                    sort_keys=True,
                )
                + "\n"
            )
            raise SystemExit(1)

    report = run_recruiter_document_execution(
        load_result["payload"],
        getattr(args, "document_type"),
        audience=getattr(args, "audience", None),
        purpose=getattr(args, "purpose", None),
        allow_document_execution=allow_provider_execution,
        executor=executor,
    )
    payload = report.to_dict()
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    raise SystemExit(0 if payload.get("status") in _SUCCESS_STATUSES else 1)


def _load_execution_report(report_path: Path, *, document_type: str) -> dict[str, Any]:
    try:
        raw_text = report_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {
            "ok": False,
            "payload": _cli_error_payload(
                "EXECUTION_REPORT_FILE_NOT_FOUND",
                document_type=document_type,
                errors=[f"execution_report_file_not_found:{report_path}"],
            ),
        }
    except OSError as exc:
        return {
            "ok": False,
            "payload": _cli_error_payload(
                "EXECUTION_REPORT_FILE_NOT_FOUND",
                document_type=document_type,
                errors=[f"execution_report_file_unreadable:{exc}"],
            ),
        }

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "payload": _cli_error_payload(
                "EXECUTION_REPORT_JSON_INVALID",
                document_type=document_type,
                errors=[f"execution_report_json_invalid:{exc.msg}"],
            ),
        }

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "payload": _cli_error_payload(
                "EXECUTION_REPORT_JSON_NOT_OBJECT",
                document_type=document_type,
                errors=["execution_report_json_not_object"],
            ),
        }

    return {"ok": True, "payload": payload}


def _cli_error_payload(status: str, *, document_type: str, errors: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "document_type": document_type,
        "writer_input_status": "CLI_INPUT_INVALID",
        "execution_status": "blocked_by_cli_input",
        "writer_called": False,
        "reviewer_called": False,
        "provider_called": False,
        "document_writer_input_packet": None,
        "document_packet": None,
        "review_result": None,
        "downstream_gates": {},
        "warnings": [],
        "errors": errors,
        "provenance": {
            "builder": "recruiter_document_execution_cli",
            "provider_called": False,
            "writes_performed": False,
        },
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
    }


def create_provider_document_executor(*, provider: str | None = None, model: str | None = None) -> Any:
    from .recruiter_document_provider_executor import build_recruiter_document_provider_executor

    return build_recruiter_document_provider_executor(provider=provider, model=model)
