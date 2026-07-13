from fam import car, gate

def _cfg(**o):
    c = {"car_warmup_daily_limit": 5}; c.update(o); return c

class OkClient:
    def __init__(self): self.started = 0
    def start_engine(self): self.started += 1; return True

def test_warmup_started_audits_and_notifies(db, monkeypatch):
    sent = []
    monkeypatch.setattr(gate, "notify_denis", lambda t: sent.append(t) or True)
    cl = OkClient()
    r = car.do_warmup(db, cl, _cfg(), requester="amina"); db.commit()
    assert r["ok"] is True and cl.started == 1 and len(sent) == 1
    rows = db.execute("SELECT payload FROM audit_log WHERE kind='car.warmup'").fetchall()
    assert len(rows) == 2  # attempt + started (attempt recorded BEFORE engine)

def test_warmup_blocked_by_daily_limit(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    import json
    for _ in range(5):
        db.execute("INSERT INTO audit_log(ts_utc,kind,actor,payload) VALUES("
                   "strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'car.warmup','tick',?)",
                   (json.dumps({"result": "started"}),))
    db.commit()
    cl = OkClient()
    r = car.do_warmup(db, cl, _cfg(), requester="denis")
    assert r == {"ok": False, "reason": "limit"} and cl.started == 0

def test_warmup_blocked_when_engine_on(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    car.record_metrics(db, car.normalize({"car_state": {"run": 1}})); db.commit()
    cl = OkClient()
    r = car.do_warmup(db, cl, _cfg(), requester="amina")
    assert r["reason"] == "already_on" and cl.started == 0
