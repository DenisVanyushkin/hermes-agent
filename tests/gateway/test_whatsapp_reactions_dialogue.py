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
    adapter._reaction_poll_task = None
    adapter.config.extra.setdefault("group_sessions_per_user", True)
    adapter.config.extra.setdefault("thread_sessions_per_user", False)
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


from unittest.mock import patch


def _reaction(**overrides):
    event = {
        "targetMessageId": "M1",
        "targetText": "Купила молоко?",
        "emoji": "👍",
        "removal": False,
        "chatId": "77011102626@s.whatsapp.net",
        "senderId": "77011102626@s.whatsapp.net",
    }
    event.update(overrides)
    return event


def _apply(adapter, event):
    adapter._dispatch_reaction_dialogue = AsyncMock()
    adapter._send_reaction = AsyncMock(return_value=True)
    asyncio.run(adapter._apply_reaction_event(event))
    return adapter._dispatch_reaction_dialogue


def _hook(adapter, stdout, returncode=0):
    """Patch the subprocess call so the hook returns `stdout`."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), b""))
    proc.returncode = returncode
    return patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))


def test_removal_never_reaches_the_dialogue_path():
    """Un-reacting must not produce a second turn, and must not even cost
    a subprocess call -- the filter has to run before the hook, not just
    before the dispatch."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    with patch("asyncio.create_subprocess_exec", AsyncMock()) as create_proc:
        dispatch = _apply(adapter, _reaction(removal=True, emoji=""))
    create_proc.assert_not_called()
    dispatch.assert_not_awaited()


def test_emoji_outside_whitelist_never_reaches_the_dialogue_path():
    """An off-whitelist emoji must not cost a subprocess either."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    with patch("asyncio.create_subprocess_exec", AsyncMock()) as create_proc:
        dispatch = _apply(adapter, _reaction(emoji="🦆"))
    create_proc.assert_not_called()
    dispatch.assert_not_awaited()


def test_dialogue_path_runs_when_no_hook_is_configured():
    adapter = _make_adapter(reaction_dialogue=True)
    dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()


def test_handled_ack_stops_before_the_dialogue_path():
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    with _hook(adapter, '{"handled": true, "react": "✅", "result": "confirmed"}'):
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_not_awaited()
    adapter._send_reaction.assert_awaited_once()


def test_unhandled_reaction_falls_through_to_the_dialogue_path():
    """A 'react' key alone must not trigger the ack side-effect -- only
    'handled: true' does. Paired with
    test_handled_ack_stops_before_the_dialogue_path, this makes the
    'handled' gate itself load-bearing: on the pre-Task-5 code (which
    keyed the ack purely off presence of 'react') this stdout would still
    call _send_reaction."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    with _hook(adapter,
               '{"handled": false, "react": "✅", "result": "unknown_message"}'):
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()
    adapter._send_reaction.assert_not_awaited()


def test_hook_nonzero_exit_falls_through_instead_of_dropping():
    """A broken ack hook must not swallow the reaction."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/false"])
    with _hook(adapter, "", returncode=1):
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()


def test_hook_non_json_output_falls_through():
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    with _hook(adapter, "not json at all"):
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()


def test_hook_non_utf8_output_falls_through():
    """Non-UTF-8 bytes on stdout must not escape as an uncaught
    UnicodeDecodeError -- that would bubble past _apply_reaction_event
    into the poll loop's generic handler, which sleeps 5s and drops every
    remaining event in the batch (exactly the 'reaction vanished' outcome
    the docstring promises not to produce)."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"\xff\xfe not utf-8", b""))
    proc.returncode = 0
    adapter._dispatch_reaction_dialogue = AsyncMock()
    adapter._send_reaction = AsyncMock(return_value=True)
    with patch("asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)):
        asyncio.run(adapter._apply_reaction_event(_reaction()))
    adapter._dispatch_reaction_dialogue.assert_awaited_once()


def test_hook_that_fails_to_start_falls_through():
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/nope"])
    adapter._dispatch_reaction_dialogue = AsyncMock()
    adapter._send_reaction = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               AsyncMock(side_effect=FileNotFoundError("/nope"))):
        asyncio.run(adapter._apply_reaction_event(_reaction()))
    adapter._dispatch_reaction_dialogue.assert_awaited_once()


from gateway.platforms.base import MessageType


def _dispatch(adapter, event):
    adapter.handle_message = AsyncMock()
    asyncio.run(adapter._dispatch_reaction_dialogue(event))
    return adapter.handle_message


def test_no_turn_when_dialogue_flag_is_off():
    adapter = _make_adapter()
    handle = _dispatch(adapter, _reaction())
    handle.assert_not_awaited()


def test_missing_target_text_is_dropped_rather_than_sent_blind():
    """After a bridge restart the store is empty; a contextless turn is
    worse than no turn."""
    adapter = _make_adapter(reaction_dialogue=True)
    handle = _dispatch(adapter, _reaction(targetText=None))
    handle.assert_not_awaited()


def test_reaction_becomes_a_text_turn_quoting_the_target():
    adapter = _make_adapter(reaction_dialogue=True)
    handle = _dispatch(adapter, _reaction())

    handle.assert_awaited_once()
    event = handle.await_args.args[0]
    assert event.message_type is MessageType.TEXT
    assert event.text == "[Реакция 👍]"
    assert event.reply_to_message_id == "M1"
    assert event.reply_to_text == "Купила молоко?"
    assert event.reply_to_is_own_message is True
    assert event.metadata["whatsapp_reaction"] == "👍"


def test_emoji_in_turn_text_is_normalised():
    adapter = _make_adapter(reaction_dialogue=True)
    handle = _dispatch(adapter, _reaction(emoji="\U0001F44D\U0001F3FD"))
    assert handle.await_args.args[0].text == "[Реакция 👍]"


def test_source_carries_chat_and_sender():
    adapter = _make_adapter(reaction_dialogue=True)
    handle = _dispatch(adapter, _reaction())
    source = handle.await_args.args[0].source
    assert source.chat_id == "77011102626@s.whatsapp.net"
    assert source.user_id == "77011102626@s.whatsapp.net"


def test_group_chat_id_produces_a_group_source():
    adapter = _make_adapter(reaction_dialogue=True)
    handle = _dispatch(adapter, _reaction(chatId="123-456@g.us"))
    assert handle.await_args.args[0].source.chat_type == "group"


def test_buffered_text_is_flushed_before_the_reaction_turn():
    """The text debounce buffer delays messages; a reaction dispatched
    straight to handle_message would otherwise overtake a message the user
    typed a second earlier."""
    adapter = _make_adapter(reaction_dialogue=True)
    order = []
    adapter.handle_message = AsyncMock(
        side_effect=lambda e: order.append(e.text))

    pending = _pending_text_event(adapter, "я в магазине")
    key = adapter._text_batch_key(pending)
    adapter._pending_text_batches[key] = pending

    asyncio.run(adapter._dispatch_reaction_dialogue(_reaction()))

    assert order == ["я в магазине", "[Реакция 👍]"]
    assert key not in adapter._pending_text_batches


def test_force_flush_cancels_the_pending_timer_task():
    adapter = _make_adapter(reaction_dialogue=True)

    async def scenario():
        pending = _pending_text_event(adapter, "текст")
        key = adapter._text_batch_key(pending)
        adapter.handle_message = AsyncMock()
        adapter._pending_text_batches[key] = pending
        timer = asyncio.create_task(asyncio.sleep(30))
        adapter._pending_text_batch_tasks[key] = timer
        await adapter._force_flush_text_batch(key)
        return timer

    timer = asyncio.run(scenario())
    assert timer.cancelled() or timer.done()


def test_force_flush_on_empty_buffer_is_a_no_op():
    adapter = _make_adapter(reaction_dialogue=True)
    adapter.handle_message = AsyncMock()
    asyncio.run(adapter._force_flush_text_batch("nothing-here"))
    adapter.handle_message.assert_not_awaited()


def _pending_text_event(adapter, text):
    """A MessageEvent shaped like one sitting in the debounce buffer."""
    from gateway.platforms.base import MessageEvent
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=adapter.build_source(
            chat_id="77011102626@s.whatsapp.net",
            chat_type="dm",
            user_id="77011102626@s.whatsapp.net",
        ),
        raw_message={},
    )
