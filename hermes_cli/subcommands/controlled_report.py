"""``hermes controlled-report`` subcommand parser.

Extracted per god-file Phase 2 convention.  Handler injected to avoid
importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_controlled_report_parser(
    subparsers,
    *,
    cmd_controlled_report: Callable,
) -> None:
    """Attach the ``controlled-report`` subcommand to *subparsers*."""
    cr_parser = subparsers.add_parser(
        "controlled-report",
        help="Lookup controlled execution reports by run ID",
        description=(
            "Retrieve controlled execution reports stored in the Hermes DB. "
            "Reports are persisted by report_run_id during controlled/manual "
            "pipeline executions."
        ),
    )
    cr_sub = cr_parser.add_subparsers(dest="cr_action")

    get_parser = cr_sub.add_parser(
        "get",
        help="Fetch a report by run ID",
    )
    get_parser.add_argument(
        "report_run_id",
        help="The report_run_id to look up",
    )
    get_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Print full sanitized report JSON",
    )
    get_parser.add_argument(
        "--path",
        action="store_true",
        dest="output_path",
        help="Print known file paths (workspace/durable) if stored",
    )

    list_parser = cr_sub.add_parser(
        "list",
        help="List recent reports",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max reports to show (default: 20)",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Print report summaries as JSON",
    )

    cr_parser.set_defaults(func=cmd_controlled_report)
