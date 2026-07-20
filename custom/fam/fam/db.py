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
  category TEXT CHECK (category IN ('grocery','pharmacy') OR category IS NULL),
                                           -- "по пути" match target (phase 5 T6)
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
CREATE TABLE IF NOT EXISTS event_series (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  place_id INTEGER REFERENCES places(id),
  weekdays TEXT NOT NULL,                  -- CSV canon: mon,tue,wed,thu,fri,sat,sun
  start_time TEXT NOT NULL,                -- HH:MM local (Asia/Almaty)
  end_time TEXT,                           -- HH:MM local, nullable
  transport TEXT NOT NULL DEFAULT 'unknown'
    CHECK (transport IN ('car','walk','public','unknown')),
  notes TEXT NOT NULL DEFAULT '',
  until_local TEXT,                        -- YYYY-MM-DD, nullable = open-ended
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','cancelled')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_series_participants (
  series_id INTEGER NOT NULL REFERENCES event_series(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  PRIMARY KEY (series_id, person_id));
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
-- 5-T3 review round 1: enforce the one-intake-per-scheduled-dose
-- invariant at the DB level. tick.meds_gen's SELECT-then-INSERT guard
-- (fam/tick.py:meds_gen) already prevents duplicates in the common
-- case, but that check-then-act is a TOCTOU race (two overlapping tick
-- runs could both pass the SELECT before either INSERTs) with no
-- constraint backing it up. med_intakes is a medical intake log where
-- a duplicate row would misrepresent doses actually taken, so the
-- invariant is enforced here rather than left to application logic
-- alone; meds_gen additionally catches the resulting IntegrityError
-- and treats it as "already generated" (see tick.py).
CREATE UNIQUE INDEX IF NOT EXISTS idx_med_intakes_med_plan ON med_intakes(med_id, plan_ts_utc);
CREATE TABLE IF NOT EXISTS shopping (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  qty TEXT NOT NULL DEFAULT '',
  added_by TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','meds')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
  created_at TEXT NOT NULL,
  done_at TEXT);
CREATE TABLE IF NOT EXISTS car_metrics (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  fuel_pct REAL, fuel_liters REAL,
  odometer_km REAL,
  engine_on INTEGER, ignition_on INTEGER,
  cabin_temp_c REAL, coolant_temp_c REAL,
  battery_v REAL, gsm_online INTEGER,
  gps_lat REAL, gps_lon REAL,
  raw_json TEXT);
CREATE INDEX IF NOT EXISTS idx_car_metrics_ts ON car_metrics(ts_utc);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  period_type TEXT NOT NULL CHECK (period_type IN ('quarter','month')),
  period TEXT NOT NULL,                -- '2026-Q3' | '2026-08'
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','declined')),
  parent_goal_id INTEGER REFERENCES goals(id),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  closed_at TEXT);                     -- UTC ISO; done И declined оба «закрывают»
CREATE INDEX IF NOT EXISTS idx_goals_period ON goals(period_type, period, status);
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

def harden_perms(db_path):
    """Best-effort chmod: the DB file itself 600, its parent dir 700.
    The DB holds meds schedules, car GPS and full outbound message
    texts -- nothing on this VM besides the owner should read it.
    Never raises (a read-only FS or foreign owner must not break init)."""
    try:
        os.chmod(db_path, 0o600)
        os.chmod(os.path.dirname(db_path) or ".", 0o700)
    except OSError:
        pass

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
    # schema 5 T6: places.category ('grocery'|'pharmacy'|NULL) drives
    # shopping.match_enroute's "по пути" corridor match -- CREATE TABLE
    # IF NOT EXISTS above covers fresh installs (category is now part of
    # the places definition, with a CHECK constraint), but places
    # already existed pre-T6 so old databases need this migration too.
    # No CHECK on the ALTER itself -- same reasoning as reminders.kind's
    # migration above (SQLite ADD COLUMN CHECK support/compat isn't
    # exercised elsewhere in this codebase); validated at the
    # places.update() layer instead.
    _ensure_column(conn, "places", "category", "category TEXT")
    # schema 6: car_metrics (phase 4) -- whole new table, CREATE TABLE IF
    # NOT EXISTS covers fresh+existing, no _ensure_column needed.
    # schema 7: recurring event series. event_series /
    # event_series_participants are whole new tables (CREATE TABLE IF NOT
    # EXISTS covers fresh+existing). events.series_id links a materialized
    # occurrence back to its series (NULL = one-off, unchanged behavior);
    # the partial UNIQUE index makes generation idempotent per slot.
    _ensure_column(conn, "events", "series_id",
                   "series_id INTEGER REFERENCES event_series(id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_series_start "
        "ON events(series_id, start_utc) WHERE series_id IS NOT NULL")
    # -- v8 (phase 7: prep & social graph) --
    _ensure_column(conn, "plans", "prep_for_event_id",
                   "prep_for_event_id INTEGER REFERENCES events(id)")
    _ensure_column(conn, "plans", "prep_when", "prep_when TEXT")
    _ensure_column(conn, "events", "prep_min", "prep_min INTEGER")
    _ensure_column(conn, "events", "prep_asked",
                   "prep_asked INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "event_series", "prep_min", "prep_min INTEGER")
    _ensure_column(conn, "people", "home_place_id",
                   "home_place_id INTEGER REFERENCES places(id)")
    # -- v9 (goals: quarter/month, no time/place lifecycle) --
    # `goals` is a whole new table -- CREATE TABLE IF NOT EXISTS above
    # covers fresh installs and pre-v9 databases alike, no _ensure_column
    # migration needed (same pattern as `plans` in 3b, `meds`/`shopping`
    # in 5).
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','9')")
    conn.execute(
        "UPDATE meta SET value='9' WHERE key='schema_version'")
    conn.commit()


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))
