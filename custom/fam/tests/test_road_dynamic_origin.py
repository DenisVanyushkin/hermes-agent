"""road.py must actually ROUTE from whereami's origin, not just consult it.

The pre-existing road tests all pass unchanged after the origin became
dynamic -- precisely because none of them have a car row or a location
hint, so every rung falls through to home and the behaviour is
bit-identical. That is the compatibility guarantee, but it means none of
them would notice if _origin_for's result were computed and then thrown
away. These tests close that hole by making the origin observably
different from home.
"""
from fam import cal, places, road, whereami

HOME_LAT, HOME_LON = 43.197391, 76.872737
# ~9 km away -- far enough that a straight-line estimate from here can
# never be confused with one from home
AWAY_LAT, AWAY_LON = 43.26, 76.94
DEST_LAT, DEST_LON = 43.20, 76.95

NOW = "2026-07-29T10:00:00+00:00"
SOON = "2026-07-29T11:00:00+00:00"


def _cfg():
    return {"road_home_lat": HOME_LAT, "road_home_lon": HOME_LON,
            "road_coef": 1.4, "road_speed_kmh": 30}


def _event_with_place(db):
    places.add(db, "Театр", lat=DEST_LAT, lon=DEST_LON)
    db.commit()
    e = cal.add(db, "Спектакль", SOON, place="Театр")
    db.commit()
    return cal.get(db, e["id"])


def _park_car_away(db, when=NOW):
    from datetime import datetime
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)",
        (when, AWAY_LAT, AWAY_LON,
         int(datetime.fromisoformat(when).timestamp()), 0))
    db.commit()


def test_travel_time_is_measured_from_the_car_not_from_home(db, monkeypatch):
    """No TOMTOM_API_KEY -> the ladder lands on the straight-line rung,
    which is a pure function of the origin. Two different origins must
    therefore give two different numbers."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event = _event_with_place(db)

    from_home, src_home = road.compute_travel_min(db, event, _cfg(), now_utc=NOW)
    assert src_home == "straight"

    _park_car_away(db)
    from_car, src_car = road.compute_travel_min(db, event, _cfg(), now_utc=NOW)
    assert src_car == "straight"

    assert from_car != from_home, (
        "origin resolved but ignored: the car is 9 km from home, the "
        "estimate cannot be identical")
    # sanity-check the direction: the car is genuinely nearer the theatre
    expected = road.straight_line_minutes(
        AWAY_LAT, AWAY_LON, DEST_LAT, DEST_LON, _cfg())
    assert from_car == expected


def test_route_polyline_starts_at_the_car(db, monkeypatch):
    """The straight rung returns a two-point degenerate polyline. Its
    first point is what plans.match_enroute measures its corridor from,
    so this is what makes "по пути" follow her actual route."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event = _event_with_place(db)
    _park_car_away(db)

    points, source = road.route_for_event(db, event, _cfg(), now_utc=NOW)
    assert source == "straight"
    assert points[0] == (AWAY_LAT, AWAY_LON)
    assert points[-1] == (DEST_LAT, DEST_LON)


def test_a_car_at_home_reproduces_the_old_numbers(db, monkeypatch):
    """The compatibility guarantee, asserted rather than assumed."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event = _event_with_place(db)
    baseline, _ = road.compute_travel_min(db, event, _cfg(), now_utc=NOW)

    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)", (NOW, HOME_LAT, HOME_LON, 1785312000, 0))
    db.commit()

    again, _ = road.compute_travel_min(db, event, _cfg(), now_utc=NOW)
    assert again == baseline


def test_recompute_road_persists_and_uses_the_dynamic_origin(db, monkeypatch):
    """End-to-end through cal.recompute_road, the path fam cal add and
    `fam road` both take."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    event = _event_with_place(db)
    _park_car_away(db)

    out = cal.recompute_road(db, event["id"])
    db.commit()

    assert out["source"] == "straight"
    assert out["minutes"] == road.straight_line_minutes(
        AWAY_LAT, AWAY_LON, DEST_LAT, DEST_LON, _cfg())
    stored = db.execute("SELECT travel_min_road FROM events WHERE id=?",
                        (event["id"],)).fetchone()["travel_min_road"]
    assert stored == out["minutes"]


def test_recompute_road_survives_home_being_unset_when_a_hint_exists(db, monkeypatch):
    """The old guard bailed out on road_home_lat being None. A shared
    location is a perfectly good origin without any home configured."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config",
                        lambda: {"road_coef": 1.4, "road_speed_kmh": 30})
    event = _event_with_place(db)
    db.execute(
        "INSERT INTO location_hints(source,lat,lon,label,ts_utc,expires_utc)"
        " VALUES ('shared',?,?,'',?,?)",
        (AWAY_LAT, AWAY_LON, NOW, "2099-01-01T00:00:00+00:00"))
    db.commit()

    out = cal.recompute_road(db, event["id"])
    assert out.get("reason") != "no_home_config"
    assert out["source"] == "straight"


def test_origin_is_resolved_per_event_not_once_globally(db, monkeypatch):
    """Two events, one of which is itself the current-location candidate:
    the ongoing event must not become its own origin."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    places.add(db, "Зал", lat=AWAY_LAT, lon=AWAY_LON)
    db.commit()
    ongoing = cal.add(db, "Йога", "2026-07-29T09:30:00+00:00", place="Зал")
    db.execute("UPDATE events SET end_utc=? WHERE id=?",
               ("2026-07-29T11:00:00+00:00", ongoing["id"]))
    db.commit()

    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW,
                                event=cal.get(db, ongoing["id"]))
    assert o["source"] == "home"
