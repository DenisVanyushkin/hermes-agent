"""tick.error audit marker on cmd_tick_* exceptions (phase 6b, task 4)."""
import pytest

from fam import audit, cli, tick


class Args:
    now = None
    json = False


def test_tick_reminders_records_tick_error_and_reraises(db, monkeypatch):
    def boom(conn, now_utc=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(tick, "reminders", boom)

    with pytest.raises(RuntimeError):
        cli.cmd_tick_reminders(Args)

    rows = audit.query(db, None, "tick.error", None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["where"] == "reminders"
    assert "kaboom" in rows[0]["payload"]["error"]


def test_tick_car_records_tick_error_and_reraises(db, monkeypatch):
    def boom(conn, now_utc=None):
        raise RuntimeError("kaboom-car")
    monkeypatch.setattr(tick, "car", boom)

    with pytest.raises(RuntimeError):
        cli.cmd_tick_car(Args)

    rows = audit.query(db, None, "tick.error", None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["where"] == "car"
    assert "kaboom-car" in rows[0]["payload"]["error"]
