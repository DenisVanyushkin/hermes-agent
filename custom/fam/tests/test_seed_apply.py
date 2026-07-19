from fam import cal, people, places, plans, rem, shopping, seed

from seed_helpers import seed_db as _seed_db


def test_apply_insert_update_delete(db):
    ev = _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"].append({"id": None, "name": "Мадина", "kind": "person", "aliases": [], "members": []})
    f["Планы"][0]["title"] = "Пироги для Таи"
    f["Покупки"] = []
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    assert people.get(db, "Мадина")
    assert plans.get(db, 1)["title"] == "Пироги для Таи"
    assert shopping.list_open(db) == []
    kinds = [r[0] for r in db.execute("SELECT kind FROM audit_log WHERE kind LIKE 'seed.%'")]
    assert "seed.Люди.insert" in kinds and "seed.Покупки.delete" in kinds


def test_apply_event_delete_is_cancel(db):
    ev = _seed_db(db)
    ev_id = ev["id"]
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    seed.apply_diff(db, seed.diff(db, {**rows, "События": []}, snap)); db.commit()
    assert cal.get(db, ev_id)["status"] == "cancelled"            # soft: выпадает из среза
    assert seed.export_rows(db)["События"] == []


def test_apply_new_event_gets_reminders(db):
    _seed_db(db)
    rem.seed_default_rules(db); db.commit()  # fresh db has no reminder_rules by default
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {**rows, "События": rows["События"] + [{"id": None, "title": "Стоматолог",
         "start": "2026-08-03 09:00", "end": None, "place": "Invictus", "transport": "walk",
         "participants": [], "prep_min": None, "notes": None}]}
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    n = db.execute("SELECT COUNT(*) FROM reminders r JOIN events e ON e.id=r.event_id "
                   "WHERE e.title='Стоматолог'").fetchone()[0]
    assert n > 0                                                  # cal.add построил цепочку


def test_apply_place_gis_url_resolves(db, monkeypatch):
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    monkeypatch.setattr(seed.geo2gis, "resolve_place_coords", lambda url: (43.2, 76.9))
    f = {**rows, "Места": rows["Места"] + [{"id": None, "name": "Аптека 36.6",
         "gis_url": "https://go.2gis.com/abc", "category": "pharmacy",
         "address": None, "lat": None, "lon": None, "travel_min": None,
         "aliases": [], "notes": None}]}
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    got = places.get(db, "Аптека 36.6")
    # resolve_place_coords contract is (lat, lon) -- see geo2gis.py.
    assert got["lat"] == 43.2
    assert got["lon"] == 76.9


def test_apply_series_update_regenerates(db):
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Серии"][0]["weekdays"] = "tue"
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    wd = {r[0] for r in db.execute(
        "SELECT strftime('%w', start_utc, '+5 hours') FROM events "
        "WHERE series_id=1 AND status='active' AND start_utc > datetime('now')")}
    assert wd == {"2"}                                            # только вторники


def test_apply_series_update_preserves_off_grid_occurrence(db):
    """A series schedule update must only cancel FUTURE occurrences still on
    the series grid (local HH:MM == the series' OLD start_time). An
    occurrence individually moved off-grid via cal.update keeps its
    series_id and survives untouched; other future on-grid occurrences are
    regenerated on the new weekdays. Also confirms generate()'s existing
    tombstone-skip behavior: the cancelled tombstone cal.update leaves at
    the occurrence's original slot still blocks regeneration there.
    """
    _seed_db(db)

    row = db.execute(
        "SELECT id, start_utc FROM events WHERE series_id=1 AND status='active' "
        "ORDER BY start_utc LIMIT 1").fetchone()
    event_id, old_start_utc = row["id"], row["start_utc"]
    dow_map = {"0": "sun", "1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat"}
    orig_day = dow_map[db.execute(
        "SELECT strftime('%w', ?, '+5 hours')", (old_start_utc,)).fetchone()[0]]

    # Reschedule this one occurrence off the 10:00 grid (same day, +1h local).
    from datetime import datetime, timedelta

    new_start = (datetime.fromisoformat(old_start_utc) + timedelta(hours=1)).isoformat(
        timespec="seconds")
    moved = cal.update(db, event_id, start_utc=new_start)
    moved_start_utc = moved["start_utc"]
    db.commit()

    # Original slot is now a cancelled tombstone (cal.update's own behavior).
    tomb = db.execute("SELECT status FROM events WHERE series_id=1 AND start_utc=?",
                       (old_start_utc,)).fetchone()
    assert tomb["status"] == "cancelled"

    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Серии"]) if r["title"] == "Тренировка")
    f["Серии"][idx]["weekdays"] = f"{orig_day},sun"  # schedule change; keeps orig_day on the grid
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()

    # The manually-moved occurrence survives, untouched, at its custom time.
    still = cal.get(db, event_id)
    assert still["status"] == "active"
    assert still["start_utc"] == moved_start_utc

    # The original (now-tombstoned) slot is still not regenerated -- generate()
    # keeps skipping an occupied (series_id, start_utc) slot even though
    # orig_day is still on the new grid.
    tomb2 = db.execute("SELECT status FROM events WHERE series_id=1 AND start_utc=?",
                        (old_start_utc,)).fetchone()
    assert tomb2["status"] == "cancelled"

    # Every OTHER future active occurrence of the series sits on the new
    # grid: orig_day or sun, at the series' unchanged 10:00 start_time.
    others = db.execute(
        "SELECT strftime('%w', start_utc, '+5 hours') AS dow, "
        "strftime('%H:%M', start_utc, '+5 hours') AS hm FROM events "
        "WHERE series_id=1 AND status='active' AND start_utc > datetime('now') "
        "AND id != ?", (event_id,)).fetchall()
    assert others, "expected regenerated occurrences on the new grid"
    allowed_dow = {k for k, v in dow_map.items() if v in (orig_day, "sun")}
    assert all(r["dow"] in allowed_dow for r in others)
    assert all(r["hm"] == "10:00" for r in others)


def test_verify_roundtrip(db):
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Планы"][0]["title"] = "Новое"
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    assert seed.verify_roundtrip(db, f)                           # повторный экспорт == файл
    assert seed.diff(db, f, seed.make_snapshot(seed.export_rows(db))).empty
