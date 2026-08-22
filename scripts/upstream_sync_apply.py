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
import ast
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
from upstream_sync_index import (  # noqa: E402
    read_blob, read_stage_zero_blob, snapshot, stage_zero_entries, tree_entries, zlist,
)
from upstream_sync_receipts import finding_key, fingerprint, receipt_matches  # noqa: E402
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


def _cached_paths(repo: Path, base: str) -> set[str]:
    return set(zlist(repo, "diff", "--cached", "--name-only", "-z", base, "--"))


def _changed_paths(repo: Path, left: str, right: str) -> set[str]:
    return set(zlist(repo, "diff", "--name-only", "-z", left, right, "--"))


def _commit_tree_contract_error(scratch: Path) -> str | None:
    unstaged = git(scratch, "diff", "--quiet", "--", check=False)
    if unstaged.returncode not in (0, 1):
        return f"could not inspect unstaged changes: {unstaged.stderr.strip()}"
    untracked = zlist(scratch, "ls-files", "--others", "--exclude-standard", "-z")
    if unstaged.returncode == 1 or untracked:
        details = []
        if unstaged.returncode == 1:
            details.append("unstaged tracked changes")
        if untracked:
            details.append("untracked files: " + ", ".join(untracked[:8]))
        return ("the clone has " + " and ".join(details) + "; the gate only examines "
                "the tree that will be committed — the edit was preserved; execute "
                "`git add -- <paths>` and retry")
    return None


def _decision_conflicts(pending: dict) -> list[str]:
    seen: dict[str, str] = {}
    conflicts: set[str] = set()
    for feature in pending.get("features", []):
        decision = feature.get("decision")
        if decision not in VALID_DECISIONS:
            continue
        for path in feature.get("files", []):
            previous = seen.get(path)
            if previous is not None and previous != decision:
                conflicts.add(path)
            seen[path] = decision
    return sorted(conflicts)


def _resolution_policy_snapshot(scratch: Path, pending: dict, local_base: str,
                                upstream_head: str, conflicts: list[str]) -> tuple[dict, str]:
    base = git(scratch, "merge-base", local_base, upstream_head).stdout.strip()
    by_file, missing = decisions_by_file(pending)
    ambiguous = _decision_conflicts(pending)
    if missing:
        raise ValueError("resolution policy has undecided features: " + ", ".join(map(str, missing)))
    if ambiguous:
        raise ValueError("resolution policy is ambiguous for path(s): " + ", ".join(ambiguous))
    missing_policy = sorted(set(conflicts) - set(by_file))
    if missing_policy:
        raise ValueError("conflicted path has no immutable resolution policy: " + ", ".join(missing_policy))
    changed = _changed_paths(scratch, base, local_base) | _changed_paths(scratch, base, upstream_head)
    return {path: by_file.get(path, "merge-both") for path in sorted(changed)}, base


def _decorate_findings(scratch: Path, prep: dict, report) -> None:
    base_entries = tree_entries(scratch, prep["merge_scope"]["merge_base"])
    ours_entries = tree_entries(scratch, prep["local_base"])
    theirs_entries = tree_entries(scratch, prep["upstream_head"])
    result_entries = stage_zero_entries(scratch)
    policies = prep.get("resolution_policy_by_path") or {}
    for item in report.findings:
        policy = policies.get(item.path, "merge-both")
        fp = fingerprint(
            path=item.path,
            kind=item.kind,
            symbol=item.symbol,
            policy=policy,
            base=snapshot(base_entries, item.path),
            ours=snapshot(ours_entries, item.path),
            theirs=snapshot(theirs_entries, item.path),
            result=snapshot(result_entries, item.path),
        )
        object.__setattr__(item, "finding_id", fp["id"])
        object.__setattr__(item, "fingerprint", fp)
        object.__setattr__(item, "policy", policy)


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


def resolve_merge_both_text(text: str, path: str | None = None) -> tuple:
    """Return (new_text, resolved_hunks, remaining_hunks).

    Rewrites only the zdiff3 blocks whose resolution is mechanical (see
    _mechanical_resolution); everything else keeps its markers for the agent.
    A block without a ``|||||||`` base section is not zdiff3 and is left alone.

    "Mechanical" means a text concatenation, which is not the same as a correct
    one: on 2026-08-19 both sides of a block shared the single closing brace
    that followed it, so joining the two bodies left one closer for two dicts
    and the module stopped parsing. When ``path`` names a Python file the
    result is therefore parsed, and a resolution that does not parse is rolled
    back whole-file and handed to a human rather than reported as closed.
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
    new_text = "".join(out)
    if resolved and path and path.endswith(".py"):
        try:
            ast.parse(new_text)
        except SyntaxError:
            # Whole-file rollback, matching the model resolver: a half-applied
            # file is harder to reason about than one that still has markers.
            return text, 0, resolved + remaining
    return new_text, resolved, remaining


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
    # A new prepare deliberately rebases the decision set onto the current live
    # HEAD. Any prior invariant receipt state is invalidated below; the pending
    # conflict decision itself is still evaluated against this live snapshot.
    stale_receipts = state / "invariants-pending.json"
    if stale_receipts.exists():
        stale_receipts.unlink()
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
        "merge_scope": {"local_parent": local_base, "upstream_parent": upstream_head,
                        "merge_base": None},
        "resolution_policy_by_path": {},
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

    try:
        policy_by_path, merge_base = _resolution_policy_snapshot(
            scratch, pending, local_base, upstream_head, conflicts
        )
    except ValueError as exc:
        summary["status"] = "policy_error"
        summary["reason"] = str(exc)
        _write_json(state / "apply-prepare.json", summary)
        emit(summary)
        return EXIT_UNRESOLVED
    summary["merge_scope"]["merge_base"] = merge_base
    summary["resolution_policy_by_path"] = policy_by_path

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
            new_text, resolved, remaining = resolve_merge_both_text(text, path=path)
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

def _invariant_report(scratch: Path, prep: dict):
    """Check the stage-0 index tree that the next commit will contain.

    All path sets and blobs come from the index/tree object database. The work
    tree is deliberately not consulted here: an unstaged edit is rejected by
    ``_commit_tree_contract_error`` before this function runs.
    """
    from upstream_sync_invariants import check_merge

    local_base = prep["local_base"]
    upstream_head = prep["upstream_head"]
    base = prep.get("merge_scope", {}).get("merge_base") or git(
        scratch, "merge-base", local_base, upstream_head
    ).stdout.strip()
    touched = _cached_paths(scratch, local_base)
    both_sides = _changed_paths(scratch, base, local_base) & _changed_paths(
        scratch, base, upstream_head
    )
    paths = sorted(touched | both_sides)
    index = stage_zero_entries(scratch)
    paths_in_result = set(index)
    base_entries = tree_entries(scratch, base)
    ours_entries = tree_entries(scratch, local_base)
    theirs_entries = tree_entries(scratch, upstream_head)

    def read_from(entries, path):
        entry = entries.get(path)
        if entry is None or entry.mode == "160000":
            return None
        return read_blob(scratch, entry.oid)

    def read_result(path):
        return read_stage_zero_blob(scratch, index, path)

    full = sorted(path for path in both_sides if path in paths_in_result)
    parse_only = [
        path for path in paths
        if path not in both_sides and path in paths_in_result
    ]
    policy = prep.get("resolution_policy_by_path") or {}
    deleted = sorted(path for path in paths if path not in paths_in_result)
    report = check_merge(
        full,
        lambda path: read_from(ours_entries, path),
        lambda path: read_from(theirs_entries, path),
        read_result,
        lambda path: read_from(base_entries, path),
        policy_by_path=policy,
    )
    parse_report = check_merge(
        parse_only,
        lambda _path: None,
        lambda _path: None,
        read_result,
        policy_by_path=policy,
    )
    report.findings.extend(f for f in parse_report.findings if f.kind == "unparseable")
    from upstream_sync_invariants import Finding
    for path in deleted:
        report.findings.append(Finding(
            path=path,
            kind="deleted_in_result",
            policy=policy.get(path),
            message=(
                "the resolved merge deletes this path from the stage-0 result; "
                "confirm the delete/modify resolution was intended"
            ),
        ))
    _decorate_findings(scratch, prep, report)
    return report


_HARD_INVARIANT_KINDS = {"unparseable", "unreadable_parent"}


def _arm_invariant_state(state: Path, prep: dict, findings: list[dict], *, expected=None, mode="block") -> None:
    """Persist the merge-scoped receipt state without rereading pending later."""
    path = state / "invariants-pending.json"
    old: dict = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
    scope = prep.get("merge_scope") or {}
    if old.get("merge_scope") != scope:
        old = {}
    pending = {}
    try:
        pending = load_pending(state)
    except (OSError, ValueError):
        pass
    origin = {
        "platform": pending.get("slack_platform"),
        "chat_id": pending.get("slack_channel"),
        "thread_id": pending.get("slack_thread_ts"),
        "user_id": pending.get("slack_user_id"),
    }
    blocking = [
        finding for finding in findings
        if not (mode == "report" and finding.get("kind") == "discarded_contribution")
    ]
    if mode == "report" and not blocking:
        status = "reported"
    else:
        status = "blocked" if any(
            f.get("kind") in _HARD_INVARIANT_KINDS for f in blocking
        ) else "awaiting_ack"
    journal = list(old.get("journal") or [])
    for event in expected or []:
        if not any(
            existing.get("event") == "expected_policy_loss"
            and existing.get("path") == event.get("path")
            and existing.get("symbol") == event.get("symbol")
            and existing.get("discarded_side") == event.get("discarded_side")
            for existing in journal
        ):
            journal.append(event)
    payload = {
        "schema": "upstream-sync-invariants-pending/v1",
        "version": 1,
        "status": status,
        "mode": mode,
        "created_at": old.get("created_at") or _now(),
        "updated_at": _now(),
        "merge_scope": scope,
        "merge_record": "apply-prepare.json",
        "origin": old.get("origin") or origin,
        "findings": findings,
        "receipts": old.get("receipts") or [],
        "journal": journal,
        "expected_policy_losses": list(expected or []),
    }
    _write_json(path, payload)


def _unacknowledged_findings(state: Path, findings: list[dict]) -> list[dict]:
    try:
        data = json.loads((state / "invariants-pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return findings
    receipts = data.get("receipts") or []
    return [
        finding for finding in findings
        if finding.get("kind") in _HARD_INVARIANT_KINDS
        or not any(receipt_matches(receipt, finding) for receipt in receipts)
    ]

def _invariant_mode(args) -> str:
    value = getattr(args, "invariant_mode", None) or os.getenv(
        "HERMES_SYNC_INVARIANT_MODE", "block"
    )
    value = str(value).strip().lower()
    if value not in {"block", "report"}:
        raise ValueError("invariant mode must be 'block' or 'report'")
    return value


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
    marked = []
    index = stage_zero_entries(scratch)
    for path in prep["conflicts"]:
        text = read_stage_zero_blob(scratch, index, path)
        if text is not None and has_conflict_markers(text):
            marked.append(path)
    if stages or marked:
        return EXIT_UNRESOLVED, {"status": "unresolved", "unmerged": sorted(stages), "with_markers": marked}
    contract_error = _commit_tree_contract_error(scratch)
    if contract_error:
        return EXIT_UNRESOLVED, {"status": "unstaged_tree", "reason": contract_error}

    # Check the race before committing anything: the host refuses a merge whose
    # first parent is not its current HEAD, so there is nothing to gain from
    # committing one — prepare has to be redone either way.
    live_head = git(live, "rev-parse", "HEAD").stdout.strip()
    if live_head != prep["local_base"]:
        return EXIT_LIVE_MOVED, {"status": "live_moved", "local_base": prep["local_base"], "live_head": live_head,
                                 "hint": "the live branch moved since prepare; run prepare again and redo the resolution"}

    # Structural gate. Deliberately before the commit: a merge that fails here
    # is not a merge anyone should be able to hand off. The preserved clone is
    # the repair surface; the commit object is not rewritten on refusal.
    break_glass = bool(getattr(args, "break_glass", False))
    invariant_mode = _invariant_mode(args)
    if break_glass:
        prep["invariants_break_glass"] = {"used_at": _now(), "mode": "manual-only"}
        prep["invariant_report"] = {
            "mode": "break-glass",
            "status": "not-run",
            "findings": [],
            "expected_policy_losses": [],
        }
    else:
        report = _invariant_report(scratch, prep)
        report_payload = report.as_dict()
        records = report_payload["findings"]
        expected = report_payload.get("expected_policy_losses", [])
        prep["invariant_report"] = {
            "mode": invariant_mode,
            "status": "reported" if invariant_mode == "report" else "blocking",
            "findings": records,
            "expected_policy_losses": expected,
        }
        if records or expected:
            _arm_invariant_state(
                state, prep, records, expected=expected, mode=invariant_mode,
            )
        blocking = [
            finding for finding in records
            if not (
                invariant_mode == "report"
                and finding.get("kind") == "discarded_contribution"
            )
        ]
        if blocking:
            active = _unacknowledged_findings(state, blocking)
            if active:
                payload = {
                    "status": "invariants_failed",
                    "ok": False,
                    "findings": active,
                    "invariant_report": prep["invariant_report"],
                }
                payload["acknowledgements_required"] = [
                    f.get("finding_id") for f in active
                    if f.get("kind") not in _HARD_INVARIANT_KINDS
                ]
                blocked_note = ""
                try:
                    current_status = json.loads(
                        (state / "invariants-pending.json").read_text(encoding="utf-8")
                    ).get("status")
                    if current_status == "blocked":
                        blocked_note = (
                            " The invariant state is blocked; receipt interception is "
                            "disabled until every hard finding is repaired."
                        )
                except (OSError, ValueError):
                    blocked_note = ""
                payload["hint"] = (
                    "nothing was committed and the clone is preserved; hard findings "
                    "must be repaired, while soft findings require one matching "
                    "fingerprint receipt each." + blocked_note
                )
                return EXIT_UNRESOLVED, payload

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
    payload = {"status": "committed", "merge_sha": merge_sha, "prep": prep}
    if prep.get("invariant_report"):
        payload["invariant_report"] = prep["invariant_report"]
    if break_glass:
        payload["invariants_break_glass"] = prep["invariants_break_glass"]
    return EXIT_OK, payload


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
    p_commit.add_argument("--break-glass", action="store_true",
                          help="manual audited emergency bypass; never supplied by systemd")
    p_commit.add_argument("--invariant-mode", choices=("block", "report"),
                          help="block findings (default) or report discarded contributions without blocking")
    p_commit.add_argument("--amend", action="store_true",
                          help="fold staged changes into the existing merge commit (gate-triage fixes), "
                               "preserving its parents")
    p_commit.set_defaults(func=cmd_commit)
    p_handoff = sub.add_parser("handoff", parents=[common])
    p_handoff.add_argument("--break-glass", action="store_true",
                           help="manual audited emergency bypass; never supplied by systemd")
    p_handoff.add_argument("--invariant-mode", choices=("block", "report"),
                           help="block findings (default) or report discarded contributions without blocking")
    p_handoff.set_defaults(func=cmd_handoff)
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
