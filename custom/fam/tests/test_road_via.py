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

HOME = (43.2220, 76.8512)
VIA = (43.2400, 76.8900)
DEST = (43.2298, 76.8823)

EVENT_WITH_COORDS = {
    "id": 1,
    "travel_min": None,
    "place": {"lat": DEST[0], "lon": DEST[1], "travel_min": 0},
}

PLAN_PLACE = {"lat": VIA[0], "lon": VIA[1]}


def _body(secs, legs_points):
    return json.dumps({"routes": [{
        "summary": {"travelTimeInSeconds": secs},
        "legs": [{"points": [{"latitude": la, "longitude": lo} for la, lo in legs_points]}],
    }]}).encode()


def test_route_via_url_has_waypoint_in_path(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    seen = {}

    def fake(url, timeout):
        seen["url"] = url
        return _body(600, [HOME, VIA, DEST])

    monkeypatch.setattr(road, "_http_get", fake)
    mins, pts, src = road.route_via(db, HOME, [VIA], DEST, CFG, now_utc=NOW)
    assert src == "tomtom"
    assert mins == 10  # ceil(600/60)
    assert pts == [HOME, VIA, DEST]
    path = seen["url"].split("/calculateRoute/")[1].split("/json")[0]
    assert path == f"{HOME[0]},{HOME[1]}:{VIA[0]},{VIA[1]}:{DEST[0]},{DEST[1]}"


def test_route_via_logs_road_call(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(road, "_http_get", lambda url, timeout: _body(300, [HOME, DEST]))
    road.route_via(db, HOME, [], DEST, CFG, now_utc=NOW)
    rows = audit.query(db, None, "road.call", None)
    assert rows and rows[0]["payload"] == {"via": 0, "minutes": 5, "source": "tomtom"}


def test_route_via_no_key_returns_none(monkeypatch, db):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    called = {}

    def fake(url, timeout):
        called["hit"] = True
        return b"{}"

    monkeypatch.setattr(road, "_http_get", fake)
    assert road.route_via(db, HOME, [VIA], DEST, CFG, now_utc=NOW) == (None, None, "none")
    assert "hit" not in called


def test_route_via_non_tomtom_provider_returns_none(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    called = {}

    def fake(url, timeout):
        called["hit"] = True
        return b"{}"

    monkeypatch.setattr(road, "_http_get", fake)
    cfg = dict(CFG, road_provider="none")
    assert road.route_via(db, HOME, [VIA], DEST, cfg, now_utc=NOW) == (None, None, "none")
    assert "hit" not in called


def test_route_via_http_failure_returns_none_and_audits_error(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    def fake(url, timeout):
        raise OSError("boom")

    monkeypatch.setattr(road, "_http_get", fake)
    assert road.route_via(db, HOME, [VIA], DEST, CFG, now_utc=NOW) == (None, None, "none")
    rows = audit.query(db, None, "road.error", None)
    assert rows and rows[0]["payload"] == {"via": 1}


def test_route_via_cap_exhausted_returns_none(monkeypatch, db):
    monkeypatch.setattr(road, "_wall_now", lambda: NOW)
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(
        road, "_http_get",
        lambda url, timeout: (_ for _ in ()).throw(AssertionError("cap should block this call")))
    cap = CFG["road_daily_cap"]
    for _ in range(cap):
        db.execute(
            "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
            (NOW, "road.call", "test", json.dumps({"minutes": 5, "source": "tomtom"})))
    db.commit()

    assert road.route_via(db, HOME, [VIA], DEST, CFG, now_utc=NOW) == (None, None, "none")
    rows = audit.query(db, None, "road.cap", None)
    assert rows and rows[0]["payload"] == {"via": 1}


def test_detour_min_is_via_minus_direct(monkeypatch, db):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    calls = []

    def fake(conn, origin, via, dest, cfg, now_utc=None):
        calls.append(list(via))
        if via:
            return 30, [origin, *via, dest], "tomtom"
        return 20, [origin, dest], "tomtom"

    monkeypatch.setattr(road, "route_via", fake)
    result = road.detour_min(db, EVENT_WITH_COORDS, PLAN_PLACE, CFG)
    assert result == 10  # 30 - 20
    assert calls == [[], [VIA]]


def test_detour_min_clamps_negative_to_zero(monkeypatch, db):
    def fake(conn, origin, via, dest, cfg, now_utc=None):
        if via:
            return 15, None, "tomtom"
        return 20, None, "tomtom"

    monkeypatch.setattr(road, "route_via", fake)
    assert road.detour_min(db, EVENT_WITH_COORDS, PLAN_PLACE, CFG) == 0


def test_detour_min_none_when_direct_leg_not_live(monkeypatch, db):
    def fake(conn, origin, via, dest, cfg, now_utc=None):
        if via:
            return 30, None, "tomtom"
        return None, None, "none"

    monkeypatch.setattr(road, "route_via", fake)
    assert road.detour_min(db, EVENT_WITH_COORDS, PLAN_PLACE, CFG) is None


def test_detour_min_none_when_via_leg_not_live(monkeypatch, db):
    def fake(conn, origin, via, dest, cfg, now_utc=None):
        if via:
            return None, None, "none"
        return 20, None, "tomtom"

    monkeypatch.setattr(road, "route_via", fake)
    assert road.detour_min(db, EVENT_WITH_COORDS, PLAN_PLACE, CFG) is None


def test_detour_min_none_without_coords(db):
    event_no_coords = {"id": 2, "place": {"lat": None, "lon": None}}
    assert road.detour_min(db, event_no_coords, PLAN_PLACE, CFG) is None
    assert road.detour_min(db, EVENT_WITH_COORDS, {"lat": None, "lon": None}, CFG) is None
