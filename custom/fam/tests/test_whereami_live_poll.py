"""Ask StarLine now, but only when the answer can still change anything.

The 30-minute fam-car.timer is fine for fuel and engine state; it is not
fine for "where is she right now". The one case that matters is a stale
fix on a MOVING car -- the rung whereami otherwise rejects outright --
and only when departure is close enough that a better answer would still
change leave_at. Everywhere else the cached row is used as-is: poll() is
two HTTP round-trips with a 20 s timeout, and tick.reminders runs every
minute.
"""
from fam import whereami

HOME = {"road_home_lat": 43.197391, "road_home_lon": 76.872737}
NOW = "2026-07-29T10:00:00+00:00"
AWAY_LAT, AWAY_LON = 43.26, 76.94
FRESH_LAT, FRESH_LON = 43.28, 76.96


def _cfg(**kw):
    cfg = dict(HOME)
    cfg.update(kw)
    return cfg


def _stale_moving_car(db):
    from datetime import datetime, timedelta
    fix = datetime.fromisoformat(NOW) - timedelta(minutes=45)
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)",
        (NOW, AWAY_LAT, AWAY_LON, int(fix.timestamp()), 80))
    db.commit()


class _Poller:
    """Stands in for car.StarlineClient().poll()."""

    def __init__(self, result=None):
        self.result = result
        self.calls = 0

    def __call__(self, conn, now=None):
        self.calls += 1
        return self.result


def _fresh_fix(now=NOW):
    from datetime import datetime
    return {"ts_utc": now, "gps_lat": FRESH_LAT, "gps_lon": FRESH_LON,
            "gps_ts": int(datetime.fromisoformat(now).timestamp()),
            "gps_speed": 0, "gps_sat": 9, "raw_json": "{}"}


def test_polls_when_the_only_fix_is_stale_and_moving(db, monkeypatch):
    _stale_moving_car(db)
    poller = _Poller(_fresh_fix())
    monkeypatch.setattr(whereami, "_live_poll", poller)

    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)

    assert poller.calls == 1
    assert o["source"] == "car"
    assert (o["lat"], o["lon"]) == (FRESH_LAT, FRESH_LON)


def test_does_not_poll_when_the_cached_fix_is_already_fresh(db, monkeypatch):
    from datetime import datetime, timedelta
    fix = datetime.fromisoformat(NOW) - timedelta(minutes=2)
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)",
        (NOW, AWAY_LAT, AWAY_LON, int(fix.timestamp()), 0))
    db.commit()
    poller = _Poller(_fresh_fix())
    monkeypatch.setattr(whereami, "_live_poll", poller)

    whereami.resolve_origin(db, _cfg(), now_utc=NOW)

    assert poller.calls == 0


def test_does_not_poll_for_a_departure_far_away(db, monkeypatch):
    """Nothing learned now survives until Thursday."""
    _stale_moving_car(db)
    poller = _Poller(_fresh_fix())
    monkeypatch.setattr(whereami, "_live_poll", poller)

    whereami.resolve_origin(db, _cfg(), now_utc=NOW,
                            at_utc="2026-08-02T10:00:00+00:00")

    assert poller.calls == 0


def test_does_not_poll_for_a_parked_stale_car(db, monkeypatch):
    """A parked car is already a usable (medium-confidence) answer -- no
    reason to spend two HTTP calls confirming it has not moved."""
    from datetime import datetime, timedelta
    fix = datetime.fromisoformat(NOW) - timedelta(minutes=120)
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)",
        (NOW, AWAY_LAT, AWAY_LON, int(fix.timestamp()), 0))
    db.commit()
    poller = _Poller(_fresh_fix())
    monkeypatch.setattr(whereami, "_live_poll", poller)

    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)

    assert poller.calls == 0
    assert o["source"] == "car" and o["confidence"] == "medium"


def test_a_failed_poll_degrades_quietly(db, monkeypatch):
    """poll() returns None on any StarLine failure; the ladder must
    simply carry on down."""
    _stale_moving_car(db)
    poller = _Poller(None)
    monkeypatch.setattr(whereami, "_live_poll", poller)

    o = whereami.resolve_origin(db, _cfg(), now_utc=NOW)

    assert poller.calls == 1
    assert o["source"] == "home"


def test_a_raising_poll_degrades_quietly(db, monkeypatch):
    def boom(conn, now=None):
        raise RuntimeError("StarLine упал")
    monkeypatch.setattr(whereami, "_live_poll", boom)
    _stale_moving_car(db)

    assert whereami.resolve_origin(db, _cfg(), now_utc=NOW)["source"] == "home"


def test_polling_can_be_disabled(db, monkeypatch):
    _stale_moving_car(db)
    poller = _Poller(_fresh_fix())
    monkeypatch.setattr(whereami, "_live_poll", poller)

    whereami.resolve_origin(db, _cfg(whereami_live_poll=False), now_utc=NOW)

    assert poller.calls == 0
