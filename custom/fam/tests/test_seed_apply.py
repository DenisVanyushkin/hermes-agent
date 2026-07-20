from datetime import datetime, timezone

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
         "address": None, "lat": None, "lon": None, "travel_min": 0,
         "aliases": [], "notes": None}]}
    # travel_min=0 (не None): в БД дефолт 0, экспорт вернёт 0 -- иначе
    # verify честно поймает расхождение (не связано с gis_url).
    d = seed.diff(db, f, snap)
    # Parse-level contract: the link is expanded into lat/lon already in the
    # diff (resolve_place_coords contract is (lat, lon) -- see geo2gis.py),
    # and gis_url is dropped from the comparison.
    ins = next(r for r in d.inserts["Места"] if r["name"] == "Аптека 36.6")
    assert ins["lat"] == 43.2 and ins["lon"] == 76.9
    assert ins["gis_url"] is None
    seed.apply_diff(db, d); db.commit()
    got = places.get(db, "Аптека 36.6")
    assert got["lat"] == 43.2
    assert got["lon"] == 76.9
    assert seed.verify_roundtrip(db, f)


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


# -- Final-review findings ---------------------------------------------------

def test_apply_place_rename(db):
    """FINDING 1: переименование места должно доходить до БД и проходить
    verify_roundtrip (раньше молча отбрасывалось _UPDATE_FIELDS)."""
    _seed_db(db)
    places.add(db, "Старый зал"); db.commit()      # никем не referenced
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Старый зал")
    rid = f["Места"][idx]["id"]
    f["Места"][idx]["name"] = "Новый зал"
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    assert places.get(db, rid)["name"] == "Новый зал"
    assert seed.verify_roundtrip(db, f)


def test_apply_gis_url_on_existing_place_updates_coords(db, monkeypatch):
    """FINDING 1: 2ГИС-ссылка на СУЩЕСТВУЮЩЕЙ строке места (lat/lon пустые)
    должна подтянуть координаты (раньше молча отбрасывалась)."""
    _seed_db(db)
    monkeypatch.setattr(seed.geo2gis, "resolve_place_coords", lambda url: (43.25, 76.95))
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    assert f["Места"][idx]["lat"] is None                      # прекондиция
    f["Места"][idx]["gis_url"] = "https://go.2gis.com/xyz"
    d = seed.diff(db, f, snap)
    upd = next(u for u in d.updates["Места"] if u["id"] == f["Места"][idx]["id"])
    assert upd["changes"]["lat"][1] == 43.25              # реальный coord-diff
    assert "gis_url" not in upd["changes"]                # ссылка -- сахар
    seed.apply_diff(db, d); db.commit()
    got = places.get(db, "Казакова")
    assert got["lat"] == 43.25
    assert got["lon"] == 76.95
    assert seed.verify_roundtrip(db, f)                   # exit-3-ловушка закрыта


def test_apply_clears_address_to_schema_default_not_null(db):
    """SYMPTOM (live import): operator clears the "адрес" cell on an
    EXISTING place (moved the value into "2ГИС-ссылка" instead) --
    normalize_row maps the blank cell to None, and places.address is
    NOT NULL DEFAULT '' in db.py. apply_diff must coerce that None to ''
    on the update path instead of crashing with a NOT NULL IntegrityError
    (and rolling back the whole import, as it did live)."""
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    assert f["Места"][idx]["address"]                          # precondition: was set
    f["Места"][idx]["address"] = None                          # cleared cell
    rid = f["Места"][idx]["id"]
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    got = places.get(db, rid)
    assert got["address"] == ""                                # schema default, never None
    assert seed.verify_roundtrip(db, f)


def test_apply_new_place_empty_address_notes(db):
    """A brand-new place row with empty address/notes cells must insert
    cleanly, landing on the schema defaults ('')."""
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {**rows, "Места": rows["Места"] + [{"id": None, "name": "Новое место",
         "address": None, "category": None, "lat": None, "lon": None,
         "gis_url": None, "travel_min": 0, "aliases": [], "notes": None}]}
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    got = places.get(db, "Новое место")
    assert got["address"] == ""
    assert got["notes"] == ""
    assert seed.verify_roundtrip(db, f)


def test_apply_new_event_no_place_empty_transport(db):
    """A new event with no place has no reason to carry a transport value
    -- an empty "транспорт" cell must land on the 'unknown' schema default,
    not crash (events.transport is NOT NULL DEFAULT 'unknown')."""
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {**rows, "События": rows["События"] + [{"id": None, "title": "Звонок",
         "start": "2026-08-05 09:00", "end": None, "place": None, "transport": None,
         "participants": [], "prep_min": None, "notes": None}]}
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    row = db.execute("SELECT transport, notes FROM events WHERE title='Звонок'").fetchone()
    assert row["transport"] == "unknown"
    assert row["notes"] == ""
    assert seed.verify_roundtrip(db, f)


def test_apply_new_shopping_and_meds_empty_optional_text(db):
    """New shopping/meds rows with empty optional text cells (qty, added_by,
    dose) must insert cleanly onto their schema defaults ('')."""
    _seed_db(db)
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {**rows,
         "Покупки": rows["Покупки"] + [{"id": None, "name": "Хлеб", "qty": None,
                                        "source": "manual", "added_by": None}],
         "Лекарства": rows["Лекарства"] + [{"id": None, "name": "Аспирин", "dose": None,
                                            "times": ["09:00"], "remaining": None,
                                            "threshold": 0, "enabled": 1}]}
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    srow = db.execute("SELECT qty, added_by FROM shopping WHERE name='Хлеб'").fetchone()
    assert srow["qty"] == "" and srow["added_by"] == ""
    mrow = db.execute("SELECT dose FROM meds WHERE name='Аспирин'").fetchone()
    assert mrow["dose"] == ""
    assert seed.verify_roundtrip(db, f)


def test_apply_gis_url_does_not_override_filled_coords(db, monkeypatch):
    """lat/lon в файле в приоритете над gis_url (контракт Col.comment)."""
    _seed_db(db)
    monkeypatch.setattr(seed.geo2gis, "resolve_place_coords",
                        lambda url: (0.0, 0.0))
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    f["Места"][idx]["lat"] = 43.1
    f["Места"][idx]["lon"] = 76.8
    f["Места"][idx]["gis_url"] = "https://go.2gis.com/xyz"
    seed.apply_diff(db, seed.diff(db, f, snap)); db.commit()
    got = places.get(db, "Казакова")
    assert got["lat"] == 43.1
    assert got["lon"] == 76.8


# -- Hard-delete FK detach (dead-row references) ---------------------------
# The diff guard (_place_referenced_outside_file / _person_referenced_
# outside_file) allows deleting a place/person still referenced by DEAD
# rows (cancelled events/series, dropped/done plans) -- those rows don't
# pin the entity in place forever. But places.place_id / event_series.
# place_id / plans.place_id / plans.person_id are plain (non-CASCADE) FKs
# (see fam/db.py init_db), so the apply-level hard DELETE must detach
# those dead references first or SQLite raises FOREIGN KEY constraint
# failed even though the diff itself was clean. Live case: "Студия танцев"
# (id 2), referenced only by two cancelled pilot events.


def test_apply_place_delete_detaches_cancelled_event_reference(db):
    """Mirrors the live incident: a place referenced ONLY by a cancelled
    event must actually be deletable by apply_diff, and the cancelled
    event must survive with place_id cleared to NULL."""
    _seed_db(db)
    places.add(db, "Студия танцев", address="ул. Тестовая 1")
    ev = cal.add(db, "Отменённая тренировка", datetime.now(timezone.utc).isoformat(),
                 place="Студия танцев", transport="car")
    cal.cancel(db, ev["id"])
    db.commit()

    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = [r for r in f["Места"] if r["name"] != "Студия танцев"]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"

    seed.apply_diff(db, d); db.commit()                    # bug: raised IntegrityError

    assert places.get(db, "Студия танцев") is None
    row = db.execute("SELECT status, place_id FROM events WHERE id=?", (ev["id"],)).fetchone()
    assert row["status"] == "cancelled"
    assert row["place_id"] is None


def test_apply_place_delete_detaches_dropped_plan_reference(db):
    """Same failure mode via a dropped plan referencing the place."""
    _seed_db(db)
    pl = places.add(db, "Студия танцев", address="ул. Тестовая 1")
    pid = db.execute(
        "INSERT INTO plans (title, place_id, status, created_at) "
        "VALUES ('Старый план', ?, 'dropped', datetime('now'))", (pl["id"],)
    ).lastrowid
    db.commit()

    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = [r for r in f["Места"] if r["name"] != "Студия танцев"]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"

    seed.apply_diff(db, d); db.commit()

    assert places.get(db, "Студия танцев") is None
    row = db.execute("SELECT status, place_id FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "dropped"
    assert row["place_id"] is None


def test_apply_person_delete_detaches_dropped_plan_reference(db):
    """A person referenced ONLY by a dropped plan must be deletable by
    apply_diff; the plan survives with person_id cleared to NULL."""
    p = people.add(db, "Дропнутый", kind="person")
    pid = db.execute(
        "INSERT INTO plans (title, person_id, status, created_at) "
        "VALUES ('Старый план', ?, 'dropped', datetime('now'))", (p["id"],)
    ).lastrowid
    db.commit()

    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"] = [r for r in f["Люди"] if r["name"] != "Дропнутый"]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"

    seed.apply_diff(db, d); db.commit()

    assert people.get(db, "Дропнутый") is None
    row = db.execute("SELECT status, person_id FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "dropped"
    assert row["person_id"] is None


def test_apply_place_delete_still_blocked_by_active_event(db):
    """Guard regression check: a place referenced by an ACTIVE event must
    still surface as a diff conflict, never reach the DELETE at all."""
    _seed_db(db)  # baseline "ДР" event already references "Invictus", active
    rows = seed.export_rows(db); snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = [r for r in f["Места"] if r["name"] != "Invictus"]
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    assert d.conflicts["Места"]
    assert places.get(db, "Invictus") is not None
