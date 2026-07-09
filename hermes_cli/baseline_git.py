"""Shared classifier for a dirty agent-repo working tree.

Used by both the autonomous preflight (to explain WHY a run was blocked) and
scripts/baseline_doctor.py (to remediate). Categories are mutually exclusive
per path with precedence: unmerged > root-owned > untracked > modified.
"""

from __future__ import annotations

import os
import subprocess
from collections import namedtuple
from pathlib import Path

DirtyEntry = namedtuple("DirtyEntry", ["category", "path"])
REPORT_ARTIFACT = "controlled_execution_report.json"


def _git_porcelain(repo: Path) -> list[tuple[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        xy = line[:2]
        path = line[3:].strip()
        # Renames render as "old -> new"; keep the destination path.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((xy, path))
    return rows


def _is_root_owned(repo: Path, rel_path: str) -> bool:
    try:
        return os.stat(repo / rel_path).st_uid == 0
    except OSError:
        return False


def classify_dirty(repo: Path) -> list[DirtyEntry]:
    """Classify uncommitted working-tree paths as unmerged, root-owned, untracked, or modified."""
    entries: list[DirtyEntry] = []
    for xy, path in _git_porcelain(repo):
        if path == REPORT_ARTIFACT:
            continue
        if "U" in xy or xy in ("AA", "DD"):
            category = "stash_conflict"
        elif _is_root_owned(repo, path):
            category = "root_owned"
        elif xy == "??":
            category = "untracked"
        else:
            category = "modified"
        entries.append(DirtyEntry(category, path))
    return entries
