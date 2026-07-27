def test_car_metrics_table_exists_and_schema_is_v7(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(car_metrics)")}
    assert {"ts_utc", "fuel_pct", "fuel_liters", "odometer_km", "engine_on",
            "ignition_on", "cabin_temp_c", "coolant_temp_c", "battery_v",
            "gsm_online", "gps_lat", "gps_lon", "raw_json"} <= cols
    # car_metrics was introduced in schema 6 (db.py's init_db comment:
    # "schema 6: car_metrics (phase 4)"). This test's actual concern is
    # "a fresh db has advanced past that point", not the CURRENT overall
    # schema_version -- pinning an exact match here just breaks this
    # unrelated test on every future, unrelated schema bump (that global
    # invariant already has its own home in test_db.py).
    v = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(v["value"]) >= 6
