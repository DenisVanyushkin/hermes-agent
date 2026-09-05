"""Replay a recorded merge from Git object trees, never the worktree."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from pathlib import Path

from upstream_sync_index import read_blob, tree_entries


@dataclass(frozen=True)
class ReplayCase:
    merge: str
    base: str
    ours_commit: str
    theirs_commit: str
    path: str
    base_text: str | None
    ours_text: str | None
    theirs_text: str | None
    result_text: str | None
    both_sides: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _blob(repo: Path, entries, path: str) -> str | None:
    entry = entries.get(path)
    if entry is None or entry.mode == "160000":
        return None
    return read_blob(repo, entry.oid)


def extract_merge_case(repo: Path, merge: str, path: str) -> ReplayCase:
    parents = _git(repo, "rev-list", "--parents", "-n1", merge).split()[1:]
    if len(parents) != 2:
        raise ValueError(f"{merge} is not a two-parent merge")
    ours_commit, theirs_commit = parents
    base = _git(repo, "merge-base", ours_commit, theirs_commit)
    ours_tree = tree_entries(repo, ours_commit)
    theirs_tree = tree_entries(repo, theirs_commit)
    base_tree = tree_entries(repo, base)
    result_tree = tree_entries(repo, merge)
    changed_ours = set(_git(repo, "diff", "--name-only", "-z", base, ours_commit, "--").split("\0"))
    changed_theirs = set(_git(repo, "diff", "--name-only", "-z", base, theirs_commit, "--").split("\0"))
    both = tuple(sorted(p for p in changed_ours & changed_theirs if p))
    return ReplayCase(
        merge=merge, base=base, ours_commit=ours_commit, theirs_commit=theirs_commit, path=path,
        base_text=_blob(repo, base_tree, path), ours_text=_blob(repo, ours_tree, path),
        theirs_text=_blob(repo, theirs_tree, path), result_text=_blob(repo, result_tree, path),
        both_sides=both,
    )
