#!/usr/bin/env python3
"""Triage a red fork-test gate: diagnose, propose a test patch, never apply it.

An upstream merge that turns a fork test red means one of exactly two things,
and no model tells them apart reliably:

* the **test is stale** — upstream changed an API our test still asserts the
  old shape of (2026-08-15: ``transcribe_audio(path)`` grew a ``model`` and a
  ``source`` parameter and our fork test still called it with one argument).
  The fix belongs in the test.
* the **merge lost our behaviour** — a customization was dropped while
  resolving a conflict, and the test is the only sensor that noticed. The fix
  belongs in the merge; changing the test here would delete the alarm.

Automation that edits the test whenever the gate is red would, sooner or later,
paper over the second case. So this module goes as far as it safely can and
stops: it gathers evidence, asks the model for a verdict plus a candidate
patch, **validates the patch before anyone sees it**, and writes an armed
``gate-triage.json``. The operator answers with one word; the finalizer's
``apply-triage-fixes`` action does the applying.

Everything is host-side and best effort — a failure here must never change the
outcome of the gate, which has already decided not to land the merge. Two env
hooks keep the whole thing testable offline: ``HERMES_SYNC_TRIAGE_CMD`` (stdin
JSON → stdout JSON, replaces the model) and ``HERMES_SYNC_TRIAGE_PYTEST_CMD``
(argv prefix, replaces the verification run).
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "upstream-sync-triage/v1"
MAX_EXCERPT_LINES = 120
MAX_DIFF_LINES = 400
MAX_UPSTREAM_SUBJECTS = 40
PYTEST_TIMEOUT = 900

SYSTEM_PROMPT = """You triage ONE failing test in a fork of an actively developed upstream project. \
The fork just merged upstream; this test passed before the merge and fails after it. Exactly one \
of two things is true and you must decide which:

- "test_outdated": upstream legitimately changed the API or behaviour the test asserts, and the \
fork's own behaviour is intact. The test should be updated to the new upstream contract while \
still asserting everything it asserted before.
- "behaviour_lost": the merge dropped or broke a local customization, and the test is correctly \
reporting a regression. The merge must be fixed, NOT the test.
- "unsure": the evidence does not settle it.

Answer "behaviour_lost" whenever the failure looks like local behaviour disappearing. A wrong \
"test_outdated" hides a real regression forever; a wrong "behaviour_lost" only costs a human \
five minutes.

For "test_outdated" only, also return a patch: the COMPLETE new contents of the test file (not a \
diff, not a fragment). It must change nothing outside that one file, must keep every assertion the \
old file made (adapted to the new contract — never deleted, never weakened to `assert True`), and \
must make the failing tests pass. Anything else is rejected before the operator sees it.

Answer as JSON: {"verdict": "...", "explanation": "<2-4 sentences>", "assertion_delta": \
"<what changed about what is asserted, or 'none'>", "patch": "<full file contents or empty>"}."""


# --------------------------------------------------------------------------- helpers

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _git(repo: Path, *args: str, check: bool = False) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[-400:]}")
    return proc.stdout


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _truncate(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    # -1: the marker itself is one of the lines the caller budgeted for.
    kept = lines[:max_lines - 1]
    kept.append(f"... [{len(lines) - max_lines + 1} more lines truncated]")
    return "\n".join(kept) + "\n"


# --------------------------------------------------------------------------- modules under test

# `\s` would match newlines and swallow the following lines whole (an
# `import a.b` directly above a `from c import d` yielded one bogus dotted name
# and lost both). Stay on one line.
_IMPORT_RE = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import\b|import[ \t]+([\w.,\t ]+))", re.M)


def _candidate_paths(dotted: str) -> list[str]:
    parts = dotted.split(".")
    out = []
    while parts:
        out.append("/".join(parts) + ".py")
        out.append("/".join(parts) + "/__init__.py")
        parts = parts[:-1]
    return out


def modules_under_test(test_rel: str, test_text: str, repo: Path, conflicts=None) -> list[str]:
    """Repo-relative production files a failing test is plausibly about.

    Three cheap signals, union'd: the test's own imports resolved against the
    tree, the twin package of the test's directory (``tests/gateway/`` →
    ``gateway/``), and the files this merge actually had conflicts in. The
    point is to CUT the diff the model gets: ``gateway/run.py`` alone is 32k
    lines, and a model handed the whole merge diff finds nothing in it.
    """
    found: list[str] = []

    def add(rel: str) -> None:
        if rel not in found and (repo / rel).is_file():
            found.append(rel)

    for from_mod, import_mods in _IMPORT_RE.findall(test_text or ""):
        dotted_names = [from_mod] if from_mod else [
            m.strip().split(" as ")[0].strip() for m in (import_mods or "").split(",")
        ]
        for dotted in dotted_names:
            if not dotted:
                continue
            for cand in _candidate_paths(dotted):
                add(cand)

    # tests/gateway/test_voice.py → everything directly under gateway/
    parts = Path(test_rel).parts
    if parts and parts[0] == "tests" and len(parts) > 1:
        twin = repo / Path(*parts[1:-1]) if len(parts) > 2 else None
        if twin and twin.is_dir():
            for child in sorted(twin.glob("*.py")):
                add(str(child.relative_to(repo)))

    for path in conflicts or []:
        add(str(path))
    return found


# --------------------------------------------------------------------------- pytest output

_SECTION_RE = re.compile(r"^_{3,}.*_{3,}$")


def pytest_excerpt(log: str, test_id: str) -> str:
    """The FAILURES section pytest printed for one test id, truncated.

    pytest heads each failure with an underscore-ruled banner carrying the test
    name; we take from the banner naming this test up to the next banner or the
    summary. If nothing matches, the caller still has the id itself — an empty
    excerpt is a missing detail, not a missing failure.
    """
    name = test_id.rsplit("::", 1)[-1]
    lines = (log or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if _SECTION_RE.match(line.strip()) and name and name in line:
            start = i
            break
    if start is None:
        return ""
    out = [lines[start]]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if _SECTION_RE.match(stripped) or stripped.startswith("===="):
            break
        out.append(line)
    return _truncate("\n".join(out), MAX_EXCERPT_LINES)


# --------------------------------------------------------------------------- evidence

def _is_fork_test(repo: Path, rel: str, upstream_head: str) -> bool:
    """A test file that does not exist on the upstream side is ours.

    That is the same definition ``run-fork-tests.sh`` uses to pick which tests
    the gate runs at all, so the label the operator reads matches the set that
    produced the failure.
    """
    if not upstream_head:
        return True
    return not _git(repo, "ls-tree", "-r", "--name-only", upstream_head, "--", rel).strip()


def collect_evidence(repo: Path, test_rel: str, test_ids: list[str], post_log: str, prep: dict,
                     merge_sha: str) -> dict:
    """Everything the model is allowed to see about one failing test file."""
    local_base = prep.get("local_base") or ""
    upstream_head = prep.get("upstream_head") or ""
    test_text = _git(repo, "show", f"{merge_sha}:{test_rel}")
    mods = modules_under_test(test_rel, test_text, repo, conflicts=prep.get("conflicts"))

    diffs = {}
    for mod in mods[:12]:
        if not local_base or not merge_sha:
            continue
        d = _git(repo, "diff", f"{local_base}..{merge_sha}", "--", mod)
        if d.strip():
            diffs[mod] = _truncate(d, MAX_DIFF_LINES)

    subjects: list[str] = []
    merge_base = _git(repo, "merge-base", local_base, upstream_head).strip() if (local_base and upstream_head) else ""
    if merge_base and upstream_head and mods:
        raw = _git(repo, "log", "--no-merges", "--format=%h %s", f"{merge_base}..{upstream_head}",
                   "--", *mods[:12])
        subjects = [l for l in raw.splitlines() if l.strip()][:MAX_UPSTREAM_SUBJECTS]

    return {
        "test_file": test_rel,
        "test_ids": test_ids,
        "test_kind": "fork" if _is_fork_test(repo, test_rel, upstream_head) else "upstream",
        "test_source": _truncate(test_text, 600),
        "excerpt": "\n\n".join(x for x in (pytest_excerpt(post_log, tid) for tid in test_ids) if x),
        "modules_under_test": mods,
        "merge_diffs": diffs,
        "upstream_commits": subjects,
    }


# --------------------------------------------------------------------------- model

def _call_model(payload: dict) -> str:
    cmd = os.environ.get("HERMES_SYNC_TRIAGE_CMD")
    if cmd:
        proc = subprocess.run(shlex.split(cmd), input=json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, encoding="utf-8", timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(f"triage command failed ({proc.returncode}): {proc.stderr.strip()[-400:]}")
        return proc.stdout
    from upstream_sync_llm import call_json_model
    return call_json_model(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))


def _parse_answer(raw: str) -> dict:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s, flags=re.S).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the model answer contains no JSON object")
    obj = json.loads(s[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("the model answer is not a JSON object")
    return obj


# --------------------------------------------------------------------------- validation

def _assert_count(text: str) -> int:
    """How many assertions a test file makes — counted on the AST, so a string
    or a comment containing the word does not inflate it."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            n += 1
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None) or ""
            if name.startswith("assert") or name in ("raises", "fail"):
                n += 1
    return n


def validate_proposal(*, repo: Path, merge_sha: str, test_file: str, patch: str,
                      test_ids: list[str]) -> tuple[bool, str]:
    """Prove the proposal is safe to show. Returns (ok, reason-if-not).

    Four gates, cheapest first: it must be a test file, it must parse, it must
    not assert less than the file it replaces, and — in a throwaway worktree on
    the merge — the failing tests must actually pass with it applied. A patch
    that fails any of them is downgraded to a diagnosis rather than shown as a
    suggestion, because a suggestion the operator can approve with one word has
    to be one we already checked.
    """
    rel = str(test_file or "").strip()
    if not rel or not patch:
        return False, "no patch"
    parts = Path(rel).parts
    if parts[0] != "tests" or not rel.endswith(".py"):
        return False, f"{rel} is not a test file — the triage only ever patches tests/*.py"
    try:
        ast.parse(patch, filename=rel)
    except SyntaxError as exc:
        return False, f"the patch does not parse: {exc.msg} (line {exc.lineno})"

    old = _git(repo, "show", f"{merge_sha}:{rel}")
    old_n, new_n = _assert_count(old), _assert_count(patch)
    if old_n >= 0 and new_n < old_n:
        return False, (f"the patch asserts less than the test it replaces ({new_n} vs {old_n} "
                       "assertions) — a weakened test is how a real regression gets hidden")

    wt = Path(tempfile.mkdtemp(prefix="hermes-triage-"))
    try:
        if _git(repo, "worktree", "add", "--detach", str(wt), merge_sha) is None:
            return False, "could not create the verification worktree"
        if not (wt / ".git").exists():
            return False, "could not create the verification worktree"
        target = wt / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(patch, encoding="utf-8")
        cmd = os.environ.get("HERMES_SYNC_TRIAGE_PYTEST_CMD")
        argv = shlex.split(cmd) if cmd else [
            os.environ.get("HERMES_PYTHON") or sys.executable, "-m", "pytest",
            "--timeout=120", "-p", "no:cacheprovider", "-q",
        ]
        try:
            proc = subprocess.run([*argv, *test_ids], cwd=str(wt), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=PYTEST_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"the verification run could not start: {exc}"
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout or "").splitlines()[-12:])
            return False, f"the tests still fail with the patch applied:\n{tail}"
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)
    return True, ""


# --------------------------------------------------------------------------- run

def _triage_one(repo: Path, evidence: dict, merge_sha: str) -> dict:
    proposal = {
        "test_file": evidence["test_file"],
        "test_ids": evidence["test_ids"],
        "test_kind": evidence["test_kind"],
        "excerpt": evidence["excerpt"],
        "modules_under_test": evidence["modules_under_test"],
        "verdict": "unsure",
        "explanation": "",
        "assertion_delta": "",
        "patch": "",
        "rejected_reason": "",
    }
    try:
        answer = _parse_answer(_call_model(evidence))
    except Exception as exc:  # best effort: a dead model still leaves a diagnosis
        proposal["explanation"] = f"the triage model could not be reached: {exc}"
        return proposal

    verdict = str(answer.get("verdict") or "unsure").strip()
    if verdict not in ("test_outdated", "behaviour_lost", "unsure"):
        verdict = "unsure"
    proposal["verdict"] = verdict
    proposal["explanation"] = str(answer.get("explanation") or "")
    proposal["assertion_delta"] = str(answer.get("assertion_delta") or "")

    patch = str(answer.get("patch") or "")
    if verdict != "test_outdated":
        # A merge that lost our behaviour is fixed in the merge. Offering a test
        # patch there is offering to hide the regression, so we drop the patch
        # even when the model volunteered one.
        proposal["rejected_reason"] = ("no patch is offered for this verdict — the fix belongs in "
                                       "the merge, not in the test") if patch else ""
        return proposal
    if not patch:
        proposal["rejected_reason"] = "the model returned no patch"
        return proposal
    ok, reason = validate_proposal(repo=repo, merge_sha=merge_sha, test_file=proposal["test_file"],
                                   patch=patch, test_ids=proposal["test_ids"])
    if ok:
        proposal["patch"] = patch
    else:
        proposal["verdict"] = "unsure"
        proposal["rejected_reason"] = reason
    return proposal


GATE_FAILURES_V2 = "upstream-sync-gate-failures/v2"


def _failure_nodeids_for_triage(failures: dict) -> list[str]:
    """Read the authoritative failure list for either persisted schema."""
    if failures.get("schema_version") == GATE_FAILURES_V2:
        blocking = failures.get("blocking_failures")
        if not isinstance(blocking, list):
            raise ValueError("v2 gate-failures has no blocking_failures list")
        nodeids = []
        for item in blocking:
            if not isinstance(item, dict) or not isinstance(item.get("nodeid"), str):
                raise ValueError("v2 blocking_failures contains an invalid entry")
            nodeids.append(item["nodeid"])
        return sorted(set(nodeids))
    return sorted({item for item in (failures.get("new_failures") or []) if item})


def run_triage(*, state: Path | str, repo: Path | str) -> int:
    """Diagnose every new failure the gate recorded; write gate-triage.json.

    Always returns 0 unless the state is unusable: the gate has already decided
    the merge does not land, and a triage that fell over must not turn that into
    a different outcome.
    """
    state, repo = Path(state), Path(repo)
    failures = _read_json(state / "gate-failures.json")
    new_failures = _failure_nodeids_for_triage(failures)
    if not new_failures:
        return 0
    merge_sha = failures.get("merge_sha") or ""
    prep = _read_json(state / "apply-prepare.json")
    try:
        post_log = (state / "gate-post.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        post_log = ""

    by_file: dict[str, list[str]] = {}
    for test_id in new_failures:
        by_file.setdefault(test_id.split("::", 1)[0], []).append(test_id)

    proposals = []
    for test_rel, ids in by_file.items():
        evidence = collect_evidence(repo, test_rel, ids, post_log, prep, merge_sha)
        proposals.append(_triage_one(repo, evidence, merge_sha))

    _write_json(state / "gate-triage.json", {
        "schema": SCHEMA,
        "status": "awaiting_triage",
        "merge_sha": merge_sha,
        "before": failures.get("before") or "",
        "proposals": proposals,
        "created_at": _now(),
        "slack_ts": None,
    })
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="triage a red upstream-sync fork-test gate")
    parser.add_argument("--state", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        return run_triage(state=args.state, repo=args.repo)
    except Exception as exc:  # never fail the caller: the gate outcome stands
        print(f"triage failed: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
