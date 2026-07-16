"""event_series domain: schema v7, add/get/list/cancel, weekday canon."""
import pytest

from fam import series, cal, people, places


def test_schema_v7(db):
    ver = db.execute("SELECT value FROM meta WHERE key=\x27schema_version\x27").fetchone()[0]
    assert ver == "7"
    tabs = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type=\x27table\x27")}
    assert {"event_series", "event_series_participants"} <= tabs
    cols = {r["name"] for r in db.execute("PRAGMA table_info(events)")}
    assert "series_id" in cols


def test_canon_weekdays():
    assert series.canon_weekdays("fri,mon,wed") == "mon,wed,fri"
    assert series.canon_weekdays(["Mon", " WED ", "mon"]) == "mon,wed"
    with pytest.raises(ValueError):
        series.canon_weekdays("funday")
    with pytest.raises(ValueError):
        series.canon_weekdays("")


def test_add_creates_series_with_place_and_participants(db):
    places.add(db, "Invictus"); people.add(db, "Амина"); db.commit()
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00",
                   place="Invictus", participants=["Амина"])
    db.commit()
    assert s["weekdays"] == "mon,wed,fri"
    assert s["start_time"] == "10:00" and s["end_time"] == "12:00"
    assert s["status"] == "active"
    assert len(s["participants"]) == 1
    from fam import audit
    assert audit.query(db, None, "cal.series.add", None)


def test_add_rejects_bad_time_and_day(db):
    with pytest.raises(ValueError):
        series.add(db, "X", "mon", "25:99")
    with pytest.raises(ValueError):
        series.add(db, "X", "notaday", "10:00")


def test_add_unknown_place_raises(db):
    with pytest.raises(cal.UnknownRefError):
        series.add(db, "X", "mon", "10:00", place="НетТакого")


def test_list_active_and_cancel(db):
    s = series.add(db, "Тренировка", "mon", "10:00"); db.commit()
    assert len(series.list_active(db)) == 1
    series.cancel(db, s["id"]); db.commit()
    assert series.list_active(db) == []
    assert series.get(db, s["id"])["status"] == "cancelled"


def test_cancel_unknown_raises(db):
    with pytest.raises(ValueError):
        series.cancel(db, 999)
