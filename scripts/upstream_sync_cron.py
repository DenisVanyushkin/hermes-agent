#!/usr/bin/env python3
"""The scheduled upstream-sync run, host-owned. Runs as the hermes user from
cron in script mode (no agent). Reads the preflight, decides, acts, and says
nothing on stdout — cron delivers stdout verbatim, and the operator-facing
messages go to Slack from here with a thread we can follow up in.

Branches:
  * an armed gate exists
      - fully decided            → resume: request apply-decisions
      - awaiting the operator    → threaded reminder, at most once a day
      - a finalize is in flight  → leave it alone
  * upstream already merged      → nothing
  * clean merge                  → request sync (backup, merge, tests, publish)
  * conflicts                    → group → memory → policy; write pending.json;
                                   nothing to ask → post "applying" + request
                                   apply-decisions; otherwise post the question

Everything the finalizer needs is in the state dir; everything the operator
needs is in the thread. A failure of THIS script (not of the sync) exits
non-zero with a message, which cron delivers as an alert.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import upstream_sync_slack as slack  # noqa: E402
from upstream_sync_decisions import group_features, load_memory  # noqa: E402
from upstream_sync_policy import decide_features, needs_operator, number_features  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
DEFAULT_STATE = os.environ.get(
    "HERMES_SYNC_STATE_DIR",
    str(HERMES_HOME / "state/upstream-sync"),
)
DEFAULT_PREFLIGHT = os.environ.get(
    "HERMES_SYNC_PREFLIGHT_CMD",
    str(HERE / "preflight-local-customizations-update.sh"),
)
DEFAULT_CHANNEL = os.environ.get("HERMES_SYNC_SLACK_CHANNEL", "C0B3X1E5SJZ")
REMIND_EVERY_HOURS = 24

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_preflight(cmd: str) -> dict:
    proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True, encoding="utf-8", timeout=900)
    if proc.returncode != 0:
        raise SystemExit(f"upstream-sync: preflight failed (rc={proc.returncode}): {proc.stderr.strip()[-800:]}")
    fences = _JSON_FENCE.findall(proc.stdout)
    if not fences:
        raise SystemExit("upstream-sync: preflight printed no ```json block")
    return json.loads(fences[-1])


def request(state: Path, action: str, **fields) -> None:
    payload = {"action": action, "requested_at": _now(), **fields}
    _write_json(state / "finalize-request.json", payload)


def in_flight(state: Path) -> bool:
    return any((state / n).exists() for n in ("finalize-request.json", "finalize-request.processing.json"))


def _post(channel: str, text: str, thread_ts=None) -> str:
    try:
        return slack.post(channel, text, thread_ts=thread_ts)
    except slack.SlackError as exc:
        log(f"upstream-sync: slack post failed: {exc}")
        return ""


# --------------------------------------------------------------------------- branches

def handle_armed_gate(state: Path, pending: dict, dry_run: bool) -> int:
    feats = pending.get("features", [])
    undecided = [f for f in feats if not f.get("decision")]
    if in_flight(state):
        log("upstream-sync: a finalize is in flight; leaving the armed gate alone")
        return 0
    if not undecided:
        log("upstream-sync: gate fully decided → resuming apply-decisions")
        if not dry_run:
            request(state, "apply-decisions", origin="cron-resume")
        else:
            print("would request apply-decisions (resume of a decided gate)")
        return 0
    last = pending.get("reminded_at") or ""
    if last:
        try:
            age = _dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(last)
            if age.total_seconds() < REMIND_EVERY_HOURS * 3600:
                log("upstream-sync: reminder throttled")
                return 0
        except ValueError:
            pass
    text = slack.reminder_text(pending)
    if dry_run:
        print("would post reminder:\n" + text)
        return 0
    _post(pending.get("slack_channel") or DEFAULT_CHANNEL, text, pending.get("slack_thread_ts"))
    pending["reminded_at"] = _now()
    _write_json(state / "pending.json", pending)
    return 0


def handle_armed_triage(state: Path, triage: dict, dry_run: bool) -> int:
    """A test-gate proposal waiting for `apply fix` / `keep test`.

    This is checked BEFORE the decision gate. After a red gate pending.json is
    still on disk and fully decided, so the decision branch would read it as
    "decided → resume apply-decisions" and re-run the whole apply against the
    same merge that just failed the gate — answering a question the operator has
    already been asked a better version of.
    """
    if in_flight(state):
        log("upstream-sync: a finalize is in flight; leaving the armed triage alone")
        return 0
    last = triage.get("reminded_at") or ""
    if last:
        try:
            age = _dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(last)
            if age.total_seconds() < REMIND_EVERY_HOURS * 3600:
                log("upstream-sync: triage reminder throttled")
                return 0
        except ValueError:
            pass
    text = slack.triage_reminder_text(triage)
    if dry_run:
        print("would post triage reminder:\n" + text)
        return 0
    _post(triage.get("slack_channel") or DEFAULT_CHANNEL, text, triage.get("slack_thread_ts"))
    triage["reminded_at"] = _now()
    _write_json(state / "gate-triage.json", triage)
    return 0


def handle_conflicts(state: Path, pf: dict, channel: str, dry_run: bool) -> int:
    memory = load_memory(state / "decision-memory.json")
    features = group_features(pf.get("conflicts", []))
    decided = number_features(decide_features(features, memory))
    ask = needs_operator(decided)
    pending = {
        "schema": "upstream-sync-pending/v1",
        "status": "awaiting_decision" if ask else "auto_apply",
        "created_at": _now(),
        "local_head": pf.get("head"), "upstream_head": pf.get("upstream_head"),
        "merge_base": pf.get("merge_base"),
        "upstream_ahead": pf.get("upstream_ahead"), "local_ahead": pf.get("local_ahead"),
        "features": decided,
        "slack_channel": channel, "slack_thread_ts": None,
    }
    text = slack.report_text(pending)
    if dry_run:
        print("would write pending.json:\n" + json.dumps(pending, indent=1, ensure_ascii=False))
        print("would post:\n" + text)
        print("would request apply-decisions" if not ask else "would wait for the operator")
        return 0
    ts = _post(channel, text)
    pending["slack_thread_ts"] = ts or None
    _write_json(state / "pending.json", pending)
    if ask:
        log(f"upstream-sync: {sum(1 for f in decided if not f.get('decision'))} feature(s) await the operator")
        return 0
    request(state, "apply-decisions", origin="cron-auto")
    log("upstream-sync: all conflicts decided by policy/memory → apply-decisions requested")
    return 0


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="host-owned upstream-sync scheduled run")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--preflight-cmd", default=DEFAULT_PREFLIGHT)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--dry-run", action="store_true", help="print what would happen; write nothing, post nothing")
    args = parser.parse_args(argv)
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)

    pf = run_preflight(args.preflight_cmd)
    log(f"upstream-sync: preflight risk={pf.get('risk')} upstream_ahead={pf.get('upstream_ahead')} "
        f"conflicts={len(pf.get('conflicts') or [])} pending={pf.get('pending_decision_present')}")

    triage_path = state / "gate-triage.json"
    if triage_path.exists():
        try:
            triage = json.loads(triage_path.read_text(encoding="utf-8"))
        except ValueError:
            triage = {}
        if triage.get("status") == "awaiting_triage":
            return handle_armed_triage(state, triage, args.dry_run)

    pending_path = state / "pending.json"
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
        except ValueError:
            pending = {}
        if pending:
            return handle_armed_gate(state, pending, args.dry_run)

    if in_flight(state):
        log("upstream-sync: a finalize is in flight; not starting another sync")
        return 0
    if int(pf.get("upstream_ahead") or 0) == 0:
        log("upstream-sync: nothing new upstream")
        return 0
    conflicts = pf.get("conflicts") or []
    if not conflicts:
        if args.dry_run:
            print(f"would request sync for upstream {pf.get('upstream_head')}")
            return 0
        request(state, "sync", upstream_sha=pf.get("upstream_head"), origin="cron")
        log(f"upstream-sync: clean → sync requested for {pf.get('upstream_head')}")
        return 0
    return handle_conflicts(state, pf, args.channel, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
