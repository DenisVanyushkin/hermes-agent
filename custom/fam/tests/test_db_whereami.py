"""schema v12 (widened): dynamic-origin support for road math.

Three additions, one purpose -- letting fam.whereami answer "откуда
считать время в пути" with something other than the static
road_home_lat/lon pair:

  * car_metrics.gps_ts/gps_speed/gps_sat -- StarLine already returns
    position.ts/s/sat_qty on every poll and car.normalize() already
    throws them away into raw_json. A parked-vs-moving decision needs
    the FIX time, not the poll time (observed ~7 min apart in prod), so
    these get real columns.
  * events.road_origin_lat/lon/source -- which point produced the
    cached travel_min_road. Without it, road_checked_at is an
    incomplete cache key: a dynamic origin can move while the time
    window says "already checked".
  * location_hints -- manual overrides and locations Amina shares over
    WhatsApp.
"""
import sqlite3

from fam import db as famdb


def test_car_metrics_has_gps_fix_columns(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(car_metrics)")}
    assert {"gps_ts", "gps_speed", "gps_sat"} <= cols


def test_events_has_road_origin_columns(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(events)")}
    assert {"road_origin_lat", "road_origin_lon", "road_origin_source"} <= cols


def test_location_hints_table(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(location_hints)")}
    assert {"id", "source", "lat", "lon", "label",
            "ts_utc", "expires_utc"} <= cols


def test_location_hints_rejects_unknown_source(db):
    db.execute("INSERT INTO location_hints(source,lat,lon,ts_utc,expires_utc)"
               " VALUES ('shared',43.2,76.9,'now','later')")
    try:
        db.execute(
            "INSERT INTO location_hints(source,lat,lon,ts_utc,expires_utc)"
            " VALUES ('gps',43.2,76.9,'now','later')")
        raise AssertionError("CHECK on source must reject unknown providers")
    except sqlite3.IntegrityError:
        pass


def test_migrates_from_v11_shaped_db(tmp_path):
    """v11 is what prod actually runs (verified 2026-07-29): the current
    SCHEMA minus every v12 addition. The v12 block is still unmigrated
    anywhere, so these columns are added by widening it rather than by
    bumping to v13 -- same reasoning, and the same controller
    authorization, as external_location and idx_plans_external_uid
    already documented in db.py's v12 comment block.
    """
    conn = sqlite3.connect(str(tmp_path / "legacy_11.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(famdb.SCHEMA)
    # Strip the v12 additions to get a v11 shape. events/plans need no
    # stripping: every v12 column there (owner, external_*, and now
    # road_origin_*) is added by _ensure_column only and never appears in
    # SCHEMA, so a fresh executescript already produces the v11 shape.
    conn.execute("DROP TABLE location_hints")
    keep = [r["name"] for r in conn.execute("PRAGMA table_info(car_metrics)")
            if r["name"] not in ("gps_ts", "gps_speed", "gps_sat")]
    conn.execute("CREATE TABLE car_metrics_v11 AS SELECT "
                 + ", ".join(keep) + " FROM car_metrics")
    conn.execute("DROP TABLE car_metrics")
    conn.execute("ALTER TABLE car_metrics_v11 RENAME TO car_metrics")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','11')")
    conn.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO events(id,title,start_utc,created_at,updated_at) "
        "VALUES (1,'старое событие','2026-07-01T00:00:00Z',"
        "'2026-07-01T00:00:00Z','2026-07-01T00:00:00Z')")
    conn.commit()

    famdb.init_db(conn)  # migrate

    car_cols = {r["name"] for r in conn.execute("PRAGMA table_info(car_metrics)")}
    assert {"gps_ts", "gps_speed", "gps_sat"} <= car_cols
    event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert {"road_origin_lat", "road_origin_lon", "road_origin_source"} <= event_cols
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "location_hints" in tables
    # pre-existing rows survive the ALTER TABLE ADD COLUMN backfill
    assert conn.execute(
        "SELECT title FROM events WHERE id=1").fetchone()["title"] == "старое событие"
    assert conn.execute(
        "SELECT road_origin_source FROM events WHERE id=1"
    ).fetchone()["road_origin_source"] is None

    famdb.init_db(conn)  # idempotent re-run must not raise
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
