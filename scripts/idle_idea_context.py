#!/usr/bin/env python3
"""Pre-run context + wake-gate for the ``idle-idea-prompt`` cron job.

Runs HOST-SIDE before the sandboxed agent (see cron/scheduler.py
``_run_job_script`` / ``_parse_wake_gate``). It does two things the
sandboxed agent cannot do reliably:

1. Deterministic wake-gate. Decides whether Denis has actually been idle
   for > IDLE_MINUTES and whether we are inside the Asia/Almaty active
   window. If not, the last stdout line is ``{"wakeAgent": false}`` and the
   scheduler skips the LLM run entirely (no cost, no delivery).

2. De-duplication memory. The agent used to keep its own history at
   ``~/.hermes/idle-ideas.json``, but that path lives on the container's
   per-task bind-mount and is discarded every run (see the #32049
   sandbox-mirror soft guard). Here we rebuild the history HOST-SIDE from
   the durable Slack log (#ideas / C0B55FPG5B7), cache it, and inject the
   already-used titles into the prompt so the model can diversify away
   from them.

Persistence is self-healing: every new idea is delivered to #ideas, so the
next wake picks it up from Slack automatically. The agent never writes state.

Fail-open philosophy: any error in the Slack/DB layer degrades to "wake with
whatever cached history we have" rather than silently suppressing ideas.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Tunables ---------------------------------------------------------------
IDLE_MINUTES = 60
ACTIVE_TZ = ZoneInfo("Asia/Almaty")
ACTIVE_START_HOUR = 9   # inclusive
ACTIVE_END_HOUR = 21    # exclusive
IDEAS_CHANNEL = "C0B55FPG5B7"
TOKEN_ENV_FILE = "/etc/job-intel/job-intel.env"
JOB_MARKER = "Cronjob Response: idle-idea-prompt"
MAX_TITLES_INJECTED = 80
SLACK_TIMEOUT = 12


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _cache_path() -> Path:
    return _hermes_home() / "idle-ideas.json"


def _state_db() -> Path:
    return _hermes_home() / "state.db"


def _gate(wake: bool) -> None:
    """Emit the wake-gate as the FINAL stdout line and exit 0."""
    print(json.dumps({"wakeAgent": wake}))
    sys.exit(0)


# --- Idle detection ---------------------------------------------------------
def last_activity_epoch() -> float | None:
    """Most recent genuine inbound-user message timestamp (epoch seconds).

    Genuine == role 'user' NOT injected by a cron job. Cron prompts all live
    in ``cron_*`` sessions, so excluding them isolates real Denis activity.
    Returns None if it cannot be determined (caller treats as "not idle" to
    stay conservative and silent).
    """
    db = _state_db()
    if not db.exists():
        return None
    uri = f"file:{urllib.parse.quote(str(db))}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            # Cron-injected prompts all live in ``cron_<id>`` sessions; genuine
            # inbound messages use timestamp-named sessions. Excluding anything
            # starting with "cron" isolates real Denis activity.
            row = con.execute(
                "SELECT MAX(timestamp) FROM messages "
                "WHERE role=? AND session_id NOT LIKE ?",
                ("user", "cron%"),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] is not None else None


def within_active_window(now_utc: datetime) -> bool:
    hour = now_utc.astimezone(ACTIVE_TZ).hour
    return ACTIVE_START_HOUR <= hour < ACTIVE_END_HOUR


# --- Slack idea history -----------------------------------------------------
def _slack_token() -> str | None:
    try:
        for line in Path(TOKEN_ENV_FILE).read_text().splitlines():
            line = line.strip()
            if line.startswith("SLACK_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _extract_title(text: str) -> str | None:
    """Pull the human idea title out of one delivered #ideas message."""
    if JOB_MARKER not in text:
        return None
    body = text.split("-------------", 1)[-1]
    # Strip a leaked role banner ("Hermes role: X ... Operation category: ...")
    body = re.sub(
        r"^\s*Hermes role:.*?Operation category:.*?\n", "", body, flags=re.S
    )
    for stop in ("To stop or manage", "\n:warning:"):
        body = body.split(stop)[0]
    for raw in body.splitlines():
        line = raw.strip().strip("*").strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("cron ", "cron job", "warning", ":warning:")) or "failed" in low:
            return None
        if line == "---":
            continue
        return line
    return None


def slack_idea_titles(token: str) -> list[str]:
    """Fetch #ideas history and return delivered idea titles (newest first)."""
    url = "https://slack.com/api/conversations.history?" + urllib.parse.urlencode(
        {"channel": IDEAS_CHANNEL, "limit": "200"}
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=SLACK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"slack conversations.history not ok: {data.get('error')}")
    titles: list[str] = []
    for msg in data.get("messages", []):
        t = _extract_title(msg.get("text", "") or "")
        if t:
            titles.append(t)
    return titles


def load_cache() -> list[str]:
    try:
        data = json.loads(_cache_path().read_text())
        return list(data.get("titles", []))
    except (OSError, json.JSONDecodeError):
        return []


def save_cache(titles: list[str]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": f"slack:{IDEAS_CHANNEL} live-refresh + backfill",
        "count": len(titles),
        "titles": titles,
    }
    tmp = _cache_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(_cache_path())


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(it.strip())
    return out


def merged_history() -> list[str]:
    """Union of live Slack titles + cached titles. Refresh cache on success."""
    cached = load_cache()
    token = _slack_token()
    if not token:
        return cached
    try:
        live = slack_idea_titles(token)
    except Exception:
        return cached  # fail-open: keep whatever we had
    merged = _dedup_keep_order(live + cached)
    try:
        save_cache(merged)
    except OSError:
        pass
    return merged


# --- Main -------------------------------------------------------------------
def main() -> None:
    now_utc = datetime.now(timezone.utc)

    if not within_active_window(now_utc):
        _gate(False)

    last = last_activity_epoch()
    if last is None:
        # Cannot confirm recent activity -> stay conservative and silent.
        _gate(False)
    idle_min = (now_utc.timestamp() - last) / 60.0
    if idle_min <= IDLE_MINUTES:
        _gate(False)

    # We are waking the agent: build the de-dup context block.
    history = merged_history()
    shown = history[:MAX_TITLES_INJECTED]

    print("### Idle-idea context (host-collected, authoritative)\n")
    print(
        f"Denis has been idle ~{int(idle_min)} min. Below are the "
        f"{len(history)} ideas ALREADY sent to him (newest first). Produce ONE "
        "genuinely NEW idea that is NOT in this list and NOT a rewording of "
        "any entry. Deliberately DIVERSIFY across life domains — not only "
        "job-search: e.g. health/habits, finance, learning, home/errands, "
        "relationships, trading, coding workflow, Hermes automations, travel. "
        "Do NOT try to read or write any local state file; this list is your "
        "complete memory.\n"
    )
    print("Already-sent ideas:")
    for i, t in enumerate(shown, 1):
        print(f"{i}. {t}")
    if len(history) > len(shown):
        print(f"... (+{len(history) - len(shown)} older)")
    # No JSON gate line here -> last line is prose -> agent wakes normally.


if __name__ == "__main__":
    main()
