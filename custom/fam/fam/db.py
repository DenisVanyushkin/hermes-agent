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
CREATE INDEX IF NOT EXISTS idx_audit_resolve_key
  ON audit_log(kind, CASE WHEN json_valid(payload)
                          THEN json_extract(payload, '$.idempotency_key') END);
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
CREATE TABLE IF NOT EXISTS location_hints (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('manual','shared')),
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  label TEXT NOT NULL DEFAULT '',        -- human-readable origin for reminders
  ts_utc TEXT NOT NULL,                  -- when the hint was recorded
  expires_utc TEXT NOT NULL);            -- TTL; whereami ignores expired rows
CREATE INDEX IF NOT EXISTS idx_location_hints_expires
  ON location_hints(expires_utc);
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
CREATE TABLE IF NOT EXISTS sent_messages (
  id INTEGER PRIMARY KEY,
  wa_message_id TEXT NOT NULL UNIQUE,   -- bridge /send's key.id (last chunk)
  chat_jid TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('reminder','med')),
  ref_id INTEGER NOT NULL,              -- reminders.id | med_intakes.id
  event_id INTEGER,                     -- reminder chains: ack scope key
  ack_status TEXT NOT NULL DEFAULT 'none'
    CHECK (ack_status IN ('none','confirmed','skipped')),
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sent_messages_kind_ref ON sent_messages(kind, ref_id);
CREATE TABLE IF NOT EXISTS sent_message_refs (
  id INTEGER PRIMARY KEY,
  sent_message_id INTEGER NOT NULL
    REFERENCES sent_messages(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('reminder','med')),
  ref_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sent_message_refs_msg
  ON sent_message_refs(sent_message_id);
CREATE INDEX IF NOT EXISTS idx_sent_message_refs_ref
  ON sent_message_refs(kind, ref_id);
CREATE TABLE IF NOT EXISTS ext_exports (
  event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  href TEXT,
  etag TEXT,
  body_hash TEXT,
  synced_at TEXT);
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
    # -- v10 (reaction acks): `sent_messages` is a whole new table --
    # CREATE TABLE IF NOT EXISTS above covers fresh installs and pre-v10
    # databases alike, no _ensure_column migration needed (same pattern
    # as `goals` in v9).
    # -- v11 (meds-defer T2.5): med_intakes.deferred_until_utc marks a
    # dose whose reminder was explicitly deferred (fam med defer), so
    # acks.build can show the deferred-to time instead of the original
    # plan_ts_utc -- series_next_utc alone can't tell a deferral apart
    # from an ordinary +45min nag, both land in the future the same way.
    _ensure_column(conn, "med_intakes", "deferred_until_utc",
                   "deferred_until_utc TEXT")
    # -- v12 (external calendar sync, Task 3): events/plans gain `owner`
    # ('hermes'|'iphone', CHECK-enforced, DEFAULT 'hermes') so the
    # upcoming CalDAV ingest can tell which side created a row without
    # touching anything that already exists -- ALTER TABLE ADD COLUMN
    # with both NOT NULL DEFAULT and CHECK backfills every pre-v12 row to
    # 'hermes' in one statement (verified against this SQLite build,
    # unlike the CHECK-on-ALTER caveat noted for reminders.kind/places.
    # category above). `external_uid`/`external_href`/`external_etag`
    # track the iCloud VEVENT identity for round-tripping; `external_seq`
    # (events only -- plans have no SEQUENCE concept) lets the sync tick
    # detect a stale write. The partial UNIQUE index on
    # events.external_uid enforces one local event per remote UID while
    # leaving every locally-created event (external_uid IS NULL) alone --
    # SQLite partial indexes simply skip NULL rows, so any number of them
    # coexist. `ext_exports` is a whole new table (CREATE TABLE IF NOT
    # EXISTS above covers fresh installs and pre-v12 databases alike, no
    # _ensure_column migration needed -- same pattern as `goals` in v9 /
    # `sent_messages` in v10): one row per Hermes-owned event that has
    # been PUT to the "Гермес" collection, so the export tick can compare
    # body_hash and skip a no-op PUT.
    #
    # `external_location` (Task 5 fix-round 4, controller-authorized into
    # this same still-unmigrated v12 block -- prod is on v11, no version
    # bump needed) holds the RAW free-text iCloud `LOCATION` of an
    # owner='iphone' row: the text exactly as it stands on Amina's phone,
    # whether or not it also happened to match a `places` entry (in which
    # case `place_id` is set too -- the two are independent). It exists
    # because that text has to be stored SOMEWHERE for the sync to diff
    # against on the next tick, and `notes` -- the column three earlier
    # attempts used -- is human-owned: `fam cal update --notes` replaces
    # it wholesale, and an LLM agent driving that command cannot be relied
    # on to reproduce any in-band delimiter a machine hid in it. A column
    # nothing but extcal reads or writes removes that whole class of
    # collision instead of re-encoding it: `notes` stays purely human,
    # `external_location` stays purely machine.
    _ensure_column(conn, "events", "owner",
                   "owner TEXT NOT NULL DEFAULT 'hermes' "
                   "CHECK (owner IN ('hermes','iphone'))")
    _ensure_column(conn, "events", "external_uid", "external_uid TEXT")
    _ensure_column(conn, "events", "external_href", "external_href TEXT")
    _ensure_column(conn, "events", "external_etag", "external_etag TEXT")
    _ensure_column(conn, "events", "external_seq", "external_seq INTEGER")
    _ensure_column(conn, "events", "external_location", "external_location TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_uid "
        "ON events(external_uid) WHERE external_uid IS NOT NULL")
    _ensure_column(conn, "plans", "owner",
                   "owner TEXT NOT NULL DEFAULT 'hermes' "
                   "CHECK (owner IN ('hermes','iphone'))")
    _ensure_column(conn, "plans", "external_uid", "external_uid TEXT")
    _ensure_column(conn, "plans", "external_href", "external_href TEXT")
    _ensure_column(conn, "plans", "external_etag", "external_etag TEXT")
    _ensure_column(conn, "plans", "external_location", "external_location TEXT")
    # Same partial UNIQUE as events.external_uid above, added to this same
    # v12 migration (Task 5 fix-round finding I3, controller-authorized:
    # prod is still on v11 as of this addition, so widening the v12
    # migration itself -- rather than bumping to a new schema version --
    # is safe; there is no already-migrated v12 database anywhere whose
    # plans.external_uid values this index could retroactively conflict
    # with). Without it, a re-applied Changeset (a retried tick after a
    # mid-batch crash, or two overlapping tick runs) could insert a SECOND
    # plans row for the same iCloud occurrence with nothing at the DB
    # level to stop it -- extcal.apply_changes' own SELECT-before-insert
    # check (mirroring tick.py::meds_gen's identical no-index-yet
    # bootstrapping pattern) is the first line of defense; this index is
    # the same TOCTOU backstop idx_events_external_uid already is for the
    # events branch.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_external_uid "
        "ON plans(external_uid) WHERE external_uid IS NOT NULL")
    # Dynamic road origin (fam/whereami.py), widened into this same
    # still-unmigrated v12 block for the third time -- prod is on v11
    # (verified against assistant.db 2026-07-29), so there is no
    # already-migrated v12 database anywhere for these columns to
    # retroactively disagree with.
    #
    # car_metrics.gps_ts/gps_speed/gps_sat: StarLine's `position` dict
    # already carries ts (unix seconds -- when the GPS FIX happened), s
    # (km/h) and sat_qty on every poll, and car.normalize() already
    # persists all three inside raw_json. They get real columns because
    # whereami's "parked vs moving vs stale" decision needs the fix time,
    # and ts_utc is NOT it: ts_utc is when fam polled, and the two were
    # ~7 minutes apart in the live row this was designed against. Reading
    # them out of raw_json per query would work but makes the hot path
    # parse a JSON blob per row; a column is cheaper and indexable.
    #
    # events.road_origin_lat/lon/source: which point produced the cached
    # travel_min_road. Until now the origin was the constant
    # road_home_lat/lon, so road_checked_at alone was a complete cache
    # key -- "when did we compute this" fully determined "is it still
    # good". With a dynamic origin that stops being true: tick.py's
    # `checked_dt >= window_open` guard would happily keep a figure
    # computed from a point Amina has since driven away from. Storing the
    # origin turns an implicit assumption into a checkable one.
    _ensure_column(conn, "car_metrics", "gps_ts", "gps_ts INTEGER")
    _ensure_column(conn, "car_metrics", "gps_speed", "gps_speed REAL")
    _ensure_column(conn, "car_metrics", "gps_sat", "gps_sat INTEGER")
    _ensure_column(conn, "events", "road_origin_lat", "road_origin_lat REAL")
    _ensure_column(conn, "events", "road_origin_lon", "road_origin_lon REAL")
    _ensure_column(conn, "events", "road_origin_source",
                   "road_origin_source TEXT")
    # Med gating (spec 2026-07-29): why a still-pending dose is being
    # held back by tick._meds_series. NULL = not held. Written only on
    # transition into/out of a hold, never on the 10-minute recheck --
    # audit_log already carries 22k+ tick.reminders rows and a
    # per-recheck audit row per dose would swamp it.
    _ensure_column(conn, "med_intakes", "gate_reason", "gate_reason TEXT")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','12')")
    conn.execute(
        "UPDATE meta SET value='12' WHERE key='schema_version'")
    conn.commit()


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))
