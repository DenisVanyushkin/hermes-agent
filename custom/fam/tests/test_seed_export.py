from datetime import datetime, timedelta, timezone

from fam import people, places, cal, series, plans, meds, shopping, seed


def _seed_db(conn):
    p_home = places.add(conn, "Казакова", address="ул. Казакова 12")
    inv = places.add(conn, "Invictus", lat=43.205156, lon=76.899298)
    aisha = people.add(conn, "Аишка", aliases=("Аиша",))
    people.set_home(conn, "Аишка", "Казакова")
    grp = people.add(conn, "татешки", kind="group")
    people.add_member(conn, "татешки", "Аишка")
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    ev = cal.add(conn, "ДР", future, place="Invictus", transport="car", participants=("татешки",))
    cal.add(conn, "Прошлое", past)                          # прошедшее — НЕ экспортируется
    sid = series.add(conn, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00",
                     place="Invictus", transport="car")
    series.generate(conn)                                    # вхождения — НЕ экспортируются
    plans.add(conn, "Пироги", deadline=None)
    meds.add(conn, "Витамин D", ["08:00"], remaining=30, threshold=5)
    shopping.add(conn, "Молоко", qty="1 л")
    conn.commit()
    return ev


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
