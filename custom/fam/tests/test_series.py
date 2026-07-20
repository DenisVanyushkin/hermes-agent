"""event_series domain: schema v7, add/get/list/cancel, weekday canon."""
import pytest

from fam import series, cal, people, places


def test_schema_v7(db):
    ver = db.execute("SELECT value FROM meta WHERE key=\x27schema_version\x27").fetchone()[0]
    assert ver == "8"
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
def test_series_copies_prep_min(db):
    from fam import rem
    people.add(db, "Денис", slug="denis")
    rem.seed_default_rules(db)
    db.commit()
    s = series.add(db, "Тренировка", "mon", "10:00",
                   participants=["Денис"], prep_min=20)
    db.commit()
    assert s["prep_min"] == 20
    created = series.generate(db, now_utc="2037-07-19T00:00:00+00:00")
    db.commit()
    assert created >= 1
    occ = db.execute(
        "SELECT id, prep_min FROM events WHERE series_id=?", (s["id"],)).fetchall()
    assert occ
    for row in occ:
        assert row["prep_min"] == 20
        fires = db.execute(
            "SELECT rule_id FROM reminders WHERE event_id=?", (row["id"],)).fetchall()
        assert fires and all(r["rule_id"] is None for r in fires)


def test_series_add_participant_future_only(db):
    from fam import rem
    people.add(db, "Тая", slug="taya")
    rem.seed_default_rules(db)
    db.commit()
    s = series.add(db, "Тренировка", "mon", "10:00")
    db.commit()
    created = series.generate(db, now_utc="2037-07-06T00:00:00+00:00")
    db.commit()
    assert created >= 2
    occ = db.execute(
        "SELECT id, start_utc FROM events WHERE series_id=? ORDER BY start_utc",
        (s["id"],)).fetchall()
    past_id = occ[0]["id"]  # 2037-07-06
    future_id = occ[2]["id"]  # 2037-07-20

    result = series.update_participants(
        db, s["id"], add=["Тая"], now_utc="2037-07-15T00:00:00+00:00")
    db.commit()

    assert result["series_id"] == s["id"]
    assert future_id in result["updated_events"]
    assert past_id not in result["updated_events"]

    past_people = {r["person_id"] for r in db.execute(
        "SELECT person_id FROM event_participants WHERE event_id=?", (past_id,))}
    future_people_names = [p["name"] for p in cal.get(db, future_id)["participants"]]
    assert past_people == set()
    assert future_people_names == ["Тая"]

    labels = {r["label"] for r in db.execute(
        "SELECT label FROM reminders WHERE event_id=? AND status='pending'",
        (future_id,))}
    assert "пора собираться" in labels  # slug:taya lead-60 stage

    s2 = series.get(db, s["id"])
    assert s2["participants"] == [
        db.execute("SELECT id FROM people WHERE slug='taya'").fetchone()["id"]]


def test_series_remove_participant(db):
    people.add(db, "Тая", slug="taya")
    db.commit()
    s = series.add(db, "Тренировка", "mon", "10:00", participants=["Тая"])
    db.commit()
    series.generate(db, now_utc="2037-07-06T00:00:00+00:00")
    db.commit()
    occ = db.execute(
        "SELECT id FROM events WHERE series_id=? ORDER BY start_utc",
        (s["id"],)).fetchall()
    future_id = occ[2]["id"]

    result = series.update_participants(
        db, s["id"], remove=["Тая"], now_utc="2037-07-15T00:00:00+00:00")
    db.commit()

    assert future_id in result["updated_events"]
    future_people = [p["name"] for p in cal.get(db, future_id)["participants"]]
    assert future_people == []
    s2 = series.get(db, s["id"])
    assert s2["participants"] == []


def test_series_update_skips_rescheduled(db):
    people.add(db, "Тая", slug="taya")
    db.commit()
    s = series.add(db, "Тренировка", "mon", "10:00")
    db.commit()
    series.generate(db, now_utc="2037-07-06T00:00:00+00:00")
    db.commit()
    occ = db.execute(
        "SELECT id, start_utc FROM events WHERE series_id=? ORDER BY start_utc",
        (s["id"],)).fetchall()
    future_id = occ[2]["id"]  # 2037-07-20 10:00 local

    # Reschedule this occurrence to 11:00 local (still future, but no longer
    # matching the series' grid slot).
    cal.update(db, future_id, start_utc="2037-07-20T06:00:00+00:00")
    db.commit()

    result = series.update_participants(
        db, s["id"], add=["Тая"], now_utc="2037-07-15T00:00:00+00:00")
    db.commit()

    assert future_id not in result["updated_events"]
    future_people = [p["name"] for p in cal.get(db, future_id)["participants"]]
    assert future_people == []


def test_series_update_unknown_person_raises(db):
    s = series.add(db, "Тренировка", "mon", "10:00")
    db.commit()
    with pytest.raises(cal.UnknownRefError):
        series.update_participants(db, s["id"], add=["НетТакого"])


def test_series_update_unknown_series_raises(db):
    with pytest.raises(ValueError):
        series.update_participants(db, 999, add=[])


# --- Final review C1: series.cancel FK-safe prep-plan cascade ---
#
# series.cancel() deletes future active occurrences outright (DELETE FROM
# events), but plans.prep_for_event_id and plans.attached_event_id both
# REFERENCE events(id), and the app runs with PRAGMA foreign_keys=ON. A
# future occurrence carrying an open prep-plan, a done prep-plan, or an
# attached plan must not turn series.cancel() into an IntegrityError -- the
# occurrence deletion has to cascade through cal._prep_cascade_cancel()
# (drops OPEN prep-plans) and null out any surviving plan's dangling
# event reference first.

def _series_with_future_occurrence(db, now_utc="2037-07-15T00:00:00+00:00"):
    """A weekly Monday series with exactly one future occurrence, plus the
    series row and that occurrence's id."""
    s = series.add(db, "Тренировка", "mon", "10:00")
    db.commit()
    series.generate(db, now_utc=now_utc)
    db.commit()
    occ = db.execute(
        "SELECT id FROM events WHERE series_id=? AND status='active' "
        "ORDER BY start_utc", (s["id"],)).fetchall()
    assert occ, "expected at least one future occurrence"
    return s, occ[-1]["id"]


def test_cancel_series_with_open_prep_plan_on_future_occurrence(db):
    from fam import plans
    s, event_id = _series_with_future_occurrence(db)
    pid = plans.add(db, "Собрать форму", prep_for_event=event_id,
                     prep_when="departure")
    db.commit()

    series.cancel(db, s["id"], now_utc="2037-07-15T00:00:00+00:00")
    db.commit()

    assert series.get(db, s["id"])["status"] == "cancelled"
    assert db.execute(
        "SELECT id FROM events WHERE id=?", (event_id,)).fetchone() is None
    p = plans.get(db, pid)
    assert p["status"] == "dropped"


def test_cancel_series_with_done_prep_plan_on_future_occurrence(db):
    from fam import plans
    s, event_id = _series_with_future_occurrence(db)
    pid = plans.add(db, "Собрать форму", prep_for_event=event_id,
                     prep_when="departure")
    db.commit()
    plans.mark(db, pid, "done")
    db.commit()

    series.cancel(db, s["id"], now_utc="2037-07-15T00:00:00+00:00")
    db.commit()

    assert series.get(db, s["id"])["status"] == "cancelled"
    assert db.execute(
        "SELECT id FROM events WHERE id=?", (event_id,)).fetchone() is None
    p = plans.get(db, pid)
    assert p["status"] == "done"
    assert p["prep_for_event_id"] is None


def test_cancel_series_with_attached_plan_on_future_occurrence(db):
    from fam import plans
    s, event_id = _series_with_future_occurrence(db)
    pid = plans.add(db, "Взять форму")
    db.commit()
    plans.attach(db, pid, event_id)
    db.commit()

    series.cancel(db, s["id"], now_utc="2037-07-15T00:00:00+00:00")
    db.commit()

    assert series.get(db, s["id"])["status"] == "cancelled"
    assert db.execute(
        "SELECT id FROM events WHERE id=?", (event_id,)).fetchone() is None
    p = plans.get(db, pid)
    assert p["status"] == "open"
    assert p["attached_event_id"] is None

