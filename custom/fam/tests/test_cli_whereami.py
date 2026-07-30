"""`fam whereami` -- the surface the LLM agent drives when Amina sends a pin.

There is no deterministic inbound hook for location messages (only
reactions have one, via reaction_hook_cmd). The WhatsApp bridge already
parses locationMessage into the text body as "[Location: имя 43.19,76.87]",
so the amina-fam skill reads that and calls `fam whereami set`. These
tests pin the contract that skill depends on.
"""
import json

from fam import cli, whereami

NOW = "2026-07-29T10:00:00+00:00"


def _cfg():
    return {"road_home_lat": 43.197391, "road_home_lon": 76.872737}


def test_set_then_show_reports_the_shared_point(db, capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", _cfg)

    assert cli.main(["whereami", "set", "--lat", "43.26", "--lon", "76.94",
                     "--json"]) == 0
    hint = json.loads(capsys.readouterr().out)
    assert hint["source"] == "shared"
    assert hint["lat"] == 43.26

    assert cli.main(["whereami", "show", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["source"] == "shared"
    assert (shown["lat"], shown["lon"]) == (43.26, 76.94)


def test_show_falls_back_to_home(db, capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", _cfg)
    assert cli.main(["whereami", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "home"


def test_show_without_any_origin(db, capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", dict)
    assert cli.main(["whereami", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "no_origin"


def test_clear_removes_hints(db, capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", _cfg)
    cli.main(["whereami", "set", "--lat", "43.26", "--lon", "76.94", "--json"])
    capsys.readouterr()

    assert cli.main(["whereami", "clear", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cleared"] == 1

    cli.main(["whereami", "show", "--json"])
    assert json.loads(capsys.readouterr().out)["source"] == "home"


def test_bad_coordinates_are_rejected_loudly(db, capsys, monkeypatch):
    """An LLM parsing "[Location: ...]" can misread the pair. A silently
    accepted 991.2 would route from the middle of nowhere; a non-zero
    exit tells the agent it failed."""
    monkeypatch.setattr(cli.gate, "load_config", _cfg)
    assert cli.main(["whereami", "set", "--lat", "991.2", "--lon", "76.94"]) == 2


def test_ttl_expiry_is_honoured(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", _cfg)
    whereami.set_hint(db, 43.26, 76.94, ttl_min=30, now_utc=NOW)
    db.commit()

    assert whereami.resolve_origin(
        db, _cfg(), now_utc="2026-07-29T10:20:00+00:00")["source"] == "shared"
    assert whereami.resolve_origin(
        db, _cfg(), now_utc="2026-07-29T10:40:00+00:00")["source"] == "home"


def test_manual_source_gets_the_longer_default_ttl(db):
    cfg = dict(_cfg())
    shared = whereami.set_hint(db, 43.26, 76.94, source="shared",
                               now_utc=NOW, cfg=cfg)
    manual = whereami.set_hint(db, 43.26, 76.94, source="manual",
                               now_utc=NOW, cfg=cfg)
    assert manual["expires_utc"] > shared["expires_utc"]
