"""2026-07-16: tests must never touch prod stores (the set-device tests
wrote device_id=D9 into the live starline-token.json), and gate must
resolve its config host->sandbox instead of mkdir'ing /home/denis inside
the docker sandbox (which then poisons db.resolve_db_path)."""
import json

from fam import car, gate


def test_token_store_paths_isolated_from_prod():
    # conftest must repoint both candidates at tmp for every test.
    assert not car.TOKEN_PATH.startswith("/home/denis")
    assert not car.SANDBOX_TOKEN_PATH.startswith("/root")


def test_config_paths_isolated_from_prod():
    assert not str(gate.CONFIG_PATH).startswith("/home/denis")
    assert not str(gate.SANDBOX_CONFIG_PATH).startswith("/root")


def test_load_config_prefers_sandbox_when_host_missing(tmp_path, monkeypatch):
    host = tmp_path / "host" / "fam-config.json"
    sandbox = tmp_path / "sandbox" / "fam-config.json"
    sandbox.parent.mkdir()
    sandbox.write_text(json.dumps({"quiet_start": "20:59"}), encoding="utf-8")
    monkeypatch.setattr(gate, "CONFIG_PATH", host)
    monkeypatch.setattr(gate, "SANDBOX_CONFIG_PATH", sandbox)
    cfg = gate.load_config()
    assert cfg["quiet_start"] == "20:59"
    # The poison: bootstrap used to mkdir the host path inside the sandbox.
    assert not host.parent.exists()


def test_load_config_bootstraps_host_when_neither_exists(tmp_path, monkeypatch):
    host = tmp_path / "host" / "fam-config.json"
    sandbox = tmp_path / "sandbox" / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", host)
    monkeypatch.setattr(gate, "SANDBOX_CONFIG_PATH", sandbox)
    cfg = gate.load_config()
    assert host.exists() and "quiet_start" in cfg  # example bootstrap, host side
