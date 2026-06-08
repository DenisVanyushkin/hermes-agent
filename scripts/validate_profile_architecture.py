#!/usr/bin/env python3
"""Validate the Hermes profile architecture MVP configs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.profile_validation import (
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    format_issues,
    validate_profile_architecture,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Hermes profile architecture configs")
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_PROFILE_REGISTRY_PATH,
        help="Path to hermes-profiles.yaml",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_MODEL_POLICY_PATH,
        help="Path to hermes-model-policy.yaml",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on any warning, not just errors",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    issues = validate_profile_architecture(args.registry, args.policy)
    output = format_issues(issues)
    print(output)

    if not issues:
        return 0

    has_error = any(issue.severity == "error" for issue in issues)
    has_warning = any(issue.severity == "warning" for issue in issues)
    if args.strict:
        return 1 if (has_error or has_warning) else 0
    return 1 if has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
