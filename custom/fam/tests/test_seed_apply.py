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
    monkeypatch.setattr(seed.geo2gis, "resolve_place_coords", lambda url: (76.9, 43.2))
    f = {**rows, "Места": rows["Места"] + [{"id": None, "name": "Аптека 36.6",
         "gis_url": "https://go.2gis.com/abc", "category": "pharmacy",
         "address": None, "lat": None, "lon": None, "travel_min": None,
         "aliases": [], "notes": None}]}
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    got = places.get(db, "Аптека 36.6")
    assert (got["lon"], got["lat"]) == (76.9, 43.2)               # 2ГИС отдаёт LON,LAT!


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


def test_verify_roundtrip(db):
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Планы"][0]["title"] = "Новое"
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    assert seed.verify_roundtrip(db, f)                           # повторный экспорт == файл
    assert seed.diff(db, f, seed.make_snapshot(seed.export_rows(db))).empty
