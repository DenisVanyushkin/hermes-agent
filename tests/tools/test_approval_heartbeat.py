"""Tests for the activity-heartbeat behavior of the blocking gateway approval wait.

Regression test for false gateway inactivity timeouts firing while the agent
is legitimately blocked waiting for a user to respond to a dangerous-command
approval prompt.  Before the fix, ``entry.event.wait(timeout=...)`` blocked
silently — no ``_touch_activity()`` calls — and the gateway's inactivity
watchdog (``agent.gateway_timeout``, default 1800s) would kill the agent
while the user was still choosing whether to approve.

The fix polls the event in short slices and fires ``touch_activity_if_due``
between slices, mirroring ``_wait_for_process`` in ``tools/environments/base.py``.

Coverage history: the original three tests (#11245) were deleted wholesale by
the upstream cull in ``66320de52`` ("remove 50 stale/broken tests to unblock
CI"), leaving this file with a docstring and no test methods.  Two of the
three still passed verbatim; the third was a fixed-``sleep(0.2)`` race that
resolved the approval *before* the worker had enqueued it (enqueueing now
takes ~1s because the smart-approval reviewer runs first), after which the
worker blocked for the full approval timeout.  Every wait below therefore
synchronizes on an event or on ``has_blocking_approval()``, never on a sleep.

Most cases drive ``_await_gateway_decision`` directly — it owns the wait loop,
and going through ``check_all_command_guards`` drags in pattern detection,
the smart reviewer and LLM config for no extra signal.  One end-to-end case
is kept to prove the real terminal-guard path still reaches the loop.
"""

import os
import re
import sys
import threading
import time
from unittest.mock import patch

# The label _await_gateway_decision passes to the activity tracker. It is
# user-visible: touch_activity_if_due renders it as "<label> (Ns elapsed)".
HEARTBEAT_LABEL = "waiting for user approval"

# _await_gateway_decision waits in min(1.0, remaining) slices and heartbeats
# between them, so a heartbeat is due roughly once per second. Bounds below
# are generous multiples of that, sized to catch a genuinely stuck loop
# without making a green run slow.
POLL_SLICE_SECONDS = 1.0


def _clear_approval_state():
    """Reset all module-level approval state between tests."""
    from tools import approval as mod
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()
    mod._session_approved.clear()
    mod._permanent_approved.clear()


def _approval_data(command: str = "rm -rf /tmp/nonexistent-heartbeat-target") -> dict:
    """A minimal approval payload shaped like the terminal guard's."""
    return {
        "command": command,
        "description": "recursive delete",
        "pattern_key": "rm_rf",
        "pattern_keys": ["rm_rf"],
    }


class TestApprovalHeartbeat:
    """The blocking gateway approval wait must fire activity heartbeats.

    Without heartbeats, the gateway's inactivity watchdog kills the agent
    thread while it's legitimately waiting for a slow user to respond to
    an approval prompt (observed in real user logs: MRB, April 2026).
    """

    SESSION_KEY = "heartbeat-test-session"

    def setup_method(self):
        from tools import interrupt as _interrupt_mod
        from tools.interrupt import set_interrupt

        _clear_approval_state()
        # Wipe ALL per-thread interrupt bits. The wait loop resolves as "deny"
        # the moment is_interrupted() is true, so a bit leaked by another suite
        # onto a recycled thread ident would end the wait before a single
        # heartbeat fired — a false failure that looks like a real regression.
        with _interrupt_mod._lock:
            _interrupt_mod._interrupted_threads.clear()
        set_interrupt(False)
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("HERMES_GATEWAY_SESSION", "HERMES_YOLO_MODE",
                      "HERMES_SESSION_KEY")
        }
        os.environ.pop("HERMES_YOLO_MODE", None)
        os.environ["HERMES_GATEWAY_SESSION"] = "1"
        # The blocking wait path reads the session key via contextvar OR
        # os.environ fallback.  Contextvars don't propagate across threads
        # by default, so env var is the portable way to drive this in tests.
        os.environ["HERMES_SESSION_KEY"] = self.SESSION_KEY

    def teardown_method(self):
        from tools import interrupt as _interrupt_mod
        from tools.interrupt import set_interrupt

        with _interrupt_mod._lock:
            _interrupt_mod._interrupted_threads.clear()
        set_interrupt(False)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _clear_approval_state()

    def _start_wait(self, target):
        """Run *target* on a daemon thread and return it."""
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    def _await_enqueued(self, timeout: float = 10.0) -> bool:
        """Block until the worker has actually queued its approval request.

        Resolving before the entry exists is a test race, not a signal:
        resolve_gateway_approval() returns 0 and the worker keeps waiting
        for the full approval timeout.
        """
        from tools.approval import has_blocking_approval

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if has_blocking_approval(self.SESSION_KEY):
                return True
            time.sleep(0.01)
        return False

    def test_heartbeat_fires_repeatedly_while_waiting(self, monkeypatch):
        """touch_activity_if_due is called once per poll slice, not once total.

        A single heartbeat before a long blocking wait would not save the
        agent — the watchdog fires on *silence since the last ping*. So this
        asserts the call happens from inside the loop, repeatedly.
        """
        from tools import approval as mod

        # Force a long timeout so the wait can only end via our resolve, never
        # by the deadline elapsing (and so a sibling suite that leaks
        # _get_approval_config can't shorten it under us).
        monkeypatch.setattr(mod, "_get_approval_timeout", lambda: 300)

        calls: list[tuple[str, dict]] = []
        second_heartbeat = threading.Event()
        notified = threading.Event()

        def _fake_touch(state, label):
            # Patching the whole function removes the real 10s throttle, so
            # every loop iteration lands here — we are measuring the loop's
            # cadence, not the tracker's (that chain is covered separately by
            # test_heartbeat_reaches_gateway_activity_callback).
            calls.append((label, state))
            if len(calls) >= 2:
                second_heartbeat.set()

        result_holder: dict = {}

        def _run_wait():
            try:
                with patch("tools.environments.base.touch_activity_if_due",
                           _fake_touch):
                    result_holder["result"] = mod._await_gateway_decision(
                        self.SESSION_KEY,
                        lambda _payload: notified.set(),
                        _approval_data(),
                    )
            except Exception as exc:  # pragma: no cover - surfaced below
                result_holder["exc"] = exc

        thread = self._start_wait(_run_wait)

        assert notified.wait(timeout=10), "approval was never enqueued/notified"
        assert second_heartbeat.wait(timeout=10 * POLL_SLICE_SECONDS), (
            f"fewer than 2 heartbeats in 10s (saw {len(calls)}) — the approval "
            "wait is blocking without periodic activity pings, which is the "
            "exact bug this test exists to catch"
        )

        assert mod.resolve_gateway_approval(self.SESSION_KEY, "once") == 1
        thread.join(timeout=10)

        assert not thread.is_alive(), "approval wait did not exit after resolve"
        assert "exc" not in result_holder, (
            f"_await_gateway_decision raised: {result_holder.get('exc')!r}"
        )
        assert result_holder["result"] == {
            "resolved": True, "choice": "once", "reason": None,
        }

        labels = {label for label, _state in calls}
        assert labels == {HEARTBEAT_LABEL}, f"unexpected heartbeat labels: {labels}"

        # Every heartbeat must carry the SAME state dict. The real
        # touch_activity_if_due throttles on state["last_touch"]; a freshly
        # built dict per iteration would reset last_touch to "now" every time
        # and the activity callback would then never fire at all.
        state_ids = {id(state) for _label, state in calls}
        assert len(state_ids) == 1, (
            "heartbeat state is rebuilt per iteration — the 10s throttle "
            "would never elapse and no ping would ever reach the gateway"
        )
        state = calls[0][1]
        assert {"last_touch", "start"} <= set(state), (
            f"heartbeat state missing keys required by touch_activity_if_due: {state}"
        )

    def test_heartbeat_reaches_gateway_activity_callback(self, monkeypatch):
        """The ping reaches the real activity callback, correctly labelled.

        Covers the far end of the chain the watchdog actually observes:
        wait loop → real touch_activity_if_due → thread-local activity
        callback. The callback is stored in a threading.local(), so it only
        resolves when registered on the *same* thread that blocks — which is
        why this registers it inside the worker, exactly as the gateway does
        on the agent's execution thread.
        """
        from tools import approval as mod
        from tools.environments import base as env_base

        monkeypatch.setattr(mod, "_get_approval_timeout", lambda: 300)

        pings: list[str] = []
        ping_seen = threading.Event()
        notified = threading.Event()
        real_touch = env_base.touch_activity_if_due

        def _fast_touch(state, label):
            # Production cadence is 10s. Shrink it through the documented
            # `interval` key so the REAL throttle + callback code runs here
            # in test time instead of being stubbed out.
            state.setdefault("interval", 0.05)
            return real_touch(state, label)

        def _on_activity(message: str) -> None:
            pings.append(message)
            ping_seen.set()

        def _run_wait():
            env_base.set_activity_callback(_on_activity)
            try:
                with patch("tools.environments.base.touch_activity_if_due",
                           _fast_touch):
                    mod._await_gateway_decision(
                        self.SESSION_KEY,
                        lambda _payload: notified.set(),
                        _approval_data(),
                    )
            finally:
                env_base.set_activity_callback(None)

        thread = self._start_wait(_run_wait)

        assert notified.wait(timeout=10), "approval was never enqueued/notified"
        assert ping_seen.wait(timeout=10 * POLL_SLICE_SECONDS), (
            "no activity ping reached the gateway callback — the watchdog "
            "sees this wait as an idle agent"
        )

        assert mod.resolve_gateway_approval(self.SESSION_KEY, "once") == 1
        thread.join(timeout=10)
        assert not thread.is_alive()

        assert re.fullmatch(rf"{re.escape(HEARTBEAT_LABEL)} \(\d+s elapsed\)",
                            pings[0]), f"unexpected activity message: {pings[0]!r}"

    def test_wait_returns_promptly_when_user_responds(self, monkeypatch):
        """Polling slices don't delay responsiveness — resolve is near-instant."""
        from tools import approval as mod

        monkeypatch.setattr(mod, "_get_approval_timeout", lambda: 300)

        result_holder: dict = {}
        notified = threading.Event()

        def _run_wait():
            result_holder["result"] = mod._await_gateway_decision(
                self.SESSION_KEY,
                lambda _payload: notified.set(),
                _approval_data("rm -rf /tmp/nonexistent-fast-target"),
            )

        thread = self._start_wait(_run_wait)
        assert notified.wait(timeout=10), "approval was never enqueued/notified"
        assert self._await_enqueued()

        start_time = time.monotonic()
        assert mod.resolve_gateway_approval(self.SESSION_KEY, "once") == 1
        thread.join(timeout=10)
        elapsed = time.monotonic() - start_time

        assert not thread.is_alive()
        assert result_holder["result"]["resolved"] is True
        assert result_holder["result"]["choice"] == "once"
        # Generous bound to tolerate CI load; the pre-heartbeat single-wait
        # impl returned in <10ms, the polling impl is bounded by one slice.
        assert elapsed < 3 * POLL_SLICE_SECONDS, (
            f"resolution took {elapsed:.2f}s — the wait loop is not observing "
            "the resolve within one poll slice"
        )

    def test_wait_survives_missing_heartbeat_helper(self, monkeypatch):
        """If tools.environments.base can't be imported, the wait still works.

        The heartbeat is best-effort instrumentation; losing it must degrade
        to the old silent-wait behavior, never break approvals outright.
        Setting the module to None in sys.modules makes the import inside
        _await_gateway_decision raise ImportError, which is far narrower than
        swapping out builtins.__import__ for every import on every thread.
        """
        from tools import approval as mod

        monkeypatch.setattr(mod, "_get_approval_timeout", lambda: 300)

        result_holder: dict = {}
        notified = threading.Event()

        def _run_wait():
            try:
                with patch.dict(sys.modules,
                                {"tools.environments.base": None}):
                    result_holder["result"] = mod._await_gateway_decision(
                        self.SESSION_KEY,
                        lambda _payload: notified.set(),
                        _approval_data("rm -rf /tmp/nonexistent-import-fail-target"),
                    )
            except Exception as exc:  # pragma: no cover - surfaced below
                result_holder["exc"] = exc

        thread = self._start_wait(_run_wait)

        assert notified.wait(timeout=10), "approval was never enqueued/notified"
        assert self._await_enqueued()
        assert mod.resolve_gateway_approval(self.SESSION_KEY, "once") == 1
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert "exc" not in result_holder, (
            "a missing heartbeat helper must not escape the wait: "
            f"{result_holder.get('exc')!r}"
        )
        assert result_holder["result"] == {
            "resolved": True, "choice": "once", "reason": None,
        }

    def test_terminal_guard_path_heartbeats_end_to_end(self):
        """The real dangerous-command path reaches the heartbeat loop.

        The unit cases above call _await_gateway_decision directly; this one
        proves check_all_command_guards still routes a gateway session into
        it, so the wiring can't rot without a failure here.
        """
        from tools.approval import (
            check_all_command_guards,
            register_gateway_notify,
            resolve_gateway_approval,
        )

        register_gateway_notify(self.SESSION_KEY, lambda _payload: None)

        first_heartbeat = threading.Event()
        heartbeat_calls: list[str] = []

        def _fake_touch(state, label):
            heartbeat_calls.append(label)
            first_heartbeat.set()

        result_holder: dict = {}

        def _run_check():
            try:
                with patch("tools.environments.base.touch_activity_if_due",
                           _fake_touch):
                    result_holder["result"] = check_all_command_guards(
                        "rm -rf /tmp/nonexistent-heartbeat-target", "local"
                    )
            except Exception as exc:  # pragma: no cover - surfaced below
                result_holder["exc"] = exc

        thread = self._start_wait(_run_check)

        # Generous: the guard runs pattern detection and the smart-approval
        # reviewer (~1s, longer when a reviewer model is reachable) before it
        # ever enqueues, so the first heartbeat is not immediate.
        assert first_heartbeat.wait(timeout=30), (
            "no heartbeat fired within 30s on the terminal-guard path — "
            "check_all_command_guards no longer reaches the heartbeat wait"
        )

        assert self._await_enqueued()
        resolve_gateway_approval(self.SESSION_KEY, "once")
        thread.join(timeout=10)

        assert not thread.is_alive(), "approval wait did not exit after resolve"
        assert "exc" not in result_holder, (
            f"check_all_command_guards raised: {result_holder.get('exc')!r}"
        )
        assert set(heartbeat_calls) == {HEARTBEAT_LABEL}, (
            f"unexpected heartbeat labels: {set(heartbeat_calls)}"
        )
        # Sanity: the approval was resolved with "once" → command approved.
        assert result_holder["result"]["approved"] is True
