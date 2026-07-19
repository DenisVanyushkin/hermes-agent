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
