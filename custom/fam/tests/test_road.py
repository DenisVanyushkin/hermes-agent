import json

import pytest

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

EVENT_NO_COORDS_PLACE_15 = {
    "id": 3,
    "travel_min": None,
    "place": {"lat": None, "lon": None, "travel_min": 15},
}

EVENT_NO_COORDS_NONE = {
    "id": 4,
    "travel_min": None,
    "place": {"lat": None, "lon": None, "travel_min": 0},
}


NOW = "2026-07-11T10:00:00+00:00"


def test_tomtom_parses_travel_time(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    body = json.dumps({"routes": [{"summary": {
        "travelTimeInSeconds": 1520, "trafficDelayInSeconds": 300}}]}).encode()
    monkeypatch.setattr(road, "_http_get", lambda url, timeout: body)
    mins = road.tomtom_route_minutes(43.24, 76.89, 43.23, 76.78,
                                      "2026-07-13T04:00:00+00:00", CFG)
    assert mins == 26  # ceil(1520/60)


def test_tomtom_url_contains_traffic_and_depart(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    seen = {}

    def fake(url, timeout):
        seen["url"] = url
        return json.dumps({"routes": [{"summary": {"travelTimeInSeconds": 60}}]}).encode()

    monkeypatch.setattr(road, "_http_get", fake)
    road.tomtom_route_minutes(43.24, 76.89, 43.23, 76.78,
                               "2026-07-13T04:00:00+00:00", CFG)
    assert "traffic=true" in seen["url"] and "departAt=2026-07-13T04%3A00%3A00" in seen["url"]
    assert CFG_KEY not in seen  # ключ не логируем в тестовых утверждениях


def test_tomtom_no_key_returns_none_without_http(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    called = {}

    def fake(url, timeout):
        called["hit"] = True
        return b"{}"

    monkeypatch.setattr(road, "_http_get", fake)
    assert road.tomtom_route_minutes(43.24, 76.89, 43.23, 76.78,
                                      "2026-07-13T04:00:00+00:00", CFG) is None
    assert "hit" not in called


def test_tomtom_http_failure_returns_none(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    def fake(url, timeout):
        raise OSError("boom")

    monkeypatch.setattr(road, "_http_get", fake)
    assert road.tomtom_route_minutes(43.24, 76.89, 43.23, 76.78,
                                      "2026-07-13T04:00:00+00:00", CFG) is None


def test_straight_line_minutes_plausible_range():
    # Almaty home (~43.222,76.851) to a Лемана-ish point ~4km away
    # (~43.230, 76.882) at coef 1.4 / 30 km/h should land in a plausible
    # single-digit-to-teens minute band, not an absurd value.
    mins = road.straight_line_minutes(43.2220, 76.8512, 43.2298, 76.8823, CFG)
    assert 5 <= mins <= 15


def test_ladder_falls_back_to_straight_then_manual(monkeypatch, db):
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: None)
    mins, src = road.compute_travel_min(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert src == "straight" and mins > 0
    assert audit.query(db, None, "road.error", None)

    mins, src = road.compute_travel_min(db, EVENT_NO_COORDS_MANUAL_40, CFG, now_utc=NOW)
    assert (mins, src) == (40, "manual")


def test_ladder_place_and_none_rungs(db):
    mins, src = road.compute_travel_min(db, EVENT_NO_COORDS_PLACE_15, CFG, now_utc=NOW)
    assert (mins, src) == (15, "place")

    mins, src = road.compute_travel_min(db, EVENT_NO_COORDS_NONE, CFG, now_utc=NOW)
    assert (mins, src) == (None, "none")


def test_successful_tomtom_call_is_audited(monkeypatch, db):
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 26)
    mins, src = road.compute_travel_min(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert (mins, src) == (26, "tomtom")
    rows = audit.query(db, None, "road.call", None)
    assert rows and rows[0]["payload"] == {
        "event_id": 1, "minutes": 26, "source": "tomtom"}


def test_daily_cap_skips_tomtom(monkeypatch, db):
    monkeypatch.setattr(
        road, "tomtom_route_minutes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cap should block this call")))
    cap = CFG["road_daily_cap"]
    for _ in range(cap):
        db.execute(
            "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
            (NOW, "road.call", "test",
             json.dumps({"event_id": 999, "minutes": 5, "source": "tomtom"})))
    db.commit()

    mins, src = road.compute_travel_min(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert src == "straight" and mins > 0
    rows = audit.query(db, None, "road.cap", None)
    assert rows and rows[0]["payload"] == {"event_id": 1}
