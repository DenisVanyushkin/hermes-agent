"""Phase 7b, Task 3: plans.detours() -- geo-matched, not-yet-attached open
plans "on the way" to an event, each carrying a live (TomTom-only)
detour_min within [detour_offer_min_min, detour_max_min]. Backs `fam cal
detours` and the first-prepare-stage offer in tick.reminders().

Each filter is exercised in isolation: reason must be "geo" (not
"person"), the plan must not already be attached, its effective place
must differ from the event's own place, and the live detour_min must
land in-bounds. road.direct_leg_min is called at most once regardless of
candidate count (budget concern from the brief).
"""
from fam import cal, gate, people, places, plans, road

DEST = (43.2298, 76.8823)
VIA = (43.2400, 76.8900)

CFG = dict(gate.CONFIG_DEFAULTS)
CFG.update({
    "road_provider": "tomtom",
    "road_home_lat": 43.2220,
    "road_home_lon": 76.8512,
    "enroute_car_km": 3.0,
    "enroute_walk_km": 0.5,
})

NOW = "2026-07-20T04:30:00+00:00"


def _event(db, transport="car", participants=()):
    places.add(db, "Клиника", lat=DEST[0], lon=DEST[1])
    db.commit()
    e = cal.add(db, "Врач", NOW, place="Клиника", transport=transport,
                participants=participants)
    db.commit()
    return e


def _stub_route_via(monkeypatch, direct=20, via=35):
    def fake_via(conn, origin, via_pts, dest, cfg, now_utc=None):
        if via_pts:
            return via, [origin, *via_pts, dest], "tomtom"
        return direct, [origin, dest], "tomtom"
    monkeypatch.setattr(road, "route_via", fake_via)


def _route(monkeypatch):
    # A straight home->event route so plans.match_enroute's geo corridor
    # check finds VIA in-corridor -- same style as test_plans_enroute.py.
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (
            [(43.2220, 76.8512), DEST], "straight"))


def test_geo_candidate_in_bounds_gets_detour_min(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch, direct=20, via=35)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == [{"plan": plans.get(db, pid), "detour_min": 15}]


def test_person_only_match_excluded(db, monkeypatch):
    people.add(db, "Тая", slug="taya")
    db.commit()
    e = _event(db, participants=["Тая"])
    plans.add(db, "Дело", person="Тая")  # no place -- person-reason only
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == []


def test_attached_plan_excluded(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == []


def test_plan_at_event_place_excluded(db, monkeypatch):
    e = _event(db)
    pid = plans.add(db, "Ещё дело в клинике", place="Клиника")
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == []


def test_detour_below_offer_min_excluded(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch, direct=20, via=21)  # detour == 1 < min(2)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == []


def test_detour_above_max_excluded(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch, direct=20, via=60)  # detour == 40 > max(30)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == []


def test_detour_bounds_are_inclusive(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch, direct=20, via=22)  # detour == 2 == min
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert offers == [{"plan": plans.get(db, pid), "detour_min": 2}]


def test_no_tomtom_key_excludes_all_candidates(db, monkeypatch):
    # Real (unpatched) road.route_via: with no TOMTOM_API_KEY it returns
    # (None, None, "none") without any HTTP call -- direct_leg_min sees a
    # non-tomtom source and bails, so every candidate is dropped.
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    _route(monkeypatch)
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)

    offers = plans.detours(db, e, CFG)
    assert offers == []


def test_direct_leg_fetched_once_for_multiple_candidates(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    places.add(db, "Почта", lat=43.2350, lon=76.8850)
    db.commit()
    plans.add(db, "Забрать заказ", place="Аптека")
    plans.add(db, "Отправить письмо", place="Почта")
    db.commit()
    _route(monkeypatch)

    direct_calls = []

    def fake_via(conn, origin, via_pts, dest, cfg, now_utc=None):
        if not via_pts:
            direct_calls.append(1)
            return 20, [origin, dest], "tomtom"
        return 30, [origin, *via_pts, dest], "tomtom"

    monkeypatch.setattr(road, "route_via", fake_via)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    offers = plans.detours(db, e, CFG)
    assert len(offers) == 2
    assert len(direct_calls) == 1  # NOT once per candidate


def test_matches_param_avoids_second_match_enroute_call(db, monkeypatch):
    e = _event(db)
    places.add(db, "Аптека", lat=VIA[0], lon=VIA[1])
    db.commit()
    plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()
    _route(monkeypatch)
    _stub_route_via(monkeypatch)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    matches = plans.match_enroute(db, e, CFG)

    calls = []
    real_match = plans.match_enroute

    def _spy(conn, event, cfg, now_utc=None, route=None):
        calls.append(1)
        return real_match(conn, event, cfg, now_utc=now_utc, route=route)

    monkeypatch.setattr(plans, "match_enroute", _spy)
    plans.detours(db, e, CFG, matches=matches)
    assert calls == []
