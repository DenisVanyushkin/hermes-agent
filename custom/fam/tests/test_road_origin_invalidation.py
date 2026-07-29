"""road_checked_at stopped being a complete cache key.

Before the origin became dynamic, "when did we compute this" fully
determined "is it still good", because the start point was the constant
road_home_lat/lon. Now the answer can go stale without the clock moving
at all: Amina drives across town and the stored figure -- computed
minutes ago, comfortably inside its freshness window -- describes a trip
she is no longer taking.

tick.road_recompute's window guard (`checked_dt >= window_open`) would
happily suppress the fix. So the guard gains a second question: has the
ORIGIN moved? And, because a moving car moves continuously, that
question is rate-limited -- otherwise one commuting event would spend the
entire road_daily_cap in under two hours.
"""
from datetime import datetime, timedelta, timezone

from fam import cal, places, road, tick

HOME_LAT, HOME_LON = 43.197391, 76.872737
AWAY_LAT, AWAY_LON = 43.26, 76.94
DEST_LAT, DEST_LON = 43.20, 76.95

NOW = "2026-07-29T10:00:00+00:00"


def _cfg(**kw):
    cfg = {"road_home_lat": HOME_LAT, "road_home_lon": HOME_LON,
           "road_coef": 1.4, "road_speed_kmh": 30,
           "road_recompute_min": [120, 60]}
    cfg.update(kw)
    return cfg


def _event_leaving_soon(db):
    """An event inside the T-120 window, so road_recompute considers it."""
    places.add(db, "Театр", lat=DEST_LAT, lon=DEST_LON)
    db.commit()
    start = (datetime.fromisoformat(NOW) + timedelta(minutes=90)).isoformat(
        timespec="seconds")
    e = cal.add(db, "Спектакль", start, place="Театр")
    db.commit()
    return e["id"]


def _car(db, lat, lon, when=NOW):
    db.execute("DELETE FROM car_metrics")
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)",
        (when, lat, lon, int(datetime.fromisoformat(when).timestamp()), 0))
    db.commit()


def _row(db, event_id):
    return db.execute(
        "SELECT travel_min_road, road_checked_at, road_origin_lat, "
        "road_origin_lon, road_origin_source FROM events WHERE id=?",
        (event_id,)).fetchone()


def test_recompute_persists_the_origin_it_used(db, monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event_id = _event_leaving_soon(db)
    _car(db, AWAY_LAT, AWAY_LON)

    tick.road_recompute(db, now_utc=NOW, cfg=_cfg())

    row = _row(db, event_id)
    assert row["road_origin_source"] == "car"
    assert row["road_origin_lat"] == AWAY_LAT
    assert row["road_origin_lon"] == AWAY_LON


def test_moved_origin_forces_a_recompute_inside_a_checked_window(db, monkeypatch):
    """The regression this whole file exists for."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event_id = _event_leaving_soon(db)
    _car(db, HOME_LAT, HOME_LON)
    tick.road_recompute(db, now_utc=NOW, cfg=_cfg(road_recompute_min=[120]))
    first = _row(db, event_id)
    assert first["road_checked_at"] is not None  # window now counts as checked

    # She drives across town; the clock barely moves, so the freshness
    # guard alone would suppress any further computation.
    later = (datetime.fromisoformat(NOW) + timedelta(minutes=15)).isoformat(
        timespec="seconds")
    _car(db, AWAY_LAT, AWAY_LON, when=later)

    tick.road_recompute(db, now_utc=later, cfg=_cfg(road_recompute_min=[120]))

    second = _row(db, event_id)
    assert second["road_origin_lat"] == AWAY_LAT
    assert second["travel_min_road"] != first["travel_min_road"]


def test_unmoved_origin_is_still_suppressed_by_the_freshness_window(db, monkeypatch):
    """The optimisation must survive: no movement, no extra TomTom spend."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event_id = _event_leaving_soon(db)
    _car(db, AWAY_LAT, AWAY_LON)
    tick.road_recompute(db, now_utc=NOW, cfg=_cfg(road_recompute_min=[120]))
    first = _row(db, event_id)

    later = (datetime.fromisoformat(NOW) + timedelta(minutes=15)).isoformat(
        timespec="seconds")
    _car(db, AWAY_LAT, AWAY_LON, when=later)  # same spot
    touched = tick.road_recompute(db, now_utc=later, cfg=_cfg(road_recompute_min=[120]))

    assert touched == 0
    assert _row(db, event_id)["road_checked_at"] == first["road_checked_at"]


def test_a_moving_car_cannot_recompute_every_tick(db, monkeypatch):
    """Rate limit: without it, one event on a 40-minute drive would issue
    a TomTom call every minute and exhaust road_daily_cap (100) before
    she arrived."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event_id = _event_leaving_soon(db)
    _car(db, HOME_LAT, HOME_LON)
    tick.road_recompute(db, now_utc=NOW, cfg=_cfg(road_recompute_min=[120]))

    # one minute later, a long way away -- moved, but far too soon
    later = (datetime.fromisoformat(NOW) + timedelta(minutes=1)).isoformat(
        timespec="seconds")
    _car(db, AWAY_LAT, AWAY_LON, when=later)
    touched = tick.road_recompute(db, now_utc=later,
                                  cfg=_cfg(road_recompute_min=[120], whereami_origin_recheck_min=10))

    assert touched == 0


def test_small_origin_drift_is_not_a_move(db, monkeypatch):
    """GPS jitter and parking on the other side of the building must not
    count as relocation."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event_id = _event_leaving_soon(db)
    _car(db, AWAY_LAT, AWAY_LON)
    tick.road_recompute(db, now_utc=NOW, cfg=_cfg(road_recompute_min=[120]))

    later = (datetime.fromisoformat(NOW) + timedelta(minutes=30)).isoformat(
        timespec="seconds")
    _car(db, AWAY_LAT + 0.002, AWAY_LON + 0.002, when=later)  # ~250 m
    touched = tick.road_recompute(db, now_utc=later, cfg=_cfg(road_recompute_min=[120]))

    assert touched == 0


def test_first_ever_computation_has_no_stored_origin_to_compare(db, monkeypatch):
    """No stored origin must mean "compute", never a crash or a skip."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event_id = _event_leaving_soon(db)
    assert _row(db, event_id)["road_origin_lat"] is None
    touched = tick.road_recompute(db, now_utc=NOW, cfg=_cfg())
    assert touched == 1
