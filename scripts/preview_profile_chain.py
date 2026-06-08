#!/usr/bin/env python3
"""Preview the composed Hermes profile chain without executing runtime actions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.profile_preview import (  # noqa: E402
    ProfilePreviewError,
    preview_profile,
    preview_to_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview the composed Hermes profile chain")
    parser.add_argument("--task", required=True, help="Task summary to preview")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task = (args.task or "").strip()
    if not task:
        print("profile preview failed: --task must not be empty", file=sys.stderr)
        return 1

    try:
        preview = preview_profile(task)
    except ProfilePreviewError as exc:
        print(f"profile preview failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(preview_to_json(preview))
    else:
        print(f"task: {preview.task}")
        print(f"overall_status: {preview.overall_status}")
        print(f"validation_status: {preview.validation_status}")
        print(f"blocked_reasons: {', '.join(preview.blocked_reasons) or 'none'}")
    if preview.validation_status != "passed" or preview.route_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
