"""Fork: the CLI subcommands this deployment adds to ``hermes``.

* ``hermes controlled-report get|list`` -- controlled execution reports.
* ``hermes role ...`` -- role packages.
* ``hermes recruiter-context|recruiter-document|recruiter-skill|recruiter-decision``
  -- the recruiter role's own entry points; hermes_cli/recruiter_routing.py lists
  them as the commands the recruiter agent runs.

Registered from hermes_cli/main.py with one call; moved from the pre-v0.22.5
main.py.
"""

from __future__ import annotations

import json
import sys

FORK_SUBCOMMANDS = frozenset({
    "controlled-report", "role",
    "recruiter-context", "recruiter-decision", "recruiter-document", "recruiter-skill",
})


def cmd_controlled_report(args) -> int:
    """Controlled execution report lookup."""
    from hermes_state import SessionDB

    action = getattr(args, "cr_action", None)
    if action not in ("get", "list"):
        print(
            "usage: hermes controlled-report <get|list> ...\n\n"
            "subcommands:\n"
            "  get    Fetch a report by run ID\n"
            "  list   List recent reports\n\n"
            "Run `hermes controlled-report get -h` for details.",
            file=sys.stderr,
        )
        return 1

    db = SessionDB()
    if action == "get":
        report_run_id = getattr(args, "report_run_id", None)
        if not report_run_id:
            print("error: report_run_id is required", file=sys.stderr)
            return 1
        row = db.get_controlled_execution_report(report_run_id)
        if row is None:
            print(f"No report found for run ID: {report_run_id}")
            return 1
        if getattr(args, "output_json", False):
            raw_json = row.get("report_json", "{}")
            try:
                print(json.dumps(json.loads(raw_json), indent=2, sort_keys=True))
            except (json.JSONDecodeError, TypeError):
                print(raw_json)
            return 0
        if getattr(args, "output_path", False):
            print(f"report_run_id: {row.get('report_run_id')}")
            print(f"workspace_path: {row.get('workspace_path') or '(none)'}")
            print(f"durable_report_path: {row.get('durable_report_path') or '(none)'}")
            print(f"workspace_report_path: {row.get('workspace_report_path') or '(none)'}")
            return 0
        print(f"Report: {row.get('report_run_id')}")
        print(f"  Status:       {row.get('status')}")
        print(f"  Pipeline:     {row.get('pipeline_id')}")
        print(f"  Mode:         {row.get('execution_mode')}")
        print(f"  Verdict:      {row.get('final_verdict')}")
        print(f"  Tests:        {row.get('tests_status')}")
        print(f"  Tests summary:{row.get('tests_summary') or ' (none)'}")
        print(f"  Created:      {row.get('created_at')}")
        print(f"  Updated:      {row.get('updated_at')}")
        return 0

    reports = db.list_controlled_execution_reports(limit=getattr(args, "limit", 20))
    if not reports:
        print("No controlled execution reports found.")
        return 0
    if getattr(args, "output_json", False):
        keys = ("report_run_id", "status", "pipeline_id", "execution_mode", "final_verdict",
                "tests_status", "created_at")
        print(json.dumps([{k: r.get(k) for k in keys} for r in reports], indent=2, sort_keys=True))
        return 0
    print(f"{'Run ID':<45} {'Status':<20} {'Pipeline':<30} {'Created':<26}")
    print("-" * 121)
    for r in reports:
        print(f"{r.get('report_run_id', ''):<45} {r.get('status', ''):<20} "
              f"{r.get('pipeline_id', ''):<30} {r.get('created_at', ''):<26}")
    return 0


def register_fork_subcommands(subparsers) -> None:
    """Attach the fork's subcommands to the top-level ``hermes`` parser."""
    from hermes_cli.recruiter_decision_cli import register_recruiter_decision_subparser
    from hermes_cli.recruiter_document_execution_cli import register_recruiter_document_subparser
    from hermes_cli.recruiter_dry_run_cli import register_recruiter_context_subparser
    from hermes_cli.recruiter_skill_execution_cli import register_recruiter_skill_subparser
    from hermes_cli.subcommands.controlled_report import build_controlled_report_parser
    from hermes_cli.subcommands.role import build_role_parser, cmd_role

    build_controlled_report_parser(subparsers, cmd_controlled_report=cmd_controlled_report)
    build_role_parser(subparsers, cmd_role=cmd_role)
    register_recruiter_context_subparser(subparsers)
    register_recruiter_document_subparser(subparsers)
    register_recruiter_skill_subparser(subparsers)
    register_recruiter_decision_subparser(subparsers)
