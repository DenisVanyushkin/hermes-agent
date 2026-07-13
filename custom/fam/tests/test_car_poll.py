from fam import car

def test_normalize_maps_known_fields_and_tolerates_missing():
    dev = {"fuel_percent": 22, "mileage": 145000, "car_state": {"ign": 0, "run": 1},
           "ctemp": -8, "etemp": 70, "battery": 12.4, "gsm_lvl": 3,
           "position": {"x": 76.9, "y": 43.2}}
    m = car.normalize(dev)
    assert m["fuel_pct"] == 22
    assert m["odometer_km"] == 145000
    assert m["engine_on"] == 1 and m["ignition_on"] == 0
    assert m["cabin_temp_c"] == -8 and m["coolant_temp_c"] == 70
    assert m["battery_v"] == 12.4
    assert m["gps_lon"] == 76.9 and m["gps_lat"] == 43.2
    assert m["raw_json"]  # full payload retained
    # missing fields -> None, no crash
    assert car.normalize({})["fuel_pct"] is None

def test_poll_returns_none_on_auth_failure(tmp_path, monkeypatch):
    class Boom:
        def get_user_id(self, slid): raise RuntimeError("dead")
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"slid_token": "S", "slnet_expires": 0, "device_id": "D"}))
    c = car.StarlineClient(token_path=str(p), _auth=Boom())
    assert c.poll() is None

def test_start_engine_calls_set_car_state(tmp_path):
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"slid_token": "S", "slnet_token": "N",
                             "slnet_expires": 9999999999, "user_id": "U", "device_id": "DEV"}))
    seen = {}
    class FakeApi:
        def __init__(self, uid, slnet): pass
        def set_car_state(self, dev, name, state): seen["args"] = (dev, name, state); return True
    class NoAuth:
        def get_user_id(self, slid): raise AssertionError("should not refresh")
    c = car.StarlineClient(token_path=str(p), _auth=NoAuth(), _api_factory=FakeApi)
    assert c.start_engine() is True
    assert seen["args"] == ("DEV", "engine", True)
