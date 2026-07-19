from fam import people, plans, seed

from seed_helpers import seed_db as _seed_db


def test_export_slice(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    assert [r["title"] for r in rows["События"]] == ["ДР"]          # без прошлого и без series-вхождений
    assert rows["События"][0]["participants"] == ["Аишка"]          # группа развёрнута при add — экспорт как есть
    assert rows["Люди"][1]["members"] == ["Аишка"]                  # группы несут состав
    assert rows["Люди"][0]["home"] == "Казакова"
    assert rows["Серии"][0]["weekdays"] == "mon,wed,fri"
    assert rows["Планы"][0]["link"] is None
    assert rows["Покупки"][0]["name"] == "Молоко"


def test_snapshot_ids(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    snap = seed.make_snapshot(rows)
    assert set(snap["sheets"]["События"]) == {str(rows["События"][0]["id"])}
    assert "exported_at_utc" in snap


def test_export_places_and_meds(db):
    _seed_db(db)
    rows = seed.export_rows(db)
    inv = [p for p in rows["Места"] if p["name"] == "Invictus"][0]
    assert inv["lat"] == 43.205156
    assert inv["gis_url"] is None
    med = rows["Лекарства"][0]
    assert med["name"] == "Витамин D"
    assert med["times"] == ["08:00"]
    assert med["enabled"] == 1


def test_export_plan_link(db):
    ev = _seed_db(db)
    plans.add(db, "Собрать документы", prep_for_event=ev["id"], prep_when="departure")
    db.commit()
    rows = seed.export_rows(db)
    linked = [p for p in rows["Планы"] if p["title"] == "Собрать документы"][0]
    assert linked["link"] == f"подготовка к событию #{ev['id']} (ДР)"
