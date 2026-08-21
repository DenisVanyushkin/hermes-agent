"""Очередь инженерных алертов оператору."""
import stat
from datetime import datetime, timedelta, timezone

import pytest

from fitness import alerts

NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_empty_queue_drains_to_nothing(hermes_home):
    assert alerts.drain(NOW) == []


def test_pushed_alert_is_drained_once(hermes_home):
    assert alerts.push("сессия умерла", NOW) is True
    assert alerts.drain(NOW) == ["сессия умерла"]
    assert alerts.drain(NOW) == []


def test_same_key_is_suppressed_inside_cooldown(hermes_home):
    assert alerts.push("сессия умерла", NOW, key="session_dead") is True
    assert alerts.push("сессия умерла", NOW + timedelta(hours=1),
                       key="session_dead") is False
    assert alerts.drain(NOW + timedelta(hours=2)) == ["сессия умерла"]


def test_same_key_fires_again_after_cooldown(hermes_home):
    alerts.push("сессия умерла", NOW, key="session_dead")
    alerts.drain(NOW)
    assert alerts.push("сессия умерла", NOW + timedelta(hours=25),
                       key="session_dead") is True


def test_different_keys_do_not_suppress_each_other(hermes_home):
    assert alerts.push("одно", NOW, key="a") is True
    assert alerts.push("другое", NOW, key="b") is True
    assert alerts.drain(NOW) == ["одно", "другое"]


def test_pending_does_not_consume_the_queue(hermes_home):
    alerts.push("сессия умерла", NOW)
    assert alerts.pending(NOW) == ["сессия умерла"]
    assert alerts.drain(NOW) == ["сессия умерла"]


def test_empty_text_is_never_queued(hermes_home):
    assert alerts.push("   ", NOW) is False
    assert alerts.drain(NOW) == []


def test_queue_file_is_not_world_readable(hermes_home):
    alerts.push("сессия умерла", NOW)
    path = hermes_home / "state" / "fitness" / alerts.ALERTS_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_garbage_file_does_not_crash(hermes_home):
    path = hermes_home / "state" / "fitness"
    path.mkdir(parents=True)
    (path / alerts.ALERTS_FILE).write_text("не json", encoding="utf-8")
    assert alerts.drain(NOW) == []
    assert alerts.push("сессия умерла", NOW) is True
    assert alerts.drain(NOW) == ["сессия умерла"]
