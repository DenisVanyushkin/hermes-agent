"""Phase 7b, Task 3: the FIRST prepare-stage of an event's reminder chain
carries a live detour_min for each geo-enroute candidate, formatted as an
offer ("По пути (+N мин): X — заехать?"). Any later stage (a second
prepare stage, or a leave stage) gets the plain fact line, unchanged from
before this task.
"""
import json

import pytest

from fam import cal, gate, places, plans, road, tick


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        self.calls.append({"kind": kind, "raw": raw,
                            "human_fallback": human_fallback})
        return self.responses.pop(0)


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "gate_model": "gpt-5.4-mini",
    "gate_provider": "openai-codex",
    "max_len_reminder": 300,
    "max_len_digest": 900,
    "reminder_max_age_min": 120,
    "road_provider": "tomtom",
    "road_home_lat": 43.2220,
    "road_home_lon": 76.8512,
    "enroute_car_km": 3.0,
    "enroute_walk_km": 0.5,
    "detour_offer_min_min": 2,
    "detour_max_min": 30,
}

NOW = "2026-07-20T04:30:00+00:00"
PAST = "2026-07-20T04:20:00+00:00"
EARLIER = "2026-07-20T03:00:00+00:00"

ROUTE = [(43.2220, 76.8512), (43.2298, 76.8823)]
DEST = (43.2298, 76.8823)  # event's own place -- distinct from the plan's
VIA = (43.2260, 76.8670)   # plan's place -- on the way, NOT the event place


def _insert_reminder(db, event_id, label="пора собираться", fire_at=PAST,
                      status="pending", anchor="leave_at", kind="prepare",
                      created_at=PAST):
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, kind, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?,?)",
        (event_id, label, anchor, kind, fire_at, status, created_at),
    )
    return cur.lastrowid


def _event_with_place(db, transport="car"):
    places.add(db, "Клиника Дента", lat=DEST[0], lon=DEST[1])
    places.add(db, "Аптека", aliases=["стоматолог"], lat=VIA[0], lon=VIA[1])
    db.commit()
    e = cal.add(db, "Врач", NOW, place="Клиника Дента", transport=transport)
    db.commit()
    return e


def _stub_route(monkeypatch, direct=20, via=35):
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))

    def fake_via(conn, origin, via_pts, dest, cfg, now_utc=None):
        if via_pts:
            return via, [origin, *via_pts, dest], "tomtom"
        return direct, [origin, dest], "tomtom"
    monkeypatch.setattr(road, "route_via", fake_via)


def test_first_prepare_stage_gets_detour_offer(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    _stub_route(monkeypatch, direct=20, via=35)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")
    _insert_reminder(db, e["id"], kind="prepare", fire_at=PAST)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути (+15 мин): Забрать заказ — заехать?"


def test_second_prepare_stage_gets_plain_fact(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    _stub_route(monkeypatch, direct=20, via=35)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    # Both prepare stages are due in this same tick (fire_at <= now):
    # reminders() processes them in fire_at order, EARLIER first. Once
    # EARLIER is delivered it flips to status='sent' -- _is_first_prepare_
    # stage's MIN() must still see it (status IN pending,sent) so the
    # LATER stage, processed second, correctly loses the "first" check.
    _insert_reminder(db, e["id"], kind="prepare", fire_at=EARLIER,
                      label="скоро собираться")
    _insert_reminder(db, e["id"], kind="prepare", fire_at=PAST,
                      label="пора собираться")
    db.commit()
    fake_deliver.responses = ["sent", "sent"]
    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert len(fake_deliver.calls) >= 2
    raws = [c["raw"] for c in fake_deliver.calls]
    assert raws[0]["enroute"] == "По пути (+15 мин): Забрать заказ — заехать?"
    assert raws[1]["enroute"] == "По пути: Забрать заказ"


def test_leave_stage_never_gets_detour_offer(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    _stub_route(monkeypatch, direct=20, via=35)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")
    _insert_reminder(db, e["id"], kind="leave", label="пора выходить",
                      fire_at=PAST)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Забрать заказ"


def test_detours_exception_falls_back_to_plain_fact(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    _stub_route(monkeypatch, direct=20, via=35)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")

    def boom(conn, event, cfg, matches=None):
        raise RuntimeError("tomtom down")

    monkeypatch.setattr(plans, "detours", boom)
    _insert_reminder(db, e["id"], kind="prepare", fire_at=PAST)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Забрать заказ"
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchall()
    payloads = [json.loads(r["payload"]) for r in rows]
    assert any(p["where"] == "detours" for p in payloads)


def test_out_of_bounds_detour_falls_back_to_plain_fact(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    _stub_route(monkeypatch, direct=20, via=21)  # detour=1 < min(2)
    monkeypatch.setenv("TOMTOM_API_KEY", "sekrit")
    _insert_reminder(db, e["id"], kind="prepare", fire_at=PAST)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Забрать заказ"
