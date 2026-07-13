"""Phase 5 Task 6: geo-'по пути' match for shopping.

A categorized place (grocery/pharmacy, places.category -- new nullable
column this task adds) near the event's route, with a non-empty
matching shopping list, surfaces as a piggyback block on leave/prepare
reminders (raw["shop_enroute"], tick.py's reminders()). Mirrors
test_plans_enroute.py's module-level match_enroute tests and
test_tick_enroute.py's tick-level integration tests -- same route-
corridor machinery (road.route_for_event/point_to_route_km, 3b), same
call-at-most-once-per-delivered-reminder guard (no new message, no
budget spend), same try/except tick.error guard.
"""
import json

import pytest

from fam import cal, cli, gate, places, road, shopping, tick

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

# A straight route from home to a place east of it (mirrors
# test_plans_enroute.py/test_tick_enroute.py).
ROUTE = [(43.2220, 76.8512), (43.2298, 76.8823)]


def _grocery(db, name="Магнум", lat=43.2260, lon=76.8670):
    places.add(db, name, lat=lat, lon=lon)
    places.update(db, name, category="grocery")


def _pharmacy(db, name="Аптека Апрель", lat=43.2260, lon=76.8670):
    places.add(db, name, lat=lat, lon=lon)
    places.update(db, name, category="pharmacy")


def _event(db, transport="car"):
    e = cal.add(db, "Событие", NOW, place=None, transport=transport)
    db.commit()
    return e


def _route_stub(conn, ev, cfg, now_utc=None):
    return (ROUTE, "straight")


def _no_route_stub(conn, ev, cfg, now_utc=None):
    return (None, "none")


# --- places.category: schema + domain read/write ---

def test_fresh_place_has_null_category(db):
    p = places.add(db, "Мега")
    db.commit()
    assert p["category"] is None


def test_update_sets_category(db):
    places.add(db, "Магнум")
    db.commit()
    p = places.update(db, "Магнум", category="grocery")
    assert p["category"] == "grocery"


def test_update_invalid_category_raises(db):
    places.add(db, "Магнум")
    db.commit()
    with pytest.raises(ValueError):
        places.update(db, "Магнум", category="bogus")


def test_update_category_to_none_clears_it(db):
    places.add(db, "Магнум")
    db.commit()
    places.update(db, "Магнум", category="grocery")
    p = places.update(db, "Магнум", category=None)
    assert p["category"] is None


# --- CLI: fam places update --category ---

def test_cli_places_update_category(db, capsys):
    places.add(db, "Магнум")
    db.commit()
    assert cli.main(["--json", "places", "update", "Магнум",
                      "--category", "grocery"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["category"] == "grocery"


def test_cli_places_update_category_rejects_bad_choice(db, capsys):
    # argparse's own choices= validation raises SystemExit(2) before
    # cli.main's try/except (cal.grid's --month/--week validation is the
    # same shape -- see test_cal_grid_bad_month_format_exit_2).
    places.add(db, "Магнум")
    db.commit()
    with pytest.raises(SystemExit) as exc:
        cli.main(["places", "update", "Магнум", "--category", "bogus"])
    assert exc.value.code == 2


# --- shopping.match_enroute (module level) ---

def test_grocery_hit_in_corridor_with_open_items(db, monkeypatch):
    _grocery(db)
    shopping.add(db, "Молоко")
    shopping.add(db, "Хлеб")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    matches = shopping.match_enroute(db, e, CFG, now_utc=NOW)

    assert len(matches) == 1
    assert matches[0]["category"] == "grocery"
    assert matches[0]["place"]["name"] == "Магнум"
    assert matches[0]["items"] == ["Молоко", "Хлеб"]


def test_grocery_no_match_when_list_empty(db, monkeypatch):
    _grocery(db)
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    assert shopping.match_enroute(db, e, CFG, now_utc=NOW) == []


def test_pharmacy_matches_only_meds_source(db, monkeypatch):
    _pharmacy(db)
    shopping.add(db, "Магний", source="meds")
    shopping.add(db, "Хлеб", source="manual")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    matches = shopping.match_enroute(db, e, CFG, now_utc=NOW)

    assert len(matches) == 1
    assert matches[0]["category"] == "pharmacy"
    assert matches[0]["items"] == ["Магний"]


def test_pharmacy_no_match_when_only_manual_items(db, monkeypatch):
    _pharmacy(db)
    shopping.add(db, "Хлеб", source="manual")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    assert shopping.match_enroute(db, e, CFG, now_utc=NOW) == []


def test_place_outside_corridor_no_match(db, monkeypatch):
    places.add(db, "Далеко", lat=43.5000, lon=77.5000)
    places.update(db, "Далеко", category="grocery")
    shopping.add(db, "Молоко")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    assert shopping.match_enroute(db, e, CFG, now_utc=NOW) == []


def test_no_route_no_match(db, monkeypatch):
    _grocery(db)
    shopping.add(db, "Молоко")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _no_route_stub)

    assert shopping.match_enroute(db, e, CFG, now_utc=NOW) == []


def test_uncategorized_place_ignored(db, monkeypatch):
    places.add(db, "Просто место", lat=43.2260, lon=76.8670)
    shopping.add(db, "Молоко")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    assert shopping.match_enroute(db, e, CFG, now_utc=NOW) == []


def test_items_capped_at_max_items(db, monkeypatch):
    _grocery(db)
    shopping.add(db, "Молоко")
    shopping.add(db, "Хлеб")
    shopping.add(db, "Яйца")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    matches = shopping.match_enroute(db, e, CFG, now_utc=NOW)
    assert matches[0]["items"] == ["Молоко", "Хлеб"]

    cfg = dict(CFG, enroute_max_items=1)
    matches2 = shopping.match_enroute(db, e, cfg, now_utc=NOW)
    assert matches2[0]["items"] == ["Молоко"]


def test_walk_threshold_tighter_than_car(db, monkeypatch):
    places.add(db, "Средне", lat=43.2360, lon=76.8670)
    places.update(db, "Средне", category="grocery")
    shopping.add(db, "Молоко")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    dist = road.point_to_route_km(43.2360, 76.8670, ROUTE)
    assert CFG["enroute_walk_km"] < dist < CFG["enroute_car_km"]

    e_walk = _event(db, transport="walk")
    assert shopping.match_enroute(db, e_walk, CFG, now_utc=NOW) == []

    e_car = _event(db, transport="car")
    matches = shopping.match_enroute(db, e_car, CFG, now_utc=NOW)
    assert len(matches) == 1


def test_dedup_by_place_grocery_and_pharmacy_both_open(db, monkeypatch):
    _grocery(db, name="Магнум")
    _pharmacy(db, name="Аптека Апрель", lat=43.2265, lon=76.8672)
    shopping.add(db, "Молоко")
    shopping.add(db, "Магний", source="meds")
    db.commit()
    e = _event(db)
    monkeypatch.setattr(road, "route_for_event", _route_stub)

    matches = shopping.match_enroute(db, e, CFG, now_utc=NOW)
    place_ids = [m["place"]["id"] for m in matches]
    assert len(place_ids) == len(set(place_ids))
    assert {m["category"] for m in matches} == {"grocery", "pharmacy"}


# --- tick.py reminders(): raw["shop_enroute"] piggyback ---

class FakeDeliver:
    """Mirrors test_tick_enroute.py's own FakeDeliver -- tick.py tests
    never touch subprocess/hermes; gate.py's own subprocess contract is
    exercised in test_gate.py."""

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


def _insert_reminder(db, event_id, label="пора выходить", fire_at=PAST,
                      status="pending", anchor="leave_at", kind="leave",
                      created_at=PAST):
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, kind, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?,?)",
        (event_id, label, anchor, kind, fire_at, status, created_at),
    )
    return cur.lastrowid


def _event_with_place(db, transport="car", place_name="Клиника Дента",
                       lat=43.2260, lon=76.8670):
    places.add(db, place_name, lat=lat, lon=lon)
    db.commit()
    e = cal.add(db, "Врач", NOW, place=place_name, transport=transport)
    db.commit()
    return e


def test_shop_enroute_block_added_for_leave_kind(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["shop_enroute"] == "Заодно: Магнум — Молоко"

    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.shop_enroute'").fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    grocery_place = places.get(db, "Магнум")
    assert payload == {"event_id": e["id"], "place_id": grocery_place["id"],
                        "n_items": 1}


def test_shop_enroute_block_added_for_prepare_kind_too(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], label="пора собираться", kind="prepare")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["shop_enroute"] == "Заодно: Магнум — Молоко"


def test_shop_enroute_no_block_when_list_empty(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert "shop_enroute" not in raw
    rows = db.execute(
        "SELECT * FROM audit_log WHERE kind='tick.shop_enroute'").fetchall()
    assert rows == []


def test_shop_enroute_pharmacy_matches_only_meds(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _pharmacy(db, name="Аптека Апрель", lat=43.2262, lon=76.8672)
    shopping.add(db, "Магний", source="meds")
    shopping.add(db, "Хлеб", source="manual")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["shop_enroute"] == "Заодно: Аптека Апрель — Магний"


def test_shop_enroute_outside_corridor_no_block(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    places.add(db, "Далеко", lat=43.5000, lon=77.5000)
    places.update(db, "Далеко", category="grocery")
    shopping.add(db, "Молоко")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert "shop_enroute" not in raw


def test_shop_enroute_exception_does_not_break_tick(db, fake_deliver, monkeypatch):
    # Same contract as the plans-enroute guard (test_tick_enroute.py):
    # match_enroute blowing up must not take down the whole minute tick
    # -- the reminder still gets delivered (just without the "заодно"
    # block), and the failure is audited.
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    db.commit()

    def boom(conn, event, cfg, now_utc=None):
        raise RuntimeError("tomtom down")

    monkeypatch.setattr(shopping, "match_enroute", boom)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["sent"] == 1
    raw = fake_deliver.calls[0]["raw"]
    assert "shop_enroute" not in raw
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["where"] == "shop_enroute"


def test_shop_enroute_no_place_never_calls_match_enroute(db, fake_deliver, monkeypatch):
    e = cal.add(db, "Звонок", NOW, place=None)
    db.commit()
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    db.commit()

    called = []
    real_match = shopping.match_enroute

    def _spy(conn, event, cfg, now_utc=None):
        called.append(event["id"])
        return real_match(conn, event, cfg, now_utc=now_utc)

    monkeypatch.setattr(shopping, "match_enroute", _spy)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert "shop_enroute" not in raw
    assert called == []


def test_shop_enroute_non_leave_prepare_kind_never_calls(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    db.commit()

    called = []
    real_match = shopping.match_enroute

    def _spy(conn, event, cfg, now_utc=None):
        called.append(event["id"])
        return real_match(conn, event, cfg, now_utc=now_utc)

    monkeypatch.setattr(shopping, "match_enroute", _spy)
    _insert_reminder(db, e["id"], kind="other")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert called == []


def test_shop_enroute_called_once_per_delivered_reminder(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    db.commit()

    calls = []
    real_match = shopping.match_enroute

    def _spy(conn, event, cfg, now_utc=None):
        calls.append(1)
        return real_match(conn, event, cfg, now_utc=now_utc)

    monkeypatch.setattr(shopping, "match_enroute", _spy)
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert len(calls) == 1


def test_shop_enroute_max_items_cap_configurable(db, fake_deliver, monkeypatch):
    e = _event_with_place(db)
    _grocery(db, name="Магнум", lat=43.2262, lon=76.8672)
    shopping.add(db, "Молоко")
    shopping.add(db, "Хлеб")
    shopping.add(db, "Яйца")
    db.commit()
    monkeypatch.setattr(road, "route_for_event", _route_stub)
    _insert_reminder(db, e["id"], kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    cfg = dict(CFG, enroute_max_items=1)
    tick.reminders(db, now_utc=NOW, cfg=cfg)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["shop_enroute"] == "Заодно: Магнум — Молоко"
