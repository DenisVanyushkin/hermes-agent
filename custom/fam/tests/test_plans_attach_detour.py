"""Phase 7b, Task 2: plans.attach()/mark() wire the detour into the
event's road figure + reminder chain, in the same transaction.

attach(): the attached plan becomes a waypoint candidate (see
test_road_detour_recompute.py for the road.py-level via-gathering) ->
travel_min_road grows, leave_at moves earlier, pending reminders are
regenerated against the new leave_at.

mark(done|dropped): an attached plan drops out of the OPEN set that
feeds the waypoint list -> the next recompute (triggered here, same
transaction) collapses the route back to direct.
"""
from datetime import datetime, timedelta, timezone

from fam import cal, gate, places, plans, rem, road

NOW = "2026-07-18T05:00:00+00:00"

DEST = (43.2298, 76.8823)
VIA = (43.2400, 76.8900)


def _cfg(tmp_path, monkeypatch):
    cfg = dict(gate.CONFIG_DEFAULTS if hasattr(gate, "CONFIG_DEFAULTS") else {})
    cfg.update({
        "road_provider": "tomtom",
        "road_home_lat": 43.2220,
        "road_home_lon": 76.8512,
        "road_coef": 1.4,
        "road_speed_kmh": 30,
        "road_daily_cap": 100,
        "road_timeout_sec": 10,
    })
    target = tmp_path / "fam-config.json"
    example = tmp_path / "fam-config.example.json"
    import json
    example.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    target.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    return cfg


def _event(db, tmp_path, monkeypatch, start="2026-08-25T09:00:00+00:00"):
    _cfg(tmp_path, monkeypatch)
    places.add(db, "Клиника", lat=DEST[0], lon=DEST[1])
    db.commit()
    # Neutralize compute_travel_min for the add()-time hook only -- the
    # actual "straight" figure is established explicitly below via a
    # direct recompute_road call once TomTom is stubbed. Restored
    # immediately after add() so later calls in the test see the real
    # via-gathering ladder.
    real_compute_travel_min = road.compute_travel_min
    monkeypatch.setattr(road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    e = cal.add(db, "Событие", start, place="Клиника")
    db.commit()
    monkeypatch.setattr(road, "compute_travel_min", real_compute_travel_min)
    return e


def _stub_route_via(monkeypatch):
    """route_via: direct (no via) -> 20 min; with any via -> 35 min. Real
    signature, no network -- exercises compute_travel_min/route_for_event's
    via-gathering for real, only the outermost HTTP-shaped call is faked.

    Also stubs the plain (no-via) tomtom_route_minutes/tomtom_route_points
    rungs to the SAME direct figure (20 min) -- compute_travel_min only
    calls route_via when it found at least one attached via; the
    no-attached-plan baseline still goes through the old single-leg
    rungs, so both paths must agree on what "direct" means for these
    tests' before/after comparisons to be meaningful.
    """
    def fake_via(conn, origin, via, dest, cfg, now_utc=None):
        if via:
            return 35, [origin, *via, dest], "tomtom"
        return 20, [origin, dest], "tomtom"
    monkeypatch.setattr(road, "route_via", fake_via)
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 20)
    monkeypatch.setattr(road, "tomtom_route_points",
                         lambda *a, **k: [(43.2220, 76.8512), DEST])


def test_attach_geo_plan_grows_travel_min_road_and_moves_leave_at_earlier(
        db, tmp_path, monkeypatch):
    # Rules must exist BEFORE the event is created -- cal.add()'s own
    # rem.regenerate() call (at add-time) is what seeds the pending
    # chain this test asserts on.
    rem.seed_default_rules(db)
    db.commit()
    e = _event(db, tmp_path, monkeypatch)

    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    # Establish the direct baseline explicitly (no attached plan yet).
    cal.recompute_road(db, e["id"])
    db.commit()
    before = cal.get(db, e["id"])
    assert before["travel_min_road"] == 20
    leave_before = rem.leave_at(db, before)
    pending_before = {r["fire_at_utc"] for r in db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending'",
        (e["id"],)).fetchall()}
    assert pending_before

    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    ok = plans.attach(db, pid, e["id"])
    db.commit()
    assert ok is True

    after = cal.get(db, e["id"])
    assert after["travel_min_road"] == 35  # grew: detour included
    leave_after = rem.leave_at(db, after)
    assert leave_after < leave_before  # earlier -- more travel needed

    pending_after = {r["fire_at_utc"] for r in db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending'",
        (e["id"],)).fetchall()}
    assert pending_after and pending_after != pending_before


def test_mark_done_on_attached_plan_reverts_route_to_direct(
        db, tmp_path, monkeypatch):
    e = _event(db, tmp_path, monkeypatch)
    rem.seed_default_rules(db)
    db.commit()

    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()
    assert cal.get(db, e["id"])["travel_min_road"] == 35

    ok = plans.mark(db, pid, "done")
    db.commit()
    assert ok is True

    after = cal.get(db, e["id"])
    assert after["travel_min_road"] == 20  # back to direct


def test_mark_dropped_on_attached_plan_reverts_route_to_direct(
        db, tmp_path, monkeypatch):
    e = _event(db, tmp_path, monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()
    assert cal.get(db, e["id"])["travel_min_road"] == 35

    plans.mark(db, pid, "dropped")
    db.commit()

    after = cal.get(db, e["id"])
    assert after["travel_min_road"] == 20


def test_attach_plan_without_coords_behaves_as_before(db, tmp_path, monkeypatch):
    e = _event(db, tmp_path, monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    cal.recompute_road(db, e["id"])
    db.commit()
    before = cal.get(db, e["id"])["travel_min_road"]
    assert before == 20

    pid = plans.add(db, "Дело без места")  # no place, no person
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()

    after = cal.get(db, e["id"])["travel_min_road"]
    assert after == before  # unchanged -- no via to add


def test_attach_plan_at_event_place_adds_no_via(db, tmp_path, monkeypatch):
    e = _event(db, tmp_path, monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    cal.recompute_road(db, e["id"])
    db.commit()
    before = cal.get(db, e["id"])["travel_min_road"]
    assert before == 20

    # Plan's place IS the event's own place ("Клиника") -- guarded out.
    pid = plans.add(db, "Ещё дело в клинике", place="Клиника")
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()

    after = cal.get(db, e["id"])["travel_min_road"]
    assert after == before  # unchanged -- via == event place, skipped


def test_periodic_recompute_preserves_detour(db, tmp_path, monkeypatch):
    """tick.road_recompute (T-120/T-60 threshold recompute) calls
    road.compute_travel_min the same way cal.recompute_road does -- since
    the via-gathering lives inside compute_travel_min itself, a later
    periodic tick re-derives the same detour-inclusive figure for as long
    as the plan stays open and attached, with no separate wiring needed
    in tick.py.
    """
    from fam import tick

    cfg = _cfg(tmp_path, monkeypatch)
    cfg["road_recompute_min"] = [120, 60]
    places.add(db, "Клиника", lat=DEST[0], lon=DEST[1])
    db.commit()
    real_compute_travel_min = road.compute_travel_min
    monkeypatch.setattr(road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    e = cal.add(db, "Врач", "2026-07-18T06:59:00+00:00", place="Клиника")
    db.commit()
    # Restore the real ladder now that add()'s own hook has run -- the
    # rest of this test exercises the real via-gathering logic.
    monkeypatch.setattr(road, "compute_travel_min", real_compute_travel_min)

    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()

    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    ok = plans.attach(db, pid, e["id"])
    db.commit()
    assert ok is True
    assert cal.get(db, e["id"])["travel_min_road"] == 35  # via applied on attach

    # Force road_checked_at stale so the T-120 window is open, then run
    # the periodic tick recompute -- it must land on the SAME via-
    # inclusive figure (35), not silently drop back to the direct one.
    db.execute("UPDATE events SET road_checked_at=NULL WHERE id=?", (e["id"],))
    db.commit()

    touched = tick.road_recompute(db, now_utc=NOW, cfg=cfg)
    assert touched == 1
    after = cal.get(db, e["id"])
    assert after["travel_min_road"] == 35  # crook preserved by the periodic tick
