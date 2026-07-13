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
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  place_id INTEGER NULL REFERENCES places(id),
  person_id INTEGER NULL REFERENCES people(id),
  deadline TEXT NULL,                     -- YYYY-MM-DD local
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','dropped')),
  attached_event_id INTEGER NULL REFERENCES events(id),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  done_at TEXT NULL);
CREATE TABLE IF NOT EXISTS meds (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  dose TEXT NOT NULL DEFAULT '',
  times TEXT NOT NULL,                    -- JSON list of "HH:MM" local
  remaining INTEGER,
  threshold INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS med_intakes (
  id INTEGER PRIMARY KEY,
  med_id INTEGER NOT NULL REFERENCES meds(id) ON DELETE CASCADE,
  plan_ts_utc TEXT NOT NULL,
  taken_ts_utc TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','taken','skipped','missed')),
  series_next_utc TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_med_intakes_status_plan ON med_intakes(status, plan_ts_utc);
CREATE INDEX IF NOT EXISTS idx_med_intakes_status_next ON med_intakes(status, series_next_utc);
CREATE TABLE IF NOT EXISTS shopping (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  qty TEXT NOT NULL DEFAULT '',
  added_by TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','meds')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
  created_at TEXT NOT NULL,
  done_at TEXT);
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
    # schema 3b: `plans` table (dela-without-time) is created fresh by
    # CREATE TABLE IF NOT EXISTS above for both new and pre-3b databases
    # -- no _ensure_column migration needed (it's a whole new table, not
    # a new column on an existing one; see db.py:117's docstring for when
    # that pattern is needed instead).
    # schema 5: `meds`/`med_intakes`/`shopping` (phase 5, meds+shopping)
    # are whole new tables, same as `plans` in 3b above -- CREATE TABLE
    # IF NOT EXISTS covers both fresh and pre-5 databases, no
    # _ensure_column migration needed.
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','5')")
    conn.execute(
        "UPDATE meta SET value='5' WHERE key='schema_version'")
    conn.commit()
