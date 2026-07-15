import json, os
from fam import car

class FakeAuth:
    def __init__(self): self.calls = []
    def get_user_id(self, slid):
        self.calls.append(("get_user_id", slid))
        # slnet_expires far in the future (unix seconds)
        return ("SLNET2", 9999999999, "UID")

def _store(tmp_path, **over):
    p = tmp_path / "starline-token.json"
    base = {"app_id": "15526", "app_token": "AT", "slid_token": "SLID",
            "user_id": "UID", "slnet_token": "SLNET", "slnet_expires": 0,
            "device_id": "DEV"}
    base.update(over)
    p.write_text(json.dumps(base), encoding="utf-8")
    return p

def test_ensure_slnet_refreshes_when_expired(tmp_path):
    p = _store(tmp_path, slnet_expires=0)
    auth = FakeAuth()
    c = car.StarlineClient(token_path=str(p), _auth=auth)
    c.ensure_slnet(now_ts=1000)
    assert ("get_user_id", "SLID") in auth.calls
    saved = json.loads(p.read_text())
    assert saved["slnet_token"] == "SLNET2"
    assert saved["slnet_expires"] == 9999999999
    assert oct(os.stat(p).st_mode)[-3:] == "600"

def test_ensure_slnet_skips_when_fresh(tmp_path):
    p = _store(tmp_path, slnet_token="FRESH", slnet_expires=9999999999)
    auth = FakeAuth()
    car.StarlineClient(token_path=str(p), _auth=auth).ensure_slnet(now_ts=1000)
    assert auth.calls == []  # not refreshed

def test_ensure_slnet_raises_authexpired_on_failure(tmp_path):
    class Boom:
        def get_user_id(self, slid): raise RuntimeError("slid dead")
    p = _store(tmp_path, slnet_expires=0)
    import pytest
    with pytest.raises(car.AuthExpired):
        car.StarlineClient(token_path=str(p), _auth=Boom()).ensure_slnet(now_ts=1000)


class FakeDevice:
    def __init__(self, alias):
        self._alias = alias


def test_list_devices_returns_id_to_alias_map(tmp_path):
    p = _store(tmp_path, slnet_expires=9999999999)

    class FakeApi:
        def __init__(self, uid, slnet):
            self.devices = {"D1": FakeDevice("Car1")}

        def update(self):
            pass

    c = car.StarlineClient(token_path=str(p), _auth=FakeAuth(), _api_factory=FakeApi)
    assert c.list_devices() == {"D1": "Car1"}


def test_list_devices_multiple(tmp_path):
    p = _store(tmp_path, slnet_expires=9999999999)

    class FakeApi:
        def __init__(self, uid, slnet):
            self.devices = {"D1": FakeDevice("Car1"), "D2": FakeDevice("Car2")}

        def update(self):
            pass

    c = car.StarlineClient(token_path=str(p), _auth=FakeAuth(), _api_factory=FakeApi)
    assert c.list_devices() == {"D1": "Car1", "D2": "Car2"}


def test_set_device_persists_to_store(tmp_path):
    p = _store(tmp_path, device_id=None)
    c = car.StarlineClient(token_path=str(p), _auth=FakeAuth())
    c.set_device("D2")
    assert json.loads(p.read_text())["device_id"] == "D2"
