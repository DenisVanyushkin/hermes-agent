from fam import shopping, seed

from seed_helpers import seed_db as _seed_db


def test_diff_classifies(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Планы"][0]["title"] = "Пироги для Таи"                     # update
    f["Покупки"] = []                                             # delete
    f["Люди"].append({"id": None, "name": "Мадина", "kind": "person"})  # insert
    d = seed.diff(db, f, snap)
    assert d.updates["Планы"][0]["changes"]["title"][1] == "Пироги для Таи"
    assert [x["id"] for x in d.deletes["Покупки"]] == [1]
    assert d.inserts["Люди"][0]["name"] == "Мадина"
    assert not d.has_conflicts


def test_delete_bounded_by_snapshot(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    shopping.add(db, "Хлеб"); db.commit()          # появилось ПОСЛЕ экспорта
    d = seed.diff(db, {**rows, "Покупки": []}, snap)
    assert [x["id"] for x in d.deletes["Покупки"]] == [1]          # «Хлеб» (id=2) неприкосновенен


def test_conflicts_block(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["События"].append({"id": None, "title": "Поездка", "start": "2026-08-02 10:00",
                         "place": "Неизвестное место", "transport": None})
    d = seed.diff(db, f, snap)
    reasons = " ".join(c["reason"] for c in d.conflicts["События"])
    assert "Неизвестное место" in reasons and "транспорт" in reasons


def test_unknown_id_is_conflict(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    d = seed.diff(db, {**rows, "Планы": [{"id": 999, "title": "x"}]}, snap)
    assert d.conflicts["Планы"]


def test_empty_diff_is_empty(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    d = seed.diff(db, f, snap)
    assert d.empty
    assert not d.has_conflicts


def test_format_report_mentions_counts(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"].append({"id": None, "name": "Мадина", "kind": "person"})
    d = seed.diff(db, f, snap)
    report = seed.format_report(d)
    assert "Люди" in report
    assert "➕" in report


def test_ref_to_in_file_inserted_place_is_not_conflict(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = f["Места"] + [{"id": None, "name": "Аптека 36.6"}]
    f["События"] = f["События"] + [{"id": None, "title": "Поездка в аптеку",
                                      "start": "2026-08-02 10:00",
                                      "place": "Аптека 36.6", "transport": "car"}]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert any(r["name"] == "Аптека 36.6" for r in d.inserts["Места"])
    assert any(r["place"] == "Аптека 36.6" for r in d.inserts["События"])


def test_ref_to_in_file_inserted_person_is_not_conflict(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"] = f["Люди"] + [{"id": None, "name": "Мадина", "kind": "person"}]
    f["Планы"] = f["Планы"] + [{"id": None, "title": "Позвонить", "person": "Мадина"}]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert any(r["name"] == "Мадина" for r in d.inserts["Люди"])
    assert any(r["person"] == "Мадина" for r in d.inserts["Планы"])


def test_ref_to_unknown_name_still_conflict(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Планы"] = f["Планы"] + [{"id": None, "title": "Позвонить", "person": "Никто Такой"}]
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    reasons = " ".join(c["reason"] for c in d.conflicts["Планы"])
    assert "Никто Такой" in reasons


def test_delete_of_referenced_place_is_conflict(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = [r for r in f["Места"] if r["name"] != "Invictus"]   # drop referenced place
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    assert d.conflicts["Места"]


def test_person_with_id_equal_to_place_id_can_be_deleted(db):
    """Regression: _person_referenced_outside_file had a bogus check
    SELECT 1 FROM people WHERE home_place_id=? that could false-positive
    if person_id happened to equal some place_id. The check was removed
    entirely (home_place_id is a foreign key to places, not people).
    Verify that a person with no external references can be deleted cleanly,
    even if their id numerically equals some place_id in the database.
    """
    from fam import people, places

    _seed_db(db)

    # Create a place with a known id
    place_dict = places.add(db, "Новое место", address="Тестовое место")
    place_id = place_dict["id"]

    # Create a person with the same id as the place (by direct insert)
    from datetime import datetime, timezone
    person_id = place_id
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO people (id, name, kind, created_at) VALUES (?, 'Петя', 'person', ?)",
        (person_id, now)
    )

    # Set the place as someone else's home (so place is referenced)
    person_dict_1 = people.add(db, "Вася", kind="person")
    people.set_home(db, person_dict_1["id"], place_id)
    db.commit()

    # Export and take snapshot
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)

    # File form: delete Петя (person with id=place_id), no other changes
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"] = [r for r in f["Люди"] if r.get("name") != "Петя"]

    d = seed.diff(db, f, snap)

    # Should be a clean delete, not a conflict
    # (The bug would have incorrectly marked it as conflict because
    # the bogus check would find the home_place_id reference)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"
    assert any(r.get("name") == "Петя" for r in d.deletes.get("Люди", []))
    assert not any(r.get("name") == "Петя" for r in d.conflicts.get("Люди", []))


# -- Final-review findings ---------------------------------------------------

def test_group_name_in_event_participants_is_conflict(db):
    """FINDING 2: группа в «участники» события -- конфликт на этапе diff."""
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["События"].append({"id": None, "title": "Чай", "start": "2026-08-02 10:00",
                         "participants": ["татешки"]})
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    reasons = " ".join(c["reason"] for c in d.conflicts["События"])
    assert "татешки" in reasons and "не группу" in reasons


def test_in_file_inserted_group_in_participants_is_conflict(db):
    """FINDING 2: группа, вставляемая этим же файлом, тоже конфликт."""
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"].append({"id": None, "name": "подруги", "kind": "group"})
    f["Серии"].append({"id": None, "title": "Посиделки", "weekdays": "mon",
                       "start_time": "18:00", "participants": ["подруги"]})
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    reasons = " ".join(c["reason"] for c in d.conflicts["Серии"])
    assert "подруги" in reasons and "не группу" in reasons


def test_time_cell_coerced_by_excel_is_accepted(db):
    """FINDING 3: Excel отдаёт datetime.time вместо строки -- это валидно."""
    import datetime as dt

    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Серии"][0]["start_time"] = dt.time(9, 30)
    d = seed.diff(db, f, snap)                     # не должно бросить TypeError
    assert not d.has_conflicts
    assert d.updates["Серии"][0]["changes"]["start_time"][1] == "09:30"


def test_datetime_cell_in_event_start_is_accepted(db):
    """FINDING 3: datetime в «начало» события -- принимается как строка."""
    import datetime as dt

    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["События"][0]["start"] = dt.datetime(2026, 8, 2, 10, 0)
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert d.updates["События"][0]["changes"]["start"][1] == "2026-08-02 10:00"


def test_garbage_time_string_is_conflict_not_traceback(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Серии"][0]["start_time"] = "около девяти"
    d = seed.diff(db, f, snap)                     # никаких исключений
    assert d.conflicts["Серии"]


def test_lat_with_russian_decimal_comma_is_parsed(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    f["Места"][idx]["lat"] = "43,5"
    f["Места"][idx]["lon"] = "76,9"
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    changes = d.updates["Места"][0]["changes"]
    assert changes["lat"][1] == 43.5
    assert changes["lon"][1] == 76.9


def test_unparseable_lat_is_conflict_not_traceback(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    f["Места"][idx]["lat"] = "сорок три"
    d = seed.diff(db, f, snap)
    assert d.conflicts["Места"]


def test_gis_url_resolution_failure_is_conflict(db, monkeypatch):
    _seed_db(db)
    monkeypatch.setattr(seed.geo2gis, "resolve_place_coords", lambda url: None)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    f["Места"][idx]["gis_url"] = "https://go.2gis.com/broken"
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    reasons = " ".join(c["reason"] for c in d.conflicts["Места"])
    assert "не удалось развернуть 2ГИС-ссылку" in reasons and "broken" in reasons


def test_legacy_unchanged_row_place_without_transport_is_noop(db):
    """Bug repro: a series row that predates the transport guardrail (place
    set, transport='unknown') must NOT trip the "место задано, но не задан
    транспорт" conflict when the file row is byte-for-byte the same as the
    current DB state (round-tripped export, untouched by a human). A no-op
    row must be skipped before any conflict checks run.
    """
    from fam import series

    _seed_db(db)
    series.add(db, "Старая серия", "tue", "09:00", place="Invictus")  # transport default "unknown"
    db.commit()

    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}   # untouched round-trip

    d = seed.diff(db, f, snap)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"
    assert d.empty


def test_legacy_row_place_without_transport_edited_is_still_conflict(db):
    """Same legacy row (place set, transport unknown), but this time the
    file actually changes a field on it (title) -- the transport conflict
    must still fire, since this is a genuine update, not a no-op.
    """
    from fam import series

    _seed_db(db)
    series.add(db, "Старая серия", "tue", "09:00", place="Invictus")  # transport default "unknown"
    db.commit()

    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Серии"]) if r["title"] == "Старая серия")
    f["Серии"][idx]["title"] = "Старая серия (изменено)"

    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    reasons = " ".join(c["reason"] for c in d.conflicts["Серии"])
    assert "место задано, но не задан транспорт" in reasons


def test_empty_travel_min_cell_against_zero_is_noop(db):
    """places.travel_min is NOT NULL DEFAULT 0. A cleared/blank cell must
    normalize to 0, not None, so a file where the operator emptied the
    column round-trips against a DB value of 0 with no proposed change."""
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    assert rows["Места"][idx]["travel_min"] == 0        # baseline: unset in DB
    f["Места"][idx]["travel_min"] = None                # operator cleared the cell
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert d.empty
    assert d.updates["Места"] == []


def test_travel_min_cell_zero_to_value_is_update(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    f["Места"][idx]["travel_min"] = 15
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert d.updates["Места"][0]["changes"]["travel_min"] == (0, 15)


def test_garbage_travel_min_is_conflict_not_crash(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    idx = next(i for i, r in enumerate(f["Места"]) if r["name"] == "Казакова")
    f["Места"][idx]["travel_min"] = "минут пятнадцать"
    d = seed.diff(db, f, snap)                          # никаких исключений
    assert d.conflicts["Места"]


def test_delete_place_referenced_only_by_cancelled_event_is_allowed(db):
    """Live case: a place referenced only by cancelled events (pilot junk)
    must be deletable -- only LIVE (active) references block a delete."""
    from datetime import datetime, timedelta, timezone

    from fam import cal, places

    _seed_db(db)
    places.add(db, "Студия танцев", address="ул. Тестовая 1")
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    ev = cal.add(db, "Отменённая тренировка", future, place="Студия танцев", transport="car")
    cal.cancel(db, ev["id"])
    db.commit()

    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = [r for r in f["Места"] if r["name"] != "Студия танцев"]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"
    assert any(r["name"] == "Студия танцев" for r in d.deletes["Места"])


def test_delete_place_referenced_by_active_event_still_conflicts(db):
    _seed_db(db)  # baseline "ДР" event already references "Invictus", active
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = [r for r in f["Места"] if r["name"] != "Invictus"]
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    assert d.conflicts["Места"]


def test_delete_person_referenced_only_by_dropped_plan_is_allowed(db):
    from fam import people

    _seed_db(db)
    p = people.add(db, "Дропнутый", kind="person")
    db.execute("INSERT INTO plans (title, person_id, status, created_at) "
               "VALUES ('Старый план', ?, 'dropped', datetime('now'))", (p["id"],))
    db.commit()

    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"] = [r for r in f["Люди"] if r["name"] != "Дропнутый"]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts, f"Unexpected conflicts: {d.conflicts}"
    assert any(r["name"] == "Дропнутый" for r in d.deletes["Люди"])


def test_delete_person_referenced_by_open_plan_still_conflicts(db):
    from fam import people

    _seed_db(db)
    p = people.add(db, "Активный", kind="person")
    db.execute("INSERT INTO plans (title, person_id, status, created_at) "
               "VALUES ('Живой план', ?, 'open', datetime('now'))", (p["id"],))
    db.commit()

    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Люди"] = [r for r in f["Люди"] if r["name"] != "Активный"]
    d = seed.diff(db, f, snap)
    assert d.has_conflicts
    assert d.conflicts["Люди"]


def test_gis_url_resolved_once_per_unique_url(db, monkeypatch):
    _seed_db(db)
    calls = []
    monkeypatch.setattr(seed.geo2gis, "resolve_place_coords",
                        lambda url: calls.append(url) or (43.2, 76.9))
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"].append({"id": None, "name": "Точка А", "gis_url": "https://go.2gis.com/one"})
    f["Места"].append({"id": None, "name": "Точка Б", "gis_url": "https://go.2gis.com/one"})
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert calls == ["https://go.2gis.com/one"]           # кэш: один вызов на url


def test_ref_written_with_hyphen_matches_in_file_place_written_with_space(db):
    # A reference cell ("Гуля-Тате") must match an in-file inserted place
    # ("Гуля Тате") that differs only by separator/case (fam.textnorm.fold),
    # not just an exact string match.
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    f = {s: [dict(r) for r in v] for s, v in rows.items()}
    f["Места"] = f["Места"] + [{"id": None, "name": "Гуля Тате"}]
    f["События"] = f["События"] + [{"id": None, "title": "К Гуле",
                                      "start": "2026-08-02 10:00",
                                      "place": "Гуля-Тате", "transport": "car"}]
    d = seed.diff(db, f, snap)
    assert not d.has_conflicts
    assert any(r["name"] == "Гуля Тате" for r in d.inserts["Места"])
    assert any(r["place"] == "Гуля-Тате" for r in d.inserts["События"])
