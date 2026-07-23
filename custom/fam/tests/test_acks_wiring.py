"""The pending-ack snapshot must be refreshed by the writers of the state
it projects: the minute tick (which creates/sends dose reminders) and the
med ack commands (which resolve them). A snapshot nobody refreshes is
worse than none -- it makes the gateway inject an answered question.
"""
import json

import pytest

from fam import acks, gate, meds, tick

CFG = {
    "target": "whatsapp:+77011102626",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "max_len_reminder": 300,
    "reminder_max_age_min": 120,
    "med_repeat_min": 45,
}

NOW = "2026-07-23T04:25:00+00:00"  # 09:25 Almaty


@pytest.fixture()
def snap_path(tmp_path, monkeypatch):
    path = tmp_path / "pending-acks.json"
    monkeypatch.setattr(acks, "resolve_path", lambda: path)
    return path


def _pending_intake(db, plan_ts_utc="2026-07-23T04:00:00+00:00"):
    med_id = meds.add(db, "мисол", ["09:00"], dose="1 таблетка", remaining=10)
    db.commit()
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, taken_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?,?)",
        (med_id, plan_ts_utc, None, "pending", None, plan_ts_utc),
    )
    db.commit()
    return cur.lastrowid


def _items(path):
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def test_tick_reminders_refreshes_snapshot(db, snap_path, monkeypatch):
    monkeypatch.setattr(gate, "deliver",
                        lambda *a, **kw: {"status": "sent", "message_id": "x"})
    intake_id = _pending_intake(db)

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert [i["id"] for i in _items(snap_path)] == [intake_id]


def test_tick_survives_a_broken_snapshot(db, monkeypatch):
    """A snapshot failure must never sink the tick."""
    monkeypatch.setattr(gate, "deliver",
                        lambda *a, **kw: {"status": "sent", "message_id": "x"})
    monkeypatch.setattr(acks, "write", lambda *a, **kw: 1 / 0)
    _pending_intake(db)

    tick.reminders(db, now_utc=NOW, cfg=CFG)  # must not raise


def test_med_taken_clears_the_snapshot(db, snap_path):
    from fam import cli
    intake_id = _pending_intake(db)
    acks.write(db, path=snap_path, now_utc=NOW)
    assert _items(snap_path)

    assert cli.main(["med", "taken", str(intake_id)]) == 0

    assert _items(snap_path) == []


def test_med_skip_clears_the_snapshot(db, snap_path):
    from fam import cli
    intake_id = _pending_intake(db)
    acks.write(db, path=snap_path, now_utc=NOW)
    assert _items(snap_path)

    assert cli.main(["med", "skip", str(intake_id)]) == 0

    assert _items(snap_path) == []
