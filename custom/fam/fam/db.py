"""SQLite core for fam: connection, pragmas, schema."""
import os, sqlite3

HOST_DB = "/home/denis/.hermes/private/amina/assistant.db"
SANDBOX_DB = "/root/.hermes/private/amina/assistant.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'person' CHECK (kind IN ('person','group')),
  slug TEXT UNIQUE,                       -- 'amina','denis','taya' for rule hooks
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
  source TEXT NOT NULL DEFAULT 'manual',  -- manual|2gis (Phase 3)
  notes TEXT NOT NULL DEFAULT '',
  travel_min INTEGER NOT NULL DEFAULT 0,  -- manual leave_at minutes (2b); 2GIS in Phase 3
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_aliases (
  alias TEXT PRIMARY KEY COLLATE NOCASE,
  place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_utc TEXT NOT NULL,                -- ISO-8601 UTC
  end_utc TEXT,                           -- nullable
  place_id INTEGER REFERENCES places(id),
  transport TEXT NOT NULL DEFAULT 'unknown'
    CHECK (transport IN ('car','walk','public','unknown')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','cancelled','done')),
  notes TEXT NOT NULL DEFAULT '',
  travel_min INTEGER,                     -- NULL = take from place; override (2b)
  travel_min_road INTEGER,                -- computed road minutes with traffic; beats manual (3a)
  road_checked_at TEXT,                   -- UTC ISO of last road computation (3a)
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
  kind TEXT NOT NULL,                     -- e.g. cal.add, people.add, tick.reminders
  actor TEXT NOT NULL DEFAULT 'agent',    -- agent|tick|admin
  payload TEXT NOT NULL DEFAULT '{}');    -- JSON
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts_utc);
CREATE TABLE IF NOT EXISTS reminder_rules (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL,                    -- 'default' | 'slug:<slug>'
  stages TEXT NOT NULL,                   -- JSON [{anchor,offset_min,label}]
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  rule_id INTEGER,
  stage_idx INTEGER,
  label TEXT NOT NULL DEFAULT '',
  anchor TEXT NOT NULL DEFAULT 'start',   -- 'start' | 'leave_at'
  kind TEXT NOT NULL DEFAULT 'leave',     -- 'prepare' | 'leave' (2c, ack по смыслу)
  fire_at_utc TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','acked','cancelled')),
  persistent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  sent_at TEXT);
CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders(status, fire_at_utc);
"""

def resolve_db_path():
    env = os.environ.get("FAM_DB")
    if env:
        if not os.path.isdir(os.path.dirname(env) or "."):
            raise SystemExit(2)
        return env
    for p in (HOST_DB, SANDBOX_DB):
        if os.path.isdir(os.path.dirname(p)):
            return p
    raise SystemExit(2)

def connect(db_path=None):
    path = db_path or resolve_db_path()
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _ensure_column(conn, table, column, add_ddl):
    """Add COLUMN to TABLE if missing. SQLite has no ADD COLUMN IF NOT
    EXISTS, so guard via PRAGMA table_info (idempotent migration)."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {add_ddl}")

def init_db(conn):
    conn.executescript(SCHEMA)
    # schema 2b: migrate pre-2b tables that predate these columns
    # (CREATE TABLE IF NOT EXISTS above only helps fresh databases).
    _ensure_column(conn, "places", "travel_min",
                   "travel_min INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "events", "travel_min", "travel_min INTEGER")
    # pre-live guards (post-review): retry cap on repeated tick delivery
    # errors (fam/tick.py) needs a per-reminder counter.
    _ensure_column(conn, "reminders", "error_count",
                   "error_count INTEGER NOT NULL DEFAULT 0")
    # schema 2c: вид стадии для ack-по-смыслу («собираемся» гасит только
    # prepare; «выходим» — всё). Старые строки получают 'leave' — для уже
    # отправленных/погашенных это неважно, а pending пересоздаст
    # rem.migrate_rules_2c.
    _ensure_column(conn, "reminders", "kind",
                   "kind TEXT NOT NULL DEFAULT 'leave'")
    # schema 3a: computed road minutes (with traffic) beat the user's
    # off-hand manual travel_min figure in leave_at() (product decision,
    # Denis 2026-07-12). Columns are inert until Task 3 starts writing them.
    _ensure_column(conn, "events", "travel_min_road",
                   "travel_min_road INTEGER")
    _ensure_column(conn, "events", "road_checked_at",
                   "road_checked_at TEXT")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','3a')")
    conn.execute(
        "UPDATE meta SET value='3a' WHERE key='schema_version'")
    conn.commit()
