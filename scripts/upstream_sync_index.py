"""Small, path-safe readers for the tree that Git will commit.

The upstream-sync gate must never turn a pathname into a revspec. In
particular, ``:path`` is ambiguous for names containing a colon and is easy to
get wrong for names beginning with ``-``. This module reads the index and tree
entries first, then addresses blobs by their object id.
"""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from pathlib import Path


@dataclass(frozen=True)
class IndexEntry:
    mode: str
    oid: str
    stage: int


def _git_bytes(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout


def index_entries(repo: Path) -> dict[str, IndexEntry]:
    """Return all index entries, including non-zero conflict stages."""
    out = _git_bytes(repo, "ls-files", "--stage", "-z")
    entries: dict[str, IndexEntry] = {}
    for raw in out.split(b"\0"):
        if not raw:
            continue
        meta, raw_path = raw.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split()
        entries[raw_path.decode("utf-8", "surrogateescape")] = IndexEntry(
            mode=mode, oid=oid, stage=int(stage)
        )
    return entries


def stage_zero_entries(repo: Path) -> dict[str, IndexEntry]:
    return {p: e for p, e in index_entries(repo).items() if e.stage == 0}


def tree_entries(repo: Path, revision: str) -> dict[str, IndexEntry]:
    """Read a revision tree without constructing a ``revision:path`` refspec."""
    out = _git_bytes(repo, "ls-tree", "-r", "-z", revision, "--")
    entries: dict[str, IndexEntry] = {}
    for raw in out.split(b"\0"):
        if not raw:
            continue
        meta, raw_path = raw.split(b"\t", 1)
        mode, _kind, oid = meta.decode("ascii").split()
        entries[raw_path.decode("utf-8", "surrogateescape")] = IndexEntry(
            mode=mode, oid=oid, stage=0
        )
    return entries


def read_blob(repo: Path, oid: str) -> str:
    """Read a blob by OID, preserving undecodable bytes with surrogateescape."""
    return _git_bytes(repo, "cat-file", "blob", oid).decode(
        "utf-8", "surrogateescape"
    )


def read_stage_zero_blob(repo: Path, entries: dict[str, IndexEntry], path: str) -> str | None:
    entry = entries.get(path)
    if entry is None or entry.mode == "160000":
        return None
    return read_blob(repo, entry.oid)


def snapshot(entries: dict[str, IndexEntry], path: str) -> dict[str, str]:
    entry = entries.get(path)
    if entry is None:
        return {"presence": "ABSENT", "mode": "ABSENT", "oid": "ABSENT"}
    return {"presence": "PRESENT", "mode": entry.mode, "oid": entry.oid}


def zlist(repo: Path, *args: str) -> list[str]:
    out = _git_bytes(repo, *args)
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]
