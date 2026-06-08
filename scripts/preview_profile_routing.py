#!/usr/bin/env python3
"""Preview deterministic Hermes profile routing without mutating runtime state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.profile_routing import RoutingError, decision_to_json, route_task  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview Hermes profile routing decisions")
    parser.add_argument("--task", required=True, help="Task text to route")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    parser.add_argument("--registry", default=None, help="Optional override registry path")
    parser.add_argument("--policy", default=None, help="Optional override policy path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    kwargs = {}
    if args.registry:
        kwargs["registry_path"] = args.registry
    if args.policy:
        kwargs["policy_path"] = args.policy

    try:
        decision = route_task(args.task, **kwargs)
    except RoutingError as exc:
        print(f"routing preview failed: {exc}", file=sys.stderr)
        return 1

    output = decision_to_json(decision)
    if args.json:
        print(output)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
