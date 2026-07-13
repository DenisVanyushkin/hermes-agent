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
