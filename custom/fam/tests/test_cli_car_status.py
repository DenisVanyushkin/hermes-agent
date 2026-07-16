"""`fam car status` must not under-report a running engine: the S96v2
auto-start sets ign=true while run stays false (phase-4 field notes), so
the status the agent reads has to expose run-OR-ign, same rule as the
warmup guard's _latest_engine_on. 2026-07-16: Hermes told Denis the
engine was off seconds after a successful remote start."""
import json

from fam import car, cli


def _insert_metric(db, **state):
    car.record_metrics(db, car.normalize({"car_state": state}))
    db.commit()


def test_status_engine_running_true_on_ign_only(db, monkeypatch, capsys):
    monkeypatch.setenv("FAM_DB", db.execute("PRAGMA database_list").fetchone()[2])
    _insert_metric(db, run=False, ign=True)
    rc = cli.main(["car", "status", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["engine_running"] is True


def test_status_engine_running_false_when_all_off(db, monkeypatch, capsys):
    monkeypatch.setenv("FAM_DB", db.execute("PRAGMA database_list").fetchone()[2])
    _insert_metric(db, run=False, ign=False)
    rc = cli.main(["car", "status", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["engine_running"] is False
