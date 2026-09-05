from datetime import datetime, timezone

import pytest

from fam import audit, cal, extcal, people, rem, series


def _seed(db):
    amina = people.add(db, "Амина", slug="amina")
    taya = people.add(db, "Тая", slug="taya")
    group = people.add(db, "татешки", kind="group")
    people.add_member(db, group["id"], taya["id"])
    db.commit()
    return amina, taya, group


def test_v14_schema_and_subject_roundtrip(db):
    _, taya, _ = _seed(db)
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "14"
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


def test_subject_views_and_export_share_filter_helper(db):
    _, taya, _ = _seed(db)
    event = cal.add(db, "Тая", "2030-01-08T10:00:00+00:00", subject_person_id=taya["id"])
    cal.add(db, "Общее", "2030-01-08T11:00:00+00:00")
    db.execute("INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) VALUES (?,?,?,?,?)", (event["id"], "/e", "e", "h", "2030-01-01T00:00:00+00:00"))
    db.commit()
    viewed = {e["id"] for e in cal.list_range(db, "2030-01-08T00:00:00+00:00", "2030-01-09T00:00:00+00:00", subject_person_id=taya["id"])}
    planned = {e["event_id"] for e in extcal._export_plan(db, {"extcal_horizon_weeks": 8}, datetime(2030, 1, 1, tzinfo=timezone.utc), subject_person_id=taya["id"])}
    assert viewed == planned


def test_reminders_use_participants_not_subject(db):
    amina, taya, _ = _seed(db)
    rem.seed_default_rules(db)
    event = cal.add(db, "Тая", "2030-01-08T10:00:00+00:00", subject_person_id=taya["id"])
    assert not any(r["scope"] == "slug:taya" for r in rem.applicable_rules(db, cal.get(db, event["id"])))
    event = cal.add(db, "Тренировка", "2030-01-08T11:00:00+00:00", subject_person_id=amina["id"], participants=["Тая"])
    assert any(r["scope"] == "slug:taya" for r in rem.applicable_rules(db, cal.get(db, event["id"])))
