"""Detect operator upstream-sync merge decisions in a Slack thread reply.

The upstream-sync conflict report asks the operator to reply with one decision
per feature, e.g. ``1: merge both, 2: merge both, 3: keep local``. The gateway
uses these helpers to recognize such a reply and check that a decision is
actually pending, so it can route the reply to the upstream-sync skill instead
of the generic pipeline orchestrator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

_VALID_OPTIONS = ("merge both", "keep local", "take upstream")

# Matches ``<n>: <option>`` where option is one of the allowed phrases. The
# option is captured greedily up to the phrase boundary; surrounding prose and
# separators (commas, newlines, "and") are tolerated.
_DECISION_RE = re.compile(
    r"(\d+)\s*:\s*(merge\s+both|keep\s+local|take\s+upstream)",
    re.IGNORECASE,
)


def parse_upstream_sync_decision_reply(text: Optional[str]) -> Optional[dict[int, str]]:
    """Parse an operator decision reply into ``{feature_id: option}``.

    Returns ``None`` when the text contains no recognizable ``N: <option>``
    pair, so a plain conversational message is never mistaken for a decision.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    decisions: dict[int, str] = {}
    for raw_id, raw_option in _DECISION_RE.findall(text):
        try:
            feature_id = int(raw_id)
        except ValueError:
            continue
        option = " ".join(raw_option.lower().split())
        if option in _VALID_OPTIONS:
            decisions[feature_id] = option

    return decisions or None


def has_pending_upstream_decision(state_dir: Path | str) -> bool:
    """Return True when ``<state_dir>/pending.json`` awaits an operator decision.

    The gateway runs as an unprivileged user, but the sandbox writes
    pending.json under a root-0700 home the gateway user cannot even traverse.
    When the status can't be read (PermissionError anywhere in the path), assume
    a decision is pending -- the queued one-shot re-checks the authoritative
    status as root inside the sandbox. The narrow decision-reply pattern is the
    primary gate, so this cannot fire on ordinary messages.
    """
    pending = Path(state_dir) / "pending.json"
    try:
        raw = pending.read_text(encoding="utf-8")
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("status") == "awaiting_decision"

import os

_SANDBOX_STATE_SUFFIX = "sandboxes/docker/default/home/.hermes/state/upstream-sync"


def default_upstream_sync_state_dir() -> Path:
    """Resolve the host-side upstream-sync state dir.

    Mirrors upstream-sync-finalize.sh: honor ``HERMES_SYNC_STATE_DIR`` when set,
    else derive from ``HERMES_HOME`` (the sandbox `/root` is bind-mounted from
    ``$HERMES_HOME/sandboxes/docker/default/home``).
    """
    override = os.getenv("HERMES_SYNC_STATE_DIR")
    if override:
        return Path(override)
    hermes_home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    return hermes_home / _SANDBOX_STATE_SUFFIX


def build_upstream_sync_decision_job_spec(
    message: str,
    source: dict,
    decisions: dict[int, str],
) -> dict:
    """Build ``create_job`` kwargs for a one-shot upstream-sync Mode B apply.

    The operator reply is carried verbatim into the prompt so the skill matches
    decisions to feature ids; ``role="engineer"`` pins the role (bypassing the
    keyword cascade), and ``deliver="origin"`` routes the report back to the
    reply thread.
    """
    decision_line = ", ".join(f"{fid}: {opt}" for fid, opt in sorted(decisions.items()))
    prompt = (
        "Operator has replied with upstream-sync merge decisions. "
        "Load the upstream-sync skill Mode B: read pending.json and apply these "
        f"decisions, then finalize.\n\nOperator decisions: {decision_line}\n\n"
        f"Original reply:\n{message}"
    )
    origin = {
        "platform": source.get("platform"),
        "chat_id": source.get("chat_id"),
        "thread_id": source.get("thread_id"),
        "user_id": source.get("user_id"),
    }
    return {
        "prompt": prompt,
        "schedule": "1m",
        "name": "upstream-sync apply (operator decision)",
        "skills": ["upstream-sync"],
        "role": "engineer",
        "deliver": "origin",
        "origin": origin,
    }
