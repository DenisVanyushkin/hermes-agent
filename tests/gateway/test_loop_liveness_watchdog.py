"""Gateway event-loop freeze backstops for issue #69089."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gateway.shutdown_watchdog import (
    _arm_loop_floor_timer,
    _reset_blocking_wait_for_tests,
    blocking_wait_active,
    note_blocking_wait,
    start_loop_liveness_watchdog,
)


def _immediate_loop() -> MagicMock:
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    loop.call_soon_threadsafe.side_effect = lambda callback: callback()
    return loop


def test_loop_liveness_watchdog_stop_during_dump_disarms_hard_exit():
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    handle_ready = threading.Event()
    handle_ref = {}
    exit_codes = []

    def stop_during_dump(*_args, **_kwargs) -> None:
        assert handle_ready.wait(timeout=2.0)
        handle_ref["handle"].stop()

    with (
        patch("gateway.shutdown_watchdog.logger.critical") as critical,
        patch(
            "gateway.shutdown_watchdog.faulthandler.dump_traceback",
            side_effect=stop_during_dump,
        ) as dump,
        patch("gateway.shutdown_watchdog.os._exit", side_effect=exit_codes.append),
    ):
        handle = start_loop_liveness_watchdog(
            loop, probe_interval=0.01, probe_timeout=0.01, max_strikes=1
        )
        assert handle is not None
        handle_ref["handle"] = handle
        handle_ready.set()
        handle.join(timeout=2.0)

    assert not handle.is_alive()
    critical.assert_called_once()
    dump.assert_called_once_with(all_threads=True)
    assert exit_codes == []


def test_loop_liveness_watchdog_stop_during_final_miss_disarms_hard_exit():
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    probe_scheduled = threading.Event()
    release_probe = threading.Event()
    probe_event_ref = {}
    handle_ref = {}
    exit_codes = []

    class FinalStrikeLimit:
        def __gt__(self, _strikes: int) -> bool:
            # If strike evaluation is reached, keep recheck #2 from masking a
            # missing post-probe recheck #1 in this boundary test.
            handle_ref["handle"]._stop_event.clear()
            return False

    def hold_scheduled_probe(callback) -> None:
        probe_event_ref["event"] = callback.__self__
        probe_scheduled.set()
        assert release_probe.wait(timeout=2.0)

    loop.call_soon_threadsafe.side_effect = hold_scheduled_probe
    with (
        patch("gateway.shutdown_watchdog.logger.critical") as critical,
        patch("gateway.shutdown_watchdog.faulthandler.dump_traceback") as dump,
        patch("gateway.shutdown_watchdog.os._exit", side_effect=exit_codes.append),
    ):
        handle = start_loop_liveness_watchdog(
            loop,
            probe_interval=0.01,
            probe_timeout=0.01,
            max_strikes=FinalStrikeLimit(),
        )
        assert handle is not None
        handle_ref["handle"] = handle
        assert probe_scheduled.wait(timeout=2.0), "watchdog did not schedule a probe"

        def stop_during_miss() -> bool:
            handle.stop()
            return False

        probe_event_ref["event"].is_set = stop_during_miss
        release_probe.set()
        handle.join(timeout=1.0)

    assert not handle.is_alive()
    assert exit_codes == []
    critical.assert_not_called()
    dump.assert_not_called()


def test_loop_liveness_watchdog_stop_after_first_recheck_skips_final_actions():
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    probe_scheduled = threading.Event()
    release_probe = threading.Event()

    def hold_scheduled_probe(callback) -> None:
        probe_scheduled.set()
        assert release_probe.wait(timeout=2.0)

    loop.call_soon_threadsafe.side_effect = hold_scheduled_probe
    with (
        patch("gateway.shutdown_watchdog.logger.critical") as critical,
        patch("gateway.shutdown_watchdog.faulthandler.dump_traceback") as dump,
        patch("gateway.shutdown_watchdog.os._exit") as hard_exit,
    ):
        handle = start_loop_liveness_watchdog(
            loop, probe_interval=0.01, probe_timeout=0.01, max_strikes=1
        )
        assert handle is not None
        assert probe_scheduled.wait(timeout=2.0), "watchdog did not schedule a probe"

        original_is_set = handle._stop_event.is_set
        is_set_calls = 0

        def stop_on_final_recheck() -> bool:
            nonlocal is_set_calls
            is_set_calls += 1
            # With the forced immediate timeout: _wait_for_probe is call 1,
            # recheck #1 is call 2, and recheck #2 is call 3.
            if is_set_calls == 3:
                handle.stop()
            return original_is_set()

        handle._stop_event.is_set = stop_on_final_recheck
        with patch(
            "gateway.shutdown_watchdog.time.monotonic", side_effect=[0.0, 1.0]
        ):
            release_probe.set()
            handle.join(timeout=1.0)

    assert is_set_calls == 3
    assert not handle.is_alive()
    critical.assert_not_called()
    dump.assert_not_called()
    hard_exit.assert_not_called()


def test_gateway_config_loop_watchdog_round_trip():
    """loop_watchdog is a config.yaml knob: default on, nested-gateway form honored."""
    from gateway.config import GatewayConfig

    assert GatewayConfig.from_dict({}).loop_watchdog is True
    assert GatewayConfig.from_dict({"loop_watchdog": False}).loop_watchdog is False
    assert (
        GatewayConfig.from_dict(
            {"gateway": {"loop_watchdog": "off"}}
        ).loop_watchdog
        is False
    )
    config = GatewayConfig.from_dict({"loop_watchdog": False})
    assert config.to_dict()["loop_watchdog"] is False


def test_gateway_runner_liveness_guards_start_and_stop():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._loop_floor_timer_handle = None
    runner._loop_liveness_watchdog = None
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    floor_timer = MagicMock()
    watchdog = MagicMock()
    watchdog.is_alive.return_value = True

    with (
        patch(
            "gateway.run._arm_loop_floor_timer", return_value=floor_timer
        ) as arm_floor,
        patch(
            "gateway.run.start_loop_liveness_watchdog", return_value=watchdog
        ) as start_watchdog,
    ):
        runner._start_loop_liveness_guards(loop)

    arm_floor.assert_called_once_with(loop)
    start_watchdog.assert_called_once_with(loop)
    assert runner._loop_floor_timer_handle is floor_timer
    assert runner._loop_liveness_watchdog is watchdog

    runner._stop_loop_liveness_guards()

    watchdog.stop.assert_called_once_with()
    floor_timer.cancel.assert_called_once_with()
    assert runner._loop_liveness_watchdog is None
    assert runner._loop_floor_timer_handle is None


# --- Известное блокирующее ожидание (2026-07-29) -----------------------------
#
# 07:21:06 пришло «почини проблемы»; ход ушёл в инженерный пайплайн, который
# синхронен целиком и выполняется прямо на цикле событий. Цикл не получал
# управления 107 секунд, сторож насчитал три пропущенные пробы и убил процесс
# (exit 75), а systemd на уборке cgroup снёс браузерный стек. Цикл при этом не
# завис: он был занят понятной работой, и опрашивающий её код это знает.
#
# Отметка, а не флаг «я занят»: если опрос прекратился (поток умер, код упал),
# отметка протухает сама и сторож возвращается к работе без чьей-либо помощи.


def test_fresh_note_marks_the_wait_as_active():
    note_blocking_wait(ttl=5.0)
    assert blocking_wait_active() is True


def test_a_stale_note_stops_suppressing():
    note_blocking_wait(ttl=0.05)
    time.sleep(0.12)
    assert blocking_wait_active() is False


def test_no_note_at_all_is_not_active():
    _reset_blocking_wait_for_tests()
    assert blocking_wait_active() is False


def test_a_wait_that_outlives_the_ceiling_stops_suppressing():
    # Потолок обязателен: настоящий зависон ВНУТРИ ожидания не должен навсегда
    # отключать сторожа, который заведён ровно ради таких случаев.
    _reset_blocking_wait_for_tests()
    for _ in range(4):
        note_blocking_wait(ttl=5.0, max_total=0.05)
        time.sleep(0.03)

    assert blocking_wait_active() is False


def test_the_ceiling_restarts_after_a_gap():
    _reset_blocking_wait_for_tests()
    note_blocking_wait(ttl=0.05, max_total=10.0)
    time.sleep(0.12)  # отметка протухла -- ожидание закончилось
    note_blocking_wait(ttl=5.0, max_total=10.0)

    assert blocking_wait_active() is True


def test_missed_probes_do_not_strike_while_the_wait_is_noted():
    _reset_blocking_wait_for_tests()
    loop = MagicMock(spec=asyncio.AbstractEventLoop)  # пробы никогда не отвечают
    exit_codes = []
    stop_noting = threading.Event()

    def _noter() -> None:
        while not stop_noting.wait(timeout=0.01):
            note_blocking_wait(ttl=5.0, max_total=60.0)

    noter = threading.Thread(target=_noter, daemon=True)
    noter.start()
    try:
        with (
            patch("gateway.shutdown_watchdog.faulthandler.dump_traceback") as dump,
            patch("gateway.shutdown_watchdog.os._exit", side_effect=exit_codes.append),
        ):
            handle = start_loop_liveness_watchdog(
                loop, probe_interval=0.01, probe_timeout=0.01, max_strikes=2
            )
            assert handle is not None
            deadline = time.monotonic() + 2.0
            while loop.call_soon_threadsafe.call_count < 6 and time.monotonic() < deadline:
                time.sleep(0.01)
            handle.stop()
            handle.join(timeout=1.0)
    finally:
        stop_noting.set()
        noter.join(timeout=1.0)

    assert loop.call_soon_threadsafe.call_count >= 6, "сторож обязан продолжать пробы"
    dump.assert_not_called()
    assert exit_codes == []


def test_strikes_resume_once_the_notes_stop():
    _reset_blocking_wait_for_tests()
    note_blocking_wait(ttl=0.05)
    time.sleep(0.12)

    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    fired = threading.Event()
    exit_codes = []

    def fake_exit(code: int) -> None:
        exit_codes.append(code)
        fired.set()

    with (
        patch("gateway.shutdown_watchdog.faulthandler.dump_traceback"),
        patch("gateway.shutdown_watchdog.os._exit", side_effect=fake_exit),
    ):
        handle = start_loop_liveness_watchdog(
            loop, probe_interval=0.01, probe_timeout=0.01, max_strikes=2
        )
        assert handle is not None
        assert fired.wait(timeout=3.0), "без свежих отметок засечки обязаны копиться"
        handle.stop()
        handle.join(timeout=1.0)

    assert exit_codes and exit_codes[0] != 0
