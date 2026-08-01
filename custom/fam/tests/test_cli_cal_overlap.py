"""CLI: гвардрейл занятого слота (--allow-overlap) на cal add/update/серии."""
import json
from datetime import datetime, timedelta, timezone

from fam import cal, cli

PY_FMT = "%Y-%m-%dT%H:%M:%S"


def _local(days_ahead, hh, mm=0):
    """Локальное ISO-время (Алматы) через N дней — CLI-гвардрейл на прошлое
    не должен срабатывать, поэтому даты всегда считаются от «сейчас»."""
    d = (datetime.now(timezone.utc).astimezone(cal.ALMATY)
         + timedelta(days=days_ahead)).date()
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00+05:00"


def _busy(db, title="Интервизия", days_ahead=3, start_hh=10, end_hh=12, end_mm=15):
    e = cal.add(db, title, _local(days_ahead, start_hh),
                end_utc=_local(days_ahead, end_hh, end_mm))
    db.commit()
    return e


def _ack_rows(db):
    return db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.overlap_ack'"
    ).fetchall()


def test_add_into_busy_slot_exits_2_and_writes_nothing(db, capsys):
    busy = _busy(db)
    rc = cli.main(["cal", "add", "--title", "Маникюр",
                   "--start", _local(3, 11), "--end", _local(3, 12)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "overlaps 1 active event" in err
    assert "Интервизия" in err
    assert f"id={busy['id']}" in err
    assert "--allow-overlap" in err
    assert db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 1
    assert db.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE kind='cal.add'"
    ).fetchone()["c"] == 1      # только seed-событие


def test_add_back_to_back_is_allowed(db, capsys):
    _busy(db)
    rc = cli.main(["cal", "add", "--title", "Массаж",
                   "--start", _local(3, 12, 15), "--end", _local(3, 13)])
    assert rc == 0
    assert db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 2


def test_add_with_allow_overlap_writes_event_and_audits_ack(db, capsys):
    busy = _busy(db)
    rc = cli.main(["cal", "add", "--title", "Маникюр",
                   "--start", _local(3, 11), "--end", _local(3, 12),
                   "--allow-overlap", "--json"])
    assert rc == 0
    new_id = json.loads(capsys.readouterr().out)["id"]
    rows = _ack_rows(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["scope"] == "add"
    assert payload["event_id"] == new_id
    assert payload["conflicts"] == [busy["id"]]


def test_allow_overlap_without_conflicts_writes_no_ack(db, capsys):
    rc = cli.main(["cal", "add", "--title", "Йога",
                   "--start", _local(4, 9), "--allow-overlap"])
    assert rc == 0
    assert _ack_rows(db) == []


def test_error_lists_at_most_three_conflicts(db, capsys):
    for i in range(5):
        _busy(db, title=f"Дело {i}", start_hh=10, end_hh=12, end_mm=15)
    rc = cli.main(["cal", "add", "--title", "Маникюр", "--start", _local(3, 11)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "overlaps 5 active events" in err
    assert "(+2 more)" in err
