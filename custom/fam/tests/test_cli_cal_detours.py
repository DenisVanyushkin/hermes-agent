"""Phase 7b, Task 3: `fam cal detours <event_id> [--json]` -- CLI surface
over plans.detours(). Same unknown-event-id -> exit 2 contract as cal
show/update/cancel; cfg comes from gate.load_config (monkeypatched here,
same pattern as test_cli_offsite.py, to avoid touching the real live
config file)."""
import json

from fam import cal, cli, places, plans


def _cfg_stub(**overrides):
    cfg = {
        "road_provider": "tomtom",
        "road_home_lat": 43.2220,
        "road_home_lon": 76.8512,
        "enroute_car_km": 3.0,
        "enroute_walk_km": 0.5,
        "detour_offer_min_min": 2,
        "detour_max_min": 30,
    }
    cfg.update(overrides)
    return cfg


def test_detours_unknown_event_exit_2(db, capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg_stub())
    rc = cli.main(["cal", "detours", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


def test_detours_json_shape(db, capsys, monkeypatch):
    places.add(db, "Клиника", lat=43.2298, lon=76.8823)
    db.commit()
    e = cal.add(db, "Врач", "2026-08-25T09:00:00+00:00", place="Клиника")
    places.add(db, "Аптека", lat=43.2400, lon=76.8900)
    db.commit()
    pid = plans.add(db, "Забрать заказ", place="Аптека")
    db.commit()

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg_stub())
    monkeypatch.setattr(
        cli.plans, "detours",
        lambda conn, event, cfg: [
            {"plan": plans.get(conn, pid), "detour_min": 12}])

    rc = cli.main(["cal", "detours", str(e["id"]), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == [{"plan_id": pid, "title": "Забрать заказ", "detour_min": 12}]


def test_detours_no_candidates_human_output(db, capsys, monkeypatch):
    places.add(db, "Клиника", lat=43.2298, lon=76.8823)
    db.commit()
    e = cal.add(db, "Врач", "2026-08-25T09:00:00+00:00", place="Клиника")
    db.commit()

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg_stub())
    monkeypatch.setattr(cli.plans, "detours", lambda conn, event, cfg: [])

    rc = cli.main(["cal", "detours", str(e["id"])])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no detour candidates" in out
