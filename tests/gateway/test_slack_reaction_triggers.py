"""Reaction 🔍/👍 on a tracked vacancy card injects a synthetic message."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.platforms.slack.adapter import SlackAdapter


def _adapter() -> SlackAdapter:
    adapter = SlackAdapter.__new__(SlackAdapter)
    adapter._channel_team = {"C123": "T1"}
    adapter._team_bot_user_ids = {"T1": "UBOT"}
    adapter._bot_user_id = "UBOT"
    adapter._handle_slack_message = AsyncMock()
    adapter._add_reaction = AsyncMock(return_value=True)
    return adapter


EVENT = {
    "type": "reaction_added",
    "reaction": "mag",
    "user": "UDENIS",
    "event_ts": "999.001",
    "item": {"type": "message", "channel": "C123", "ts": "111.222"},
}

MESSAGE_ROW = {
    "vacancy_id": 7,
    "title": "Head of Product",
    "company": "Acme",
    "canonical_url": "https://acme.example/jobs/1",
    "url": "https://acme.example/jobs/1",
}


def _run(adapter, event, *, message_row=MESSAGE_ROW, dedup=True):
    store = MagicMock()
    store.find_vacancy_message.return_value = message_row
    with (
        patch("job_intel.cli._store", return_value=store),
        patch("job_intel.cli._logical_aliases_for_channel_id", return_value=[]),
        patch("job_intel.reaction_triggers.should_process", return_value=dedup),
    ):
        asyncio.run(adapter._maybe_dispatch_vacancy_reaction_task(dict(event)))


def test_mag_injects_synthetic_evaluation_message():
    adapter = _adapter()
    _run(adapter, EVENT)
    adapter._handle_slack_message.assert_awaited_once()
    synthetic = adapter._handle_slack_message.await_args.args[0]
    assert synthetic["channel"] == "C123"
    assert synthetic["thread_ts"] == "111.222"
    assert synthetic["ts"] == "999.001"
    assert synthetic["user"] == "UDENIS"
    assert synthetic["text"].startswith("<@UBOT>")
    assert "русском" in synthetic["text"]


def test_plus_one_injects_package_message():
    adapter = _adapter()
    _run(adapter, dict(EVENT, reaction="+1"))
    synthetic = adapter._handle_slack_message.await_args.args[0]
    assert "application-package-orchestrator" in synthetic["text"]
    assert "English" in synthetic["text"]


def test_unrelated_reaction_is_noop():
    adapter = _adapter()
    _run(adapter, dict(EVENT, reaction="eyes"))
    adapter._handle_slack_message.assert_not_awaited()


def test_reaction_removed_is_noop():
    adapter = _adapter()
    _run(adapter, dict(EVENT, type="reaction_removed"))
    adapter._handle_slack_message.assert_not_awaited()


def test_untracked_message_is_noop():
    adapter = _adapter()
    _run(adapter, EVENT, message_row=None)
    adapter._handle_slack_message.assert_not_awaited()


def test_redelivered_event_is_noop():
    adapter = _adapter()
    _run(adapter, EVENT, dedup=False)
    adapter._handle_slack_message.assert_not_awaited()
