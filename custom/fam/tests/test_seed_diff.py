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
