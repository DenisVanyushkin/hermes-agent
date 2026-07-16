"""fam car stop (2026-07-16): remote engine stop, mirroring warmup's
guard/audit/notify shape minus the daily limit (stopping is physically
harmless). do_stop must re-poll live telemetry first: the 30-min tick
cadence means the latest car_metrics row routinely predates a remote
start, and the already_off guard must not trust it."""
import json

from fam import car, cli, gate


def _cfg(**o):
    c = {"car_warmup_daily_limit": 5}
    c.update(o)
    return c


class StopClient:
    def __init__(self, ok=True, poll_state=None):
        self.ok = ok
        self.stopped = 0
        self.poll_state = poll_state
        self.last_error = None

    def poll(self):
        if self.poll_state is None:
            return None
        return car.normalize({"car_state": self.poll_state})

    def stop_engine(self):
        self.stopped += 1
        if not self.ok:
            self.last_error = "api code=500 desc=boom"
        return self.ok


def _stop_rows(db):
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='car.stop' ORDER BY id").fetchall()
    return [json.loads(r["payload"]) for r in rows]


def test_stop_happy_path_audits_and_notifies(db, monkeypatch):
    sent = []
    monkeypatch.setattr(gate, "notify_denis", lambda t: sent.append(t) or True)
    cl = StopClient(poll_state={"ign": True, "run": False})
    r = car.do_stop(db, cl, _cfg(), requester="denis")
    db.commit()
    assert r["ok"] is True and cl.stopped == 1 and len(sent) == 1
    results = [p["result"] for p in _stop_rows(db)]
    assert results == ["attempt", "stopped"]


def test_stop_already_off_skips_api(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    cl = StopClient(poll_state={"ign": False, "run": False})
    r = car.do_stop(db, cl, _cfg(), requester="denis")
    assert r == {"ok": False, "reason": "already_off"} and cl.stopped == 0


def test_stop_trusts_fresh_poll_over_stale_row(db, monkeypatch):
    # Stale row says engine off; the live poll says it's running.
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    car.record_metrics(db, car.normalize({"car_state": {"ign": False, "run": False}}))
    db.commit()
    cl = StopClient(poll_state={"ign": True, "run": False})
    r = car.do_stop(db, cl, _cfg(), requester="denis")
    assert r["ok"] is True and cl.stopped == 1


def test_stop_failed_audit_carries_error_detail(db, monkeypatch):
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    cl = StopClient(ok=False, poll_state={"ign": True})
    r = car.do_stop(db, cl, _cfg(), requester="amina")
    db.commit()
    assert r == {"ok": False, "reason": "failed"}
    failed = [p for p in _stop_rows(db) if p["result"] == "failed"]
    assert failed and failed[0]["error"] == "api code=500 desc=boom"


def test_cli_stop_dry_run_without_confirm(db, monkeypatch, capsys):
    monkeypatch.setenv("FAM_DB", db.execute("PRAGMA database_list").fetchone()[2])
    # Dry run must not even construct a client, let alone hit the API.
    monkeypatch.setattr(car, "StarlineClient",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("client built on dry run")))
    rc = cli.main(["car", "stop"])
    assert rc == 0
    assert "dry run" in capsys.readouterr().out


def test_cli_stop_confirm_runs_do_stop(db, monkeypatch, capsys):
    monkeypatch.setenv("FAM_DB", db.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(gate, "notify_denis", lambda t: True)
    cl = StopClient(poll_state={"ign": True})
    monkeypatch.setattr(car, "StarlineClient", lambda *a, **k: cl)
    rc = cli.main(["car", "stop", "--confirm", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and cl.stopped == 1
