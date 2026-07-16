import json

from fam import audit, road

CFG = {
    "road_provider": "tomtom",
    "road_home_lat": 43.2220,
    "road_home_lon": 76.8512,
    "road_coef": 1.4,
    "road_speed_kmh": 30,
    "road_daily_cap": 100,
    "road_timeout_sec": 10,
}

CFG_KEY = "sekrit-tomtom-key"

NOW = "2026-07-11T10:00:00+00:00"

EVENT_WITH_COORDS = {
    "id": 1,
    "travel_min": None,
    "place": {"lat": 43.2298, "lon": 76.8823, "travel_min": 0},
}

EVENT_NO_COORDS_MANUAL_40 = {
    "id": 2,
    "travel_min": 40,
    "place": {"lat": None, "lon": None, "travel_min": 0},
}


def test_tomtom_route_points_parses_legs(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    body = json.dumps({"routes": [{"legs": [
        {"points": [
            {"latitude": 43.24, "longitude": 76.89},
            {"latitude": 43.235, "longitude": 76.88},
        ]},
        {"points": [
            {"latitude": 43.235, "longitude": 76.88},
            {"latitude": 43.23, "longitude": 76.78},
        ]},
    ]}]}).encode()
    monkeypatch.setattr(road, "_http_get", lambda url, timeout: body)
    pts = road.tomtom_route_points(43.24, 76.89, 43.23, 76.78,
                                    "2026-07-13T04:00:00+00:00", CFG)
    assert pts == [
        (43.24, 76.89), (43.235, 76.88), (43.235, 76.88), (43.23, 76.78),
    ]


def test_tomtom_route_points_no_key_returns_none_without_http(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    called = {}

    def fake(url, timeout):
        called["hit"] = True
        return b"{}"

    monkeypatch.setattr(road, "_http_get", fake)
    assert road.tomtom_route_points(43.24, 76.89, 43.23, 76.78,
                                     "2026-07-13T04:00:00+00:00", CFG) is None
    assert "hit" not in called


def test_tomtom_route_points_http_failure_returns_none(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    def fake(url, timeout):
        raise OSError("boom")

    monkeypatch.setattr(road, "_http_get", fake)
    assert road.tomtom_route_points(43.24, 76.89, 43.23, 76.78,
                                     "2026-07-13T04:00:00+00:00", CFG) is None


def test_tomtom_route_points_malformed_response_returns_none(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(road, "_http_get", lambda url, timeout: b"{}")
    assert road.tomtom_route_points(43.24, 76.89, 43.23, 76.78,
                                     "2026-07-13T04:00:00+00:00", CFG) is None


def test_point_to_route_km_on_line_is_near_zero():
    # Almaty-ish route: straight segment along a meridian-ish line.
    route = [(43.2220, 76.8512), (43.2298, 76.8823)]
    # Midpoint of the segment should be ~0 distance from the route.
    mid_lat = (43.2220 + 43.2298) / 2
    mid_lon = (76.8512 + 76.8823) / 2
    km = road.point_to_route_km(mid_lat, mid_lon, route)
    assert km < 0.05


def test_point_to_route_km_one_km_off():
    # East-west segment at constant latitude so a pure-latitude offset is
    # exactly perpendicular to the segment (1 deg lat ~ 111.32 km).
    route = [(43.2220, 76.8512), (43.2220, 76.8823)]
    off_lat = 43.2220 + 1 / 111.32
    off_lon = 76.8650  # well within the segment's longitude span
    km = road.point_to_route_km(off_lat, off_lon, route)
    assert 0.95 <= km <= 1.05


def test_point_to_route_km_empty_route_is_inf():
    assert road.point_to_route_km(43.22, 76.85, []) == float("inf")


def test_route_for_event_no_key_falls_back_to_straight_pair(monkeypatch, db):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    pts, src = road.route_for_event(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert src == "straight"
    assert pts == [
        (CFG["road_home_lat"], CFG["road_home_lon"]),
        (EVENT_WITH_COORDS["place"]["lat"], EVENT_WITH_COORDS["place"]["lon"]),
    ]


def test_route_for_event_cap_exhausted_falls_back_and_audits(monkeypatch, db):
    monkeypatch.setattr(road, "_wall_now", lambda: NOW)
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(
        road, "tomtom_route_points",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cap should block this call")))
    cap = CFG["road_daily_cap"]
    for _ in range(cap):
        db.execute(
            "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
            (NOW, "road.call", "test",
             json.dumps({"event_id": 999, "minutes": 5, "source": "tomtom"})))
    db.commit()

    pts, src = road.route_for_event(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert src == "straight"
    rows = audit.query(db, None, "road.cap", None)
    assert rows and rows[0]["payload"] == {"event_id": 1}


def test_route_for_event_success_is_audited_with_points_count(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    fake_points = [(43.24, 76.89), (43.23, 76.78)]
    monkeypatch.setattr(road, "tomtom_route_points", lambda *a, **k: fake_points)
    pts, src = road.route_for_event(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert (pts, src) == (fake_points, "tomtom")
    rows = audit.query(db, None, "road.call", None)
    assert rows and rows[0]["payload"] == {
        "event_id": 1, "points": 2, "source": "tomtom"}


def test_route_for_event_tomtom_error_falls_back_and_audits(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(road, "tomtom_route_points", lambda *a, **k: None)
    pts, src = road.route_for_event(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert src == "straight"
    assert audit.query(db, None, "road.error", None)


def test_route_for_event_no_coords_returns_none(db):
    pts, src = road.route_for_event(db, EVENT_NO_COORDS_MANUAL_40, CFG, now_utc=NOW)
    assert (pts, src) == (None, "none")
