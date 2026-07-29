"""Dialogue path for WhatsApp emoji reactions.

A reaction that the ack hook did not consume becomes an ordinary agent
turn, carrying the text of the message it reacted to in the standard
reply_to_* fields (spec: reactions-dialogue, 2026-07-29).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig
from plugins.platforms.whatsapp.adapter import WhatsAppAdapter


def _make_adapter(**extra):
    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = PlatformConfig(enabled=True, extra=dict(extra))
    adapter._reaction_hook_cmd = extra.get("reaction_hook_cmd") or None
    adapter._reaction_dialogue = bool(extra.get("reaction_dialogue", False))
    adapter._reaction_poll_task = None
    adapter._message_handler = AsyncMock()
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    return adapter


def test_polling_not_armed_when_both_flags_off():
    adapter = _make_adapter()
    asyncio.run(_arm(adapter))
    assert adapter._reaction_poll_task is None


def test_polling_armed_by_hook_cmd_alone():
    adapter = _make_adapter(reaction_hook_cmd=["/bin/true"])
    asyncio.run(_arm(adapter))
    assert adapter._reaction_poll_task is not None
    adapter._reaction_poll_task.cancel()


def test_polling_armed_by_dialogue_flag_alone():
    """The dialogue path must work with no ack hook configured at all."""
    adapter = _make_adapter(reaction_dialogue=True)
    asyncio.run(_arm(adapter))
    assert adapter._reaction_poll_task is not None
    adapter._reaction_poll_task.cancel()


async def _arm(adapter):
    adapter._running = False          # the loop exits immediately
    adapter._http_session = None
    adapter._start_reaction_polling()
