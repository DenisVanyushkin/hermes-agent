"""Phase 7b, Task 2: compute_travel_min/route_for_event pick up attached
open plans' effective place as waypoints (road.route_via), so a detour to
run an errand on the way inflates travel_min_road -- and route_for_event's
polyline reflects the same detour. Guard: a via identical to the event's
own place is never passed to route_via.
"""
import json

from fam import audit, cal, people, places, plans, road

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
NOW = "2026-07-18T05:00:00+00:00"

HOME = (43.2220, 76.8512)
DEST = (43.2298, 76.8823)
VIA = (43.2400, 76.8900)


def _event_with_place(db, monkeypatch, lat=DEST[0], lon=DEST[1], name="Клиника"):
    # cal.add()'s own road hook reads the REAL on-disk config via
    # gate.load_config() -- neutralize compute_travel_min for this add()
    # call only (same technique test_tick.py's _add_event_neutral_road
    # uses), so setup never depends on the host's live fam-config.json.
    # Restored right after, so callers see the real ladder.
    real = road.compute_travel_min
    monkeypatch.setattr(road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    places.add(db, name, lat=lat, lon=lon)
    db.commit()
    e = cal.add(db, "Событие", NOW, place=name)
    db.commit()
    monkeypatch.setattr(road, "compute_travel_min", real)
    return e


def _attach_geo_plan(db, monkeypatch, event_id, lat=VIA[0], lon=VIA[1], name="Аптека"):
    real = road.compute_travel_min
    monkeypatch.setattr(road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    places.add(db, name, lat=lat, lon=lon)
    db.commit()
    pid = plans.add(db, "Забрать заказ", place=name)
    db.commit()
    plans.attach(db, pid, event_id)
    db.commit()
    monkeypatch.setattr(road, "compute_travel_min", real)
    return pid


def test_compute_travel_min_uses_via_when_plan_attached(monkeypatch, db):
    # Setup (cal.add / plans.attach) runs its own internal recompute_road
    # too -- keep TOMTOM_API_KEY unset until after setup so those internal
    # calls harmlessly land on the straight rung instead of doing a real
    # HTTP request.
    e = _event_with_place(db, monkeypatch)
    _attach_geo_plan(db, monkeypatch, e["id"])
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    seen = {}

    def fake_route_via(conn, origin, via, dest, cfg, now_utc=None):
        seen["via"] = list(via)
        return 42, [origin, *via, dest], "tomtom"

    monkeypatch.setattr(road, "route_via", fake_route_via)
    monkeypatch.setattr(road, "tomtom_route_minutes",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("via present -- must not call the no-via rung")))

    minutes, source = road.compute_travel_min(db, e, CFG, now_utc=NOW)
    assert (minutes, source) == (42, "tomtom")
    assert seen["via"] == [VIA]


def test_compute_travel_min_no_attached_plan_uses_old_ladder(monkeypatch, db):
    e = _event_with_place(db, monkeypatch)
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    monkeypatch.setattr(road, "route_via",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("no attached plan -- must not probe via")))
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 12)

    minutes, source = road.compute_travel_min(db, e, CFG, now_utc=NOW)
    assert (minutes, source) == (12, "tomtom")


def test_compute_travel_min_plan_without_coords_unaffected(monkeypatch, db):
    e = _event_with_place(db, monkeypatch)
    pid = plans.add(db, "Дело без места")  # no place, no person -> no via
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    monkeypatch.setattr(road, "route_via",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("plan has no coords -- must not probe via")))
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 12)

    minutes, source = road.compute_travel_min(db, e, CFG, now_utc=NOW)
    assert (minutes, source) == (12, "tomtom")


def test_compute_travel_min_via_equals_event_place_is_skipped(monkeypatch, db):
    e = _event_with_place(db, monkeypatch)
    # Plan's place IS the event's own place -- guard must drop it as a via.
    pid = plans.add(db, "Дело в той же клинике", place="Клиника")
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    monkeypatch.setattr(road, "route_via",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("via == event place -- must not probe via")))
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 12)

    minutes, source = road.compute_travel_min(db, e, CFG, now_utc=NOW)
    assert (minutes, source) == (12, "tomtom")


def test_compute_travel_min_via_probe_failure_falls_to_straight_not_direct_tomtom(
        monkeypatch, db):
    e = _event_with_place(db, monkeypatch)
    _attach_geo_plan(db, monkeypatch, e["id"])
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    monkeypatch.setattr(road, "route_via", lambda *a, **k: (None, None, "none"))
    monkeypatch.setattr(road, "tomtom_route_minutes",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("failed via probe must not retry the no-via rung")))

    minutes, source = road.compute_travel_min(db, e, CFG, now_utc=NOW)
    assert source == "straight"
    assert minutes == road.straight_line_minutes(*HOME, *DEST, CFG)


def test_dropped_plan_no_longer_counted_as_via(monkeypatch, db):
    e = _event_with_place(db, monkeypatch)
    pid = _attach_geo_plan(db, monkeypatch, e["id"])
    plans.mark(db, pid, "dropped")
    db.commit()
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    monkeypatch.setattr(road, "route_via",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("dropped plan -- must not probe via")))
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 12)

    minutes, source = road.compute_travel_min(db, e, CFG, now_utc=NOW)
    assert (minutes, source) == (12, "tomtom")


def test_route_for_event_uses_via_when_plan_attached(monkeypatch, db):
    e = _event_with_place(db, monkeypatch)
    _attach_geo_plan(db, monkeypatch, e["id"])
    e = cal.get(db, e["id"])
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)

    seen = {}

    def fake_route_via(conn, origin, via, dest, cfg, now_utc=None):
        seen["via"] = list(via)
        return 42, [origin, *via, dest], "tomtom"

    monkeypatch.setattr(road, "route_via", fake_route_via)
    monkeypatch.setattr(road, "tomtom_route_points",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("via present -- must not call the no-via rung")))

    points, source = road.route_for_event(db, e, CFG, now_utc=NOW)
    assert source == "tomtom"
    assert points == [HOME, VIA, DEST]
    assert seen["via"] == [VIA]
