"""Reply quotes through busy-session merges (fork behaviour on v0.22.5).

Moved from the fork's tests/gateway/test_active_session_text_merge.py, which the
v0.22.5 merge replaced with upstream's: only the reply-quote cases, which test
gateway/platforms/local_reply_quote.py wired into merge_pending_message_event and
the text debounce buffer.
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


















# ──────────────────────────────────────────────────────────────────────────
# Task 6.5: reply-quote coherence through busy-session merges.
#
# The reply-quote group (``reply_to_message_id``, ``reply_to_text``,
# ``reply_to_author_id``, ``reply_to_author_name``,
# ``reply_to_is_own_message``) is one unit: it moves together or not at all.
# A merged turn must never carry the id of one message beside the quoted text
# of another (run.py renders the quote only when id AND text are truthy, so an
# incoherent pair degrades quietly into a confidently wrong quote).
#
# Both busy-session merge paths -- interrupt-mode
# ``merge_pending_message_event`` and queue-mode debounce -- follow the same
# rule: adopt the incoming event's group when it HAS a renderable quote,
# otherwise leave the pending group untouched. Nothing ever clears a quote.
# ──────────────────────────────────────────────────────────────────────────


def _quote_group(
    event: MessageEvent,
) -> tuple[str | None, str | None, str | None, str | None, bool]:
    """All five reply-quote fields, so a partial copy cannot pass unnoticed."""
    return (
        event.reply_to_message_id,
        event.reply_to_text,
        event.reply_to_author_id,
        event.reply_to_author_name,
        event.reply_to_is_own_message,
    )


def _quote_kwargs(tag: str, *, is_own: bool = True) -> dict:
    """A complete, self-consistent quote group identified by ``tag``."""
    return {
        "reply_to_message_id": f"tgt-{tag}",
        "reply_to_text": f"quoted text {tag}",
        "reply_to_author_id": f"author-id-{tag}",
        "reply_to_author_name": f"Author {tag}",
        "reply_to_is_own_message": is_own,
    }


def _make_quoted_event(
    text: str,
    *,
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    reply_to_author_id: str | None = None,
    reply_to_author_name: str | None = None,
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
        reply_to_author_id=reply_to_author_id,
        reply_to_author_name=reply_to_author_name,
        reply_to_is_own_message=reply_to_is_own_message,
    )


# ── interrupt mode: merge_pending_message_event ────────────────────────────


def test_text_merge_adopts_incoming_reply_quote():
    """Pending without a quote + incoming with one -> incoming group wins."""
    pending = {}
    existing = _make_quoted_event("part one")
    incoming = _make_quoted_event("part two", **_quote_kwargs("one"))
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    merged = pending["s"]
    assert merged.text == "part one\npart two"
    assert _quote_group(merged) == _quote_group(incoming)
    assert merged.reply_to_author_id == "author-id-one"
    assert merged.reply_to_author_name == "Author one"


def test_text_merge_keeps_existing_quote_when_incoming_has_none():
    """An unquoted follow-up must not erase the pending event's quote."""
    pending = {}
    existing = _make_quoted_event("part one", **_quote_kwargs("one"))
    expected = _quote_group(existing)
    incoming = _make_quoted_event("part two")
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    merged = pending["s"]
    assert merged.text == "part one\npart two"
    assert _quote_group(merged) == expected


def test_text_merge_prefers_latest_quote_when_both_quoted():
    """Two different quoted targets -> the later (incoming) group wins whole."""
    pending = {}
    existing = _make_quoted_event("part one", **_quote_kwargs("one"))
    incoming = _make_quoted_event("part two", **_quote_kwargs("two", is_own=False))
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    assert _quote_group(pending["s"]) == _quote_group(incoming)


def test_text_merge_keeps_renderable_quote_over_unrenderable_incoming_one():
    """Finding 4: an id without text is not a quote.

    Telegram replies to a caption-less media message arrive with
    ``reply_to_message_id`` set and ``reply_to_text`` None. run.py needs both
    to render anything, so such an incoming event must not displace a pending
    quote that WOULD render.
    """
    pending = {}
    existing = _make_quoted_event("part one", **_quote_kwargs("one"))
    expected = _quote_group(existing)
    incoming = _make_quoted_event(
        "part two",
        reply_to_message_id="tgt-captionless",
        reply_to_text=None,
    )
    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    assert _quote_group(pending["s"]) == expected


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
        **_quote_kwargs("photo"),
    )
    merge_pending_message_event(pending, "s", existing)
    merge_pending_message_event(pending, "s", incoming)

    merged = pending["s"]
    assert merged.media_urls == ["/tmp/a.jpg", "/tmp/b.jpg"]
    assert _quote_group(merged) == _quote_group(incoming)


def test_media_merge_adopts_incoming_reply_quote():
    """The mixed media branch obeys the same adoption rule."""
    pending = {}
    existing = _make_quoted_event(
        "voice note",
        message_type=MessageType.VOICE,
        media_urls=["/tmp/a.ogg"],
        media_types=["audio"],
    )
    incoming = _make_quoted_event("and a caption", **_quote_kwargs("media"))
    merge_pending_message_event(pending, "s", existing)
    merge_pending_message_event(pending, "s", incoming)

    merged = pending["s"]
    assert merged.media_urls == ["/tmp/a.ogg"]
    assert _quote_group(merged) == _quote_group(incoming)


def test_media_merge_keeps_existing_quote_when_incoming_has_none():
    pending = {}
    existing = _make_quoted_event(
        "voice note",
        message_type=MessageType.VOICE,
        media_urls=["/tmp/a.ogg"],
        media_types=["audio"],
        **_quote_kwargs("media"),
    )
    expected = _quote_group(existing)
    incoming = _make_quoted_event("and a caption")
    merge_pending_message_event(pending, "s", existing)
    merge_pending_message_event(pending, "s", incoming)

    assert _quote_group(pending["s"]) == expected


@pytest.mark.parametrize(
    "existing_kwargs,incoming_kwargs",
    [
        ({}, {}),
        ({}, _quote_kwargs("two")),
        (_quote_kwargs("one"), {}),
        (_quote_kwargs("one"), _quote_kwargs("two", is_own=False)),
        (
            _quote_kwargs("one"),
            {"reply_to_message_id": "tgt-captionless", "reply_to_text": None},
        ),
    ],
)
def test_merge_never_mixes_quote_fields_from_different_events(
    existing_kwargs, incoming_kwargs
):
    """Coherence invariant: the merged group always comes from ONE event.

    Whatever the merge decides, the resulting five-field group must be exactly
    one of the two input groups -- never a hybrid.
    """
    pending = {}
    existing = _make_quoted_event("part one", **existing_kwargs)
    incoming = _make_quoted_event("part two", **incoming_kwargs)
    existing_group = _quote_group(existing)
    incoming_group = _quote_group(incoming)

    merge_pending_message_event(pending, "s", existing, merge_text=True)
    merge_pending_message_event(pending, "s", incoming, merge_text=True)

    assert _quote_group(pending["s"]) in (existing_group, incoming_group)


# ── queue mode: busy-text debounce ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_debounce_carries_whole_quote_group_from_incoming():
    """An incoming quote is adopted whole, even though the event has an id.

    This is the reaction-arrives-second order: the reaction's entire meaning
    lives in its quote, and it must reach the agent intact.
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
        reply_to_text="did you take your pills?",
        reply_to_is_own_message=True,
    )
    await adapter.handle_message(reaction)

    buffered = _debounced_event(adapter, session_key)
    assert buffered.text == "plain follow-up\n[Реакция 👍]"
    assert _quote_group(buffered) == _quote_group(reaction)


@pytest.mark.asyncio
async def test_queue_debounce_keeps_buffered_quote_when_incoming_has_none():
    """Reaction buffered FIRST, ordinary typed message merged into it.

    The typed message has its own ``message_id`` and no quote. The buffered
    reaction's quote is the whole point of that turn and must survive -- this
    is the arrival order the reviewer demonstrated.
    """
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    reaction = _make_quoted_event(
        "[Реакция 👍]",
        message_id=None,
        reply_to_message_id="tgt-reaction",
        reply_to_text="did you take your pills?",
        reply_to_author_id="bot",
        reply_to_author_name="Amina",
        reply_to_is_own_message=True,
    )
    expected = _quote_group(reaction)
    session_key = build_session_key(reaction.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(reaction)
    typed = _make_quoted_event("да, выпила", message_id="msg-typed")
    await adapter.handle_message(typed)

    buffered = _debounced_event(adapter, session_key)
    assert buffered.text == "[Реакция 👍]\nда, выпила"
    # message_id still tracks the latest message: anchoring is its job.
    assert buffered.message_id == "msg-typed"
    assert _quote_group(buffered) == expected
    assert buffered.reply_to_text == "did you take your pills?"


@pytest.mark.asyncio
async def test_queue_debounce_keeps_renderable_quote_over_unrenderable_incoming():
    """Finding 4 on the debounce path: an id without text is not a quote."""
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    first = _make_quoted_event("quoted follow-up", **_quote_kwargs("one"))
    expected = _quote_group(first)
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(first)
    second = _make_quoted_event(
        "second follow-up",
        message_id="msg-second",
        reply_to_message_id="tgt-captionless",
        reply_to_text=None,
    )
    await adapter.handle_message(second)

    assert _quote_group(_debounced_event(adapter, session_key)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_kwargs",
    [
        # Ordinary typed follow-up: own message id, no quote.
        {"message_id": "msg-second"},
        # Follow-up carrying its own quote, no message id (reaction shape).
        dict({"message_id": None}, **_quote_kwargs("two", is_own=False)),
        # Follow-up carrying both its own id and its own quote.
        dict({"message_id": "msg-second"}, **_quote_kwargs("two", is_own=False)),
        # Unrenderable incoming quote: id without text.
        {
            "message_id": "msg-second",
            "reply_to_message_id": "tgt-captionless",
            "reply_to_text": None,
        },
    ],
)
async def test_queue_debounce_never_mixes_quote_fields_from_different_events(
    second_kwargs,
):
    """Coherence invariant on the debounce path.

    The buffered turn's five-field quote group must always be exactly one of
    the two input groups -- an id from one message beside text from another is
    the bug this guards.
    """
    adapter = _make_adapter()
    adapter._busy_text_debounce_seconds = 1.0

    first = _make_quoted_event("quoted follow-up", **_quote_kwargs("one"))
    session_key = build_session_key(first.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    await adapter.handle_message(first)

    second = _make_quoted_event("second follow-up", **second_kwargs)
    first_group = _quote_group(first)
    second_group = _quote_group(second)
    await adapter.handle_message(second)

    assert _quote_group(_debounced_event(adapter, session_key)) in (
        first_group,
        second_group,
    )
