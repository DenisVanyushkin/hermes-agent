"""Materialized occurrences behave as ordinary events (grid/range/done/reminders)."""
from fam import cli, series, places, cal

NOW = "2026-07-13T00:00:00+05:00"


def test_occurrences_visible_in_range_and_gridable(db, capsys):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00")
    db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    capsys.readouterr()
    rc = cli.main(["cal", "range", "2026-07-13T00:00:00+05:00",
                   "2026-07-20T00:00:00+05:00"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Тренировка" in out


def test_occurrence_carries_place(db):
    places.add(db, "Invictus", lat=43.205156, lon=76.899298); db.commit()
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00",
                   place="Invictus", transport="car"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    ev = db.execute("SELECT id FROM events WHERE series_id=? LIMIT 1",
                    (s["id"],)).fetchone()
    got = cal.get(db, ev["id"])                       # a materialized occurrence
    assert got["place"]["name"] == "Invictus"        # is an ordinary event with place
    assert got["transport"] == "car"
    assert got["end_utc"] is not None
    assert got["series_id"] == s["id"]


def test_done_on_occurrence_does_not_touch_series(db):
    s = series.add(db, "Тренировка", "mon", "10:00"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    ev = db.execute("SELECT id FROM events WHERE series_id=? ORDER BY start_utc "
                    "LIMIT 1", (s["id"],)).fetchone()
    cal.done(db, ev["id"]); db.commit()
    assert db.execute("SELECT status FROM events WHERE id=?",
                      (ev["id"],)).fetchone()["status"] == "done"
    assert series.get(db, s["id"])["status"] == "active"
