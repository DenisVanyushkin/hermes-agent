"""fam.whereami -- откуда считать время в пути.

The ladder: manual/shared hint -> car GPS (only when the car is AWAY
from home) -> place of the current/just-ended event -> home. Home is the
always-available bottom rung, so a fresh install with nothing configured
behaves exactly as it did before this module existed.
"""
from fam import whereami

HOME = {"road_home_lat": 43.197391, "road_home_lon": 76.872737}
NOW = "2026-07-29T10:00:00+00:00"

# ~9 km north-east of home -- comfortably outside every radius here
AWAY_LAT, AWAY_LON = 43.26, 76.94


def _cfg(**kw):
    cfg = dict(HOME)
    cfg.update(kw)
    return cfg


def _car(db, lat=AWAY_LAT, lon=AWAY_LON, fix_age_min=5, speed=0, ts=NOW):
    """Insert one car_metrics row whose GPS fix is `fix_age_min` old."""
    from datetime import datetime, timedelta
    fix_dt = datetime.fromisoformat(ts) - timedelta(minutes=fix_age_min)
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed,gps_sat)"
        " VALUES (?,?,?,?,?,?)",
        (ts, lat, lon, int(fix_dt.timestamp()), speed, 9))
    db.commit()


def _hint(db, source, lat, lon, ts=NOW, expires="2026-07-29T23:00:00+00:00",
          label=""):
    db.execute(
        "INSERT INTO location_hints(source,lat,lon,label,ts_utc,expires_utc)"
        " VALUES (?,?,?,?,?,?)", (source, lat, lon, label, ts, expires))
    db.commit()


def _event(db, lat, lon, start, end, title="встреча"):
    cur = db.execute(
        "INSERT INTO places(name,lat,lon,created_at) VALUES (?,?,?,?)",
        (title + "-место", lat, lon, NOW))
    place_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO events(title,start_utc,end_utc,place_id,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?)", (title, start, end, place_id, NOW, NOW))
    db.commit()
    return cur.lastrowid


# --- bottom rung -------------------------------------------------------

def test_falls_back_to_home_when_nothing_is_known(db):
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert (o["lat"], o["lon"]) == (HOME["road_home_lat"], HOME["road_home_lon"])
    assert o["source"] == "home"


def test_returns_none_when_even_home_is_unconfigured(db):
    """Pre-existing behaviour: callers bailed out on no_home_config. The
    resolver must keep giving them a way to detect that, not invent a
    coordinate."""
    assert whereami.resolve_origin(db, {}, now_utc=NOW) is None


# --- car rung ----------------------------------------------------------

def test_fresh_fix_away_from_home_wins(db):
    _car(db, fix_age_min=3)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "car"
    assert o["confidence"] == "high"
    assert (o["lat"], o["lon"]) == (AWAY_LAT, AWAY_LON)


def test_car_parked_at_home_does_not_win(db):
    """The whole point of the home-radius check: a car sitting in the
    driveway is not evidence about Amina, it is the absence of evidence.
    Falling through keeps the calendar rung reachable."""
    _car(db, lat=HOME["road_home_lat"], lon=HOME["road_home_lon"], fix_age_min=3)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "home"


def test_stale_fix_on_a_stopped_car_is_still_usable(db):
    """Parked two hours ago at the mall -- she is very likely still near
    it, so this is worth using, just not with full confidence."""
    _car(db, fix_age_min=120, speed=0)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "car"
    assert o["confidence"] == "medium"


def test_stale_fix_on_a_moving_car_is_rejected(db):
    """80 km/h forty minutes ago says nothing about where she is now."""
    _car(db, fix_age_min=40, speed=80)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "home"


def test_fix_time_beats_poll_time(db):
    """ts_utc is when fam polled; gps_ts is when the fix happened. A row
    inserted seconds ago can carry an hour-old fix -- freshness must be
    judged on the latter (observed ~7 min apart in prod)."""
    _car(db, fix_age_min=90, speed=90, ts=NOW)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "home"  # rejected on fix age, despite a fresh row


# --- event rung --------------------------------------------------------

def test_ongoing_event_elsewhere_wins_over_home(db):
    """Denis's rule: if the calendar says she is somewhere right now,
    compute from there and say so."""
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "event"
    assert (o["lat"], o["lon"]) == (AWAY_LAT, AWAY_LON)


def test_car_away_from_home_beats_the_calendar(db):
    """Physical evidence outranks a plan: the calendar says where she
    intended to be, the car says where she is."""
    _event(db, 43.30, 77.00,
           "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00")
    _car(db, fix_age_min=3)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "car"


def test_the_target_event_is_not_its_own_origin(db):
    """Computing travel TO an event must never start FROM that event."""
    eid = _event(db, AWAY_LAT, AWAY_LON,
                 "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00")
    event = {"id": eid}
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW, event=event)
    assert o["source"] == "home"


def test_long_finished_event_is_ignored(db):
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-07-29T05:00:00+00:00", "2026-07-29T06:00:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "home"


def test_just_finished_event_still_counts(db):
    """She cannot have gone far in ten minutes."""
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-07-29T08:00:00+00:00", "2026-07-29T09:50:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "event"


def test_cancelled_event_is_ignored(db):
    eid = _event(db, AWAY_LAT, AWAY_LON,
                 "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00")
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (eid,))
    db.commit()
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "home"


# --- hint rung ---------------------------------------------------------

def test_shared_location_beats_everything(db):
    _car(db, fix_age_min=1)
    _event(db, 43.30, 77.00,
           "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00")
    _hint(db, "shared", 43.28, 76.95)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "shared"
    assert o["confidence"] == "high"


def test_expired_hint_is_ignored(db):
    _hint(db, "shared", 43.28, 76.95, expires="2026-07-29T09:00:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "home"


def test_most_recent_hint_wins_regardless_of_kind(db):
    """Deliberately recency-ordered rather than manual-over-shared: a
    manual override set this morning must not outrank a location Amina
    shared an hour ago. Whoever spoke last knows best."""
    _hint(db, "manual", 43.10, 76.80, ts="2026-07-29T06:00:00+00:00")
    _hint(db, "shared", 43.28, 76.95, ts="2026-07-29T09:30:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "shared"
    assert (o["lat"], o["lon"]) == (43.28, 76.95)


def test_manual_override_wins_when_it_is_the_newer_one(db):
    _hint(db, "shared", 43.28, 76.95, ts="2026-07-29T06:00:00+00:00")
    _hint(db, "manual", 43.10, 76.80, ts="2026-07-29T09:30:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert o["source"] == "manual"


# --- two clocks --------------------------------------------------------
# now_utc is wall-clock ("how stale is this GPS fix"); at_utc is the
# departure anchor, which for cal.recompute_road can be days ahead
# ("where will she be when she sets off"). road.py already carries this
# exact distinction for _tomtom_calls_today; conflating them here would
# route Thursday's trip from today's parking spot.

def test_departure_far_ahead_ignores_todays_car_position(db):
    _car(db, fix_age_min=3)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW,
                                at_utc="2026-08-02T10:00:00+00:00")
    assert o["source"] == "home"


def test_departure_far_ahead_still_uses_the_calendar(db):
    """A recurring class that ends at 17:50 tells us where she sets off
    from next Tuesday, no matter where the car is standing today."""
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-08-02T09:00:00+00:00", "2026-08-02T09:50:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW,
                                at_utc="2026-08-02T10:00:00+00:00")
    assert o["source"] == "event"


def test_event_rung_is_evaluated_at_departure_not_now(db):
    """The event is over by wall-clock NOW but ongoing at departure -- it
    must count, since departure is what we are routing for."""
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-07-29T10:20:00+00:00", "2026-07-29T10:50:00+00:00")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW,
                                at_utc="2026-07-29T10:40:00+00:00")
    assert o["source"] == "event"


def test_at_utc_defaults_to_now(db):
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00")
    assert whereami.resolve_origin(db, _cfg(), now_utc=NOW)["source"] == "event"


# --- contract ----------------------------------------------------------

def test_never_raises_on_garbage(db):
    """road.py's module contract is that road math never throws; the
    resolver sits inside that contract and must honour it."""
    for bad_cfg in ({}, {"road_home_lat": "нет"}, {"road_home_lat": None},
                    {"road_home_lat": 43.1, "road_home_lon": "юг"}):
        whereami.resolve_origin(db, bad_cfg, now_utc=NOW)
    whereami.resolve_origin(db, _cfg(), now_utc="не-дата")
    whereami.resolve_origin(db, _cfg(), now_utc=NOW, event={"id": "не-число"})


def test_result_always_carries_a_human_label(db):
    _car(db, fix_age_min=3)
    assert whereami.resolve_origin(db, _cfg(), now_utc=NOW)["label"]
    db.execute("DELETE FROM car_metrics")
    db.commit()
    assert whereami.resolve_origin(db, _cfg(), now_utc=NOW)["label"]


def test_event_label_names_the_place(db):
    _event(db, AWAY_LAT, AWAY_LON,
           "2026-07-29T09:30:00+00:00", "2026-07-29T11:00:00+00:00",
           title="йога")
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert "йога-место" in o["label"]


def test_car_label_uses_a_known_place_when_close_enough(db):
    """"от машины" is a poor thing to read in a reminder when we know the
    car is standing at a place Amina has a name for."""
    db.execute("INSERT INTO places(name,lat,lon,created_at) VALUES (?,?,?,?)",
               ("Спортзал", AWAY_LAT, AWAY_LON, NOW))
    db.commit()
    _car(db, fix_age_min=3)
    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)
    assert "Спортзал" in o["label"]
