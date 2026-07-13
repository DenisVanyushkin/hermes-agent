"""Task 4 (3b): 'По пути' block piggybacks on leave/prepare reminders.

Zero new messages: the block is added to the SAME raw dict already
built for gate.deliver -- no extra send, no extra budget. plans.match_enroute
is called at most once per reminder that actually reaches gate.deliver
(never on every minute tick -- only when a reminder is due and about to
be delivered), because it may internally call road.route_for_event
(TomTom, daily-capped).
"""
import pytest

from fam import audit, cal, gate, people, places, plans, road, tick


class FakeDeliver:
    """Mirrors test_tick.py's own FakeDeliver -- tick.py tests never touch
    subprocess/hermes; gate.py's own subprocess contract is exercised in
    test_gate.py."""

    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        self.calls.append({
            "kind": kind, "raw": raw, "human_fallback": human_fallback,
            "force": force, "now_utc": now_utc,
        })
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
    "road_home_lat": 43.2220,
    "road_home_lon": 76.8512,
    "enroute_car_km": 3.0,
    "enroute_walk_km": 0.5,
}

NOW = "2026-07-20T04:30:00+00:00"
PAST = "2026-07-20T04:20:00+00:00"

# A straight route from home to a place east of it (mirrors test_plans_enroute.py).
ROUTE = [(43.2220, 76.8512), (43.2298, 76.8823)]


def _insert_reminder(db, event_id, label="пора выходить", fire_at=PAST,
                      status="pending", anchor="leave_at", kind="leave",
                      created_at=PAST):
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, kind, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?,?)",
        (event_id, label, anchor, kind, fire_at, status, created_at),
    )
    return cur.lastrowid


def _event_with_place(db, transport="car"):
    places.add(db, "Клиника Дента", aliases=["стоматолог"],
               lat=43.2260, lon=76.8670)
    db.commit()
    e = cal.add(db, "Врач", NOW, place="Клиника Дента", transport=transport)
    db.commit()
    return e


def test_enroute_block_added_for_leave_kind_geo_match(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    pid = plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Забрать заказ"

    rows = [r for r in db.execute(
        "SELECT kind, payload FROM audit_log WHERE kind='tick.enroute'")]
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["event_id"] == e["id"]
    assert payload["plan_ids"] == [pid]


def test_enroute_block_added_for_prepare_kind_too(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))
    _insert_reminder(db, e["id"], label="пора собираться", kind="prepare")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Забрать заказ"


def test_enroute_exception_does_not_break_tick(db, fake_deliver, monkeypatch):
    # Final review Finding 2: match_enroute blowing up must not take down
    # the whole minute tick -- the reminder still gets delivered (just
    # without the "по пути" block), and the failure is audited.
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()

    def boom(conn, event, cfg, now_utc=None):
        raise RuntimeError("tomtom down")

    monkeypatch.setattr(plans, "match_enroute", boom)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["sent"] == 1
    raw = fake_deliver.calls[0]["raw"]
    assert "enroute" not in raw
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'"
    ).fetchall()
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["where"] == "enroute"


def test_no_open_plans_leaves_raw_unchanged_regression(db, fake_deliver):
    e = _event_with_place(db)
    db.commit()
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert "enroute" not in raw
    rows = db.execute(
        "SELECT * FROM audit_log WHERE kind='tick.enroute'").fetchall()
    assert rows == []


def test_no_place_never_matches_and_never_calls_match_enroute(db, fake_deliver, monkeypatch):
    people.add(db, "Тая", slug="taya")
    db.commit()
    e = cal.add(db, "Звонок", NOW, place=None)
    db.commit()
    plans.add(db, "Дело", person="Тая")
    db.commit()

    called = []
    real_match = plans.match_enroute

    def _spy(conn, event, cfg, now_utc=None):
        called.append(event["id"])
        return real_match(conn, event, cfg, now_utc=now_utc)

    monkeypatch.setattr(plans, "match_enroute", _spy)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert "enroute" not in raw
    assert called == []


def test_non_leave_prepare_kind_never_calls_match_enroute(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()

    called = []
    real_match = plans.match_enroute

    def _spy(conn, event, cfg, now_utc=None):
        called.append(event["id"])
        return real_match(conn, event, cfg, now_utc=now_utc)

    monkeypatch.setattr(plans, "match_enroute", _spy)
    _insert_reminder(db, e["id"], kind="other")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert called == []


def test_max_items_cap_defaults_to_two(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    p1 = plans.add(db, "Первое дело", place="стоматолог")
    p2 = plans.add(db, "Второе дело", place="стоматолог")
    p3 = plans.add(db, "Третье дело", place="стоматолог")
    db.commit()
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Первое дело; Второе дело"

    import json
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.enroute'").fetchone()
    payload = json.loads(row["payload"])
    assert payload["plan_ids"] == [p1, p2]


def test_max_items_cap_configurable(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Первое дело", place="стоматолог")
    plans.add(db, "Второе дело", place="стоматолог")
    plans.add(db, "Третье дело", place="стоматолог")
    db.commit()
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    cfg = dict(CFG, enroute_max_items=1)
    tick.reminders(db, now_utc=NOW, cfg=cfg)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Первое дело"


def test_tomtom_unavailable_falls_back_to_straight_route(db, fake_deliver, monkeypatch):
    # route_for_event's own TomTom/straight fallback contract (Task 2)
    # is exercised in test_road.py; here we only need to confirm the
    # tick-level integration still builds the block when the source is
    # "straight" (TomTom unreachable/capped), not just "tomtom".
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()

    def _fake_route(conn, ev, cfg, now_utc=None):
        return (ROUTE, "straight")

    monkeypatch.setattr(road, "route_for_event", _fake_route)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["enroute"] == "По пути: Забрать заказ"


def test_match_enroute_called_once_per_delivered_reminder(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    plans.add(db, "Забрать заказ", place="стоматолог")
    db.commit()

    calls = []
    real_match = plans.match_enroute

    def _spy(conn, event, cfg, now_utc=None):
        calls.append(1)
        return real_match(conn, event, cfg, now_utc=now_utc)

    monkeypatch.setattr(plans, "match_enroute", _spy)
    monkeypatch.setattr(
        road, "route_for_event",
        lambda conn, ev, cfg, now_utc=None: (ROUTE, "straight"))
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert len(calls) == 1
