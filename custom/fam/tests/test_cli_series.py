"""CLI: cal add --repeat, cal series list/cancel, tick cal-gen."""
from datetime import datetime

from fam import cli, cal


def _series_events(db, sid):
    return db.execute("SELECT start_utc FROM events WHERE series_id=?",
                      (sid,)).fetchall()


def test_cal_add_repeat_creates_series_and_occurrences(db, capsys):
    assert cli.main(["places", "add", "Invictus"]) == 0
    rc = cli.main(["cal", "add", "--title", "Тренировка", "--repeat", "weekly",
                   "--days", "mon,wed,fri", "--start-time", "10:00",
                   "--end-time", "12:00", "--place", "Invictus",
                   "--transport", "car"])
    assert rc == 0
    sid = db.execute("SELECT id FROM event_series").fetchone()["id"]
    evs = _series_events(db, sid)
    assert len(evs) > 0
    # every occurrence lands on mon/wed/fri
    for r in evs:
        wd = datetime.fromisoformat(r["start_utc"]).astimezone(cal.ALMATY).weekday()
        assert wd in {0, 2, 4}


def test_cal_add_repeat_requires_days_and_time(db):
    assert cli.main(["cal", "add", "--title", "X", "--repeat", "weekly",
                     "--start-time", "10:00"]) == 2      # no --days
    assert cli.main(["cal", "add", "--title", "X", "--repeat", "weekly",
                     "--days", "mon"]) == 2               # no --start-time


def test_cal_add_without_start_or_repeat_errors(db):
    assert cli.main(["cal", "add", "--title", "X"]) == 2


def test_cal_add_bad_day_errors(db):
    assert cli.main(["cal", "add", "--title", "X", "--repeat", "weekly",
                     "--days", "funday", "--start-time", "10:00"]) == 2


def test_cal_series_list_and_cancel(db, capsys):
    cli.main(["cal", "add", "--title", "Тренировка", "--repeat", "weekly",
              "--days", "mon,wed,fri", "--start-time", "10:00"])
    capsys.readouterr()
    assert cli.main(["cal", "series", "list"]) == 0
    out = capsys.readouterr().out
    assert "Тренировка" in out and "mon,wed,fri" in out
    sid = db.execute("SELECT id FROM event_series").fetchone()["id"]
    assert cli.main(["cal", "series", "cancel", str(sid)]) == 0
    assert db.execute("SELECT status FROM event_series WHERE id=?",
                      (sid,)).fetchone()["status"] == "cancelled"


def test_cal_series_cancel_unknown_errors(db):
    assert cli.main(["cal", "series", "cancel", "999"]) == 2


def test_tick_cal_gen_idempotent(db, capsys):
    cli.main(["cal", "add", "--title", "Тренировка", "--repeat", "weekly",
              "--days", "mon,wed,fri", "--start-time", "10:00"])
    sid = db.execute("SELECT id FROM event_series").fetchone()["id"]
    c1 = len(_series_events(db, sid))
    capsys.readouterr()
    assert cli.main(["tick", "cal-gen"]) == 0
    c2 = len(_series_events(db, sid))
    assert c2 == c1  # already materialized at add-time -> no dupes


def test_regular_cal_add_unchanged(db, capsys):
    rc = cli.main(["cal", "add", "--title", "Разовое",
                   "--start", "2026-12-31T10:00:00+05:00"])
    assert rc == 0
    assert db.execute("SELECT COUNT(*) c FROM events WHERE series_id IS NULL "
                      "AND title=\x27Разовое\x27").fetchone()["c"] == 1


def test_cal_add_series_place_without_transport_exit_2(db, capsys):
    assert cli.main(["places", "add", "Invictus"]) == 0
    rc = cli.main(["cal", "add", "--title", "Тренировка", "--repeat", "weekly",
                   "--days", "mon,wed,fri", "--start-time", "10:00",
                   "--end-time", "12:00", "--place", "Invictus"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--transport car|walk|public" in captured.err
    assert db.execute("SELECT COUNT(*) c FROM event_series").fetchone()["c"] == 0
