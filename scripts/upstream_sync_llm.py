#!/usr/bin/env python3
"""LLM as a function for upstream-sync: one call per conflict hunk, validated.

This is deliberately NOT an agent. The model gets one zdiff3 block (ours,
base, theirs) with surrounding context and the operator's decision, and must
answer with the resolved text for that region — nothing else. The caller
validates: no conflict markers, and for ``.py`` files the whole file must parse
once every hunk is substituted. One retry per hunk with the validation error
fed back; a file with any failed hunk is left with ALL its markers (no
half-resolved files — the test gate and the operator get a clean signal).

The model call is pluggable three ways, in this order:
  1. ``call_model=`` argument (tests, other callers);
  2. ``HERMES_SYNC_RESOLVER_CMD`` — a command that receives the hunk payload as
     JSON on stdin and prints the resolution (bash callers, tests);
  3. the repo's own model client (``agent.auxiliary_client``) on the
     ``coding`` tier of the model policy — the same path the review gates use.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

CONFLICT_START = "<<<<<<< "
CONFLICT_BASE = "||||||| "
CONFLICT_SEP = "======="
CONFLICT_END = ">>>>>>> "
CONTEXT_LINES = 40
MODEL_TIER = "coding"

SYSTEM_PROMPT = """You resolve ONE git merge conflict hunk in a fork of an actively developed \
upstream project. You are given the local side ("ours"), the merge base, and the upstream side \
("theirs"), plus surrounding context and the operator's decision.

Decision semantics:
- merge-both: BOTH intents must survive. Keep the local behaviour and adopt upstream's structure \
and changes. If both sides added something, keep both. If upstream moved or renamed code the \
local side modified, port the local modification into upstream's new structure — do not paste the \
old local block back.
- keep-local: output the local side.
- take-upstream: output the upstream side.

Rules: output ONLY the resolved replacement for the conflicted region — no conflict markers, no \
explanation, no surrounding context lines, no code fences. Preserve indentation and the file's \
style. Answer as JSON: {"resolution": "<text>"}."""


class ResolverError(RuntimeError):
    pass


# --------------------------------------------------------------------------- parsing zdiff3

def _parse_blocks(text: str) -> tuple:
    """Return (segments, blocks): segments alternate literal text and block
    indexes; blocks are dicts with ours/base/theirs/raw."""
    lines = text.splitlines(keepends=True)
    segments: list = []
    blocks: list = []
    buf: list = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(CONFLICT_START):
            buf.append(lines[i]); i += 1; continue
        ours: list = []; base = None; theirs: list = []; section = "ours"; j = i + 1; closed = False
        while j < len(lines):
            line = lines[j]
            if section == "ours" and line.startswith(CONFLICT_BASE):
                base, section = [], "base"
            elif section in ("ours", "base") and line.rstrip("\r\n") == CONFLICT_SEP:
                section = "theirs"
            elif section == "theirs" and line.startswith(CONFLICT_END):
                closed = True; break
            else:
                (ours if section == "ours" else base if section == "base" else theirs).append(line)
            j += 1
        if not closed:
            buf.extend(lines[i:]); break
        segments.append("".join(buf)); buf = []
        blocks.append({"ours": "".join(ours), "base": "".join(base or []), "theirs": "".join(theirs),
                       "raw": "".join(lines[i:j + 1])})
        segments.append(len(blocks) - 1)
        i = j + 1
    segments.append("".join(buf))
    return segments, blocks


def _context(segments: list, idx: int) -> tuple:
    """Literal text before/after block ``idx`` (last/first CONTEXT_LINES lines)."""
    before = "".join(s for s in segments[:idx * 2 + 1] if isinstance(s, str))
    after = "".join(s for s in segments[idx * 2 + 2:] if isinstance(s, str))
    b = before.splitlines(keepends=True)[-CONTEXT_LINES:]
    a = after.splitlines(keepends=True)[:CONTEXT_LINES]
    return "".join(b), "".join(a)


# --------------------------------------------------------------------------- model answers

def parse_model_answer(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s, flags=re.S).strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and "resolution" in obj:
                return str(obj["resolution"])
        except ValueError:
            start, end = s.find("{"), s.rfind("}")
            if start != -1 and end > start:
                try:
                    obj = json.loads(s[start:end + 1])
                    if isinstance(obj, dict) and "resolution" in obj:
                        return str(obj["resolution"])
                except ValueError:
                    pass
    return raw if raw.endswith("\n") else raw + ("\n" if raw else "")


def normalize_resolution(text: str, *, ours: str, theirs: str) -> str:
    if text and not text.endswith("\n") and (ours.endswith("\n") or theirs.endswith("\n")):
        text += "\n"
    return text


def has_markers(text: str) -> bool:
    return any(l.startswith((CONFLICT_START, CONFLICT_BASE, CONFLICT_END)) for l in text.splitlines())


# --------------------------------------------------------------------------- callers

def _call_via_cmd(cmd: str, payload: dict) -> str:
    proc = subprocess.run(shlex.split(cmd), input=json.dumps(payload, ensure_ascii=False),
                          capture_output=True, text=True, encoding="utf-8", timeout=600)
    if proc.returncode != 0:
        raise ResolverError(f"resolver command failed ({proc.returncode}): {proc.stderr.strip()[-500:]}")
    return proc.stdout


def call_json_model(system: str, user: str) -> str:
    """One JSON-mode completion on the ``coding`` tier. Raises ResolverError.

    Mirrors hermes_cli.legal_review_gate._default_llm_call — the host-side model
    path the review gates already run in production. Shared with the gate triage
    (``upstream_sync_triage.py``) so there is exactly one place that knows how a
    host script reaches a model.
    """
    from hermes_cli.review_gate import resolve_reviewer_model
    from agent.auxiliary_client import resolve_provider_client

    resolved = resolve_reviewer_model(MODEL_TIER)
    provider, model = resolved["provider"], resolved["model"]
    client, resolved_model = resolve_provider_client(provider, model, raw_codex=False, async_mode=False)
    if client is None:
        raise ResolverError(f"unable to resolve client for {provider} / {model}")
    response = client.chat.completions.create(
        model=resolved_model or model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    choice = response.choices[0]
    content = getattr(getattr(choice, "message", None), "content", None)
    if not content:
        raise ResolverError("model returned empty content")
    return str(content)


def _call_via_model(payload: dict) -> str:
    user = json.dumps({k: payload[k] for k in ("path", "decision", "before", "ours", "base", "theirs",
                                                "after", "local_subjects", "previous_error", "attempt")},
                      ensure_ascii=False)
    return call_json_model(SYSTEM_PROMPT, user)


def default_call_model(payload: dict) -> str:
    cmd = os.environ.get("HERMES_SYNC_RESOLVER_CMD")
    if cmd:
        return _call_via_cmd(cmd, payload)
    return _call_via_model(payload)


# --------------------------------------------------------------------------- resolve

# A block this lopsided is a bad question rather than a hard one. On 2026-08-19
# git produced 211 lines ours against 1 line theirs: the actual disagreement was
# one signature, and the other 209 lines were a local feature that happened to
# sit above it with no anchor of its own. The model answered the question it was
# shown and dropped the rest. Conservative on purpose - the trade is a rare
# operator interruption against a lost apply cycle.
def _line_count(side) -> int:
    if isinstance(side, str):
        return len(side.splitlines())
    return len(side)


def _skew_limits() -> tuple:
    return (
        int(os.environ.get("HERMES_SYNC_MAX_BLOCK_LINES", "100")),
        int(os.environ.get("HERMES_SYNC_MAX_BLOCK_SKEW", "10")),
    )


def _too_lopsided(ours, theirs):
    """Return an explanation when the block should go to a human, else None.

    Measured in lines: the sides arrive as text, and character counts make a
    block of long lines look lopsided when it is not.
    """
    max_lines, max_skew = _skew_limits()
    n_ours, n_theirs = _line_count(ours), _line_count(theirs)
    big, small = max(n_ours, n_theirs), min(n_ours, n_theirs)
    if big + small < max_lines:
        return None
    if small and big / small < max_skew:
        return None
    if not small and big < max_lines:
        return None
    return (
        f"block too lopsided for the resolver: {n_ours} lines ours against "
        f"{n_theirs} lines theirs. The real disagreement is the smaller side; "
        f"the rest is unrelated code the merge dragged into this block. Resolve it "
        f"by hand, or raise HERMES_SYNC_MAX_BLOCK_SKEW to send it anyway."
    )


def resolve_text(text: str, *, path: str, decision: str, local_subjects=None, upstream_head: str = "",
                 call_model=None, max_attempts: int = 2) -> tuple:
    """Resolve every conflict block in ``text``. Returns (new_text, report).

    report = {"resolved": n, "failed": m, "errors": [..]}. If any block fails,
    new_text == text (whole-file rollback).
    """
    call = call_model or default_call_model
    segments, blocks = _parse_blocks(text)
    report = {"resolved": 0, "failed": 0, "errors": []}
    if not blocks:
        return text, report
    resolutions: list = []
    for idx, block in enumerate(blocks):
        before, after = _context(segments, idx)
        prev_error = None
        answer = None
        lopsided = _too_lopsided(block["ours"], block["theirs"])
        if lopsided:
            report["failed"] += 1
            report["errors"].append(f"hunk {idx + 1}: {lopsided}")
            resolutions.append(None)
            continue
        for attempt in range(1, max_attempts + 1):
            payload = {"path": path, "decision": decision, "before": before, "after": after,
                       "ours": block["ours"], "base": block["base"], "theirs": block["theirs"],
                       "local_subjects": list(local_subjects or []), "upstream_head": upstream_head,
                       "attempt": attempt, "previous_error": prev_error}
            try:
                raw = call(payload)
            except Exception as exc:  # the caller must never crash on a bad hunk
                prev_error = f"resolver error: {exc}"
                continue
            candidate = normalize_resolution(parse_model_answer(raw), ours=block["ours"], theirs=block["theirs"])
            if has_markers(candidate):
                prev_error = "the answer still contains conflict markers (<<<<<<< / ||||||| / >>>>>>>)"
                continue
            answer = candidate
            break
        if answer is None:
            report["failed"] += 1
            report["errors"].append(f"hunk {idx + 1}: {prev_error or 'no answer'}")
            resolutions.append(None)
        else:
            resolutions.append(answer)
            report["resolved"] += 1
    if report["failed"]:
        return text, report
    out = []
    for seg in segments:
        out.append(seg if isinstance(seg, str) else resolutions[seg])
    new_text = "".join(out)
    if path.endswith(".py"):
        try:
            ast.parse(new_text, filename=path)
        except SyntaxError as exc:
            report["failed"] = len(blocks)
            report["resolved"] = 0
            report["errors"].append(f"syntax error after resolution: {exc.msg} (line {exc.lineno})")
            return text, report
    return new_text, report


def resolve_file(file_path: Path, *, rel_path: str, decision: str, local_subjects=None,
                 upstream_head: str = "", call_model=None) -> dict:
    """Resolve one file on disk. Returns the report; writes only on full success."""
    text = file_path.read_text(encoding="utf-8", errors="surrogateescape")
    new_text, report = resolve_text(text, path=rel_path, decision=decision, local_subjects=local_subjects,
                                    upstream_head=upstream_head, call_model=call_model)
    if report["failed"] == 0 and report["resolved"] > 0:
        file_path.write_text(new_text, encoding="utf-8", errors="surrogateescape")
        report["written"] = True
    else:
        report["written"] = False
    return report


if __name__ == "__main__":  # manual probe: python3 upstream_sync_llm.py < hunk.json
    print(default_call_model(json.load(sys.stdin)))
