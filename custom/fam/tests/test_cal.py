import pytest
from fam import cal, people, places

def _seed(db):
    people.add(db, "Тая", slug="taya")
    people.add(db, "Денис", slug="denis")
    places.add(db, "Клиника Дента", aliases=["стоматолог"])
    db.commit()

def test_add_resolves_refs_and_roundtrips(db):
    _seed(db)
    e = cal.add(db, "Тае к стоматологу", "2026-07-15T05:00:00+00:00",
                place="стоматолог", participants=["Тая"], transport="car")
    db.commit()
    got = cal.get(db, e["id"])
    assert got["place"]["name"] == "Клиника Дента"
    assert [p["name"] for p in got["participants"]] == ["Тая"]
    assert got["start_local"].startswith("2026-07-15T10:00")  # Almaty = UTC+5

def test_add_unknown_person_raises_without_insert(db):
    _seed(db)
    with pytest.raises(cal.UnknownRefError):
        cal.add(db, "Обед", "2026-07-15T07:00:00+00:00", participants=["Айгуль"])
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

def test_group_participant_expands(db):
    _seed(db)
    for n in ("Мадина", "Салтанат"): people.add(db, n)
    g = people.add(db, "татешки", kind="group")
    people.add_member(db, "татешки", "Мадина"); people.add_member(db, "татешки", "Салтанат")
    e = cal.add(db, "Чай", "2026-07-16T09:00:00+00:00", participants=["татешки"])
    db.commit()
    names = {p["name"] for p in cal.get(db, e["id"])["participants"]}
    assert names == {"Мадина", "Салтанат"}

def test_day_query_uses_almaty_boundaries(db):
    _seed(db)
    cal.add(db, "Утро", "2026-07-15T01:00:00+00:00")   # 06:00 Almaty 15-го
    cal.add(db, "Ночь-до", "2026-07-14T18:00:00+00:00") # 23:00 Almaty 14-го
    db.commit()
    titles = [e["title"] for e in cal.day(db, "2026-07-15")]
    assert titles == ["Утро"]

def test_cancel_hides_from_day(db):
    _seed(db)
    e = cal.add(db, "Отменить", "2026-07-15T05:00:00+00:00"); db.commit()
    cal.cancel(db, e["id"]); db.commit()
    assert cal.day(db, "2026-07-15") == []
