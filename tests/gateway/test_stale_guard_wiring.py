"""Тик stale-guard: алерт, простой, рестарт, бюджет (спека 2026-07-30)."""

import pytest


class _FakeRunner:
    def __init__(self, idle=True):
        self._idle = idle
        self.restart_calls = 0

    def _stale_guard_is_idle(self, idle_timeout_seconds):
        return self._idle

    def request_restart(self, *, detached=False, via_service=False):
        self.restart_calls += 1
        return True


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


def test_skew_plus_idle_triggers_exactly_one_restart(monkeypatch, wiring):
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["hermes_state.py"])
    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state)

    assert runner.restart_calls == 1
    assert len(wiring["sent"]) == 1
    assert "hermes_state.py" in wiring["sent"][0]


def test_skew_without_idle_alerts_but_does_not_restart(monkeypatch, wiring):
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["run_agent.py"])
    runner = _FakeRunner(idle=False)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state)

    assert runner.restart_calls == 0
    assert len(wiring["sent"]) == 1


def test_no_skew_does_nothing(monkeypatch, wiring):
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: [])
    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state)

    assert runner.restart_calls == 0
    assert wiring["sent"] == []


def test_alert_is_sent_once_per_episode(monkeypatch, wiring):
    """Скос живёт до рестарта — повторять алерт на каждом тике нельзя."""
    from gateway import run as gateway_run

    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])
    runner = _FakeRunner(idle=False)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state)
    gateway_run._stale_guard_tick(runner, _cfg(), state)
    gateway_run._stale_guard_tick(runner, _cfg(), state)

    assert len(wiring["sent"]) == 1


def test_exhausted_budget_blocks_restart_and_alerts_once(monkeypatch, wiring):
    from gateway import run as gateway_run
    from gateway.stale_guard import record_auto_restart

    record_auto_restart(wiring["home"], 1_000_000.0)
    record_auto_restart(wiring["home"], 1_000_001.0)
    monkeypatch.setattr(gateway_run.time, "time", lambda: 1_000_002.0)
    monkeypatch.setattr(gateway_run, "detect_module_skew", lambda root: ["a.py"])

    runner = _FakeRunner(idle=True)
    state = {}

    gateway_run._stale_guard_tick(runner, _cfg(), state)
    gateway_run._stale_guard_tick(runner, _cfg(), state)

    assert runner.restart_calls == 0
    assert any("авторестарт отключён" in t for t in wiring["sent"])
    assert sum("авторестарт отключён" in t for t in wiring["sent"]) == 1


def test_detector_failure_does_not_propagate(monkeypatch, wiring):
    from gateway import run as gateway_run

    def _boom(root):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(gateway_run, "detect_module_skew", _boom)
    runner = _FakeRunner(idle=True)

    gateway_run._stale_guard_tick(runner, _cfg(), {})  # не должно бросить

    assert runner.restart_calls == 0
