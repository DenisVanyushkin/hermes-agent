from datetime import datetime, timezone, timedelta
from fam import car, gate

def _cfg(**o):
    c = {"car_fuel_low_pct": 25, "car_fuel_hysteresis": 5, "car_staleness_hours": 24}
    c.update(o); return c

def test_fuel_flag_hysteresis(db):
    cfg = _cfg()
    assert car.update_fuel_flag(db, 22, cfg) is True     # below -> set
    assert car.update_fuel_flag(db, 27, cfg) is True     # inside band -> stays set
    assert car.update_fuel_flag(db, 31, cfg) is False    # above +hyst -> clear
    assert car.fuel_is_low(db) is False

def test_record_metrics_inserts_and_audits(db):
    m = car.normalize({"fuel": {"val": 40, "type": "percents"}})
    rid = car.record_metrics(db, m); db.commit()
    assert db.execute("SELECT COUNT(*) n FROM car_metrics").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) n FROM audit_log WHERE kind='tick.car'").fetchone()["n"] == 1

def test_staleness_true_when_no_data_and_alerts_once(db, monkeypatch):
    sent = []
    monkeypatch.setattr(gate, "notify_denis", lambda t: sent.append(t) or True)
    cfg = _cfg()
    assert car.check_staleness(db, cfg) is True  # no rows -> stale
    car.maybe_alert_staleness(db, cfg)           # transition -> alert
    car.maybe_alert_staleness(db, cfg)           # already alerted -> silent
    assert len(sent) == 1

def test_tick_car_records_via_fake_client(db, monkeypatch):
    class FakeClient:
        def poll(self): return car.normalize({"fuel": {"val": 20, "type": "percents"}, "ctemp": -3})
    from fam import tick
    tick.car(db, client=FakeClient(), cfg=_cfg())
    assert db.execute("SELECT COUNT(*) n FROM car_metrics").fetchone()["n"] == 1
    assert car.fuel_is_low(db) is True
