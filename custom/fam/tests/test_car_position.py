"""car.normalize/record_metrics must persist the GPS FIX metadata.

StarLine's `position` dict carries more than x/y: `ts` is when the fix
happened (unix seconds), `s` is speed in km/h, `sat_qty` the satellite
count. All three already reached raw_json; whereami needs them as
columns to tell "parked here" from "was here 40 minutes ago at 80 km/h".
"""
from fam import car


def _pos(**kw):
    pos = {"x": 43.2, "y": 76.9, "ts": 1785313312, "s": 0,
           "dir": 0, "sat_qty": 9, "r": 0}
    pos.update(kw)
    return {"position": pos}


def test_normalize_maps_fix_time_speed_and_satellites():
    m = car.normalize(_pos())
    assert m["gps_ts"] == 1785313312
    assert m["gps_speed"] == 0
    assert m["gps_sat"] == 9


def test_normalize_maps_moving_car():
    m = car.normalize(_pos(s=72))
    assert m["gps_speed"] == 72


def test_normalize_tolerates_position_without_fix_metadata():
    """Older StarLine firmware (and the fixtures in test_car_poll.py)
    send x/y only -- absent fields stay None rather than raising."""
    m = car.normalize({"position": {"x": 43.2, "y": 76.9}})
    assert m["gps_lat"] == 43.2 and m["gps_lon"] == 76.9
    assert m["gps_ts"] is None
    assert m["gps_speed"] is None
    assert m["gps_sat"] is None


def test_normalize_tolerates_missing_position():
    m = car.normalize({})
    assert m["gps_ts"] is None and m["gps_speed"] is None and m["gps_sat"] is None


def test_record_metrics_persists_fix_columns(db):
    """record_metrics' `cols` tuple is hand-maintained (car.py), so a
    column added to the schema and to normalize() but forgotten there
    would silently never be written. This is that regression test."""
    car.record_metrics(db, car.normalize(_pos(s=42)))
    row = db.execute(
        "SELECT gps_ts, gps_speed, gps_sat FROM car_metrics").fetchone()
    assert row["gps_ts"] == 1785313312
    assert row["gps_speed"] == 42
    assert row["gps_sat"] == 9
