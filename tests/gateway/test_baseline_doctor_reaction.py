"""🧹 baseline-doctor reaction runs the doctor; 📥/🙈/📦 applies an action."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.platforms.slack.adapter import SlackAdapter


def _adapter() -> SlackAdapter:
    adapter = SlackAdapter.__new__(SlackAdapter)
    adapter._bot_user_id = "UBOT"
    adapter._team_bot_user_ids = {}
    return adapter


def test_broom_by_operator_runs_doctor():
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock(return_value={
        "messages": [{"user": "UBOT", "bot_id": "B1",
                      "text": "final_verdict: autonomous_preflight_blocked"}]
    })
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.000"})
    adapter._get_client = lambda ch: client

    recorded = {}
    doctor_result = {"clean": False, "fixed": [], "remaining": [
        {"path": "scripts/x.py", "category": "untracked", "hint": "script"}]}
    with (
        patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}),
        patch("plugins.platforms.slack.adapter.run_baseline_doctor", return_value=doctor_result),
        patch("plugins.platforms.slack.adapter.record_pending",
              side_effect=lambda ts, rem: recorded.update({ts: rem})),
    ):
        event = {"type": "reaction_added", "reaction": "broom", "user": "UOP",
                 "item": {"channel": "C1", "ts": "111.222"}}
        asyncio.run(adapter._maybe_run_baseline_doctor(event))

    client.chat_postMessage.assert_awaited()
    assert recorded == {"999.000": [{"path": "scripts/x.py", "category": "untracked", "hint": "script"}]}


def test_broom_by_non_operator_ignored():
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock()
    adapter._get_client = lambda ch: client
    with patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}):
        event = {"type": "reaction_added", "reaction": "broom", "user": "UINTRUDER",
                 "item": {"channel": "C1", "ts": "111.222"}}
        asyncio.run(adapter._maybe_run_baseline_doctor(event))
    client.conversations_history.assert_not_awaited()


def test_action_reaction_applies_and_rechecks():
    adapter = _adapter()
    client = MagicMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "aaa"})
    adapter._get_client = lambda ch: client

    applied = {}

    def fake_apply(repo, action, remaining):
        applied["action"] = action
        return {"applied": action, "paths": ["scripts/x.py"], "ok": True, "detail": "done"}

    with (
        patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}),
        patch("plugins.platforms.slack.adapter.pop_pending",
              return_value=[{"path": "scripts/x.py", "category": "untracked"}]),
        patch("plugins.platforms.slack.adapter._apply_action", side_effect=fake_apply),
        patch("plugins.platforms.slack.adapter.run_baseline_doctor",
              return_value={"clean": True, "fixed": [], "remaining": []}),
    ):
        event = {"type": "reaction_added", "reaction": "see_no_evil", "user": "UOP",
                 "item": {"channel": "C1", "ts": "999.000"}}
        asyncio.run(adapter._maybe_apply_baseline_action(event))

    assert applied["action"] == "gitignore"
    client.chat_postMessage.assert_awaited()


def test_action_reaction_unknown_ts_ignored():
    adapter = _adapter()
    client = MagicMock()
    client.chat_postMessage = AsyncMock()
    adapter._get_client = lambda ch: client
    with (
        patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}),
        patch("plugins.platforms.slack.adapter.pop_pending", return_value=None),
    ):
        event = {"type": "reaction_added", "reaction": "package", "user": "UOP",
                 "item": {"channel": "C1", "ts": "unknown"}}
        asyncio.run(adapter._maybe_apply_baseline_action(event))
    client.chat_postMessage.assert_not_awaited()
