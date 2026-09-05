import sqlite3
import pytest

EXPECTED_TABLES = {
    "meta", "events", "event_participants", "people",
    "people_aliases", "group_members", "places", "audit_log",
    "reminder_rules", "reminders",
}

# Snapshot of the schema-2a SCHEMA string (pre-2b), used to build a
# 2a-shaped DB so the migration path in init_db can be exercised against
# a real "old" database rather than one that was already created 2b-shaped.
LEGACY_2A_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'person' CHECK (kind IN ('person','group')),
  slug TEXT UNIQUE,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people_aliases (
  alias TEXT PRIMARY KEY COLLATE NOCASE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS group_members (
  group_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, person_id));
CREATE TABLE IF NOT EXISTS places (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  address TEXT NOT NULL DEFAULT '',
  lat REAL, lon REAL,
  source TEXT NOT NULL DEFAULT 'manual',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_aliases (
  alias TEXT PRIMARY KEY COLLATE NOCASE,
  place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_utc TEXT,
  place_id INTEGER REFERENCES places(id),
  transport TEXT NOT NULL DEFAULT 'unknown'
    CHECK (transport IN ('car','walk','public','unknown')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','cancelled','done')),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);
CREATE TABLE IF NOT EXISTS event_participants (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  PRIMARY KEY (event_id, person_id));
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'agent',
  payload TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts_utc);
"""


def _make_2a_db(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(LEGACY_2A_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','2a')")
    conn.commit()
    return conn


# Snapshot of the schema-2b `reminders` table (pre-2c, no `kind` column),
# used to build a 2b-shaped DB so the 2b->2c column migration in init_db
# can be exercised against a real "old" database rather than one that was
# already created 2c-shaped. Every other 2b table/column is unaffected by
# this migration, so only `reminders` needs to be re-declared here -- the
# rest is created via the live SCHEMA/`_ensure_column` path, same as any
# fresh database.
LEGACY_2B_REMINDERS = """
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  rule_id INTEGER,
  stage_idx INTEGER,
  label TEXT NOT NULL DEFAULT '',
  anchor TEXT NOT NULL DEFAULT 'start',
  fire_at_utc TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','acked','cancelled')),
  persistent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  error_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders(status, fire_at_utc);
"""


def _make_2b_db(path):
    from fam import db as famdb
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Build the rest of the 2b schema via the real (current) SCHEMA/
    # migrations, then swap in the pre-2c `reminders` shape so only the
    # `kind` column migration is actually being exercised.
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE reminders")
    conn.executescript(LEGACY_2B_REMINDERS)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','2b')")
    conn.execute("UPDATE meta SET value='2b' WHERE key='schema_version'")
    conn.commit()
    return conn


@pytest.fixture()
def legacy_2b_conn(tmp_path):
    conn = _make_2b_db(tmp_path / "legacy_2b.db")
    yield conn
    conn.close()


@pytest.fixture()
def fresh_conn(db):
    return db


def test_init_creates_all_tables(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows if not r["name"].startswith("sqlite_")}
    assert EXPECTED_TABLES <= names

def test_init_is_idempotent(db):
    from fam import db as famdb
    famdb.init_db(db)  # second run must not raise

def test_wal_and_fk_enabled(db):
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

def test_resolve_db_path_fam_db_parent_missing(monkeypatch):
    monkeypatch.setenv("FAM_DB", "/nonexistent/x/assistant.db")
    from fam import db as famdb
    with pytest.raises(SystemExit):
        famdb.resolve_db_path()

def test_harden_perms(tmp_path):
    import os
    from fam import db as famdb
    d = tmp_path / "amina"; d.mkdir()
    f = d / "assistant.db"; f.write_bytes(b"")
    os.chmod(f, 0o644); os.chmod(d, 0o775)
    famdb.harden_perms(str(f))
    assert (f.stat().st_mode & 0o777) == 0o600
    assert (d.stat().st_mode & 0o777) == 0o700

def test_harden_perms_missing_file_never_raises(tmp_path):
    from fam import db as famdb
    famdb.harden_perms(str(tmp_path / "nope.db"))  # no exception


# ---- schema 2b migration ----

# Renamed from test_fresh_db_is_2b: the name had gone stale since the 2c
# migration (final-review 2c minor) and would have gone stale again at 3a;
# name it after what it actually checks instead of a hardcoded version.
def test_fresh_db_schema_version_current(db):
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

def test_fresh_db_has_med_intakes_deferred_until_utc_column(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(med_intakes)")}
    assert "deferred_until_utc" in cols

def test_init_db_is_idempotent_with_deferred_until_utc_column(db):
    from fam import db as famdb
    famdb.init_db(db)  # re-run must not raise (column already present)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(med_intakes)")}
    assert "deferred_until_utc" in cols
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

def test_migration_from_2a_adds_tables_and_columns(tmp_path):
    from fam import db as famdb
    conn = _make_2a_db(tmp_path / "legacy.db")

    # sanity: pre-migration 2a db lacks the 2b additions
    place_cols = {r["name"] for r in conn.execute("PRAGMA table_info(places)")}
    assert "travel_min" not in place_cols
    tables_before = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "reminders" not in tables_before

    famdb.init_db(conn)  # migrate

    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"reminder_rules", "reminders"} <= tables

    place_cols = {r["name"] for r in conn.execute("PRAGMA table_info(places)")}
    assert "travel_min" in place_cols
    event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "travel_min" in event_cols
    assert "travel_min_road" in event_cols
    assert "road_checked_at" in event_cols

    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_reminders_fire" in idx

    # re-run is harmless (idempotent migration)
    famdb.init_db(conn)
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
    conn.close()

def test_places_travel_min_default_zero(db):
    db.execute(
        "INSERT INTO places(name, created_at) VALUES ('Home', '2026-01-01T00:00:00Z')")
    row = db.execute(
        "SELECT travel_min FROM places WHERE name='Home'").fetchone()
    assert row["travel_min"] == 0

def test_events_travel_min_nullable(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    row = db.execute("SELECT travel_min FROM events WHERE title='E'").fetchone()
    assert row["travel_min"] is None

def test_reminders_status_check_rejects_bogus(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    eid = db.execute("SELECT id FROM events").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO reminders(event_id, fire_at_utc, status, created_at) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'bogus', '2026-01-01T00:00:00Z')",
            (eid,))

def test_reminders_status_accepts_valid_values(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    eid = db.execute("SELECT id FROM events").fetchone()["id"]
    for status in ("pending", "sent", "acked", "cancelled"):
        db.execute(
            "INSERT INTO reminders(event_id, fire_at_utc, status, created_at) "
            "VALUES (?, '2026-01-01T00:00:00Z', ?, '2026-01-01T00:00:00Z')",
            (eid, status))
    db.commit()
    count = db.execute("SELECT COUNT(*) c FROM reminders").fetchone()["c"]
    assert count == 4

def test_reminders_event_fk_cascade(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    eid = db.execute("SELECT id FROM events").fetchone()["id"]
    db.execute(
        "INSERT INTO reminders(event_id, fire_at_utc, status, created_at) "
        "VALUES (?, '2026-01-01T00:00:00Z', 'pending', '2026-01-01T00:00:00Z')",
        (eid,))
    db.commit()
    db.execute("DELETE FROM events WHERE id=?", (eid,))
    db.commit()
    count = db.execute("SELECT COUNT(*) c FROM reminders").fetchone()["c"]
    assert count == 0

def test_reminder_rules_table_shape(db):
    db.execute(
        "INSERT INTO reminder_rules(scope, stages, enabled, created_at) "
        "VALUES ('default', '[]', 1, '2026-01-01T00:00:00Z')")
    row = db.execute(
        "SELECT scope, stages, enabled FROM reminder_rules").fetchone()
    assert row["scope"] == "default"
    assert row["stages"] == "[]"
    assert row["enabled"] == 1

def test_idx_reminders_fire_exists(db):
    idx = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_reminders_fire" in idx


# ---- schema 2c migration: reminders.kind ----

def test_reminders_kind_column_exists(fresh_conn):
    cols = {r["name"] for r in fresh_conn.execute("PRAGMA table_info(reminders)")}
    assert "kind" in cols

def test_legacy_2b_db_gets_kind_column(legacy_2b_conn):
    from fam import db as famdb
    cols_before = {r["name"] for r in
                   legacy_2b_conn.execute("PRAGMA table_info(reminders)")}
    assert "kind" not in cols_before

    famdb.init_db(legacy_2b_conn)

    cols = {r["name"] for r in
            legacy_2b_conn.execute("PRAGMA table_info(reminders)")}
    assert "kind" in cols
    assert legacy_2b_conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

# ---- schema 3a migration: events.travel_min_road, events.road_checked_at ----

def test_fresh_db_has_travel_min_road_columns(fresh_conn):
    cols = {r["name"] for r in fresh_conn.execute("PRAGMA table_info(events)")}
    assert "travel_min_road" in cols
    assert "road_checked_at" in cols

def test_legacy_2c_db_gets_travel_min_road_columns(tmp_path):
    from fam import db as famdb
    conn = sqlite3.connect(str(tmp_path / "legacy_2c.db"))
    conn.row_factory = sqlite3.Row
    # A 2c-shaped db is just the current SCHEMA (which already includes
    # the 3a columns) minus those two columns -- drop/recreate events
    # without them to get a genuine pre-3a shape.
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE events")
    conn.executescript("""
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_utc TEXT,
  place_id INTEGER REFERENCES places(id),
  transport TEXT NOT NULL DEFAULT 'unknown'
    CHECK (transport IN ('car','walk','public','unknown')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','cancelled','done')),
  notes TEXT NOT NULL DEFAULT '',
  travel_min INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);
""")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','2c')")
    conn.execute("UPDATE meta SET value='2c' WHERE key='schema_version'")
    conn.commit()

    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "travel_min_road" not in cols_before
    assert "road_checked_at" not in cols_before

    famdb.init_db(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "travel_min_road" in cols
    assert "road_checked_at" in cols
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
    conn.close()

def test_events_travel_min_road_nullable(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    row = db.execute(
        "SELECT travel_min_road, road_checked_at FROM events WHERE title='E'").fetchone()
    assert row["travel_min_road"] is None
    assert row["road_checked_at"] is None


# ---- schema 5 review round 1: med_intakes unique index ----
# Phase 5 Task 3 review finding: tick.meds_gen's idempotency (fam/tick.py)
# only ever relied on a SELECT-then-INSERT app-level check, a TOCTOU race
# with nothing enforcing it at the DB layer. med_intakes is a medical
# intake log, so a duplicate (med_id, plan_ts_utc) row is a real-world
# incorrectness (the same scheduled dose double-counted), not just noise
# -- hence a UNIQUE index rather than leaving it to application logic
# alone. Added via CREATE UNIQUE INDEX IF NOT EXISTS (db.py's SCHEMA), no
# schema_version bump needed since it applies cleanly to the empty
# med_intakes table on any existing install (see db.py comment at the
# schema-5 migration block).

def _insert_med(conn):
    cur = conn.execute(
        "INSERT INTO meds(name, times, created_at, updated_at) "
        "VALUES ('Магний', '[\"08:00\"]', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
    return cur.lastrowid


def test_idx_med_intakes_med_plan_exists(db):
    idx = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_med_intakes_med_plan" in idx


def test_med_intakes_duplicate_med_id_plan_ts_utc_rejected(db):
    med_id = _insert_med(db)
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?)",
        (med_id, "2026-01-01T03:00:00+00:00", "pending",
         "2026-01-01T03:00:00+00:00", "2026-01-01T03:00:00+00:00"))
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
            "series_next_utc, created_at) VALUES (?,?,?,?,?)",
            (med_id, "2026-01-01T03:00:00+00:00", "pending",
             "2026-01-01T03:00:00+00:00", "2026-01-01T03:00:00+00:00"))


def test_med_intakes_same_plan_ts_utc_different_med_allowed(db):
    # The unique index is on the (med_id, plan_ts_utc) PAIR, not
    # plan_ts_utc alone -- two different meds legitimately scheduled for
    # the same instant must not collide.
    med_a = _insert_med(db)
    med_b = _insert_med(db)
    for med_id in (med_a, med_b):
        db.execute(
            "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
            "series_next_utc, created_at) VALUES (?,?,?,?,?)",
            (med_id, "2026-01-01T03:00:00+00:00", "pending",
             "2026-01-01T03:00:00+00:00", "2026-01-01T03:00:00+00:00"))
    db.commit()
    count = db.execute("SELECT COUNT(*) c FROM med_intakes").fetchone()["c"]
    assert count == 2


# ---- schema 8 migration: prep & social graph columns ----

def test_schema_v8_columns(db):
    cols = lambda t: {r[1] for r in db.execute(f"PRAGMA table_info({t})")}
    assert {"prep_for_event_id", "prep_when"} <= cols("plans")
    assert {"prep_min", "prep_asked"} <= cols("events")
    assert "prep_min" in cols("event_series")
    assert "home_place_id" in cols("people")
    ver = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0]
    assert int(ver) == 12

def test_schema_v8_migrates_from_v7(tmp_path):
    from fam import db as famdb
    conn = sqlite3.connect(str(tmp_path / "legacy_7.db"))
    conn.row_factory = sqlite3.Row
    # A v7-shaped db is the current SCHEMA (which already includes the v8
    # columns) minus those columns -- drop/recreate the affected tables
    # without them to get a genuine pre-v8 shape, matching the pattern
    # used for the 2c->3a migration test above.
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE plans")
    conn.execute("DROP TABLE events")
    conn.execute("DROP TABLE event_series")
    conn.execute("DROP TABLE people")
    conn.executescript("""
CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'person' CHECK (kind IN ('person','group')),
  slug TEXT UNIQUE,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_utc TEXT NOT NULL,
  end_utc TEXT,
  place_id INTEGER REFERENCES places(id),
  transport TEXT NOT NULL DEFAULT 'unknown'
    CHECK (transport IN ('car','walk','public','unknown')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','cancelled','done')),
  notes TEXT NOT NULL DEFAULT '',
  travel_min INTEGER,
  travel_min_road INTEGER,
  road_checked_at TEXT,
  series_id INTEGER REFERENCES event_series(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);
CREATE TABLE event_series (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  place_id INTEGER REFERENCES places(id),
  weekdays TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT,
  transport TEXT NOT NULL DEFAULT 'unknown'
    CHECK (transport IN ('car','walk','public','unknown')),
  notes TEXT NOT NULL DEFAULT '',
  until_local TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','cancelled')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE plans (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  place_id INTEGER NULL REFERENCES places(id),
  person_id INTEGER NULL REFERENCES people(id),
  deadline TEXT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','dropped')),
  attached_event_id INTEGER NULL REFERENCES events(id),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  done_at TEXT NULL);
""")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','7')")
    conn.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    # existing rows that must survive the migration untouched
    conn.execute(
        "INSERT INTO events(id,title,start_utc,created_at,updated_at) "
        "VALUES (1,'старое событие','2026-07-01T00:00:00Z',"
        "'2026-07-01T00:00:00Z','2026-07-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO plans(id,title,status,created_at) "
        "VALUES (1,'старый план','open','2026-07-01T00:00:00Z')")
    conn.commit()

    cols = lambda t: {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
    assert "prep_for_event_id" not in cols("plans")
    assert "prep_min" not in cols("events")
    assert "prep_min" not in cols("event_series")
    assert "home_place_id" not in cols("people")

    famdb.init_db(conn)  # migrate

    assert {"prep_for_event_id", "prep_when"} <= cols("plans")
    assert {"prep_min", "prep_asked"} <= cols("events")
    assert "prep_min" in cols("event_series")
    assert "home_place_id" in cols("people")
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

    # pre-existing rows survived the ALTER TABLE ADD COLUMN migration
    ev = conn.execute("SELECT title FROM events WHERE id=1").fetchone()
    assert ev["title"] == "старое событие"
    pl = conn.execute("SELECT title FROM plans WHERE id=1").fetchone()
    assert pl["title"] == "старый план"

    # re-run is harmless (idempotent migration)
    famdb.init_db(conn)
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
    conn.close()


# ---- schema 9 migration: goals (quarter/month, no time/place lifecycle) ----

def test_schema_v9_goals_table(db):
    cols = {r["name"]: r for r in db.execute("PRAGMA table_info(goals)")}
    assert {"id", "title", "period_type", "period", "status",
            "parent_goal_id", "notes", "created_at",
            "closed_at"} <= cols.keys()
    assert cols["title"]["notnull"] == 1
    assert cols["period_type"]["notnull"] == 1
    assert cols["period"]["notnull"] == 1
    assert cols["status"]["notnull"] == 1
    assert cols["status"]["dflt_value"] == "'open'"
    assert cols["created_at"]["notnull"] == 1
    assert cols["closed_at"]["notnull"] == 0

    idx = {r["name"] for r in db.execute("PRAGMA index_list(goals)")}
    assert "idx_goals_period" in idx

    # CHECK (period_type IN ('quarter','month'))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO goals(title,period_type,period,created_at) "
            "VALUES ('bad','year','2026',?)", ("2026-07-20T00:00:00Z",))
    # CHECK (status IN ('open','done','declined'))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO goals(title,period_type,period,status,created_at) "
            "VALUES ('bad','month','2026-08','archived',?)",
            ("2026-07-20T00:00:00Z",))

    db.execute(
        "INSERT INTO goals(title,period_type,period,created_at) "
        "VALUES ('quarterly goal','quarter','2026-Q3',?)",
        ("2026-07-20T00:00:00Z",))
    db.commit()
    row = db.execute("SELECT * FROM goals WHERE title='quarterly goal'").fetchone()
    assert row["status"] == "open"
    assert row["notes"] == ""
    assert row["closed_at"] is None

    ver = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0]
    assert int(ver) == 12

def test_schema_v9_migrates_from_v8(tmp_path):
    from fam import db as famdb
    conn = sqlite3.connect(str(tmp_path / "legacy_8.db"))
    conn.row_factory = sqlite3.Row
    # A v8-shaped db is the current SCHEMA (which already includes the
    # goals table) minus that whole table -- goals is a brand new table
    # in v9, same class of migration as `plans` in 3b / `meds` in 5.
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE goals")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','8')")
    conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    # existing data that must survive the migration untouched
    conn.execute(
        "INSERT INTO events(id,title,start_utc,created_at,updated_at) "
        "VALUES (1,'старое событие','2026-07-01T00:00:00Z',"
        "'2026-07-01T00:00:00Z','2026-07-01T00:00:00Z')")
    conn.commit()

    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "goals" not in tables

    famdb.init_db(conn)  # migrate

    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "goals" in tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(goals)")}
    assert {"period_type", "period", "status", "parent_goal_id",
            "closed_at"} <= cols
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

    # pre-existing data survived untouched
    ev = conn.execute("SELECT title FROM events WHERE id=1").fetchone()
    assert ev["title"] == "старое событие"

    # re-run is harmless (idempotent migration)
    famdb.init_db(conn)
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"


# ---- schema 12 migration: external calendar (owner, external_*, ext_exports) ----

def test_schema_v12_columns_and_table(db):
    cols_events = {r["name"] for r in db.execute("PRAGMA table_info(events)")}
    assert {"owner", "external_uid", "external_href", "external_etag",
            "external_seq", "external_location"} <= cols_events
    cols_plans = {r["name"] for r in db.execute("PRAGMA table_info(plans)")}
    assert {"owner", "external_uid", "external_href", "external_etag",
            "external_location"} <= cols_plans
    # controller decision #1: external_seq lives on events only
    assert "external_seq" not in cols_plans
    # Task 5 fix-round 4: `external_location` (BOTH tables) holds the raw
    # free-text iCloud LOCATION of an owner='iphone' row. It exists so the
    # sync never has to hide machine data in the human-owned `notes`
    # column, which `fam cal update --notes` replaces wholesale.

    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ext_exports" in tables
    exports_cols = {r["name"] for r in db.execute("PRAGMA table_info(ext_exports)")}
    assert {"event_id", "href", "etag", "body_hash", "synced_at"} <= exports_cols

    idx = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_events_external_uid" in idx
    # Task 5 fix-round finding I3: plans.external_uid gets the same
    # partial UNIQUE events.external_uid already had -- added to this
    # same v12 migration (controller-authorized: prod was still on v11
    # when this was added, so there is no already-migrated database this
    # could retroactively conflict with).
    assert "idx_plans_external_uid" in idx

    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"


def test_schema_v12_migrates_from_v11(tmp_path):
    from fam import db as famdb
    conn = sqlite3.connect(str(tmp_path / "legacy_11.db"))
    conn.row_factory = sqlite3.Row
    # A v11-shaped db is the current SCHEMA minus `ext_exports` -- that
    # table is brand new in v12 (same class of migration as `goals` in
    # v9 / `sent_messages` in v10). events/plans never carried
    # owner/external_* in SCHEMA's CREATE TABLE text to begin with (same
    # convention as prep_min/home_place_id in v8, deferred_until_utc in
    # v11 above: additive columns on *existing* tables only ever reach
    # them through _ensure_column, the historical CREATE TABLE strings
    # are never edited), so executing the current SCHEMA already yields
    # a pre-v12-shaped events/plans -- only ext_exports needs dropping.
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE ext_exports")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','11')")
    conn.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
    # existing rows that must survive the migration and get owner='hermes'
    conn.execute(
        "INSERT INTO events(id,title,start_utc,created_at,updated_at) "
        "VALUES (1,'старое событие','2026-07-01T00:00:00Z',"
        "'2026-07-01T00:00:00Z','2026-07-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO plans(id,title,status,created_at) "
        "VALUES (1,'старый план','open','2026-07-01T00:00:00Z')")
    conn.commit()

    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "owner" not in cols_before
    tables_before = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ext_exports" not in tables_before

    famdb.init_db(conn)  # migrate

    # every pre-existing row got owner='hermes' (controller decision #3)
    ev = conn.execute(
        "SELECT owner, title, external_location FROM events WHERE id=1").fetchone()
    assert ev["owner"] == "hermes"
    assert ev["title"] == "старое событие"
    pl = conn.execute(
        "SELECT owner, title, external_location FROM plans WHERE id=1").fetchone()
    assert pl["owner"] == "hermes"
    assert pl["title"] == "старый план"
    # fix-round 4: the new external_location column backfills to NULL on
    # every pre-existing row -- a locally-created (hermes) row has no
    # external location by definition, and nothing reads it for one.
    assert ev["external_location"] is None
    assert pl["external_location"] is None

    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ext_exports" in tables

    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"

    # re-run is harmless (idempotent migration)
    famdb.init_db(conn)
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
    conn.close()


def test_events_owner_defaults_to_hermes(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    row = db.execute("SELECT owner FROM events WHERE title='E'").fetchone()
    assert row["owner"] == "hermes"


def test_plans_owner_defaults_to_hermes(db):
    db.execute(
        "INSERT INTO plans(title, status, created_at) "
        "VALUES ('P','open','2026-01-01T00:00:00Z')")
    row = db.execute("SELECT owner FROM plans WHERE title='P'").fetchone()
    assert row["owner"] == "hermes"


def test_events_owner_check_rejects_bogus(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO events(title, start_utc, created_at, updated_at, owner) "
            "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',"
            "'2026-01-01T00:00:00Z','bogus')")


def test_plans_owner_check_rejects_bogus(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO plans(title, status, created_at, owner) "
            "VALUES ('P','open','2026-01-01T00:00:00Z','bogus')")


def test_events_owner_accepts_iphone(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at, owner) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',"
        "'2026-01-01T00:00:00Z','iphone')")
    row = db.execute("SELECT owner FROM events WHERE title='E'").fetchone()
    assert row["owner"] == "iphone"


def test_events_external_uid_partial_unique_allows_multiple_null(db):
    # partial index (WHERE external_uid IS NOT NULL) must not treat the
    # many locally-created events (external_uid left NULL) as duplicates
    # of one another -- SQLite indexes simply skip NULL rows.
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E1','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E2','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    db.commit()  # both rows have external_uid IS NULL
    count = db.execute(
        "SELECT COUNT(*) c FROM events WHERE external_uid IS NULL").fetchone()["c"]
    assert count == 2


def test_events_external_uid_unique_rejects_duplicate(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at, external_uid) "
        "VALUES ('E1','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',"
        "'2026-01-01T00:00:00Z','uid-1')")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO events(title, start_utc, created_at, updated_at, external_uid) "
            "VALUES ('E2','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',"
            "'2026-01-01T00:00:00Z','uid-1')")


def test_plans_external_uid_partial_unique_allows_multiple_null(db):
    # Same partial-index rationale as the events test above, mirrored for
    # plans (Task 5 fix-round finding I3).
    db.execute("INSERT INTO plans(title, created_at) VALUES ('P1','2026-01-01T00:00:00Z')")
    db.execute("INSERT INTO plans(title, created_at) VALUES ('P2','2026-01-01T00:00:00Z')")
    db.commit()  # both rows have external_uid IS NULL
    count = db.execute(
        "SELECT COUNT(*) c FROM plans WHERE external_uid IS NULL").fetchone()["c"]
    assert count == 2


def test_plans_external_uid_unique_rejects_duplicate(db):
    db.execute(
        "INSERT INTO plans(title, created_at, external_uid) "
        "VALUES ('P1','2026-01-01T00:00:00Z','uid-1')")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO plans(title, created_at, external_uid) "
            "VALUES ('P2','2026-01-01T00:00:00Z','uid-1')")


def test_ext_exports_table_shape_and_cascade(db):
    db.execute(
        "INSERT INTO events(title, start_utc, created_at, updated_at) "
        "VALUES ('E','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    eid = db.execute("SELECT id FROM events").fetchone()["id"]
    db.execute(
        "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?, 'https://example/hermes.ics', '\"etag1\"', 'hash1', "
        "'2026-01-01T00:00:00Z')", (eid,))
    db.commit()
    row = db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (eid,)).fetchone()
    assert row["href"] == "https://example/hermes.ics"
    assert row["body_hash"] == "hash1"

    # ON DELETE CASCADE: deleting the event drops its export row too
    db.execute("DELETE FROM events WHERE id=?", (eid,))
    db.commit()
    count = db.execute("SELECT COUNT(*) c FROM ext_exports").fetchone()["c"]
    assert count == 0


def test_resolve_receipt_migration_adds_table_to_existing_db(tmp_path):
    from fam import db as famdb

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE resolve_receipts")
    conn.execute("DROP INDEX idx_audit_resolve_key")
    conn.commit()

    famdb.migrate_resolve_receipts(conn)

    tables = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    indexes = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "resolve_receipts" in tables
    assert "idx_audit_resolve_key" in indexes
    conn.close()
