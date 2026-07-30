"""Тик stale-guard: алерт, простой, рестарт, бюджет (спека 2026-07-30)."""

import asyncio
import os
import threading
import time

import pytest


class _FakeRunner:
    def __init__(self, idle=True):
        self._idle = idle
        self.restart_calls = 0
        self.restarted = threading.Event()

    def _stale_guard_is_idle(self, idle_timeout_seconds):
        return self._idle

    def request_restart(self, *, detached=False, via_service=False):
        self.restart_calls += 1
        self.restarted.set()
        return True


@pytest.fixture
def gw_loop():
    """Живой event loop в отдельном потоке — как у настоящего гейтвея.

    Тик крутится в потоке housekeeping и обязан планировать рестарт на этот
    loop, а не звать asyncio.create_task у себя (C1).
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


@pytest.fixture
def wiring(monkeypatch, tmp_path):
    """Общая обвязка: детектор, алерты и HERMES_HOME подменены."""
    from gateway import run as gateway_run

    sent = []
    monkeypatch.setattr(gateway_run, "send_operator_alert", lambda ch, text: sent.append(text))
    monkeypatch.setattr(
        gateway_run, "get_alert_config", lambda cfg: {"channel": "telegram:1", "dedup_minutes": 15}
    )
    monkeypatch.setattr(gateway_run, "_stale_guard_hermes_home", lambda: tmp_path)
    return {"sent": sent, "home": tmp_path}


def _cfg(**over):
    base = {
        "check_every_minutes": 5,
        "idle_timeout_minutes": 10,
        "max_auto_restarts_per_hour": 2,
        "watch_files": [],
    }
    base.update(over)
    return base


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_skew_plus_idle_triggers_exactly_one_restart(monkeypatch, wiring, gw_loop):
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["hermes_state.py"])
    runner = _FakeRunner(idle=True)
    state = {}

    # Первый тик только алертит (рестарт отложен на тик — см. I5), второй
    # рестартует.
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    assert runner.restarted.wait(timeout=5)
    assert runner.restart_calls == 1
    assert len(wiring["sent"]) == 1
    assert "hermes_state.py" in wiring["sent"][0]


def test_first_alert_pass_defers_the_restart(monkeypatch, wiring, gw_loop):
    """Алерт уходит демон-потоком: рестарт в том же проходе убил бы его."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    assert len(wiring["sent"]) == 1
    time.sleep(0.2)
    assert runner.restart_calls == 0


def test_skew_without_idle_alerts_but_does_not_restart(monkeypatch, wiring, gw_loop):
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["run_agent.py"])
    runner = _FakeRunner(idle=False)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    time.sleep(0.2)
    assert runner.restart_calls == 0
    assert len(wiring["sent"]) == 1


def test_no_skew_does_nothing(monkeypatch, wiring, gw_loop):
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: [])
    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    assert runner.restart_calls == 0
    assert wiring["sent"] == []


def test_alert_is_sent_once_per_episode(monkeypatch, wiring, gw_loop):
    """Скос живёт до рестарта — повторять алерт на каждом тике нельзя."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    runner = _FakeRunner(idle=False)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    assert len(wiring["sent"]) == 1


def test_exhausted_budget_blocks_restart_and_alerts_once(monkeypatch, wiring, gw_loop):
    from gateway import run as gateway_run
    from gateway.stale_guard import record_auto_restart

    assert record_auto_restart(wiring["home"], 1_000_000.0) is True
    assert record_auto_restart(wiring["home"], 1_000_001.0) is True
    monkeypatch.setattr(gateway_run.time, "time", lambda: 1_000_002.0)
    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])

    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    time.sleep(0.2)
    assert runner.restart_calls == 0
    assert any("авторестарт отключён" in t for t in wiring["sent"])
    assert sum("авторестарт отключён" in t for t in wiring["sent"]) == 1


def test_exhausted_budget_latches_off_for_the_process(monkeypatch, wiring, gw_loop):
    """Через час скользящее окно опустеет — сторож всё равно не оживает (I4)."""
    from gateway import run as gateway_run
    from gateway.stale_guard import record_auto_restart

    record_auto_restart(wiring["home"], 1_000_000.0)
    record_auto_restart(wiring["home"], 1_000_001.0)
    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])

    now = {"t": 1_000_002.0}
    monkeypatch.setattr(gateway_run.time, "time", lambda: now["t"])

    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)  # алерт
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)  # бюджет
    assert state["guard_disabled"] is True

    now["t"] = 1_000_002.0 + 7200  # окно давно пустое
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    time.sleep(0.2)
    assert runner.restart_calls == 0


def test_unwritable_budget_blocks_the_restart(monkeypatch, wiring, gw_loop):
    """Бюджет — единственная защита от петли: не пишется → не рестартуем (I3)."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    monkeypatch.setattr(gateway_run, "budget_writable", lambda home: False)

    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)

    time.sleep(0.2)
    assert runner.restart_calls == 0
    assert state["guard_disabled"] is True
    assert any("не пишется" in t for t in wiring["sent"])


def test_detector_failure_does_not_propagate(monkeypatch, wiring, gw_loop):
    from gateway import run as gateway_run

    def _boom(root):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(gateway_run, "detect_module_skew", _boom)
    runner = _FakeRunner(idle=True)

    gateway_run._stale_guard_tick(runner, _cfg(), {}, loop=gw_loop)  # не должно бросить

    assert runner.restart_calls == 0


def test_boot_label_from_snapshot_time_is_used_in_the_alert(monkeypatch, wiring, gw_loop):
    """M6: человеку показывается момент СНИМКА, а не старта housekeeping."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    runner = _FakeRunner(idle=False)

    gateway_run._stale_guard_tick(runner, _cfg(), {"boot_label": "04:05:06"}, loop=gw_loop)

    assert "04:05:06" in wiring["sent"][0]


# ---------------------------------------------------------------------------
# C1 — настоящий GatewayRunner.request_restart из НЕ-loop потока
# ---------------------------------------------------------------------------


def _real_runner(monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    runner = GatewayRunner(GatewayConfig())
    calls = []

    async def _fake_stop(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(runner, "stop", _fake_stop)
    monkeypatch.setattr(runner, "_stale_guard_is_idle", lambda seconds: True)
    return runner, calls


def test_real_request_restart_is_scheduled_onto_the_loop(monkeypatch, wiring, gw_loop):
    """Настоящий request_restart зовёт asyncio.create_task — только на loop."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    runner, stop_calls = _real_runner(monkeypatch)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)  # алерт
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=gw_loop)  # рестарт

    assert _wait(lambda: bool(stop_calls)), "stop() так и не был вызван"
    assert runner._restart_requested is True
    assert runner._restart_via_service is True
    assert stop_calls[0].get("service_restart") is True


def test_offloop_tick_without_loop_does_not_latch_restart_flag(monkeypatch, wiring):
    """Без loop рестарт не планируется и НЕ выжигает _restart_task_started."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    runner, _ = _real_runner(monkeypatch)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=None)
    gateway_run._stale_guard_tick(runner, _cfg(), state, loop=None)

    assert runner._restart_task_started is False


# ---------------------------------------------------------------------------
# I2 — простой считается по ВСЕЙ работе, включая cron
# ---------------------------------------------------------------------------


def test_idle_predicate_counts_running_cron_jobs(monkeypatch):
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    runner = GatewayRunner(GatewayConfig())
    runner._last_inbound_at = 0.0  # входящих не было очень давно
    monkeypatch.setattr(runner, "_running_agent_count", lambda: 0)
    monkeypatch.setattr(runner, "_active_api_run_count", lambda: 0)
    monkeypatch.setattr(runner, "_scale_to_zero_has_live_background_work", lambda: False)

    monkeypatch.setattr(runner, "_active_cron_job_count", lambda: 0)
    assert runner._stale_guard_is_idle(600) is True

    monkeypatch.setattr(runner, "_active_cron_job_count", lambda: 1)
    assert runner._stale_guard_is_idle(600) is False


# ---------------------------------------------------------------------------
# I6 / M8 — вооружение сторожа
# ---------------------------------------------------------------------------


@pytest.fixture
def arming(monkeypatch, tmp_path):
    from gateway import run as gateway_run

    taken = []

    def _take(root, watch_files=None):
        taken.append(root)
        return 7

    monkeypatch.setattr(gateway_run, "take_snapshot", _take)
    monkeypatch.setattr(gateway_run, "_stale_guard_hermes_home", lambda: tmp_path)
    return {"taken": taken}


def _no_supervisor(monkeypatch):
    import gateway.restart as gateway_restart
    from gateway.restart import EXTERNAL_GATEWAY_SUPERVISOR_ENV
    from gateway import run as gateway_run

    for var in ("INVOCATION_ID", "HERMES_S6_SUPERVISED_CHILD", "XPC_SERVICE_NAME",
                EXTERNAL_GATEWAY_SUPERVISOR_ENV):
        monkeypatch.delenv(var, raising=False)
    real_exists = os.path.exists
    monkeypatch.setattr(
        gateway_run.os.path,
        "exists",
        lambda p: False if p in ("/.dockerenv", "/run/.containerenv") else real_exists(p),
    )
    assert gateway_restart.is_gateway_supervisor_process() is False


def _with_supervisor(monkeypatch):
    from gateway.restart import EXTERNAL_GATEWAY_SUPERVISOR_ENV

    monkeypatch.setenv(EXTERNAL_GATEWAY_SUPERVISOR_ENV, "1")


_ENABLED = {"gateway": {"stale_code_guard": {"enabled": True}}}


def test_no_config_block_takes_no_snapshot(monkeypatch, arming):
    """M8: без блока в конфиге не происходит НИЧЕГО — снимок не снимается."""
    from gateway import run as gateway_run

    _with_supervisor(monkeypatch)

    assert gateway_run._stale_guard_arm({}) == (None, None)
    assert gateway_run._stale_guard_arm({"gateway": {}}) == (None, None)
    assert gateway_run._stale_guard_arm(
        {"gateway": {"stale_code_guard": {"enabled": False}}}
    ) == (None, None)
    assert arming["taken"] == []


def test_without_supervisor_the_guard_refuses_to_arm(monkeypatch, arming):
    """I6: exit 75 без супервизора — это просто смерть гейтвея."""
    from gateway import run as gateway_run

    _no_supervisor(monkeypatch)

    assert gateway_run._stale_guard_arm(_ENABLED) == (None, None)
    assert arming["taken"] == []


def test_with_supervisor_the_guard_arms_and_snapshots(monkeypatch, arming):
    from gateway import run as gateway_run

    _with_supervisor(monkeypatch)

    cfg, label = gateway_run._stale_guard_arm(_ENABLED)

    assert cfg is not None and cfg["check_every_minutes"] == 5
    assert label and len(label) == len("00:00:00")
    assert len(arming["taken"]) == 1


def test_unwritable_budget_refuses_to_arm(monkeypatch, arming):
    """I3: без рабочего бюджета вооружаться нельзя."""
    from gateway import run as gateway_run

    _with_supervisor(monkeypatch)
    monkeypatch.setattr(gateway_run, "budget_writable", lambda home: False)

    assert gateway_run._stale_guard_arm(_ENABLED) == (None, None)
    assert arming["taken"] == []


def test_arming_never_raises(monkeypatch, arming):
    from gateway import run as gateway_run

    def _boom(cfg):
        raise RuntimeError("config exploded")

    monkeypatch.setattr(gateway_run, "get_stale_guard_config", _boom)

    assert gateway_run._stale_guard_arm(_ENABLED) == (None, None)


# ---------------------------------------------------------------------------
# I7 / M8 — цикл housekeeping
# ---------------------------------------------------------------------------


class _FiniteStop:
    """stop_event, который позволяет циклу сделать ровно N проходов."""

    def __init__(self, ticks):
        self.left = ticks

    def is_set(self):
        if self.left <= 0:
            return True
        self.left -= 1
        return False

    def wait(self, timeout=None):
        return False


def test_housekeeping_does_no_guard_work_when_disabled(monkeypatch):
    """M8: фича выключена → тик сторожа не зовётся ни разу."""
    from gateway import run as gateway_run

    calls = []
    monkeypatch.setattr(
        gateway_run, "_stale_guard_tick", lambda *a, **kw: calls.append(a)
    )
    monkeypatch.setattr(
        gateway_run, "_stale_guard_load_config", lambda: pytest.fail("config read")
    )

    gateway_run._start_gateway_housekeeping(
        _FiniteStop(20), adapters=None, loop=None, interval=0,
        runner=object(), stale_guard_cfg=None,
    )

    assert calls == []


def test_housekeeping_ticks_the_guard_on_its_cadence(monkeypatch):
    from gateway import run as gateway_run

    calls = []
    monkeypatch.setattr(
        gateway_run, "_stale_guard_tick", lambda *a, **kw: calls.append(kw)
    )

    gateway_run._start_gateway_housekeeping(
        _FiniteStop(20), adapters=None, loop="LOOP", interval=0,
        runner=object(), stale_guard_cfg=_cfg(), stale_guard_boot_label="01:02:03",
    )

    assert len(calls) == 4  # тики 5, 10, 15, 20
    assert calls[0]["loop"] == "LOOP"
