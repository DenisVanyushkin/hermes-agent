#!/usr/bin/env python3
"""Preview Engineer approval classification without mutating runtime state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.profile_approval import (  # noqa: E402
    ApprovalError,
    classify_engineer_approval,
    decision_to_json,
)
from hermes_cli.profile_routing import RoutingError, route_task  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview Engineer approval classification")
    parser.add_argument("--task", required=True, help="Task text to classify")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    parser.add_argument("--target-host", default=None, help="Optional target host")
    parser.add_argument("--target-service", default=None, help="Optional target service")
    parser.add_argument("--intended-change", default=None, help="Optional intended change summary")
    parser.add_argument(
        "--commands-or-control-script",
        default=None,
        help="Optional commands or control script to inspect for mutation cues",
    )
    parser.add_argument("--expected-effect", default=None, help="Optional expected effect")
    parser.add_argument("--risk", default=None, help="Optional operator-provided risk note")
    parser.add_argument("--rollback-plan", default=None, help="Optional rollback plan")
    parser.add_argument(
        "--evidence-before",
        action="append",
        default=None,
        help="Optional evidence item before approval (repeatable)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task = (args.task or "").strip()
    if not task:
        print("approval preview failed: --task must not be empty", file=sys.stderr)
        return 1

    evidence_before = list(args.evidence_before or [])

    try:
        route_decision = route_task(task)
        preview = classify_engineer_approval(
            task,
            route_decision=route_decision,
            target_host=args.target_host,
            target_service=args.target_service,
            intended_change=args.intended_change,
            commands_or_control_script=args.commands_or_control_script,
            expected_effect=args.expected_effect,
            risk=args.risk,
            rollback_plan=args.rollback_plan,
            evidence_before=evidence_before,
        )
    except (ApprovalError, RoutingError, ValueError) as exc:
        print(f"approval preview failed: {exc}", file=sys.stderr)
        return 1

    output = decision_to_json(preview)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
