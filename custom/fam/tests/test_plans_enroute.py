from fam import cal, people, places, plans, road

CFG = {
    "road_home_lat": 43.2220,
    "road_home_lon": 76.8512,
    "enroute_car_km": 3.0,
    "enroute_walk_km": 0.5,
}

NOW = "2026-07-13T05:00:00+00:00"

# A straight route from home to a place east of it.
ROUTE = [(43.2220, 76.8512), (43.2298, 76.8823)]


def _seed(db):
    people.add(db, "Тая", slug="taya")
    places.add(db, "Клиника Дента", aliases=["стоматолог"], lat=43.2260, lon=76.8670)
    places.add(db, "Далеко", aliases=["далеко"], lat=43.5000, lon=77.5000)
    db.commit()


def _event(db, transport="car"):
    e = cal.add(db, "Событие", NOW, place=None, transport=transport)
    db.commit()
    return e


def test_geo_hit_in_corridor(db, monkeypatch):
    _seed(db)
    pid = plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert len(matches) == 1
    assert matches[0]["plan"]["id"] == pid
    assert matches[0]["reason"] == "geo"


def test_geo_miss_outside_corridor(db, monkeypatch):
    _seed(db)
    plans.add(db, "Далёкое дело", place="далеко")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert matches == []


def test_walk_threshold_tighter_than_car(db, monkeypatch):
    _seed(db)
    # "Клиника Дента" is roughly ~2.9km-ish from segment - use it to test
    # walk threshold rejects while car threshold (default test above)
    # accepts. We construct a place near but outside 0.5km, inside 3km.
    places.add(db, "Средне", aliases=["средне"], lat=43.2360, lon=76.8670)
    pid = plans.add(db, "Пешее дело", place="средне")
    db.commit()
    e = _event(db, transport="walk")
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))

    dist = road.point_to_route_km(43.2360, 76.8670, ROUTE)
    assert CFG["enroute_walk_km"] < dist < CFG["enroute_car_km"]

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert matches == []

    e2 = _event(db, transport="car")
    matches2 = plans.match_enroute(db, e2, CFG, now_utc=NOW)
    assert len(matches2) == 1
    assert matches2[0]["plan"]["id"] == pid


def test_person_hit_by_participant(db, monkeypatch):
    _seed(db)
    pid = plans.add(db, "Дело Таи", person="Тая")
    db.commit()
    e = cal.add(db, "Событие с Таей", NOW, participants=["Тая"], transport="car")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (None, "none"))

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert len(matches) == 1
    assert matches[0]["plan"]["id"] == pid
    assert matches[0]["reason"] == "person"


def test_attached_plan_excluded(db, monkeypatch):
    _seed(db)
    pid = plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    e = _event(db)
    other_e = cal.add(db, "Другое событие", NOW, transport="car")
    db.commit()
    plans.attach(db, pid, other_e["id"])
    db.commit()
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert matches == []


def test_geo_and_person_dedup_prefers_geo(db, monkeypatch):
    _seed(db)
    pid = plans.add(db, "Дело Таи рядом", place="стоматолог", person="Тая")
    db.commit()
    e = cal.add(db, "Событие с Таей", NOW, participants=["Тая"], transport="car")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert len(matches) == 1
    assert matches[0]["plan"]["id"] == pid
    assert matches[0]["reason"] == "geo"


def test_no_route_and_no_person_no_geo_hit(db, monkeypatch):
    _seed(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", lambda conn, ev, cfg, now_utc=None: (None, "none"))

    matches = plans.match_enroute(db, e, CFG, now_utc=NOW)
    assert matches == []
