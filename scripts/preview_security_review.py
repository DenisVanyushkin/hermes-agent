#!/usr/bin/env python3
"""Preview/write Security Auditor review artifacts without mutating runtime state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.profile_security_review import (  # noqa: E402
    SecurityReviewError,
    preview_security_review,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview Security Auditor review artifacts")
    parser.add_argument("--task", required=True, help="Task summary to review")
    parser.add_argument("--review-id", default=None, help="Optional review identifier")
    parser.add_argument("--route-json", default=None, help="Optional route decision JSON")
    parser.add_argument("--approval-json", default=None, help="Optional approval preview JSON")
    parser.add_argument("--evidence", action="append", default=None, help="Evidence note (repeatable)")
    parser.add_argument("--assumption", action="append", default=None, help="Assumption note (repeatable)")
    parser.add_argument("--required-change", action="append", default=None, help="Required change (repeatable)")
    parser.add_argument("--residual-risk", action="append", default=None, help="Residual risk (repeatable)")
    parser.add_argument("--output-root", default=None, help="Explicit docs root for preview/write safety")
    parser.add_argument("--write", action="store_true", help="Write the artifact instead of previewing only")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task = (args.task or "").strip()
    if not task:
        print("security review preview failed: --task must not be empty", file=sys.stderr)
        return 1

    try:
        route_data = _json_or_none(args.route_json, "--route-json")
        approval_data = _json_or_none(args.approval_json, "--approval-json")
        result = preview_security_review(
            task,
            review_id=args.review_id,
            route_decision=route_data if route_data is not None else None,
            approval_preview=approval_data if approval_data is not None else None,
            evidence=list(args.evidence or []),
            assumptions=list(args.assumption or []),
            required_changes=list(args.required_change or []),
            residual_risks=list(args.residual_risk or []),
            output_root=Path(args.output_root).expanduser() if args.output_root else None,
            write=args.write,
        )
    except (ValueError, SecurityReviewError, json.JSONDecodeError) as exc:
        print(f"security review preview failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(result_to_json(result))
    else:
        print(result.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
