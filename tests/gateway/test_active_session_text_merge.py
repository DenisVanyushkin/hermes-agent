"""Regression tests for active-session TEXT follow-up queueing.

When the agent is actively running, rapid text follow-ups should survive as
one next-turn pending message instead of clobbering each other. In
``busy_text_mode=queue`` those active follow-ups first pass through a short
debounce so bursty multi-message thoughts are merged before the active drain
hands off the next turn.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Minimal telegram stub so importing gateway.platforms.base does not pull
# in the real python-telegram-bot dependency.
_tg = sys.modules.get("telegram") or types.ModuleType("telegram")
_tg.constants = sys.modules.get("telegram.constants") or types.ModuleType("telegram.constants")
_ct = MagicMock()
_ct.PRIVATE = "private"
_ct.GROUP = "group"
_ct.SUPERGROUP = "supergroup"
_tg.constants.ChatType = _ct
sys.modules.setdefault("telegram", _tg)
sys.modules.setdefault("telegram.constants", _tg.constants)
sys.modules.setdefault("telegram.ext", types.ModuleType("telegram.ext"))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    merge_pending_message_event,
)
from gateway.session import SessionSource, build_session_key


def _make_event(
    text: str,
    chat_id: str = "12345",
    *,
    chat_type: str = "dm",
    user_id: str = "u1",
    user_name: str | None = None,
    thread_id: str | None = None,
) -> MessageEvent:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        user_name=user_name,
        thread_id=thread_id,
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        message_id=f"msg-{text[:8]}",
    )


class _DummyAdapter(BasePlatformAdapter):  # type: ignore[misc]
    async def connect(self, *, is_reconnect: bool = False):
        pass

    async def disconnect(self):
        pass

    async def get_chat_info(self, chat_id):
        return None

    async def send(self, *args, **kwargs):
        return SendResult(success=True, message_id="x")


def _make_initialized_adapter() -> BasePlatformAdapter:
    return _DummyAdapter(PlatformConfig(enabled=True, token="***"), Platform.TELEGRAM)


def _make_adapter() -> BasePlatformAdapter:
    """Build a BasePlatformAdapter without running its heavy __init__."""
    adapter = object.__new__(_DummyAdapter)
    adapter.config = PlatformConfig(enabled=True, token="***")
    adapter.platform = Platform.TELEGRAM
    adapter._message_handler = AsyncMock(return_value=None)
    adapter._busy_session_handler = None
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    adapter._session_tasks = {}
    adapter._background_tasks = set()
    adapter._post_delivery_callbacks = {}
    adapter._expected_cancelled_tasks = set()
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._fatal_error_retryable = True
    adapter._fatal_error_handler = None
    adapter._running = True
    adapter._busy_text_mode = "queue"
    adapter._busy_text_debounce_seconds = 0.1
    adapter._busy_text_hard_cap_seconds = 1.0
    adapter._text_debounce = {}
    adapter._auto_tts_default = False
    adapter._auto_tts_enabled_chats = set()
    adapter._auto_tts_disabled_chats = set()
    adapter._typing_paused = set()
    return adapter


def _debounced_event(adapter: BasePlatformAdapter, session_key: str) -> MessageEvent:
    return adapter._text_debounce[session_key].event


@pytest.mark.asyncio
async def test_rapid_text_followups_accumulate_instead_of_replacing():
    """Rapid TEXT follow-ups must all survive in the pending event."""
    adapter = _make_adapter()
    adapter._busy_text_mode = ""  # direct-merge behavior, no debounce
    first = _make_event("part one")
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(_make_event("part two"))
    await adapter.handle_message(_make_event("part three"))

    pending = adapter._pending_messages[session_key]
    assert pending.text == "part two\npart three"
    assert not adapter._active_sessions[session_key].is_set()


@pytest.mark.asyncio
async def test_debounce_buffers_rapid_text_then_flushes_to_pending():
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 0.05

    first = _make_event("part one")
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(_make_event("part two"))
    assert session_key in adapter._text_debounce
    assert _debounced_event(adapter, session_key).text == "part two"
    assert session_key not in adapter._pending_messages

    await adapter.handle_message(_make_event("part three"))
    assert _debounced_event(adapter, session_key).text == "part two\npart three"

    await asyncio.sleep(0.15)

    assert session_key not in adapter._text_debounce
    assert adapter._pending_messages[session_key].text == "part two\npart three"


@pytest.mark.asyncio
async def test_debounce_resets_timer_on_new_arrival():
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 0.1

    first = _make_event("one")
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(first)
    task1 = adapter._text_debounce[session_key].task
    assert task1 is not None
    assert not task1.done()

    await adapter.handle_message(_make_event("two"))
    task2 = adapter._text_debounce[session_key].task
    assert task2 is not None
    assert task2 is not task1
    await asyncio.sleep(0)
    assert task1.cancelled() or task1.done()
    assert adapter._text_debounce[session_key].task is task2

    await adapter.handle_message(_make_event("three"))
    task3 = adapter._text_debounce[session_key].task
    assert task3 is not None
    assert task3 is not task2

    await asyncio.sleep(0.2)
    assert session_key not in adapter._text_debounce
    assert adapter._pending_messages[session_key].text == "one\ntwo\nthree"


@pytest.mark.asyncio
async def test_active_drain_force_flushes_debounce_before_release():
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0
    processed: list[str] = []

    async def _handler(event):
        processed.append(event.text)
        if event.text == "current":
            await adapter.handle_message(_make_event("follow up"))
        return None

    adapter._message_handler = _handler
    current = _make_event("current")
    session_key = build_session_key(current.source)

    task = asyncio.create_task(adapter._process_message_background(current, session_key))
    adapter._session_tasks[session_key] = task
    await asyncio.wait_for(task, timeout=1.0)

    for _ in range(20):
        if processed == ["current", "follow up"] and session_key not in adapter._active_sessions:
            break
        await asyncio.sleep(0.05)

    assert processed == ["current", "follow up"]
    assert session_key not in adapter._text_debounce
    assert session_key not in adapter._pending_messages
    assert session_key not in adapter._active_sessions


@pytest.mark.asyncio
async def test_force_flush_cancels_timer_without_duplicate_processing():
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 0.2

    event = _make_event("queued once")
    session_key = build_session_key(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(event)
    timer_task = adapter._text_debounce[session_key].task

    flushed = await adapter._flush_text_debounce_now(session_key)
    assert flushed is True
    assert session_key not in adapter._text_debounce
    assert adapter._pending_messages[session_key].text == "queued once"

    await asyncio.sleep(0.3)
    assert timer_task is not None
    assert timer_task.cancelled() or timer_task.done()
    assert adapter._pending_messages[session_key].text == "queued once"


@pytest.mark.asyncio
async def test_text_debounce_does_not_merge_different_senders():
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    first = _make_event(
        "from alice",
        chat_type="group",
        user_id="alice",
        user_name="Alice",
        thread_id="topic-1",
    )
    second = _make_event(
        "from bob",
        chat_type="group",
        user_id="bob",
        user_name="Bob",
        thread_id="topic-1",
    )
    session_key = build_session_key(first.source)
    assert session_key == build_session_key(second.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(first)
    await adapter.handle_message(second)

    assert adapter._pending_messages[session_key].text == "from alice"
    assert _debounced_event(adapter, session_key).text == "from bob"


@pytest.mark.asyncio
async def test_control_and_clarify_messages_bypass_text_debounce():
    adapter = _make_adapter()
    started: list[str] = []

    def _fake_start(event, session_key, *, interrupt_event=None):
        started.append(event.text)
        return True

    adapter._start_session_processing = _fake_start  # type: ignore[method-assign]

    await adapter.handle_message(_make_event("/status"))
    assert started == ["/status"]
    assert adapter._text_debounce == {}

    answer = _make_event("clarify answer")
    session_key = build_session_key(answer.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    adapter._message_handler = AsyncMock(return_value=None)

    with patch("tools.clarify_gateway.get_pending_for_session", return_value=object()):
        await adapter.handle_message(answer)

    adapter._message_handler.assert_awaited_once_with(answer)
    assert session_key not in adapter._text_debounce
    assert session_key not in adapter._pending_messages


@pytest.mark.asyncio
async def test_debounce_skipped_when_busy_text_mode_not_queue():
    adapter = _make_adapter()
    adapter._busy_text_mode = ""
    event = _make_event("direct merge")
    session_key = build_session_key(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(event)

    assert adapter._pending_messages[session_key].text == "direct merge"
    assert session_key not in adapter._text_debounce


def test_debounce_respects_env_var_override(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_TEXT_DEBOUNCE_SECONDS", "2.5")
    adapter = _make_initialized_adapter()
    assert adapter._busy_text_debounce_seconds == 2.5


@pytest.mark.asyncio
async def test_debounce_cleanup_in_cancel_background_tasks():
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    event = _make_event("cleanup test")
    session_key = build_session_key(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    await adapter.handle_message(event)

    assert session_key in adapter._text_debounce

    await adapter.cancel_background_tasks()

    assert session_key not in adapter._text_debounce


@pytest.mark.asyncio
async def test_single_followup_is_stored_as_is():
    adapter = _make_adapter()
    adapter._busy_text_mode = ""
    first = _make_event("only one")
    session_key = build_session_key(first.source)

    adapter._active_sessions[session_key] = asyncio.Event()
    await adapter.handle_message(first)

    pending = adapter._pending_messages[session_key]
    assert pending is first
    assert pending.text == "only one"
    assert not adapter._active_sessions[session_key].is_set()


def test_adapter_defaults_to_interrupt_mode(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_BUSY_TEXT_MODE", raising=False)
    adapter = _make_initialized_adapter()
    assert adapter._busy_text_mode == "interrupt"
    assert not adapter._is_queue_text_debounce_candidate(_make_event("hello"))


def test_adapter_is_queue_text_debounce_candidate_when_queue_set():
    # _make_adapter() pins _busy_text_mode="queue" to exercise debounce.
    adapter = _make_adapter()
    assert adapter._is_queue_text_debounce_candidate(_make_event("hello world"))


def test_command_messages_bypass_debounce_even_in_queue_mode():
    adapter = _make_adapter()
    assert not adapter._is_queue_text_debounce_candidate(_make_event(""))
    assert not adapter._is_queue_text_debounce_candidate(_make_event("/stop"))


def test_busy_text_mode_respects_env_var_override(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_TEXT_MODE", "interrupt")
    adapter = _make_initialized_adapter()
    assert adapter._busy_text_mode == "interrupt"
    assert not adapter._is_queue_text_debounce_candidate(_make_event("test"))


# ──────────────────────────────────────────────────────────────────────────
# Task 6.5: reply-quote coherence through busy-session merges.
#
# ``reply_to_message_id`` / ``reply_to_text`` / ``reply_to_is_own_message``
# are one coherent triple: they move together or not at all. A merged turn
# must never carry the id of one message beside the quoted text of another
# (run.py renders the quote only when id AND text are truthy, so an
# incoherent pair degrades quietly into a confidently wrong quote).
# ──────────────────────────────────────────────────────────────────────────


def _quote_triple(event: MessageEvent) -> tuple[str | None, str | None, bool]:
    return (
        event.reply_to_message_id,
        event.reply_to_text,
        event.reply_to_is_own_message,
    )


def _make_quoted_event(
    text: str,
    *,
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    reply_to_is_own_message: bool = False,
    message_id: str | None = "auto",
    message_type: MessageType = MessageType.TEXT,
    media_urls: list[str] | None = None,
    media_types: list[str] | None = None,
    chat_id: str = "12345",
    user_id: str = "u1",
) -> MessageEvent:
    """Build a MessageEvent with an explicit (possibly absent) reply quote."""
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        chat_type="dm",
        user_id=user_id,
    )
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=source,
        message_id=f"msg-{text[:8]}" if message_id == "auto" else message_id,
        media_urls=list(media_urls or []),
        media_types=list(media_types or []),
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
        reply_to_is_own_message=reply_to_is_own_message,
    )


def test_text_merge_adopts_incoming_reply_quote():
    """Pending without a quote + incoming with one -> incoming triple wins."""
    pending = {}
    existing = _make_quoted_event("part one")
    incoming = _make_quoted_event(
        "part two",
        reply_to_message_id="tgt-1",
        reply_to_text="quoted target one",
        reply_to_is_own_message=True,
    )
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    merged = pending["s"]
    assert merged.text == "part one\npart two"
    assert _quote_triple(merged) == ("tgt-1", "quoted target one", True)


def test_text_merge_keeps_existing_quote_when_incoming_has_none():
    """An unquoted follow-up must not erase the pending event's quote."""
    pending = {}
    existing = _make_quoted_event(
        "part one",
        reply_to_message_id="tgt-1",
        reply_to_text="quoted target one",
        reply_to_is_own_message=True,
    )
    incoming = _make_quoted_event("part two")
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    merged = pending["s"]
    assert merged.text == "part one\npart two"
    assert _quote_triple(merged) == ("tgt-1", "quoted target one", True)


def test_text_merge_prefers_latest_quote_when_both_quoted():
    """Two different quoted targets -> the later (incoming) triple wins whole."""
    pending = {}
    existing = _make_quoted_event(
        "part one",
        reply_to_message_id="tgt-1",
        reply_to_text="quoted target one",
        reply_to_is_own_message=True,
    )
    incoming = _make_quoted_event(
        "part two",
        reply_to_message_id="tgt-2",
        reply_to_text="quoted target two",
        reply_to_is_own_message=False,
    )
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    merged = pending["s"]
    assert _quote_triple(merged) == ("tgt-2", "quoted target two", False)


def test_photo_burst_merge_adopts_incoming_reply_quote():
    """PHOTO+PHOTO burst branch obeys the same adoption rule."""
    pending = {}
    existing = _make_quoted_event(
        "first shot",
        message_type=MessageType.PHOTO,
        media_urls=["/tmp/a.jpg"],
        media_types=["image"],
    )
    incoming = _make_quoted_event(
        "second shot",
        message_type=MessageType.PHOTO,
        media_urls=["/tmp/b.jpg"],
        media_types=["image"],
        reply_to_message_id="tgt-photo",
        reply_to_text="quoted photo target",
        reply_to_is_own_message=True,
    )
    merge_pending_message_event(pending, "s", existing)
    merge_pending_message_event(pending, "s", incoming)

    merged = pending["s"]
    assert merged.media_urls == ["/tmp/a.jpg", "/tmp/b.jpg"]
    assert _quote_triple(merged) == ("tgt-photo", "quoted photo target", True)


def test_media_merge_adopts_incoming_reply_quote():
    """The mixed media branch obeys the same adoption rule."""
    pending = {}
    existing = _make_quoted_event(
        "voice note",
        message_type=MessageType.VOICE,
        media_urls=["/tmp/a.ogg"],
        media_types=["audio"],
    )
    incoming = _make_quoted_event(
        "and a caption",
        reply_to_message_id="tgt-media",
        reply_to_text="quoted media target",
        reply_to_is_own_message=True,
    )
    merge_pending_message_event(pending, "s", existing)
    merge_pending_message_event(pending, "s", incoming)

    merged = pending["s"]
    assert merged.media_urls == ["/tmp/a.ogg"]
    assert _quote_triple(merged) == ("tgt-media", "quoted media target", True)


def test_media_merge_keeps_existing_quote_when_incoming_has_none():
    pending = {}
    existing = _make_quoted_event(
        "voice note",
        message_type=MessageType.VOICE,
        media_urls=["/tmp/a.ogg"],
        media_types=["audio"],
        reply_to_message_id="tgt-media",
        reply_to_text="quoted media target",
        reply_to_is_own_message=True,
    )
    incoming = _make_quoted_event("and a caption")
    merge_pending_message_event(pending, "s", existing)
    merge_pending_message_event(pending, "s", incoming)

    assert _quote_triple(pending["s"]) == ("tgt-media", "quoted media target", True)


@pytest.mark.parametrize(
    "existing_kwargs,incoming_kwargs",
    [
        ({}, {}),
        (
            {},
            {
                "reply_to_message_id": "tgt-2",
                "reply_to_text": "quoted two",
                "reply_to_is_own_message": True,
            },
        ),
        (
            {
                "reply_to_message_id": "tgt-1",
                "reply_to_text": "quoted one",
                "reply_to_is_own_message": True,
            },
            {},
        ),
        (
            {
                "reply_to_message_id": "tgt-1",
                "reply_to_text": "quoted one",
                "reply_to_is_own_message": True,
            },
            {
                "reply_to_message_id": "tgt-2",
                "reply_to_text": "quoted two",
                "reply_to_is_own_message": False,
            },
        ),
    ],
)
def test_merge_never_mixes_quote_fields_from_different_events(
    existing_kwargs, incoming_kwargs
):
    """Coherence invariant: the merged triple always comes from ONE event.

    Whatever the merge decides, the resulting (id, text, is_own) tuple must be
    byte-for-byte one of the two input triples -- never a hybrid.
    """
    pending = {}
    existing = _make_quoted_event("part one", **existing_kwargs)
    incoming = _make_quoted_event("part two", **incoming_kwargs)
    existing_triple = _quote_triple(existing)
    incoming_triple = _quote_triple(incoming)

    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    assert _quote_triple(pending["s"]) in (existing_triple, incoming_triple)


@pytest.mark.asyncio
async def test_queue_debounce_carries_whole_quote_triple_from_incoming():
    """Anchor derived from the incoming event's quote -> text+flag follow it.

    This is the WhatsApp-reaction shape: a synthetic event with no message_id
    of its own whose entire meaning lives in the reply quote.
    """
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    first = _make_quoted_event("plain follow-up")
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(first)
    reaction = _make_quoted_event(
        "[Реакция 👍]",
        message_id=None,
        reply_to_message_id="tgt-reaction",
        reply_to_text="the message that was reacted to",
        reply_to_is_own_message=True,
    )
    await adapter.handle_message(reaction)

    buffered = _debounced_event(adapter, session_key)
    assert buffered.text == "plain follow-up\n[Реакция 👍]"
    assert _quote_triple(buffered) == (
        "tgt-reaction",
        "the message that was reacted to",
        True,
    )


@pytest.mark.asyncio
async def test_queue_debounce_clears_stale_quote_when_anchor_is_own_message_id():
    """Anchor derived from the incoming event's OWN id -> no quote survives.

    The first buffered event's quote must be cleared, not left beside an id
    that points at a different message.
    """
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    first = _make_quoted_event(
        "quoted follow-up",
        reply_to_message_id="tgt-1",
        reply_to_text="stale quoted text",
        reply_to_is_own_message=True,
    )
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(first)
    second = _make_quoted_event("unquoted follow-up", message_id="msg-second")
    await adapter.handle_message(second)

    buffered = _debounced_event(adapter, session_key)
    assert buffered.message_id == "msg-second"
    assert buffered.reply_to_message_id == "msg-second"
    assert buffered.reply_to_text is None
    assert buffered.reply_to_is_own_message is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_kwargs",
    [
        # Anchor comes from the second event own message id.
        {"message_id": "msg-second"},
        # Anchor comes from the second event own quote.
        {
            "message_id": None,
            "reply_to_message_id": "tgt-2",
            "reply_to_text": "quoted two",
            "reply_to_is_own_message": False,
        },
    ],
)
async def test_queue_debounce_never_mixes_quote_fields_from_different_events(
    second_kwargs,
):
    """Coherence invariant on the debounce path.

    Whenever the buffered turn ends up carrying quoted text at all, the whole
    (id, text, is_own) triple must be exactly one input event triple -- an id
    from one message beside text from another is the bug this guards.
    """
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    first = _make_quoted_event(
        "quoted follow-up",
        reply_to_message_id="tgt-1",
        reply_to_text="quoted one",
        reply_to_is_own_message=True,
    )
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    await adapter.handle_message(first)

    second = _make_quoted_event("second follow-up", **second_kwargs)
    first_triple = _quote_triple(first)
    second_triple = _quote_triple(second)
    await adapter.handle_message(second)

    buffered = _debounced_event(adapter, session_key)
    if buffered.reply_to_text is not None:
        assert _quote_triple(buffered) in (first_triple, second_triple)
