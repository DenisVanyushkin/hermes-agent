# custom/fam/tests/test_car_hooks.py
from fam import car

def _cfg(**o):
    c = {"car_cabin_suggest_enabled": True, "car_cabin_temp_low_c": 0,
         "car_cabin_temp_high_c": 30}
    c.update(o); return c

def _add_metric(db, **f):
    m = car.normalize(f if "car_state" in f else {**f})
    car.record_metrics(db, m); db.commit()

def test_no_hooks_for_non_car_event(db):
    assert car.departure_hooks(db, {"transport": "walk"}, _cfg()) == []

def test_fuel_hook_when_low(db):
    car._meta_set(db, "car_fuel_low", "1"); db.commit()
    hooks = car.departure_hooks(db, {"transport": "car"}, _cfg())
    assert any("заправься" in h for h in hooks)

def test_cabin_cold_suggests_warmup(db):
    _add_metric(db, fuel_percent=80, ctemp=-8)
    hooks = car.departure_hooks(db, {"transport": "car"}, _cfg())
    assert any("прогрев" in h for h in hooks)

def test_cabin_in_band_no_suggestion(db):
    _add_metric(db, fuel_percent=80, ctemp=18)
    assert car.departure_hooks(db, {"transport": "car"}, _cfg()) == []

def test_suggest_disabled(db):
    _add_metric(db, fuel_percent=80, ctemp=-8)
    assert car.departure_hooks(db, {"transport": "car"}, _cfg(car_cabin_suggest_enabled=False)) == []


# --- reminders() integration: piggyback into raw["car"], mirroring
# test_tick_enroute.py's raw["enroute"] integration test. ---
from fam import cal, gate, places, tick

TICK_CFG = {
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
    "car_cabin_suggest_enabled": True,
    "car_cabin_temp_low_c": 0,
    "car_cabin_temp_high_c": 30,
}
NOW = "2026-07-20T04:30:00+00:00"
PAST = "2026-07-20T04:20:00+00:00"


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        self.calls.append({"kind": kind, "raw": raw})
        return self.responses.pop(0)


def _car_event(db, transport="car"):
    places.add(db, "Клиника Дента", aliases=["стоматолог"],
               lat=43.2260, lon=76.8670)
    db.commit()
    e = cal.add(db, "Врач", NOW, place="Клиника Дента", transport=transport)
    db.commit()
    return e


def _insert_reminder(db, event_id, kind="leave", fire_at=PAST):
    db.execute(
        "INSERT INTO reminders(event_id, label, anchor, kind, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?,?)",
        (event_id, "пора выходить", "leave_at", kind, fire_at, "pending", fire_at),
    )
    db.commit()


def test_reminders_piggybacks_car_hook(db, monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    e = _car_event(db)
    car._meta_set(db, "car_fuel_low", "1"); db.commit()
    _insert_reminder(db, e["id"], kind="leave")
    fd.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=TICK_CFG)

    raw = fd.calls[0]["raw"]
    assert "заправься" in raw["car"]
