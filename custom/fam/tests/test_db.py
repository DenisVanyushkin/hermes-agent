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


# ---- schema 2b migration ----

def test_fresh_db_is_2b(db):
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "2b"

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
    ).fetchone()["value"] == "2b"

    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"reminder_rules", "reminders"} <= tables

    place_cols = {r["name"] for r in conn.execute("PRAGMA table_info(places)")}
    assert "travel_min" in place_cols
    event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "travel_min" in event_cols

    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_reminders_fire" in idx

    # re-run is harmless (idempotent migration)
    famdb.init_db(conn)
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "2b"
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
