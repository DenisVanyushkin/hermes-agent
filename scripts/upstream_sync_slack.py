#!/usr/bin/env python3
"""Slack side of the host-owned upstream-sync: posting and message texts.

Two responsibilities, both host-side and stdlib-only:

* ``post(channel, text, thread_ts=None) -> ts`` — one chat.postMessage call.
  The token comes from ``SLACK_BOT_TOKEN`` or ``~/.hermes/.env`` (the same
  lookup the finalizer's curl uses). ``HERMES_SYNC_SLACK_CMD`` replaces the
  transport with a command (payload JSON on stdin, ts on stdout) so bash
  callers and tests never touch the network.
* Composers — pure functions from state files to operator-facing text: the
  conflict report, the reminder, the applied/failed summaries. Everything the
  operator reads is asserted in tests, not discovered in the channel.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SLACK_API = "https://slack.com/api/chat.postMessage"
DEFAULT_ENV_FILE = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / ".env"

_TOKEN_LINE = re.compile(r'^\s*(?:export\s+)?SLACK_BOT_TOKEN=(["\']?)(.*?)\1\s*$')


class SlackError(RuntimeError):
    pass


# --------------------------------------------------------------------------- transport

def read_token(env_file: Path | str | None = None) -> str:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if token:
        return token
    path = Path(env_file) if env_file else DEFAULT_ENV_FILE
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _TOKEN_LINE.match(line)
            if m:
                token = m.group(2).strip()
    except OSError:
        pass
    if not token:
        raise SlackError(f"no Slack token: SLACK_BOT_TOKEN unset and none in {path}")
    return token


def _http_post(url: str, payload: dict, token: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cmd_post(cmd: str, payload: dict) -> str:
    proc = subprocess.run(
        shlex.split(cmd), input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if proc.returncode != 0:
        raise SlackError(f"HERMES_SYNC_SLACK_CMD failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def post(channel: str, text: str, thread_ts: str | None = None, *,
         token: str | None = None, env_file=None, http=None) -> str:
    """Post one message; return its ts. Raises SlackError on any failure."""
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    override = os.environ.get("HERMES_SYNC_SLACK_CMD")
    if override and http is None:
        return _cmd_post(override, payload)
    token = token or read_token(env_file)
    try:
        reply = (http or _http_post)(SLACK_API, payload, token)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise SlackError(f"slack transport error: {exc}") from exc
    if not reply.get("ok"):
        raise SlackError(f"slack api error: {reply.get('error', 'unknown')}")
    return str(reply.get("ts", ""))


# --------------------------------------------------------------------------- composers

def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _short(sha) -> str:
    return str(sha or "")[:10]


def _feature_line(f: dict) -> str:
    files = ", ".join(f"`{p}`" for p in f.get("files", []))
    subj = f.get("local_subjects") or []
    hint = f"; local: {subj[0]}" + (f" (+{len(subj) - 1})" if len(subj) > 1 else "") if subj else ""
    return f"- *{f.get('id')}* {files}{hint}"


def report_text(pending: dict) -> str:
    feats = pending.get("features", [])
    asked = [f for f in feats if f.get("status") == "awaiting_decision" or not f.get("decision")]
    auto = [f for f in feats if f not in asked]
    lines = [
        "*Upstream sync — conflicts against upstream "
        f"`{_short(pending.get('upstream_head'))}`*",
        f"Upstream: {_fmt_int(pending.get('upstream_ahead', '?'))} commits waiting · "
        f"local: {_fmt_int(pending.get('local_ahead', '?'))} since the merge base · "
        f"conflicting files: {sum(len(f.get('files', [])) for f in feats)}.",
        "",
    ]
    if auto:
        lines.append("*Resolved automatically* (no reply needed):")
        for f in auto:
            lines.append(f"{_feature_line(f)} → `{f.get('decision')}` [{f.get('source') or 'policy'}]")
        lines.append("")
    if asked:
        lines.append("*Your decision needed* (security-sensitive paths):")
        for f in asked:
            lines.append(_feature_line(f))
        lines.append("")
        lines.append("Reply in this thread, one per line, e.g.:")
        lines.append("```")
        for f in asked:
            lines.append(f"{f.get('id')}: merge-both")
        lines.append("```")
        lines.append("Options: `merge-both` / `keep-local` / `take-upstream`. "
                     "The apply starts as soon as every listed feature has an answer.")
    else:
        lines.append("No decision needed — applying automatically. The result lands in this thread.")
    return "\n".join(lines).rstrip() + "\n"


def reminder_text(pending: dict) -> str:
    asked = [f for f in pending.get("features", [])
             if f.get("status") == "awaiting_decision" or not f.get("decision")]
    lines = ["*Upstream sync is still waiting for your decision* on "
             f"upstream `{_short(pending.get('upstream_head'))}`:", ""]
    for f in asked:
        lines.append(_feature_line(f))
    lines += ["", "Reply in this thread, one per line:", "```"]
    lines += [f"{f.get('id')}: merge-both" for f in asked]
    lines += ["```", "Options: `merge-both` / `keep-local` / `take-upstream`."]
    return "\n".join(lines) + "\n"


def _resolution_summary(prep: dict) -> list[str]:
    out = []
    auto = prep.get("auto_resolved") or []
    llm = prep.get("llm_resolved") or []
    manual = [m.get("path") if isinstance(m, dict) else m for m in (prep.get("needs_manual") or [])]
    if auto:
        out.append(f"- mechanically resolved: {', '.join(f'`{p}`' for p in auto)}")
    if llm:
        out.append(f"- resolved by the model, hunk by hunk: {', '.join(f'`{p}`' for p in llm)}")
    left = [p for p in manual if p not in llm]
    if left:
        out.append(f"- resolved by hand: {', '.join(f'`{p}`' for p in left)}")
    return out


def applied_text(prep: dict, result: dict) -> str:
    lines = [
        f"*Upstream sync applied* — upstream `{_short(prep.get('upstream_head'))}` "
        f"merged into `local/customizations` (was `{_short(prep.get('local_base'))}`).",
        f"Conflicting files: {len(prep.get('conflicts') or [])}.",
    ]
    lines += _resolution_summary(prep)
    lines += [
        f"- backup ref: `{result.get('backup_ref') or 'n/a'}`",
        "- fork tests: no new failures · smoketest: passed · gateway restarted · pushed",
        f"- decisions recorded into memory · finished {result.get('finished_at', '')}",
    ]
    return "\n".join(lines) + "\n"


MAX_PATCH_CHARS = 2500

_VERDICT_LABEL = {
    "test_outdated": "the test is stale — upstream changed the contract",
    "behaviour_lost": "the merge lost local behaviour — this is a real regression",
    "unsure": "undecided — no safe patch to offer",
}


def _proposal_lines(p: dict) -> list[str]:
    ids = ", ".join(f"`{i}`" for i in (p.get("test_ids") or [])) or f"`{p.get('test_file')}`"
    kind = "fork test" if p.get("test_kind") == "fork" else "upstream test"
    verdict = str(p.get("verdict") or "unsure")
    lines = [f"*{p.get('test_file')}* ({kind}) — {ids}",
             f"- verdict: `{verdict}` — {_VERDICT_LABEL.get(verdict, '')}"]
    if p.get("explanation"):
        lines.append(f"- {p['explanation']}")
    if p.get("assertion_delta"):
        lines.append(f"- assertions: {p['assertion_delta']}")
    excerpt = (p.get("excerpt") or "").strip()
    if excerpt:
        tail = "\n".join(excerpt.splitlines()[-6:])
        lines += ["```", tail[-800:], "```"]
    patch = p.get("patch") or ""
    if patch:
        shown = patch if len(patch) <= MAX_PATCH_CHARS else (
            patch[:MAX_PATCH_CHARS] + f"\n... [truncated — full patch in `gate-triage.json`]\n")
        lines += ["- proposed new contents of the test file:", "```", shown, "```"]
    elif p.get("rejected_reason"):
        lines.append(f"- no patch offered: {p['rejected_reason']}")
    return lines


def triage_text(triage: dict) -> str:
    """The proposal the operator answers with one word.

    The reply parser matches the WHOLE message, so this must print the literal
    accepted words — an operator who paraphrases ("ok, apply fix") gets no
    reaction at all, which is exactly the confusion the ops gate has trained us
    to pre-empt in the message itself.
    """
    proposals = triage.get("proposals") or []
    has_patch = any(p.get("patch") for p in proposals)
    lines = [
        "*Upstream sync — the fork tests went red on this merge* "
        f"(`{_short(triage.get('merge_sha'))}`). Nothing has been applied.",
        "Either the test is stale (upstream changed the contract) or the merge dropped local "
        "behaviour and the test is the alarm. Triage below — the call is yours.",
        "",
    ]
    for p in proposals:
        lines += _proposal_lines(p)
        lines.append("")
    if has_patch:
        lines += [
            "Reply in this thread with *exactly one* of these words — the whole message, "
            "nothing before or after it:",
            "```",
            "apply fix",
            "keep test",
            "```",
            "`apply fix` — amend the merge with the patch above and re-run the gate (one attempt). "
            "`keep test` — the regression is real: nothing is applied, the merge clone is kept "
            "for you.",
        ]
    else:
        lines += [
            "No patch is offered, so there is nothing to approve. Reply `keep test` to close the "
            "gate and take the merge clone yourself:",
            "```",
            "keep test",
            "```",
        ]
    lines.append("Full triage (untruncated patches and evidence): `gate-triage.json` in the state dir.")
    return "\n".join(lines).rstrip() + "\n"


def triage_reminder_text(triage: dict) -> str:
    lines = ["*Upstream sync is still waiting for your call on the red test gate* "
             f"(`{_short(triage.get('merge_sha'))}`):", ""]
    for p in triage.get("proposals") or []:
        verdict = str(p.get("verdict") or "unsure")
        lines.append(f"- `{p.get('test_file')}` → `{verdict}`"
                     + (" (patch ready)" if p.get("patch") else " (diagnosis only)"))
    lines += ["", "Reply with exactly `apply fix` or `keep test` (the whole message)."]
    return "\n".join(lines) + "\n"


_GATE_SOURCE_LABEL = {
    "baseline": "baseline (before merge)",
    "merged": "post (after merge)",
    "upstream_parent": "upstream-parent probe",
}


def _gate_source_label(source: str) -> str:
    return _GATE_SOURCE_LABEL.get(source, source or "unknown run")


def gate_report_text(failures: dict) -> str:
    """Render the persisted node-aware gate outcome for the operator.

    Baseline is explicitly informational; only post-merge buckets can block.
    An unreadable run is an infrastructure ``UNKNOWN`` and must never look
    like a clean run or a merge regression.
    """
    common = failures.get("common_path") or []
    post_only = failures.get("post_only_path") or []
    pre_existing = failures.get("pre_existing") or []
    unknown = failures.get("unknown") or []
    unreadable = failures.get("unreadable_runs") or []
    blocking = failures.get("blocking_failures") or []

    lines = [
        "*Fork test gate*",
        "- baseline (before merge): informational only; never blocks.",
        "- post (after merge): admission result; blocking buckets come from this run.",
        f"- common_path: {len(common)}",
        f"- post_only_path: {len(post_only)}",
        f"- pre_existing: {len(pre_existing)} (informational)",
    ]
    for label, items in (("common_path", common), ("post_only_path", post_only)):
        if items:
            lines.append(f"- {label} failures:")
            for item in items:
                lines.append(f"  - `{item.get('nodeid') or item.get('path') or 'unknown outcome'}`")
    if unreadable:
        lines += [
            "- verdict: `UNKNOWN` — gate infrastructure failure; this is not a merge regression.",
            "- unreadable runs:",
        ]
        for run in unreadable:
            lines.append(
                f"  - {_gate_source_label(str(run.get('source') or ''))} "
                f"stage `{run.get('stage') or 'unknown'}`"
            )
    elif unknown:
        lines += [
            "- verdict: `UNKNOWN` — gate outcome could not be classified; this is not a merge regression.",
            f"- unknown outcomes: {len(unknown)}",
        ]
    elif blocking:
        lines.append("- verdict: `BLOCK` — new failures were found in the merged tree.")
    else:
        lines.append("- verdict: `PASS` — clean; no new blocking failures.")

    for item in unknown:
        nodeid = item.get("nodeid") or item.get("path") or "unknown outcome"
        lines.append(
            f"  - `{nodeid}` ({item.get('source', 'unknown')} / {item.get('stage', 'unknown')})"
        )
    return "\n".join(lines) + "\n"


def failed_text(
    prep: dict,
    result: dict,
    *,
    scratch: str = "",
    triage: dict | None = None,
    gate_failures: dict | None = None,
) -> str:
    stage = result.get("failed_stage") or "unknown"
    lines = [
        f"*Upstream sync NOT applied* — stage `{stage}` failed for upstream "
        f"`{_short(prep.get('upstream_head'))}`.",
    ]
    unresolved = prep.get("unresolved") or []
    if unresolved:
        lines.append("Files the model could not resolve (markers left in place):")
        for u in unresolved:
            if isinstance(u, dict):
                lines.append(f"- `{u.get('path')}` — {u.get('reason', '')}")
            else:
                lines.append(f"- `{u}`")
    # A red test gate has a diagnosis attached; showing the raw log tail instead
    # buries it. The triage carries the failing tests, the verdict and (maybe) a
    # patch the operator can approve with one word.
    gate_report = gate_report_text(gate_failures) if gate_failures else ""
    if stage == "test-gate" and (triage or {}).get("proposals"):
        return "\n".join(lines) + "\n\n" + gate_report + "\n" + triage_text(triage or {})
    if stage == "test-gate" and gate_report:
        return "\n".join(lines) + "\n\n" + gate_report
    detail = (result.get("detail") or "").strip()
    if detail and stage != "resolve":
        tail = "\n".join(detail.splitlines()[-8:])
        lines += ["```", tail[-1200:], "```"]
    lines += _resolution_summary(prep)
    lines.append(
        f"The live repository is untouched (or rolled back to `{result.get('backup_ref')}`)"
        if result.get("backup_ref") else "The live repository is untouched."
    )
    if scratch:
        lines.append(f"The merge clone is preserved at `{scratch}` and the decision is kept armed: "
                     "fix the files there, then re-run the apply "
                     "(`upstream-sync-finalize` action `apply-decisions`).")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="upstream-sync Slack helper")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_post = sub.add_parser("post", help="post a message; prints its ts")
    p_post.add_argument("--channel", required=True)
    p_post.add_argument("--text", help="message text (or --text-file)")
    p_post.add_argument("--text-file", help="read the text from this file")
    p_post.add_argument("--thread", default=None, help="thread_ts to reply under")
    args = parser.parse_args(argv)
    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    if not text:
        print("post: --text or --text-file required", file=sys.stderr)
        return 2
    try:
        print(post(args.channel, text, thread_ts=args.thread))
    except SlackError as exc:
        print(f"post failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
