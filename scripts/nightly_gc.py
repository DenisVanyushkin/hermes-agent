#!/usr/bin/env python3
"""Nightly cleanup of the two stores that grow without bound.

Run worktrees under /tmp/hermes-gateway-autonomous-runs (~200 MB each) and the
per-session review-debt files under ~/.hermes/cache/review_gate. Both arrived
with the engineering-pipeline rework and neither had an owner for old entries.

The governing rule for both: **age is not permission to delete.** A worktree
holding uncommitted work is exactly what an operator comes back for, and a debt
file with outstanding findings *is* the reviewer's objection — removing it would
not tidy anything, it would silently discharge the objection and let the commit
gate pass. Only provably-spent entries go; anything still carrying work is kept
and reported.

Runs as a no-agent script cron job, so it prints nothing on a quiet night.

Usage:
    venv/bin/python scripts/nightly_gc.py [--days 14] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

#: Matches the diagnostics collector's rotation window.
DEFAULT_MAX_AGE_DAYS = 14
DEBT_SUBPATH = ("cache", "review_gate")


def resolve_hermes_home() -> Path:
    """Same resolution the other nightly scripts use.

    Script-mode cron runs with cwd set to the *script* directory, which is
    outside the repo, so nothing may be inferred from the working directory.
    """
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path("/home/hermes/.hermes")


def prune_review_debt(
    root: Path, *, max_age_seconds: float, now: float
) -> tuple[list[str], list[str]]:
    """Drop spent debt files. Returns (removed, kept_because_indebted).

    A file whose ``outstanding`` is non-empty is never removed, however old.
    That is the whole safety property: the file is the review objection, so
    deleting it discharges the review. Unreadable files are removed once stale --
    ``ReviewGateState.load`` already treats them as a clean slate, so they carry
    nothing.
    """
    root = Path(root)
    if not root.is_dir():
        return [], []

    removed: list[str] = []
    kept: list[str] = []
    for path in sorted(root.glob("*.json")):
        try:
            if now - path.stat().st_mtime <= max_age_seconds:
                continue
        except OSError:
            continue
        try:
            outstanding = (json.loads(path.read_text(encoding="utf-8")) or {}).get("outstanding")
        except (OSError, json.JSONDecodeError):
            outstanding = None  # inert: load() would have read it as a clean slate
        if isinstance(outstanding, dict) and outstanding:
            kept.append(str(path))
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    return removed, kept


def summarize(*, worktrees: list[str], debt_removed: list[str], debt_kept: list[str]) -> str:
    """One short block, or empty when there is nothing to say.

    Retained debt is reported even though nothing was deleted: an unresolved
    review sitting there for weeks is the useful signal, not the disk it uses.
    """
    lines: list[str] = []
    if worktrees:
        lines.append(f"run worktrees removed: {len(worktrees)}")
        lines.extend(f"  • {Path(p).name}" for p in worktrees)
    if debt_removed:
        lines.append(f"settled review-debt files removed: {len(debt_removed)}")
    if debt_kept:
        lines.append(f"review debt still outstanding in {len(debt_kept)} session(s):")
        lines.extend(f"  • {Path(p).stem}" for p in debt_kept)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    hermes_home = resolve_hermes_home()
    repo_root = hermes_home / "hermes-agent"
    sys.path.insert(0, str(repo_root))

    max_age = args.days * 86400
    now = time.time()

    worktrees: list[str] = []
    try:
        from hermes_cli.pipeline_autonomous_execution import (  # noqa: E402
            AUTONOMOUS_WORKSPACE_ROOT,
            sweep_run_worktrees,
        )

        if not args.dry_run:
            worktrees = sweep_run_worktrees(
                repo_root=repo_root,
                runs_root=AUTONOMOUS_WORKSPACE_ROOT,
                max_age_seconds=max_age,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001
        # Cleanup must never be the reason a night fails. Say so and carry on to
        # the other store rather than aborting both.
        print(f"run-worktree sweep failed: {exc}", file=sys.stderr)

    debt_root = hermes_home.joinpath(*DEBT_SUBPATH)
    debt_removed, debt_kept = ([], [])
    try:
        if args.dry_run:
            debt_removed, debt_kept = [], []
        else:
            debt_removed, debt_kept = prune_review_debt(
                debt_root, max_age_seconds=max_age, now=now
            )
    except Exception as exc:  # noqa: BLE001
        print(f"review-debt prune failed: {exc}", file=sys.stderr)

    report = summarize(
        worktrees=worktrees, debt_removed=debt_removed, debt_kept=debt_kept
    )
    if report:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
