from fam import car, gate

def _cfg(**o):
    c = {"car_warmup_daily_limit": 5}; c.update(o); return c

class OkClient:
    def __init__(self): self.started = 0
    def start_engine(self): self.started += 1; return True

class _FakeClient:
    def __init__(self, start_ok=True): self.start_ok = start_ok
    def start_engine(self): return self.start_ok

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
    # Every real warmup writes an attempt+started pair (attempt commits
    # before the physical start; see do_warmup), and the daily limit
    # counts attempt rows -- seed attempt rows here, not bare started
    # rows, so this test still exercises the real counting logic.
    for _ in range(5):
        db.execute("INSERT INTO audit_log(ts_utc,kind,actor,payload) VALUES("
                   "strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'car.warmup','tick',?)",
                   (json.dumps({"result": "attempt"}),))
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

def test_warmup_attempt_row_committed_before_engine(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    class CheckClient:
        def start_engine(self):
            # attempt row must be durable (committed) before the engine starts
            assert not db.in_transaction, "attempt audit not committed before engine start"
            n = db.execute("SELECT COUNT(*) c FROM audit_log WHERE kind='car.warmup'").fetchone()["c"]
            assert n == 1  # the 'attempt' row is already persisted
            return True
    r = car.do_warmup(db, CheckClient(), {"car_warmup_daily_limit": 5}, requester="denis")
    assert r["ok"] is True

def test_failed_attempt_consumes_daily_limit(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    cfg = {"car_warmup_daily_limit": 1}
    client = _FakeClient(start_ok=False)      # reuse this file's fake pattern
    r1 = car.do_warmup(db, client, cfg, "amina")
    assert r1 == {"ok": False, "reason": "failed"}
    r2 = car.do_warmup(db, client, cfg, "amina")
    assert r2 == {"ok": False, "reason": "limit"}   # attempt already counted
    assert not db.in_transaction                     # every path commits
