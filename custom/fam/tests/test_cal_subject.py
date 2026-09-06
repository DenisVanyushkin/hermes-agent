from datetime import datetime, timezone

import pytest

from fam import audit, cal, cli, extcal, people, rem, series


def _seed(db):
    amina = people.add(db, "Амина", slug="amina")
    taya = people.add(db, "Тая", slug="taya")
    group = people.add(db, "татешки", kind="group")
    people.add_member(db, group["id"], taya["id"])
    db.commit()
    return amina, taya, group


def test_v14_schema_and_subject_roundtrip(db):
    _, taya, _ = _seed(db)
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "15"
    assert "subject_person_id" in {r["name"] for r in db.execute("PRAGMA table_info(events)")}
    assert "subject_person_id" in {r["name"] for r in db.execute("PRAGMA table_info(event_series)")}
    event = cal.add(db, "Математика", "2030-01-08T10:00:00+00:00", subject_person_id=taya["id"])
    db.commit()
    assert cal.get(db, event["id"])["subject"] == {"id": taya["id"], "name": "Тая", "slug": "taya"}
    rows = audit.query(db, None, "cal.add", None, 10)
    assert rows[0]["payload"]["subject"]["slug"] == "taya"
    cal.update(db, event["id"], subject_person_id=None)
    db.commit()
    rows = audit.query(db, None, "cal.update", None, 10)
    assert rows[0]["payload"]["subject"] is None


def test_group_subject_rejected_before_event_or_series_insert(db):
    _, _, group = _seed(db)
    with pytest.raises(ValueError, match="group"):
        cal.add(db, "Группа", "2030-01-08T10:00:00+00:00", subject_person_id=group["id"])
    with pytest.raises(ValueError, match="group"):
        series.add(db, "Группа", "tue", "10:00", subject_person_id=group["id"])
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM event_series").fetchone()[0] == 0


def test_subject_update_clear_and_tombstone_inherit(db):
    _, taya, _ = _seed(db)
    s = series.add(db, "Тренировка", "tue", "10:00", until_local="2030-02-01", subject_person_id=taya["id"])
    series.generate(db, now_utc="2030-01-01T00:00:00+00:00")
    event_id = db.execute("SELECT id FROM events WHERE series_id=? ORDER BY start_utc LIMIT 1", (s["id"],)).fetchone()[0]
    old = db.execute("SELECT start_utc FROM events WHERE id=?", (event_id,)).fetchone()[0]
    cal.update(db, event_id, start_utc="2030-01-09T10:00:00+00:00")
    tomb = db.execute("SELECT subject_person_id FROM events WHERE series_id=? AND status='cancelled' AND start_utc=?", (s["id"], old)).fetchone()
    assert tomb[0] == taya["id"]
    cal.update(db, event_id, subject_person_id=None)
    assert db.execute("SELECT subject_person_id FROM events WHERE id=?", (event_id,)).fetchone()[0] is None


def test_series_subject_propagates_only_old_subject_grid_rows(db):
    amina, taya, _ = _seed(db)
    s = series.add(db, "Тренировка", "tue", "10:00", until_local="2030-02-01", subject_person_id=amina["id"])
    series.generate(db, now_utc="2030-01-01T00:00:00+00:00")
    ids = [r["id"] for r in db.execute("SELECT id FROM events WHERE series_id=? ORDER BY start_utc", (s["id"],))]
    cal.update(db, ids[0], subject_person_id=taya["id"])
    result = series.update_participants(db, s["id"], subject_person_id=taya["id"], now_utc="2030-01-01T00:00:00+00:00")
    assert ids[0] not in result["updated_events"]
    assert db.execute("SELECT subject_person_id FROM event_series WHERE id=?", (s["id"],)).fetchone()[0] == taya["id"]
    values = [r[0] for r in db.execute("SELECT subject_person_id FROM events WHERE series_id=?", (s["id"],))]
    assert all(value == taya["id"] for value in values)


def test_subject_views_and_global_export_plan_share_stored_filter(db):
    _, taya, _ = _seed(db)
    event = cal.add(db, "Тая", "2030-01-08T10:00:00+00:00", subject_person_id=taya["id"])
    cal.add(db, "Общее", "2030-01-08T11:00:00+00:00")
    db.commit()
    viewed = {
        e["id"] for e in cal.list_range(
            db, "2030-01-08T00:00:00+00:00", "2030-01-09T00:00:00+00:00",
            subject_person_id=taya["id"])
    }
    cfg = {
        "extcal_write_calendar": "https://caldav.icloud.com/hermes/",
        "extcal_taya_calendar": "https://caldav.icloud.com/taya/",
        "extcal_horizon_weeks": 8,
    }
    route_ids = {
        item["event_id"] for item in extcal._export_route_plan(
            db, cfg, datetime(2030, 1, 1, tzinfo=timezone.utc))
        if item.get("target") == "taya"
    }
    assert viewed == {event["id"]}
    assert route_ids == viewed



def test_reminders_use_participants_not_subject(db):
    amina, taya, _ = _seed(db)
    rem.seed_default_rules(db)
    event = cal.add(db, "Тая", "2030-01-08T10:00:00+00:00", subject_person_id=taya["id"])
    assert not any(r["scope"] == "slug:taya" for r in rem.applicable_rules(db, cal.get(db, event["id"])))
    event = cal.add(db, "Тренировка", "2030-01-08T11:00:00+00:00", subject_person_id=amina["id"], participants=["Тая"])
    assert any(r["scope"] == "slug:taya" for r in rem.applicable_rules(db, cal.get(db, event["id"])))


def test_group_subject_rejected_on_all_four_cli_writers(db, capsys):
    _, taya, group = _seed(db)
    assert cli.main(["cal", "add", "--title", "one", "--start", "2030-01-08T10:00:00+00:00", "--for-person", "татешки"]) == 2
    capsys.readouterr()
    assert cli.main(["cal", "add", "--title", "weekly", "--repeat", "weekly", "--days", "tue", "--start-time", "10:00", "--until", "2030-02-01", "--for-person", "татешки"]) == 2
    capsys.readouterr()
    event = cal.add(db, "existing", "2030-01-08T11:00:00+00:00")
    s = series.add(db, "series", "tue", "12:00", until_local="2030-02-01")
    series.generate(db, now_utc="2030-01-01T00:00:00+00:00")
    db.commit()
    assert cli.main(["cal", "update", str(event["id"]), "--for-person", "татешки"]) == 2
    capsys.readouterr()
    assert cli.main(["cal", "series", "update", str(s["id"]), "--for-person", "татешки"]) == 2
    capsys.readouterr()
    assert db.execute("SELECT COUNT(*) FROM events WHERE subject_person_id=?", (group["id"],)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM event_series WHERE subject_person_id=?", (group["id"],)).fetchone()[0] == 0
    assert db.execute("SELECT subject_person_id FROM events WHERE id=?", (event["id"],)).fetchone()[0] is None
    assert db.execute("SELECT subject_person_id FROM event_series WHERE id=?", (s["id"],)).fetchone()[0] is None


def test_series_clear_subject_clears_future_occurrences(db, capsys):
    _, taya, _ = _seed(db)
    s = series.add(db, "series", "tue", "10:00", until_local="2030-02-01", subject_person_id=taya["id"])
    series.generate(db, now_utc="2030-01-01T00:00:00+00:00")
    db.commit()
    assert cli.main(["cal", "series", "update", str(s["id"]), "--clear-for-person", "--json"]) == 0
    capsys.readouterr()
    assert db.execute("SELECT subject_person_id FROM event_series WHERE id=?", (s["id"],)).fetchone()[0] is None
    assert db.execute("SELECT COUNT(*) FROM events WHERE series_id=? AND subject_person_id IS NOT NULL", (s["id"],)).fetchone()[0] == 0


def test_series_subject_preserves_individual_denis_override(db):
    amina, taya, _ = _seed(db)
    denis = people.add(db, "Денис", slug="denis")
    s = series.add(db, "series", "tue", "10:00", until_local="2030-02-01", subject_person_id=amina["id"])
    series.generate(db, now_utc="2030-01-01T00:00:00+00:00")
    ids = [r["id"] for r in db.execute("SELECT id FROM events WHERE series_id=? ORDER BY start_utc", (s["id"],))]
    cal.update(db, ids[0], subject_person_id=denis["id"])
    series.update_participants(db, s["id"], subject_person_id=taya["id"], now_utc="2030-01-01T00:00:00+00:00")
    values = {r["id"]: r["subject_person_id"] for r in db.execute("SELECT id, subject_person_id FROM events WHERE series_id=?", (s["id"],))}
    assert values[ids[0]] == denis["id"]
    assert all(values[event_id] == taya["id"] for event_id in ids[1:])


def test_day_range_filter_and_no_flag_regression(db):
    _, taya, _ = _seed(db)
    cal.add(db, "Тая", "2030-01-08T05:00:00+00:00", subject_person_id=taya["id"])
    cal.add(db, "Общее", "2030-01-08T06:00:00+00:00")
    db.commit()
    all_day = {e["title"] for e in cal.day(db, "2030-01-08")}
    subject_day = {e["title"] for e in cal.day(db, "2030-01-08", subject_person_id=taya["id"])}
    all_range = {e["title"] for e in cal.list_range(db, "2030-01-08T00:00:00+00:00", "2030-01-09T00:00:00+00:00")}
    subject_range = {e["title"] for e in cal.list_range(db, "2030-01-08T00:00:00+00:00", "2030-01-09T00:00:00+00:00", subject_person_id=taya["id"])}
    assert all_day == {"Тая", "Общее"}
    assert subject_day == subject_range == {"Тая"}
    assert all_range == all_day


def test_fresh_and_v13_migrated_schema_match_and_history_stays_null(tmp_path):
    import sqlite3
    from fam import db as famdb

    def shape(conn, table):
        return {
            row["name"]: (row["type"], row["notnull"], row["dflt_value"], row["pk"])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }

    fresh = famdb.connect(str(tmp_path / "fresh.db"))
    famdb.init_db(fresh)
    fresh_shape = {table: shape(fresh, table) for table in ("events", "event_series")}

    legacy = sqlite3.connect(str(tmp_path / "legacy.db"))
    legacy.row_factory = sqlite3.Row
    famdb.init_db(legacy)
    legacy.execute("ALTER TABLE events DROP COLUMN subject_person_id")
    legacy.execute("ALTER TABLE event_series DROP COLUMN subject_person_id")
    legacy.execute("DROP TABLE ext_exports_taya")
    legacy.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
    legacy.execute(
        "INSERT INTO events(title,start_utc,created_at,updated_at) "
        "VALUES('history','2030-01-08T10:00:00+00:00','2030-01-01','2030-01-01')"
    )
    legacy.commit()

    famdb.init_db(legacy)
    migrated_shape = {table: shape(legacy, table) for table in ("events", "event_series")}
    assert migrated_shape == fresh_shape
    assert legacy.execute(
        "SELECT subject_person_id FROM events WHERE title='history'"
    ).fetchone()[0] is None
    assert legacy.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0] == "15"
    fresh.close()
    legacy.close()
