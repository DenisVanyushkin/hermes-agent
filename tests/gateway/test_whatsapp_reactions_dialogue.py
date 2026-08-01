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


from unittest.mock import patch, MagicMock
import psutil


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
    """Assert the cancellation INSIDE the running loop. asyncio.run() cancels
    any leftover task at loop shutdown, so checking timer.cancelled() only
    after the loop has closed would pass even with no cancel() call at all --
    that false-positive shape is why this test previously asserted nothing
    (review finding #2, 2026-07-29)."""
    adapter = _make_adapter(reaction_dialogue=True)

    async def scenario():
        pending = _pending_text_event(adapter, "текст")
        key = adapter._text_batch_key(pending)
        adapter.handle_message = AsyncMock()
        adapter._pending_text_batches[key] = pending
        timer = asyncio.create_task(asyncio.sleep(30))
        adapter._pending_text_batch_tasks[key] = timer
        await adapter._force_flush_text_batch(key)
        # Give the cancellation a chance to land while the loop is still
        # running, before asyncio.run()'s own shutdown sweep could mask it.
        await asyncio.sleep(0)
        assert timer.cancelled()

    asyncio.run(scenario())


def test_force_flush_on_empty_buffer_is_a_no_op():
    adapter = _make_adapter(reaction_dialogue=True)
    adapter.handle_message = AsyncMock()
    asyncio.run(adapter._force_flush_text_batch("nothing-here"))
    adapter.handle_message.assert_not_awaited()


def test_force_flush_does_not_lose_text_whose_timer_already_started_delivering_it():
    """Review finding #1 (critical): if the real debounce timer
    (_enqueue_text_event / _flush_text_batch) already popped the pending
    event out of _pending_text_batches and is suspended inside
    handle_message's own I/O, a force-flush racing in behind it must not
    cancel that in-flight dispatch -- doing so silently drops the user's
    text. Reproduced against the real timer machinery, not a stand-in."""
    adapter = _make_adapter(reaction_dialogue=True)
    adapter._text_batch_delay_seconds = 0
    order = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_handle_message(event):
        order.append(event.text)
        started.set()
        await release.wait()

    adapter.handle_message = slow_handle_message

    async def scenario():
        pending = _pending_text_event(adapter, "я в магазине")
        adapter._enqueue_text_event(pending)
        key = adapter._text_batch_key(pending)
        timer = adapter._pending_text_batch_tasks[key]

        # Let the real timer fire, pop the pending event, and suspend
        # inside handle_message before the reaction's force-flush runs.
        await started.wait()

        await adapter._force_flush_text_batch(key)
        release.set()
        await timer

    asyncio.run(scenario())
    assert order == ["я в магазине"]


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


def _hung_hook(reap_behavior="ok", terminate_side_effect=None):
    """Patch create_subprocess_exec to return a proc whose communicate()
    never completes, so the (also patched) asyncio.wait_for around it
    times out deterministically instead of on the real 30s clock.

    ``reap_behavior`` controls what happens on the second wait_for call
    -- the bounded reap after the kill:
      "ok"            -- proc.wait() completes normally (the common case:
                          the tree-kill worked and there is nothing left
                          holding the pipes open).
      "hang"          -- the reap's own wait_for also times out, as it
                          would for real if a surviving descendant still
                          held the inherited pipes open (the scenario C1
                          exists to bound).
      "lookup_error"  -- proc.wait() raises ProcessLookupError (the
                          process was already reaped by the time we got
                          here).

    ``terminate_side_effect``, if given, is raised by the patched
    ``_terminate_bridge_process`` instead of it succeeding.

    proc.returncode is pinned to None: these are all "still alive when
    the timeout fires" scenarios, which is what makes the tree-kill
    guard (NEW-1: never tree-kill an already-exited proc, since its pid
    may have been recycled) let the call through in the first place.
    """
    proc = AsyncMock()
    proc.pid = 4242
    proc.returncode = None  # still running -- these tests are about that case

    async def _never_completes(*_a, **_kw):
        await asyncio.sleep(999)

    proc.communicate = _never_completes
    proc.kill = MagicMock()

    if reap_behavior == "lookup_error":
        async def _wait(*_a, **_kw):
            raise ProcessLookupError()
        proc.wait = _wait
    else:
        proc.wait = AsyncMock(return_value=None)

    calls = {"n": 0}

    async def fake_wait_for(aw, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # the hook-communicate wait: always the timeout under test
            aw.close()
            raise asyncio.TimeoutError()
        # the reap wait, following the kill
        if reap_behavior == "hang":
            aw.close()
            raise asyncio.TimeoutError()
        return await aw

    create_patch = patch("asyncio.create_subprocess_exec",
                          AsyncMock(return_value=proc))
    wait_for_patch = patch("asyncio.wait_for", fake_wait_for)
    terminate_patch = patch(
        "plugins.platforms.whatsapp.adapter._terminate_bridge_process",
        MagicMock(side_effect=terminate_side_effect))
    return proc, create_patch, wait_for_patch, terminate_patch, calls


def test_timed_out_hook_still_falls_through_to_dialogue():
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc, create_patch, wait_for_patch, terminate_patch, _calls = _hung_hook()
    with create_patch, wait_for_patch, terminate_patch:
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()


def test_timed_out_hook_kills_the_process_tree_not_just_the_child():
    """A hook that forked instead of exec'ing leaves a live grandchild
    after a plain proc.kill() -- the fix must kill the whole tree via
    the same helper the bridge-process shutdown path uses, not just the
    direct child."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc, create_patch, wait_for_patch, terminate_patch, _calls = _hung_hook()
    with create_patch, wait_for_patch, terminate_patch as terminate_mock:
        _apply(adapter, _reaction())
    terminate_mock.assert_called_once_with(proc, force=True)
    proc.kill.assert_not_called()


def test_timed_out_hook_reap_is_bounded_when_the_tree_survives_the_kill():
    """C1: asyncio.Process.wait() returns when the inherited pipe
    transports close, not merely when the pid dies -- a killed hook
    whose descendant still holds those pipes would hang an unbounded
    reap forever, turning a 30s stall into the poll loop never
    advancing again. The reap must be bounded and the timeout on it
    swallowed, same as the kill it follows."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc, create_patch, wait_for_patch, terminate_patch, calls = _hung_hook(
        reap_behavior="hang")
    with create_patch, wait_for_patch, terminate_patch:
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()
    # Proves the reap actually went through asyncio.wait_for (bounded)
    # rather than a bare `await proc.wait()` -- if it had regressed to
    # the latter, fake_wait_for would only ever see the one call for
    # communicate() and this would catch that.
    assert calls["n"] == 2


def test_timed_out_hook_reap_process_lookup_error_is_swallowed():
    """The process may already have been reaped by the time the bounded
    wait() runs -- that race is not an error, and the dialogue path
    must still run."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc, create_patch, wait_for_patch, terminate_patch, _calls = _hung_hook(
        reap_behavior="lookup_error")
    with create_patch, wait_for_patch, terminate_patch:
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()


def test_timed_out_hook_tree_kill_access_denied_falls_back_to_plain_kill():
    """review NEW-2: _terminate_bridge_process already swallows
    psutil.NoSuchProcess internally, and never raises a bare
    ProcessLookupError or PermissionError -- psutil.AccessDenied is the
    one failure mode it does NOT catch, so that is what a genuine
    permission failure looks like from the caller's side. Falls back to
    a plain proc.kill() rather than treating that as fatal."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc, create_patch, wait_for_patch, terminate_patch, _calls = _hung_hook(
        terminate_side_effect=psutil.AccessDenied())
    with create_patch, wait_for_patch, terminate_patch:
        dispatch = _apply(adapter, _reaction())
    proc.kill.assert_called_once()
    dispatch.assert_awaited_once()


def test_timed_out_hook_tree_kill_generic_failure_is_swallowed():
    """A tree-kill failure that is not psutil.AccessDenied (e.g. some
    other psutil oddity) must not escape and must not stop the dialogue
    path from running -- and must NOT trigger the proc.kill() fallback,
    since that fallback is reserved for the one failure mode we can
    positively identify as \"we have permission but couldn't act\"."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    proc, create_patch, wait_for_patch, terminate_patch, _calls = _hung_hook(
        terminate_side_effect=RuntimeError("psutil oddity"))
    with create_patch, wait_for_patch, terminate_patch:
        dispatch = _apply(adapter, _reaction())
    proc.kill.assert_not_called()
    dispatch.assert_awaited_once()


def test_create_subprocess_exec_raising_timeout_error_does_not_crash():
    """Minor 1: asyncio.TimeoutError is a plain OSError subclass on
    3.11+, so create_subprocess_exec itself (not just the wait_for
    around communicate()) could in principle raise it. That must not
    reach the TimeoutError handler with an unbound `proc` -- it is
    structurally impossible here because create_subprocess_exec has its
    own try/except, but this pins the observable behaviour: graceful
    fall-through, no crash."""
    adapter = _make_adapter(reaction_dialogue=True,
                            reaction_hook_cmd=["/bin/true"])
    with patch("asyncio.create_subprocess_exec",
               AsyncMock(side_effect=asyncio.TimeoutError())):
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()


def test_orphaned_grandchild_hook_never_tree_kills_a_recycled_pid():
    """review NEW-1, real subprocess (every other test here mocks
    create_subprocess_exec/wait_for/the tree-kill helper, which is
    exactly why this failure mode was invisible to the suite).

    A hook shaped like `sh -c "... &"` forks a grandchild and exits
    immediately -- proc.returncode is set almost at once -- but the
    grandchild inherits and keeps open the stdout/stderr pipes, so
    wait_for(communicate()) still times out. By then proc.pid may have
    been recycled by the OS for an unrelated process; the guard
    (`if proc.returncode is None`) must keep _terminate_bridge_process
    from ever being called against it. Asserted on the guard itself,
    not on wall-clock timing.
    """
    adapter = _make_adapter(
        reaction_dialogue=True,
        reaction_hook_cmd=["/bin/sh", "-c", "sleep 2 &"])
    with patch("plugins.platforms.whatsapp.adapter._REACTION_HOOK_TIMEOUT_S", 0.3), \
         patch("plugins.platforms.whatsapp.adapter._terminate_bridge_process") as terminate_mock:
        dispatch = _apply(adapter, _reaction())
    dispatch.assert_awaited_once()
    terminate_mock.assert_not_called()
