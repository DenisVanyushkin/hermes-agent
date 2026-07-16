from fam import car, gate

def _cfg(**o):
    c = {"car_warmup_daily_limit": 5}; c.update(o); return c

class OkClient:
    def __init__(self): self.started = 0
    def start_engine(self): self.started += 1; return True

class _FakeClient:
    def __init__(self, start_ok=True): self.start_ok = start_ok
    def start_engine(self): return self.start_ok

def _insert_metric(db, engine_on=None, ignition_on=None, now=None):
    # Build the car_state dict the way StarlineClient._device_data would,
    # then reuse normalize()+record_metrics so the row shape matches
    # production exactly (bool-or-None per flag). `now` pins ts_utc when
    # a test needs deterministic row ordering (iso timestamps have
    # seconds precision; two inserts in one test can otherwise tie).
    state = {}
    if engine_on is not None:
        state["run"] = engine_on
    if ignition_on is not None:
        state["ign"] = ignition_on
    car.record_metrics(db, car.normalize({"car_state": state}, now=now))
    db.commit()

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

def test_already_on_via_ignition_only(db, monkeypatch):
    # Auto-started S96v2 shape: reports ign=true while run stays false
    # (phase-4 field notes). The already_on guard must still fire so the
    # daily limit isn't the only protection against a double-start.
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    _insert_metric(db, engine_on=0, ignition_on=1)   # auto-started S96v2 shape
    cfg = {"car_warmup_daily_limit": 5}
    r = car.do_warmup(db, _FakeClient(start_ok=True), cfg, "amina")
    assert r == {"ok": False, "reason": "already_on"}

def test_already_on_survives_newer_partial_row(db, monkeypatch):
    # Flags go stale independently across polls: normalize() sets each
    # only when the device reported it. A newer row carrying only
    # ignition_on=0 (engine_on NULL) must not mask an older row's
    # engine_on=1 -- each flag is read at its own latest non-NULL row.
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    _insert_metric(db, engine_on=1, now="2026-07-16T09:00:00+00:00")
    _insert_metric(db, ignition_on=0, now="2026-07-16T09:05:00+00:00")
    cfg = {"car_warmup_daily_limit": 5}
    r = car.do_warmup(db, _FakeClient(start_ok=True), cfg, "amina")
    assert r == {"ok": False, "reason": "already_on"}

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

def test_failed_attempt_does_not_consume_daily_limit(db, monkeypatch):
    # Policy change 2026-07-16: a failed attempt is refunded, so a retry
    # is allowed; only successful starts pin the limit.
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    cfg = {"car_warmup_daily_limit": 1}
    client = _FakeClient(start_ok=False)      # reuse this file's fake pattern
    r1 = car.do_warmup(db, client, cfg, "amina")
    assert r1 == {"ok": False, "reason": "failed"}
    r2 = car.do_warmup(db, client, cfg, "amina")
    assert r2 == {"ok": False, "reason": "failed"}  # retried, not limit-blocked
    assert not db.in_transaction                     # every path commits


# --- 2026-07-16 fixes: failed attempts must not consume the daily limit,
# --- and the failed audit row must carry the client's error detail.

def _seed_warmup_rows(db, results):
    import json
    for res in results:
        db.execute("INSERT INTO audit_log(ts_utc,kind,actor,payload) VALUES("
                   "strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),'car.warmup','agent',?)",
                   (json.dumps({"result": res}),))
    db.commit()

def test_count_refunds_failed_attempts(db):
    # attempt+failed pair = refunded; attempt+started = consumed;
    # bare attempt (in-flight racer) = still conservatively consumed.
    _seed_warmup_rows(db, ["attempt", "failed", "attempt", "started", "attempt"])
    assert car.warmup_count_today(db) == 2

def test_warmup_allowed_after_failed_attempts(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    _seed_warmup_rows(db, ["attempt", "failed"] * 5)  # 5 failures, limit 5
    cl = OkClient()
    r = car.do_warmup(db, cl, _cfg(), requester="denis")
    assert r["ok"] is True and cl.started == 1

def test_warmup_failed_audit_carries_error_detail(db, monkeypatch):
    import json
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    class FailClient:
        last_error = None
        def start_engine(self):
            self.last_error = "FileNotFoundError: no token store"
            return False
    r = car.do_warmup(db, FailClient(), _cfg(), requester="amina"); db.commit()
    assert r["ok"] is False
    rows = db.execute("SELECT payload FROM audit_log WHERE kind='car.warmup'").fetchall()
    failed = [json.loads(x["payload"]) for x in rows
              if json.loads(x["payload"])["result"] == "failed"]
    assert failed and failed[0]["error"] == "FileNotFoundError: no token store"
