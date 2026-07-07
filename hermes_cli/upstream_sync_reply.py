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
    """Return True when ``<state_dir>/pending.json`` awaits an operator decision."""
    pending = Path(state_dir) / "pending.json"
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("status") == "awaiting_decision"
