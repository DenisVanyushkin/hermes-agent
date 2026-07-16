"""CLI wiring: `fam car set-device` discovery/auto-set/multiple-devices."""
import json
from fam import cli, car


def _store(tmp_path, **over):
    p = tmp_path / "starline-token.json"
    base = {"app_id": "15526", "app_token": "AT", "slid_token": "SLID",
            "user_id": "UID", "slnet_token": "SLNET", "slnet_expires": 9999999999,
            "device_id": None}
    base.update(over)
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def test_set_device_auto_sets_when_one_device(tmp_path, monkeypatch, capsys):
    p = _store(tmp_path)
    monkeypatch.setattr(car, "TOKEN_PATH", str(p))
    monkeypatch.setattr(car.StarlineClient, "list_devices", lambda self: {"D1": "Car1"})

    rc = cli.main(["car", "set-device"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "device_id=D1" in out and "Car1" in out
    assert json.loads(p.read_text())["device_id"] == "D1"


def test_set_device_lists_when_multiple_devices(tmp_path, monkeypatch, capsys):
    p = _store(tmp_path)
    monkeypatch.setattr(car, "TOKEN_PATH", str(p))
    monkeypatch.setattr(car.StarlineClient, "list_devices",
                         lambda self: {"D1": "Car1", "D2": "Car2"})

    rc = cli.main(["car", "set-device"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "D1" in out and "Car1" in out
    assert "D2" in out and "Car2" in out
    # no auto-set on ambiguity
    assert json.loads(p.read_text())["device_id"] is None


def test_set_device_no_devices(tmp_path, monkeypatch, capsys):
    p = _store(tmp_path)
    monkeypatch.setattr(car, "TOKEN_PATH", str(p))
    monkeypatch.setattr(car.StarlineClient, "list_devices", lambda self: {})

    rc = cli.main(["car", "set-device"])
    assert rc == 0
    assert "no devices" in capsys.readouterr().out


def test_set_device_explicit_arg(tmp_path, monkeypatch, capsys):
    p = _store(tmp_path)
    monkeypatch.setattr(car, "TOKEN_PATH", str(p))

    rc = cli.main(["car", "set-device", "D9"])
    assert rc == 0
    assert "device_id=D9" in capsys.readouterr().out
    assert json.loads(p.read_text())["device_id"] == "D9"
