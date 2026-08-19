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
from upstream_sync_policy import decide_paths, number_features  # noqa: E402

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


def apply_policy_to_new_conflicts(state: Path, pending: dict, new_paths: list,
                                  subjects_by_path: dict) -> tuple:
    """Decide undecided conflict paths by policy and record them in pending.json.

    Returns (by_file_additions, still_asking). Plain paths become decided
    features (source policy); security paths become awaiting features — those
    are returned as still_asking and the pending status flips to
    awaiting_decision, so the operator gets asked about exactly them.
    """
    memory_path = state / "decision-memory.json"
    try:
        memory = json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else {}
    except ValueError:
        memory = {}
    decided = decide_paths(new_paths, memory, subjects_by_path)
    start = len(pending.get("features", [])) + 1
    numbered = number_features(decided, start=start)
    additions: dict = {}
    asking: list = []
    for feat in numbered:
        pending.setdefault("features", []).append(feat)
        if feat.get("decision"):
            for path in feat["files"]:
                additions[path] = feat["decision"]
        else:
            asking.extend(feat["files"])
    if asking:
        pending["status"] = "awaiting_decision"
    _write_json(state / "pending.json", pending)
    return additions, sorted(asking)


def local_subjects_for(repo: Path, base: str, head: str, path: str) -> list:
    proc = git(repo, "log", "--format=%s", f"{base}..{head}", "--", path, check=False)
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()] if proc.returncode == 0 else []


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
    # The finalizer itself calls prepare from inside apply-decisions, i.e. while
    # its own request is the one in flight — it passes --in-flight-ok. Anyone
    # else racing a running finalize is refused.
    if not getattr(args, "in_flight_ok", False):
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
        "auto_resolved": [], "needs_manual": [], "policy_decided": [],
        "handed_off_at": None, "merge_sha": None,
    }
    if summary["new_conflicts"] and args.auto_policy:
        merge_base = git(scratch, "merge-base", "HEAD", upstream_head).stdout.strip()
        subjects = {p: local_subjects_for(scratch, merge_base, "HEAD", p) for p in summary["new_conflicts"]}
        additions, asking = apply_policy_to_new_conflicts(state, pending, summary["new_conflicts"], subjects)
        by_file.update(additions)
        summary["policy_decided"] = sorted(additions)
        summary["new_conflicts"] = asking
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
    if undecided and args.auto_policy:
        merge_base = git(scratch, "merge-base", "HEAD", upstream_head).stdout.strip()
        subjects = {p: local_subjects_for(scratch, merge_base, "HEAD", p) for p in undecided}
        additions, asking = apply_policy_to_new_conflicts(state, pending, undecided, subjects)
        by_file.update(additions)
        summary.setdefault("policy_decided", [])
        summary["policy_decided"] = sorted(set(summary["policy_decided"]) | set(additions))
        undecided = asking
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

def _commit_merge(args) -> tuple:
    """Shared by commit and handoff: refuse unresolved/moved, commit, verify parents.

    Returns (exit_code, payload). On success payload has merge_sha and the
    prepare record has been updated; nothing about a finalize request here.
    """
    state, live = Path(args.state), Path(args.live)
    scratch = state / args.scratch
    prep_path = state / "apply-prepare.json"
    if not prep_path.exists():
        return EXIT_USAGE, {"status": "error", "reason": "apply-prepare.json missing — run prepare first"}
    prep = json.loads(prep_path.read_text(encoding="utf-8"))
    if prep.get("status") != "ready":
        return EXIT_USAGE, {"status": "error", "reason": f"prepare ended with {prep.get('status')!r}, not ready"}

    stages = unmerged_stages(scratch)
    marked = [
        p for p in prep["conflicts"]
        if (scratch / p).exists()
        and has_conflict_markers((scratch / p).read_text(encoding="utf-8", errors="surrogateescape"))
    ]
    if stages or marked:
        return EXIT_UNRESOLVED, {"status": "unresolved", "unmerged": sorted(stages), "with_markers": marked}

    # Check the race before committing anything: the host refuses a merge whose
    # first parent is not its current HEAD, so there is nothing to gain from
    # committing one — prepare has to be redone either way.
    live_head = git(live, "rev-parse", "HEAD").stdout.strip()
    if live_head != prep["local_base"]:
        return EXIT_LIVE_MOVED, {"status": "live_moved", "local_base": prep["local_base"], "live_head": live_head,
                                 "hint": "the live branch moved since prepare; run prepare again and redo the resolution"}

    merge_head_file = scratch / ".git" / "MERGE_HEAD"
    amend = bool(getattr(args, "amend", False))
    if amend:
        # The gate-triage path: test files inside the clone were patched after
        # the merge was already committed, and the host will only land a commit
        # whose parents are exactly (live HEAD, gated upstream) — so a fresh
        # commit on top is unlandable. Fold the patch into the merge itself.
        if merge_head_file.exists():
            return EXIT_GIT_FAILED, {"status": "error",
                                     "reason": "--amend with an uncommitted merge in the clone — there is no merge "
                                               "commit to fold into; run commit without --amend first"}
        head_parents = git(scratch, "rev-list", "--parents", "-n1", "HEAD").stdout.split()[1:]
        if head_parents != [prep["local_base"], prep["upstream_head"]]:
            return EXIT_GIT_FAILED, {"status": "error",
                                     "reason": f"--amend refused: the clone HEAD parents {head_parents} are not "
                                               "(local_base, upstream_head)"}
        git(scratch, *MERGE_IDENTITY, "commit", "-q", "--amend", "--no-edit")
    elif merge_head_file.exists():
        if merge_head_file.read_text().strip() != prep["upstream_head"]:
            return EXIT_GIT_FAILED, {"status": "error",
                                     "reason": "the clone is mid-merge of something other than the gated upstream "
                                               "point — run prepare again"}
        git(scratch, *MERGE_IDENTITY, "commit", "-q", "--no-edit")
    # else: nothing conflicted and `git merge` committed on the spot in prepare,
    # or commit already ran; the parent check below proves it is the right
    # merge either way (which is what makes commit idempotent).
    merge_sha = git(scratch, "rev-parse", "HEAD").stdout.strip()
    parents = git(scratch, "rev-list", "--parents", "-n1", merge_sha).stdout.split()[1:]
    if parents != [prep["local_base"], prep["upstream_head"]]:
        return EXIT_GIT_FAILED, {"status": "error",
                                 "reason": f"merge parents {parents} are not (local_base, upstream_head) — "
                                           "run prepare again"}
    prep["merge_sha"] = merge_sha
    _write_json(prep_path, prep)
    return EXIT_OK, {"status": "committed", "merge_sha": merge_sha, "prep": prep}


def cmd_commit(args) -> int:
    """Commit the resolved merge in the clone; no finalize request. The host
    finalizer's apply-decisions calls this and lands the result itself."""
    code, payload = _commit_merge(args)
    payload.pop("prep", None)
    emit(payload)
    return code


def cmd_handoff(args) -> int:
    state = Path(args.state)
    # Same guard prepare has. Two agents answering the same gate handed off the
    # same merge twice on 2026-08-15; the second request was processed after the
    # first had already landed it and reported the apply as failed.
    for name in ("finalize-request.json", "finalize-request.processing.json"):
        if (state / name).exists():
            emit({"status": "error",
                  "reason": f"{name} exists — a finalize is already in flight; wait for its result"})
            return EXIT_USAGE
    code, payload = _commit_merge(args)
    if code != EXIT_OK:
        emit(payload)
        return code
    prep = payload["prep"]
    merge_sha = payload["merge_sha"]
    requested_at = _now()
    _write_json(state / "finalize-request.json", {
        "action": "apply-merge",
        "upstream_sha": prep["upstream_head"],
        "merge_sha": merge_sha,
        "scratch_repo": args.scratch,
        "requested_at": requested_at,
    })
    prep["handed_off_at"] = requested_at
    _write_json(state / "apply-prepare.json", prep)
    emit({"status": "handed_off", "merge_sha": merge_sha, "requested_at": requested_at})
    return EXIT_OK


# --------------------------------------------------------------------------- resolve-llm

def cmd_resolve_llm(args) -> int:
    """Run the per-hunk model resolver over needs_manual; stage what it closes.

    Whole-file semantics: a file with any failed hunk keeps ALL its markers and
    is reported under ``unresolved`` with the reason. Exit 0 when nothing is
    left, 6 (unresolved) otherwise — the caller decides whether that is fatal.
    """
    from upstream_sync_llm import resolve_file

    state = Path(args.state)
    scratch = state / args.scratch
    prep_path = state / "apply-prepare.json"
    if not prep_path.exists():
        emit({"status": "error", "reason": "apply-prepare.json missing — run prepare first"})
        return EXIT_USAGE
    prep = json.loads(prep_path.read_text(encoding="utf-8"))
    if prep.get("status") != "ready":
        emit({"status": "error", "reason": f"prepare ended with {prep.get('status')!r}, not ready"})
        return EXIT_USAGE
    pending = load_pending(state) if (state / "pending.json").exists() else {}
    by_file, _ = decisions_by_file(pending)
    subjects: dict = {}
    for feat in pending.get("features", []):
        for path in feat.get("files", []):
            subjects[path] = feat.get("local_subjects") or []

    llm_resolved: list = list(prep.get("llm_resolved") or [])
    unresolved: list = []
    still_manual: list = []
    for item in prep.get("needs_manual") or []:
        path = item["path"] if isinstance(item, dict) else item
        file_path = scratch / path
        if not file_path.exists():
            unresolved.append({"path": path, "reason": "delete/modify conflict — a human call"})
            still_manual.append(item)
            continue
        report = resolve_file(
            file_path, rel_path=path, decision=by_file.get(path, "merge-both"),
            local_subjects=subjects.get(path), upstream_head=prep.get("upstream_head", ""),
        )
        if report.get("written"):
            git(scratch, "add", "--", path)
            llm_resolved.append(path)
        elif report["resolved"] == 0 and report["failed"] == 0:
            # No blocks left in the file (someone resolved it by hand) — stage it.
            git(scratch, "add", "--", path)
            llm_resolved.append(path)
        else:
            unresolved.append({"path": path, "reason": "; ".join(report["errors"])[:400],
                               "resolved_hunks": report["resolved"], "failed_hunks": report["failed"]})
            still_manual.append(item)

    prep["llm_resolved"] = sorted(set(llm_resolved))
    prep["unresolved"] = unresolved
    prep["needs_manual"] = still_manual
    prep["resolved_at"] = _now()
    _write_json(prep_path, prep)
    payload = {"status": "resolved" if not unresolved else "unresolved",
               "llm_resolved": prep["llm_resolved"], "unresolved": unresolved}
    emit(payload)
    return EXIT_OK if not unresolved else EXIT_UNRESOLVED


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
    p_prep = sub.add_parser("prepare", parents=[common])
    p_prep.add_argument("--auto-policy", action="store_true",
                        help="decide undecided plain paths by policy (merge-both); security paths still ask")
    p_prep.add_argument("--in-flight-ok", action="store_true",
                        help="do not refuse when a finalize request is in flight (the finalizer's own call)")
    p_prep.set_defaults(func=cmd_prepare)
    sub.add_parser("resolve-llm", parents=[common]).set_defaults(func=cmd_resolve_llm)
    p_commit = sub.add_parser("commit", parents=[common])
    p_commit.add_argument("--amend", action="store_true",
                          help="fold staged changes into the existing merge commit (gate-triage fixes), "
                               "preserving its parents")
    p_commit.set_defaults(func=cmd_commit)
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
