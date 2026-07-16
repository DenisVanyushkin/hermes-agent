from fam import car

def _live_shape(fuel_val=16, fuel_type="percents", mileage_val=36616):
    return {
        "battery": 13.3, "ctemp": 47, "etemp": 73, "status": 1, "gsm_lvl": 4,
        "car_state": {"run": True, "ign": True},
        "position": {"x": 43.2, "y": 76.9},
        "fuel": {"val": fuel_val, "ts": 1, "type": fuel_type} if fuel_type else {},
        "mileage": {"val": mileage_val, "ts": 1} if mileage_val is not None else {},
    }

def test_normalize_maps_live_shape_percents_fuel():
    m = car.normalize(_live_shape())
    assert m["fuel_pct"] == 16 and m["fuel_liters"] is None
    assert m["odometer_km"] == 36616
    assert m["engine_on"] is True and m["ignition_on"] is True
    assert m["cabin_temp_c"] == 47 and m["coolant_temp_c"] == 73
    assert m["battery_v"] == 13.3
    assert m["gsm_online"] is True
    assert m["gps_lat"] == 43.2 and m["gps_lon"] == 76.9
    assert m["raw_json"]  # full payload retained

def test_normalize_maps_litres_fuel():
    m = car.normalize(_live_shape(fuel_val=40, fuel_type="litres"))
    assert m["fuel_liters"] == 40 and m["fuel_pct"] is None

def test_normalize_empty_fuel_and_mileage_are_none():
    d = _live_shape(fuel_type=None, mileage_val=None)
    m = car.normalize(d)
    assert m["fuel_pct"] is None and m["fuel_liters"] is None
    assert m["odometer_km"] is None

def test_normalize_tolerates_missing_device_data():
    m = car.normalize({})
    assert all(m[k] is None for k in (
        "fuel_pct", "fuel_liters", "odometer_km", "engine_on", "ignition_on",
        "cabin_temp_c", "coolant_temp_c", "battery_v", "gsm_online",
        "gps_lat", "gps_lon"))
    assert m["raw_json"] == "{}"

def test_device_data_returns_empty_dict_when_device_missing(tmp_path, monkeypatch):
    import json as _json
    p = tmp_path / "t.json"
    p.write_text(_json.dumps({"user_id": "U", "slnet_token": "N", "device_id": "MISSING"}))
    class FakeApi:
        def __init__(self, uid, slnet):
            self.devices = {}
        def update(self): pass
        def update_obd(self): pass
    c = car.StarlineClient(token_path=str(p), _auth=None, _api_factory=FakeApi)
    assert c._device_data() == {}
    assert car.normalize(c._device_data())["fuel_pct"] is None

def test_device_data_tolerates_update_obd_failure(tmp_path):
    import json as _json
    p = tmp_path / "t.json"
    p.write_text(_json.dumps({"user_id": "U", "slnet_token": "N", "device_id": "DEV"}))
    class FakeDevice:
        _battery = 13.3
    class FakeApi:
        def __init__(self, uid, slnet):
            self.devices = {"DEV": FakeDevice()}
        def update(self): pass
        def update_obd(self): raise RuntimeError("obd down")
    c = car.StarlineClient(token_path=str(p), _auth=None, _api_factory=FakeApi)
    d = c._device_data()
    assert d["battery"] == 13.3
    assert d["fuel"] == {} and d["mileage"] == {}

def test_poll_returns_none_on_auth_failure(tmp_path, monkeypatch):
    class Boom:
        def get_user_id(self, slid): raise RuntimeError("dead")
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"slid_token": "S", "slnet_expires": 0, "device_id": "D"}))
    c = car.StarlineClient(token_path=str(p), _auth=Boom())
    assert c.poll() is None

def _car_store(tmp_path):
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"slid_token": "S", "slnet_token": "N",
                             "slnet_expires": 9999999999, "user_id": "U", "device_id": "DEV"}))
    return p


class _NoAuth:
    def get_user_id(self, slid):
        raise AssertionError("should not refresh")


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_start_engine_posts_ign_and_returns_true_on_code_200(tmp_path, monkeypatch):
    p = _car_store(tmp_path)
    seen = {}

    def fake_post(url, json, headers, timeout=20):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        return _FakeResponse({"code": 200})

    monkeypatch.setattr(car, "_http_post", fake_post)
    c = car.StarlineClient(token_path=str(p), _auth=_NoAuth(), _api_factory=None)
    assert c.start_engine() is True
    assert "DEV" in seen["url"]
    assert seen["json"] == {"type": "ign", "ign": 1}
    assert seen["headers"] == {"Cookie": "slnet=N"}


def test_start_engine_returns_false_on_code_400(tmp_path, monkeypatch):
    p = _car_store(tmp_path)

    def fake_post(url, json, headers, timeout=20):
        return _FakeResponse({"code": 400, "codestring": "Bad Request"})

    monkeypatch.setattr(car, "_http_post", fake_post)
    c = car.StarlineClient(token_path=str(p), _auth=_NoAuth(), _api_factory=None)
    assert c.start_engine() is False


def test_start_engine_returns_false_on_network_error(tmp_path, monkeypatch):
    p = _car_store(tmp_path)

    def fake_post(url, json, headers, timeout=20):
        raise ConnectionError("network down")

    monkeypatch.setattr(car, "_http_post", fake_post)
    c = car.StarlineClient(token_path=str(p), _auth=_NoAuth(), _api_factory=None)
    assert c.start_engine() is False
