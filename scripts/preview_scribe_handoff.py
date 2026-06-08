#!/usr/bin/env python3
"""Preview/write Scribe handoff artifacts without mutating runtime state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.profile_handoff import (  # noqa: E402
    HandoffError,
    preview_scribe_handoff,
    result_to_json,
)


def _json_or_none(raw: str | None, label: str) -> dict | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must decode to a JSON object")
    return parsed


def _extend_from_json_and_repeatable(values: list[str] | None, json_blob: str | None) -> list[str]:
    collected: list[str] = []
    if json_blob:
        parsed = json.loads(json_blob)
        if not isinstance(parsed, list):
            raise ValueError("JSON input must decode to a list")
        collected.extend(str(item) for item in parsed if str(item).strip())
    if values:
        collected.extend(str(item) for item in values if str(item).strip())
    return collected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview Scribe handoff artifacts")
    parser.add_argument("--task", required=True, help="Task summary to hand off")
    parser.add_argument("--task-id", default=None, help="Optional task identifier")
    parser.add_argument("--route-json", default=None, help="Optional route decision JSON")
    parser.add_argument("--approval-json", default=None, help="Optional approval preview JSON")
    parser.add_argument("--evidence", action="append", default=None, help="Evidence item (repeatable)")
    parser.add_argument("--evidence-json", default=None, help="JSON list of evidence items")
    parser.add_argument("--changed-state", action="append", default=None, help="Changed state item (repeatable)")
    parser.add_argument("--changed-state-json", default=None, help="JSON list of changed state entries")
    parser.add_argument("--changed-files", action="append", default=None, help="Changed file item (repeatable)")
    parser.add_argument("--changed-files-json", default=None, help="JSON list of changed file entries")
    parser.add_argument("--decision", action="append", default=None, help="Decision item (repeatable)")
    parser.add_argument("--followup", action="append", default=None, help="Open follow-up item (repeatable)")
    parser.add_argument("--no-update-required", action="store_true", help="Mark handoff as no-update-required")
    parser.add_argument("--no-update-rationale", default=None, help="Rationale when no update is required")
    parser.add_argument("--output-root", default=None, help="Explicit docs root for preview/write safety")
    parser.add_argument(
        "--destination",
        choices=["handoff", "current-operational-state", "open-questions"],
        default="handoff",
        help="Where to write the artifact",
    )
    parser.add_argument("--write", action="store_true", help="Write the artifact instead of previewing only")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task = (args.task or "").strip()
    if not task:
        print("handoff preview failed: --task must not be empty", file=sys.stderr)
        return 1

    try:
        route_data = _json_or_none(args.route_json, "--route-json")
        approval_data = _json_or_none(args.approval_json, "--approval-json")
        evidence = _extend_from_json_and_repeatable(args.evidence, args.evidence_json)
        changed_state = _extend_from_json_and_repeatable(args.changed_state, args.changed_state_json)
        changed_files = _extend_from_json_and_repeatable(args.changed_files, args.changed_files_json)
        decisions = list(args.decision or [])
        followups = list(args.followup or [])
        result = preview_scribe_handoff(
            task,
            task_id=args.task_id,
            route_decision=route_data if route_data is not None else None,
            approval_preview=approval_data if approval_data is not None else None,
            evidence=evidence,
            changed_state=changed_state,
            changed_files=changed_files,
            decisions=decisions,
            open_followups=followups,
            no_update_required=args.no_update_required,
            no_update_rationale=args.no_update_rationale,
            output_root=Path(args.output_root).expanduser() if args.output_root else None,
            destination=args.destination,
            write=args.write,
        )
    except (ValueError, HandoffError, json.JSONDecodeError) as exc:
        print(f"handoff preview failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(result_to_json(result))
    else:
        print(result.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
