#!/usr/bin/env python3
"""Baseline doctor: classify + remediate a dirty agent-repo working tree.

Auto-fixes only root-owned sandbox leftovers (chown back to hermes). Everything
else is classified and reported for an operator decision — never touched here.
See docs/superpowers/specs/2026-07-07-baseline-doctor-design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes_cli.baseline_git import DirtyEntry, classify_dirty  # noqa: E402

AGENT_REPO = Path(__file__).resolve().parent.parent


def _default_chown(path: str) -> bool:
    proc = subprocess.run(
        ["sudo", "-n", "chown", "hermes:hermes", path],
        cwd=AGENT_REPO,
        capture_output=True,
    )
    return proc.returncode == 0


def _hint(entry: DirtyEntry) -> str:
    if (
        entry.category == "untracked"
        and entry.path.startswith("scripts/")
        and entry.path.endswith((".py", ".sh"))
    ):
        return "looks like a deployed-but-uncommitted script — consider committing it"
    if entry.category == "modified":
        return "tracked file with uncommitted changes"
    if entry.category == "stash_conflict":
        return "stuck/conflicted merge or stash state — needs manual resolution"
    return "untracked file"


def run_doctor(repo: Path, *, chown: Callable[[str], bool] | None = None) -> dict:
    repo = repo.resolve()
    if repo != AGENT_REPO.resolve() and os.getenv("BASELINE_DOCTOR_ALLOW_ANY_REPO") != "1":
        raise ValueError(f"baseline_doctor refuses non-agent repo: {repo}")
    chown = chown or _default_chown
    fixed: list[dict] = []
    remaining: list[dict] = []
    for entry in classify_dirty(repo):
        if entry.category == "root_owned":
            if chown(entry.path):
                fixed.append({"path": entry.path, "category": "root_owned", "action": "chown"})
            else:
                remaining.append(
                    {"path": entry.path, "category": "root_owned",
                     "hint": "chown failed — check sudo -n"}
                )
        else:
            remaining.append(
                {"path": entry.path, "category": entry.category, "hint": _hint(entry)}
            )
    # Re-check after chown so a tree that was ONLY root-owned reports clean.
    still_dirty = classify_dirty(repo) if fixed else []
    clean = len(remaining) == 0 and not still_dirty
    return {"clean": clean, "fixed": fixed, "remaining": remaining}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(AGENT_REPO))
    args = parser.parse_args()
    print(json.dumps(run_doctor(Path(args.repo))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
