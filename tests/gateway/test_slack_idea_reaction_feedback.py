"""👍 idea capture must tell the operator what happened, not only the log.

Before 2026-08-20 every capture failed silently for two months: the only trace
was a logger.exception line nobody reads. Success and failure now both leave a
reaction on the idea post.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.platforms.slack.adapter import SlackAdapter

EVENT = {
    "type": "reaction_added",
    "reaction": "+1",
    "user": "UDENIS",
    "team": "T1",
    "event_ts": "999.001",
    "item": {"type": "message", "channel": "C123", "ts": "111.222"},
}

BOT_MESSAGE = {"text": "Cronjob Response: idle-idea-prompt — some idea", "bot_id": "B1"}


def _adapter() -> SlackAdapter:
    adapter = SlackAdapter.__new__(SlackAdapter)
    adapter._channel_team = {"C123": "T1"}
    adapter._team_bot_user_ids = {"T1": "UBOT"}
    adapter._bot_user_id = "UBOT"
    adapter._add_reaction = AsyncMock(return_value=True)
    client = MagicMock()
    client.conversations_history = AsyncMock(return_value={"messages": [dict(BOT_MESSAGE)]})
    adapter._get_client = MagicMock(return_value=client)
    return adapter


def _run(adapter, monkeypatch, process_event):
    monkeypatch.setenv("SLACK_IDEA_REACTION_CHANNELS", "C123")
    monkeypatch.setenv("SLACK_IDEA_DOC_ID", "doc-123")
    monkeypatch.setenv("SLACK_IDEA_REACTION_EMOJIS", "+1,thumbsup")
    with patch("job_intel.idea_reaction_capture.process_event", process_event):
        asyncio.run(adapter._handle_slack_idea_reaction_event(dict(EVENT)))


def _reacted(adapter) -> list[str]:
    return [call.args[2] for call in adapter._add_reaction.await_args_list]


def test_successful_capture_marks_the_idea_post(monkeypatch):
    adapter = _adapter()
    _run(adapter, monkeypatch, MagicMock(return_value={"status": "ok", "doc_id": "doc-123"}))
    assert _reacted(adapter) == ["memo"]


def test_failed_capture_marks_the_idea_post(monkeypatch):
    adapter = _adapter()
    boom = MagicMock(side_effect=RuntimeError("Not authenticated"))
    with pytest.raises(RuntimeError):
        _run(adapter, monkeypatch, boom)
    assert _reacted(adapter) == ["x"]


def test_ignored_capture_leaves_no_reaction(monkeypatch):
    adapter = _adapter()
    ignored = MagicMock(return_value={"status": "ignored", "reason": "duplicate"})
    _run(adapter, monkeypatch, ignored)
    assert _reacted(adapter) == []
