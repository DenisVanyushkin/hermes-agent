#!/usr/bin/env python3
"""Mode B mechanics for upstream-sync — the part that needs no judgement.

The operator has decided per conflicting file (pending.json). This script turns
that into a merge commit the host finalizer will accept, and hands it over. It
runs inside the sandbox by default (see the DEFAULT_* paths) or on the host via
``--live``/``--state``; it never writes to the live checkout.

  prepare  clone --shared the live checkout into <state>/<scratch>, check that
           the decisions still cover the conflicts of the LIVE HEAD against the
           GATED upstream point, merge with zdiff3, apply keep-local and
           take-upstream mechanically, close the merge-both hunks that are
           mechanical, and list what is left for a human. Writes
           <state>/apply-prepare.json.
  handoff  refuse leftover markers / unmerged paths / a live branch that moved,
           commit the merge, verify its parents are (live HEAD, gated upstream),
           write <state>/finalize-request.json with action apply-merge.
  wait     poll <state>/finalize-result.json for a result newer than the
           hand-off.

Why not "is upstream still where it was?": upstream always moves while a gate
waits, and the host only ever accepts a merge into the gated point anyway. The
question that matters is whether the decisions still cover the conflict set.
Asking the other one made every apply look stale — and the ref it asked with,
``origin/main`` inside a clone of the live checkout, is the fork's own stale
main rather than upstream at all (2026-08-15).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upstream_sync_gate import parse_merge_tree  # noqa: E402

SCHEMA = "upstream-sync-apply/v1"
VALID_DECISIONS = ("keep-local", "take-upstream", "merge-both")
DEFAULT_STATE = "/root/.hermes/state/upstream-sync"
DEFAULT_LIVE = "/workspace/live-hermes"
DEFAULT_SCRATCH = "scratch"
LOCAL_BRANCH = "local/customizations"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING_DECISIONS = 3
EXIT_NEW_CONFLICTS = 4
EXIT_GIT_FAILED = 5
EXIT_UNRESOLVED = 6
EXIT_LIVE_MOVED = 7
EXIT_APPLY_FAILED = 8
EXIT_TIMEOUT = 9

CONFLICT_START = "<<<<<<< "
CONFLICT_BASE = "||||||| "
CONFLICT_SEP = "======="
CONFLICT_END = ">>>>>>> "

MERGE_IDENTITY = ("-c", "user.name=Hermes Agent", "-c", "user.email=hermes@local")


class GitError(RuntimeError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc


def emit(payload: dict) -> None:
    """Machine-readable result on stdout (last line), one human line on stderr."""
    print(json.dumps(payload, ensure_ascii=False))
    print(f"upstream_sync_apply: {payload.get('status')}", file=sys.stderr)


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_pending(state: Path) -> dict:
    return json.loads((state / "pending.json").read_text(encoding="utf-8"))


def decisions_by_file(pending: dict) -> tuple[dict, list]:
    """Map path -> decision, plus the ids of features without a valid decision."""
    by_file: dict = {}
    missing: list = []
    for feature in pending.get("features", []):
        decision = feature.get("decision")
        if decision not in VALID_DECISIONS:
            missing.append(feature.get("id"))
            continue
        for path in feature.get("files", []):
            by_file[path] = decision
    return by_file, missing


def conflict_paths(repo: Path, ours: str, theirs: str) -> list:
    proc = git(repo, "merge-tree", "--write-tree", "--name-only", ours, theirs, check=False)
    if proc.returncode not in (0, 1):
        raise GitError(f"merge-tree failed ({proc.returncode}): {proc.stderr.strip()}")
    return parse_merge_tree(proc.stdout).conflicted_paths


def unmerged_stages(repo: Path) -> dict:
    """path -> set of index stages present (1 base, 2 ours, 3 theirs)."""
    out = git(repo, "ls-files", "-u", "-z").stdout
    stages: dict = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        stage = int(meta.split(" ")[2])
        stages.setdefault(path, set()).add(stage)
    return stages


def take_side(repo: Path, path: str, stages: set, *, ours: bool) -> None:
    """keep-local / take-upstream for one path, honouring delete/modify conflicts."""
    stage = 2 if ours else 3
    if stage in stages:
        git(repo, "checkout", "--ours" if ours else "--theirs", "--", path)
        git(repo, "add", "--", path)
    else:  # that side deleted the file, so keeping its side means removing it
        git(repo, "rm", "-q", "--", path)


def count_conflict_blocks(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith(CONFLICT_START))


def _mechanical_resolution(ours: list, base: list, theirs: list):
    """The resolution for one zdiff3 block, or None when a human must decide.

    These are the three shapes that closed 53 of 98 hunks by hand during the
    2026-08-09 merge; everything else was a judgement call then too.
    """
    if any(l.startswith((CONFLICT_START, CONFLICT_BASE, CONFLICT_END))
           for l in ours + base + theirs):
        return None                                  # nested markers: hands off
    if not base and ours and theirs:
        ours_lines = {l.strip() for l in ours if l.strip()}
        theirs_lines = {l.strip() for l in theirs if l.strip()}
        if ours_lines & theirs_lines:
            return None                              # both added overlapping content
        return ours + theirs                         # both added distinct content here
    if ours == base:
        return theirs                                # only upstream changed
    if theirs == base:
        return ours                                  # only local changed
    return None


def resolve_merge_both_text(text: str) -> tuple:
    """Return (new_text, resolved_hunks, remaining_hunks).

    Rewrites only the zdiff3 blocks whose resolution is mechanical (see
    _mechanical_resolution); everything else keeps its markers for the agent.
    A block without a ``|||||||`` base section is not zdiff3 and is left alone.
    """
    lines = text.splitlines(keepends=True)
    out: list = []
    resolved = remaining = 0
    i = 0
    while i < len(lines):
        if not lines[i].startswith(CONFLICT_START):
            out.append(lines[i])
            i += 1
            continue
        ours: list = []
        base = None
        theirs: list = []
        section = "ours"
        j = i + 1
        closed = False
        while j < len(lines):
            line = lines[j]
            if section == "ours" and line.startswith(CONFLICT_BASE):
                base, section = [], "base"
            elif section in ("ours", "base") and line.rstrip("\r\n") == CONFLICT_SEP:
                section = "theirs"
            elif section == "theirs" and line.startswith(CONFLICT_END):
                closed = True
                break
            else:
                target = ours if section == "ours" else (base if section == "base" else theirs)
                target.append(line)
            j += 1
        if not closed:
            out.extend(lines[i:])
            remaining += 1
            break
        resolution = _mechanical_resolution(ours, base, theirs) if base is not None else None
        if resolution is None:
            out.extend(lines[i:j + 1])
            remaining += 1
        else:
            out.extend(resolution)
            resolved += 1
        i = j + 1
    return "".join(out), resolved, remaining


def has_conflict_markers(text: str) -> bool:
    return any(
        line.startswith(CONFLICT_START)
        or line.startswith(CONFLICT_END)
        or line.startswith(CONFLICT_BASE)
        for line in text.splitlines()
    )


# --------------------------------------------------------------------------- prepare

def cmd_prepare(args) -> int:
    state, live = Path(args.state), Path(args.live)
    scratch = state / args.scratch
    for name in ("finalize-request.json", "finalize-request.processing.json"):
        if (state / name).exists():
            emit({"status": "error",
                  "reason": f"{name} exists — a finalize is in flight; wait for it first"})
            return EXIT_USAGE
    pending = load_pending(state)
    upstream_head = pending["upstream_head"]
    by_file, missing = decisions_by_file(pending)
    if missing:
        emit({"status": "missing_decisions", "features": missing})
        return EXIT_MISSING_DECISIONS

    if scratch.exists():
        shutil.rmtree(scratch)
    clone = subprocess.run(
        ["git", "clone", "-q", "--shared", str(live), str(scratch)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if clone.returncode != 0:
        raise GitError(f"git clone --shared failed: {clone.stderr.strip()}")

    live_head = git(live, "rev-parse", "HEAD").stdout.strip()
    local_base = git(scratch, "rev-parse", "HEAD").stdout.strip()
    branch = git(scratch, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if local_base != live_head or branch != LOCAL_BRANCH:
        emit({"status": "error",
              "reason": f"live checkout is not on {LOCAL_BRANCH} "
                        f"(clone HEAD {local_base} on {branch}, live HEAD {live_head})"})
        return EXIT_GIT_FAILED
    if git(scratch, "cat-file", "-e", f"{upstream_head}^{{commit}}", check=False).returncode != 0:
        emit({"status": "error",
              "reason": f"gated upstream commit {upstream_head} is not in the live object store"})
        return EXIT_GIT_FAILED

    conflicts = conflict_paths(scratch, "HEAD", upstream_head)
    summary = {
        "schema": SCHEMA, "status": "", "prepared_at": _now(),
        "local_base": local_base, "upstream_head": upstream_head,
        "pending_local_head": pending.get("local_head"),
        "scratch": str(scratch), "conflicts": conflicts,
        "new_conflicts": sorted(set(conflicts) - set(by_file)),
        "no_longer_conflicting": sorted(set(by_file) - set(conflicts)),
        "auto_resolved": [], "needs_manual": [],
        "handed_off_at": None, "merge_sha": None,
    }
    if summary["new_conflicts"]:
        summary["status"] = "new_conflicts"
        _write_json(state / "apply-prepare.json", summary)
        emit(summary)
        return EXIT_NEW_CONFLICTS

    # Identity is passed explicitly: the sandbox has none configured, and a
    # conflict-free merge commits on the spot.
    merge = git(scratch, *MERGE_IDENTITY,
                "-c", "merge.conflictStyle=zdiff3", "-c", "rerere.enabled=false",
                "merge", "--no-edit", upstream_head, check=False)
    stages = unmerged_stages(scratch)
    if merge.returncode != 0 and not stages:
        emit({"status": "error",
              "reason": f"merge failed without conflicts: {merge.stderr.strip()}"})
        return EXIT_GIT_FAILED
    undecided = sorted(set(stages) - set(by_file))
    if undecided:  # merge-tree and merge disagreed; never resolve what nobody decided
        summary["status"] = "new_conflicts"
        summary["new_conflicts"] = undecided
        _write_json(state / "apply-prepare.json", summary)
        emit(summary)
        return EXIT_NEW_CONFLICTS

    for path in sorted(stages):
        decision = by_file[path]
        if decision == "keep-local":
            take_side(scratch, path, stages[path], ours=True)
            summary["auto_resolved"].append(path)
        elif decision == "take-upstream":
            take_side(scratch, path, stages[path], ours=False)
            summary["auto_resolved"].append(path)
        else:
            file_path = scratch / path
            if not file_path.exists():  # delete/modify under merge-both: a human call
                summary["needs_manual"].append(
                    {"path": path, "resolved_hunks": 0, "remaining_hunks": 1})
                continue
            text = file_path.read_text(encoding="utf-8", errors="surrogateescape")
            new_text, resolved, remaining = resolve_merge_both_text(text)
            if resolved:
                file_path.write_text(new_text, encoding="utf-8", errors="surrogateescape")
            if remaining == 0:
                git(scratch, "add", "--", path)
                summary["auto_resolved"].append(path)
            else:
                summary["needs_manual"].append(
                    {"path": path, "resolved_hunks": resolved, "remaining_hunks": remaining})

    summary["status"] = "ready"
    _write_json(state / "apply-prepare.json", summary)
    emit(summary)
    return EXIT_OK


# --------------------------------------------------------------------------- handoff

def cmd_handoff(args) -> int:
    state, live = Path(args.state), Path(args.live)
    scratch = state / args.scratch
    prep_path = state / "apply-prepare.json"
    if not prep_path.exists():
        emit({"status": "error", "reason": "apply-prepare.json missing — run prepare first"})
        return EXIT_USAGE
    prep = json.loads(prep_path.read_text(encoding="utf-8"))
    if prep.get("status") != "ready":
        emit({"status": "error", "reason": f"prepare ended with {prep.get('status')!r}, not ready"})
        return EXIT_USAGE

    stages = unmerged_stages(scratch)
    marked = [
        p for p in prep["conflicts"]
        if (scratch / p).exists()
        and has_conflict_markers((scratch / p).read_text(encoding="utf-8", errors="surrogateescape"))
    ]
    if stages or marked:
        emit({"status": "unresolved", "unmerged": sorted(stages), "with_markers": marked})
        return EXIT_UNRESOLVED

    # Check the race before committing anything: the host refuses a merge whose
    # first parent is not its current HEAD, so there is nothing to gain from
    # committing one — the agent has to redo prepare either way.
    live_head = git(live, "rev-parse", "HEAD").stdout.strip()
    if live_head != prep["local_base"]:
        emit({"status": "live_moved", "local_base": prep["local_base"], "live_head": live_head,
              "hint": "the live branch moved since prepare; run prepare again and redo the resolution"})
        return EXIT_LIVE_MOVED

    merge_head_file = scratch / ".git" / "MERGE_HEAD"
    if merge_head_file.exists():
        if merge_head_file.read_text().strip() != prep["upstream_head"]:
            emit({"status": "error",
                  "reason": "the clone is mid-merge of something other than the gated upstream "
                            "point — run prepare again"})
            return EXIT_GIT_FAILED
        git(scratch, *MERGE_IDENTITY, "commit", "-q", "--no-edit")
    # else: nothing conflicted and `git merge` committed on the spot in prepare;
    # the parent check below proves it is the right merge either way.
    merge_sha = git(scratch, "rev-parse", "HEAD").stdout.strip()
    parents = git(scratch, "rev-list", "--parents", "-n1", merge_sha).stdout.split()[1:]
    if parents != [prep["local_base"], prep["upstream_head"]]:
        emit({"status": "error",
              "reason": f"merge parents {parents} are not (local_base, upstream_head) — "
                        "run prepare again"})
        return EXIT_GIT_FAILED

    requested_at = _now()
    _write_json(state / "finalize-request.json", {
        "action": "apply-merge",
        "upstream_sha": prep["upstream_head"],
        "merge_sha": merge_sha,
        "scratch_repo": args.scratch,
        "requested_at": requested_at,
    })
    prep["handed_off_at"] = requested_at
    prep["merge_sha"] = merge_sha
    _write_json(prep_path, prep)
    emit({"status": "handed_off", "merge_sha": merge_sha, "requested_at": requested_at})
    return EXIT_OK


# --------------------------------------------------------------------------- wait

def cmd_wait(args) -> int:
    state = Path(args.state)
    after = args.after
    if not after:
        prep_path = state / "apply-prepare.json"
        if prep_path.exists():
            after = json.loads(prep_path.read_text(encoding="utf-8")).get("handed_off_at") or ""
    result_path = state / "finalize-result.json"
    deadline = time.monotonic() + args.timeout
    while True:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except ValueError:
                result = None
            if result and str(result.get("finished_at", "")) > str(after):
                emit(result)
                return EXIT_OK if result.get("status") == "ok" else EXIT_APPLY_FAILED
        if time.monotonic() >= deadline:
            emit({"status": "timeout", "waited_seconds": args.timeout,
                  "hint": "the finalizer did not answer; check finalize-detail.log on the host"})
            return EXIT_TIMEOUT
        time.sleep(args.interval)


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    # The path options live on the SUBPARSERS only (i.e. after the command):
    # a subparser's defaults overwrite values the parent already parsed, so
    # `--state X prepare` would silently fall back to the default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", default=DEFAULT_STATE, help="upstream-sync state dir")
    common.add_argument("--live", default=DEFAULT_LIVE, help="live checkout (read only)")
    common.add_argument("--scratch", default=DEFAULT_SCRATCH,
                        help="clone directory name under --state")
    parser = argparse.ArgumentParser(description="upstream-sync Mode B mechanics")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", parents=[common]).set_defaults(func=cmd_prepare)
    sub.add_parser("handoff", parents=[common]).set_defaults(func=cmd_handoff)
    p_wait = sub.add_parser("wait", parents=[common])
    p_wait.add_argument("--after", default="",
                        help="ISO time; results not newer than this are ignored")
    p_wait.add_argument("--timeout", type=float, default=1800.0)
    p_wait.add_argument("--interval", type=float, default=15.0)
    p_wait.set_defaults(func=cmd_wait)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GitError as exc:
        emit({"status": "error", "reason": str(exc)})
        return EXIT_GIT_FAILED
    except (OSError, ValueError, KeyError) as exc:
        emit({"status": "error", "reason": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
