def test_car_metrics_table_exists_and_schema_is_v7(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(car_metrics)")}
    assert {"ts_utc", "fuel_pct", "fuel_liters", "odometer_km", "engine_on",
            "ignition_on", "cabin_temp_c", "coolant_temp_c", "battery_v",
            "gsm_online", "gps_lat", "gps_lon", "raw_json"} <= cols
    v = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert v["value"] == "9"
