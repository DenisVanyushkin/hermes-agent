import json

import pytest

from fam import audit, cal, places, rem, road, tick

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
    # `assert CFG_KEY not in seen` (checking dict keys, not values) was
    # vacuous -- the key legitimately IS in the URL. Nothing meaningful
    # to assert about key leakage here; the no-audit-leak guarantee is
    # covered separately by the audit-payload tests below.


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
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
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
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(road, "tomtom_route_minutes", lambda *a, **k: 26)
    mins, src = road.compute_travel_min(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert (mins, src) == (26, "tomtom")
    rows = audit.query(db, None, "road.call", None)
    assert rows and rows[0]["payload"] == {
        "event_id": 1, "minutes": 26, "source": "tomtom"}


def test_daily_cap_skips_tomtom(monkeypatch, db):
    # _tomtom_calls_today now counts against real wall-clock (see fix #1
    # -- caller's now_utc is only the depart anchor), so pin wall-clock to
    # NOW here to keep the seeded rows and the cap check on the same day.
    monkeypatch.setattr(road, "_wall_now", lambda: NOW)
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
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


def test_daily_cap_binds_on_wall_clock_not_depart_anchor(monkeypatch, db):
    """now_utc passed to compute_travel_min is the DEPART anchor and can be
    days in the future (e.g. a future event's start_utc). The cap must
    still bind against TODAY's (wall-clock) road.call rows -- audit rows
    are stamped wall-clock, not against the event's day."""
    from datetime import datetime, timezone

    wall_now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(road, "_wall_now", lambda: wall_now.isoformat(timespec="seconds"))
    monkeypatch.setenv("TOMTOM_API_KEY", CFG_KEY)
    monkeypatch.setattr(
        road, "tomtom_route_minutes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cap should block this call")))

    cap = CFG["road_daily_cap"]
    wall_now_iso = wall_now.isoformat(timespec="seconds")
    for _ in range(cap):
        db.execute(
            "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
            (wall_now_iso, "road.call", "test",
             json.dumps({"event_id": 999, "minutes": 5, "source": "tomtom"})))
    db.commit()

    # depart anchor for an event 3 days out -- landing on a DIFFERENT
    # Almaty day than wall-clock now.
    future_depart = "2026-07-14T10:00:00+00:00"
    mins, src = road.compute_travel_min(db, EVENT_WITH_COORDS, CFG, now_utc=future_depart)
    assert src == "straight" and mins > 0
    rows = audit.query(db, None, "road.cap", None)
    assert rows and rows[0]["payload"] == {"event_id": 1}


def test_no_key_skips_tomtom_silently(monkeypatch, db):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(
        road, "tomtom_route_minutes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no-key should skip this call")))
    mins, src = road.compute_travel_min(db, EVENT_WITH_COORDS, CFG, now_utc=NOW)
    assert src == "straight" and mins > 0
    assert audit.query(db, None, "road.error", None) == []
    assert audit.query(db, None, "road.call", None) == []
    assert audit.query(db, None, "road.cap", None) == []


# ==== golive finding 3: road_recompute rolls back partial state on error ====
# Setup below is copied verbatim from test_tick.py's road_recompute suite
# (there is no separate helper module for this -- those tests are the
# canonical setup pattern for a T-window road_recompute candidate):
# rem.seed_default_rules + a place with home-distance coords + an event
# 119 minutes out (opens the T-120 window with travel_min_road still
# None -> leave_at == start), and neutralizing compute_travel_min during
# cal.add so its own add-time road hook -- which reads the REAL on-disk
# config on this prod host -- doesn't fire for real.

ROAD_RECOMPUTE_NOW = "2026-07-20T04:30:00+00:00"
ROAD_RECOMPUTE_CFG = {
    "road_home_lat": 43.2220, "road_home_lon": 76.8512, "road_coef": 1.4,
    "road_speed_kmh": 30, "road_daily_cap": 100, "road_timeout_sec": 10,
    "road_recompute_min": [120, 60],
}


def test_road_recompute_error_rolls_back_partial_state(db, monkeypatch):
    """Mirrors the fix already applied to _meds_series' except-branch
    (fam/tick.py): road_recompute's per-event try body can UPDATE
    events.travel_min_road, log a road.recompute audit row, and then
    call rem.regenerate -- which itself DELETEs the event's pending
    reminder chain before INSERTing a fresh one. If rem.regenerate
    raises AFTER that DELETE (a mid-regenerate failure), the surrounding
    except must roll back the WHOLE transaction before auditing
    road.hook_error -- otherwise the event is committed with
    travel_min_road changed, a road.recompute audit row, and ZERO
    pending reminders (the fresh chain's INSERTs never happened).
    """
    rem.seed_default_rules(db)
    db.commit()
    places.add(db, "Клиника", lat=43.2298, lon=76.8823)
    db.commit()

    # Neutralize the add-time hook (see module-comment above) -- keeps
    # travel_min_road at None like a from-scratch DB, exactly as
    # test_tick.py's _add_event_neutral_road does.
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    event = cal.add(db, "Врач", "2026-07-20T06:29:00+00:00", place="Клиника")
    db.commit()

    before = db.execute(
        "SELECT COUNT(*) c FROM reminders WHERE event_id=? AND status='pending'",
        (event["id"],)).fetchone()["c"]
    assert before > 0

    # Now the recompute call under test finds a CHANGED minute figure,
    # entering the UPDATE + audit.log(road.recompute) + rem.regenerate
    # branch (see tick.road_recompute's docstring).
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (45, "tomtom"))

    # rem.regenerate's real contract is DELETE pending, then INSERT a
    # fresh chain. Simulate a failure mid-regenerate by performing the
    # real DELETE (the partial write that must NOT survive) and then
    # raising -- this is the only way to prove the fix's rollback
    # actually undoes writes that happened BEFORE the exception, rather
    # than merely skip a no-op (see brief step 2's warning: if
    # rem.regenerate is monkeypatched to raise before ever touching the
    # DB, the test would pass even without the fix).
    def boom_after_delete(conn, event_id, now_utc=None):
        conn.execute(
            "DELETE FROM reminders WHERE event_id=? AND status='pending'",
            (event_id,))
        raise RuntimeError("regen failed mid-way")

    monkeypatch.setattr(tick.rem, "regenerate", boom_after_delete)

    touched = tick.road_recompute(
        db, now_utc=ROAD_RECOMPUTE_NOW, cfg=ROAD_RECOMPUTE_CFG)
    assert touched == 0  # this event's own try body never finished cleanly

    after = db.execute(
        "SELECT COUNT(*) c FROM reminders WHERE event_id=? AND status='pending'",
        (event["id"],)).fetchone()["c"]
    assert after == before  # partial UPDATE/DELETE rolled back

    got = cal.get(db, event["id"])
    assert got["travel_min_road"] is None  # the UPDATE was rolled back too

    assert not audit.query(db, None, "road.recompute", None)  # rolled back
    err = audit.query(db, None, "road.hook_error", None)
    assert err and err[0]["payload"] == {"event_id": event["id"]}
