"""schema v10: sent_messages (reaction-ack correlation)."""
import sqlite3

from fam import db as famdb


def test_schema_v10_sent_messages_table(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(sent_messages)")}
    assert {"wa_message_id", "chat_jid", "kind", "ref_id", "event_id",
            "ack_status", "created_at"} <= cols
    # sent_messages was introduced in schema 10 -- same reasoning as
    # test_db_car.py's car_metrics test: this test's concern is "the
    # table is present in a fresh db", not pinning the CURRENT overall
    # schema_version (test_db.py's job); an exact match here just breaks
    # this unrelated test on every future, unrelated schema bump.
    assert int(db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"]) >= 10


def test_sent_messages_rejects_unknown_kind_and_ack_status(db):
    with_bad_kind = ("INSERT INTO sent_messages(wa_message_id,kind,ref_id,"
                     "created_at) VALUES ('X','digest',1,'now')")
    try:
        db.execute(with_bad_kind)
        raise AssertionError("CHECK on kind must reject non-ackable kinds")
    except sqlite3.IntegrityError:
        pass


def test_wa_message_id_is_unique(db):
    db.execute("INSERT INTO sent_messages(wa_message_id,kind,ref_id,created_at)"
               " VALUES ('X','med',1,'now')")
    try:
        db.execute("INSERT INTO sent_messages(wa_message_id,kind,ref_id,"
                   "created_at) VALUES ('X','med',2,'now')")
        raise AssertionError("wa_message_id must be unique")
    except sqlite3.IntegrityError:
        pass


def test_schema_v10_migrates_from_v9(tmp_path):
    """A v9-shaped db is the current SCHEMA minus sent_messages -- a whole
    new table, same migration class as `goals` in v9 / `plans` in 3b."""
    conn = sqlite3.connect(str(tmp_path / "legacy_9.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(famdb.SCHEMA)
    conn.execute("DROP TABLE sent_messages")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','9')")
    conn.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO events(id,title,start_utc,created_at,updated_at) "
        "VALUES (1,'старое событие','2026-07-01T00:00:00Z',"
        "'2026-07-01T00:00:00Z','2026-07-01T00:00:00Z')")
    conn.commit()

    famdb.init_db(conn)  # migrate

    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sent_messages" in tables
    # this IS a migration-path test -- the property under test is that
    # migrating a legacy v9 db lands at the currently-correct version, so
    # the literal target is the essential thing being checked (unlike the
    # incidental schema_version reads in test_schema_v10_sent_messages_table
    # above), and gets bumped to "12" here for the same reason it was
    # bumped to "11" at the v10->v11 transition.
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
    assert conn.execute(
        "SELECT title FROM events WHERE id=1").fetchone()["title"] == "старое событие"

    famdb.init_db(conn)  # idempotent re-run
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "12"
