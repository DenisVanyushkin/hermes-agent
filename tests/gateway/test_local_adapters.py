"""Fork adapter behaviour wired into upstream v0.22.5's Slack and WhatsApp adapters.

Drives the real message handler / bridge attach of each adapter; only the
platform clients are doubles.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.slack.adapter import SlackAdapter


def _slack(**extra):
    a = SlackAdapter(PlatformConfig(enabled=True, token="***", extra=dict(extra)))
    a._app = MagicMock()
    a._app.client = AsyncMock()
    a._app.client.users_info = AsyncMock(return_value={
        "user": {"is_bot": False, "profile": {"display_name": "Denis"}, "real_name": "Denis"}})
    a._bot_user_id = "U_BOT"
    a._running = True
    a.handle_message = AsyncMock()
    return a


@pytest.fixture(autouse=True)
def _redirect_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path / "doc_cache")
    monkeypatch.setattr("gateway.platforms.base.VIDEO_CACHE_DIR", tmp_path / "video_cache")


@pytest.mark.asyncio
async def test_every_processed_message_gets_the_ack_reaction_not_only_addressed_ones():
    """A free-response channel message is processed without a mention; it is
    acked like a DM (upstream reacts only when directly addressed)."""
    adapter = _slack(free_response_channels=["C123"])

    await adapter._handle_slack_message({
        "text": "добавь в список", "user": "U_USER", "channel": "C123",
        "channel_type": "channel", "ts": "1700000000.000100",
    })

    adapter.handle_message.assert_awaited()
    assert "1700000000.000100" in adapter._reacting_message_ids


@pytest.mark.asyncio
async def test_a_reply_in_a_feedback_thread_is_consumed_before_the_agent():
    adapter = _slack()
    with patch("job_intel.cli.run_feedback_thread_reply",
               return_value=json.dumps({"status": "classified"})) as parse:
        await adapter._handle_slack_message({
            "text": "не подходит, это продажи", "user": "U_USER", "channel": "D1",
            "channel_type": "im", "ts": "1700000000.000200", "thread_ts": "1700000000.000001",
        })

    parse.assert_called_once()
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_thread_reply_the_feedback_loop_does_not_claim_reaches_the_agent():
    adapter = _slack()
    with patch("job_intel.cli.run_feedback_thread_reply",
               return_value=json.dumps({"status": "not_a_feedback_thread"})):
        await adapter._handle_slack_message({
            "text": "а что по погоде?", "user": "U_USER", "channel": "D1",
            "channel_type": "im", "ts": "1700000000.000300", "thread_ts": "1700000000.000001",
        })

    adapter.handle_message.assert_awaited()


def test_attaching_to_a_bridge_arms_the_reaction_poll_when_configured():
    from tests.gateway.test_whatsapp_reactions_dialogue import _make_adapter

    adapter = _make_adapter(reaction_dialogue=True)
    adapter._running = True
    adapter._profile_photo_path = None
    adapter._bridge_port = 1  # nothing listens: the loops just log and back off

    async def scenario():
        adapter._attach_to_bridge(None)
        try:
            assert adapter._reaction_poll_task is not None
        finally:
            adapter._running = False
            for task in (adapter._poll_task, adapter._reaction_poll_task):
                if task:
                    task.cancel()
            await asyncio.gather(adapter._poll_task, adapter._reaction_poll_task, return_exceptions=True)
            await adapter._http_session.close()

    asyncio.run(scenario())


def test_the_bridge_log_is_rotated_before_it_is_reopened():
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    source = inspect.getsource(WhatsAppAdapter)
    rotate = source.index("_rotate_bridge_log_if_large(self._bridge_log)")
    assert rotate < source.index('open(self._bridge_log, "a"', rotate)
