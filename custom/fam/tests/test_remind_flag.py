"""Per-event / per-series `remind` flag (schema v16) and the Taya lead by subject.

An event with remind=0 stays on the calendar (digest, views, export) but never
gets a Hermes reminder chain. The flag lives on the event and on its series, so
it survives regeneration triggers and is inherited by occurrences materialized
later -- the leak `fam rem cancel` could not close (live 2026-09-24: a new
Robotics occurrence arrived with a fresh pending chain after every earlier one
had been cancelled by hand).
"""
import json

from fam import audit, cal, cli, people, rem, series

NOW = "2030-01-01T00:00:00+00:00"


def _seed(db):
    rem.seed_default_rules(db)
    rem.migrate_rules_2c(db)
    amina = people.add(db, "Амина", slug="amina")
    taya = people.add(db, "Тая", slug="taya")
    db.commit()
    return amina, taya


def _pending(db, event_id):
    return db.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=? AND status='pending'",
        (event_id,)).fetchone()[0]


def _first_offset(db, scope):
    stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope=?", (scope,)).fetchone()[0])
    return min(s["offset_min"] for s in stages)


def _earliest_fire(db, event_id):
    return db.execute(
        "SELECT MIN(fire_at_utc) FROM reminders WHERE event_id=? AND status='pending'",
        (event_id,)).fetchone()[0]


def test_v16_schema_adds_remind_defaulting_to_on(db):
    _seed(db)
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "16"
    for table in ("events", "event_series"):
        cols = {r["name"]: r for r in db.execute(f"PRAGMA table_info({table})")}
        assert "remind" in cols
    e = cal.add(db, "Врач", "2030-01-08T10:00:00+00:00")
    assert e["remind"] == 1
    assert _pending(db, e["id"]) > 0


def test_add_without_remind_builds_no_chain_but_keeps_event(db):
    _seed(db)
    e = cal.add(db, "Карате", "2030-01-08T10:00:00+00:00", remind=False)
    assert e["remind"] == 0
    assert e["status"] == "active"
    assert _pending(db, e["id"]) == 0
    rows = audit.query(db, None, "rem.regenerate", None, 5)
    assert rows[0]["payload"] == {"event_id": e["id"], "created": 0}


def test_update_toggles_chain_and_other_edits_do_not_resurrect_it(db):
    _seed(db)
    e = cal.add(db, "Робототехника", "2030-01-08T10:00:00+00:00")
    assert _pending(db, e["id"]) > 0

    off = cal.update(db, e["id"], remind=False)
    assert off["remind"] == 0
    assert _pending(db, e["id"]) == 0

    cal.update(db, e["id"], start_utc="2030-01-09T10:00:00+00:00", travel_min=20)
    assert _pending(db, e["id"]) == 0

    on = cal.update(db, e["id"], remind=True)
    assert on["remind"] == 1
    assert _pending(db, e["id"]) > 0


def test_series_without_remind_covers_occurrences_materialized_later(db):
    _seed(db)
    s = series.add(db, "Робототехника", "wed,fri", "15:15", remind=False)
    assert s["remind"] == 0
    series.generate(db, now_utc=NOW)
    later = "2030-03-01T00:00:00+00:00"  # horizon slides: new occurrences appear
    before = db.execute("SELECT COUNT(*) FROM events WHERE series_id=?",
                        (s["id"],)).fetchone()[0]
    assert series.generate(db, now_utc=later) > 0
    rows = db.execute("SELECT id, remind FROM events WHERE series_id=?",
                      (s["id"],)).fetchall()
    assert len(rows) > before
    assert {r["remind"] for r in rows} == {0}
    assert db.execute(
        "SELECT COUNT(*) FROM reminders r JOIN events e ON e.id=r.event_id "
        "WHERE e.series_id=?", (s["id"],)).fetchone()[0] == 0


def test_series_set_remind_flips_future_occurrences_and_future_generation(db):
    _seed(db)
    s = series.add(db, "Актёрское мастерство", "tue,thu", "14:00")
    series.generate(db, now_utc=NOW)
    ids = [r["id"] for r in db.execute(
        "SELECT id FROM events WHERE series_id=? ORDER BY start_utc", (s["id"],))]
    assert all(_pending(db, i) > 0 for i in ids)

    result = series.set_remind(db, s["id"], False, now_utc=NOW)
    assert sorted(result["updated_events"]) == sorted(ids)
    assert series.get(db, s["id"])["remind"] == 0
    assert all(_pending(db, i) == 0 for i in ids)
    series.generate(db, now_utc="2030-03-01T00:00:00+00:00")
    assert {r["remind"] for r in db.execute(
        "SELECT remind FROM events WHERE series_id=?", (s["id"],))} == {0}

    series.set_remind(db, s["id"], True, now_utc=NOW)
    assert all(_pending(db, i) > 0 for i in ids)
    rows = audit.query(db, None, "cal.series.remind", None, 5)
    assert rows[0]["payload"]["remind"] is True


def test_series_set_remind_leaves_past_occurrences_alone(db):
    _seed(db)
    s = series.add(db, "Кружок", "tue", "14:00")
    series.generate(db, now_utc=NOW)
    first = db.execute("SELECT id, start_utc FROM events WHERE series_id=? "
                       "ORDER BY start_utc LIMIT 1", (s["id"],)).fetchone()
    series.set_remind(db, s["id"], False, now_utc=first["start_utc"])
    assert db.execute("SELECT remind FROM events WHERE id=?",
                      (first["id"],)).fetchone()[0] == 1


def test_iphone_owner_still_silent_regardless_of_remind(db):
    _seed(db)
    e = cal.add(db, "Йога", "2030-01-08T10:00:00+00:00")
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (e["id"],))
    assert rem.regenerate(db, e["id"]) == 0
    assert _pending(db, e["id"]) == 0


def test_taya_subject_without_participation_gets_taya_lead(db):
    _, taya = _seed(db)
    start = "2030-01-08T10:00:00+00:00"
    plain = cal.add(db, "Врач", start)
    hers = cal.add(db, "Ортодонт", "2030-01-09T10:00:00+00:00",
                   subject_person_id=taya["id"])
    assert _first_offset(db, "slug:taya") < _first_offset(db, "default")
    from datetime import datetime, timedelta
    def lead(event_id, start_iso):
        return (datetime.fromisoformat(start_iso)
                - datetime.fromisoformat(_earliest_fire(db, event_id)))
    assert lead(plain["id"], start) == timedelta(minutes=-_first_offset(db, "default"))
    assert lead(hers["id"], "2030-01-09T10:00:00+00:00") == timedelta(
        minutes=-_first_offset(db, "slug:taya"))


def test_subject_change_regenerates_chain(db):
    _, taya = _seed(db)
    start = "2030-01-08T10:00:00+00:00"
    e = cal.add(db, "Ортодонт", start)
    default_first = _earliest_fire(db, e["id"])
    cal.update(db, e["id"], subject_person_id=taya["id"])
    assert _earliest_fire(db, e["id"]) < default_first


def test_series_subject_change_regenerates_occurrence_chains(db):
    _, taya = _seed(db)
    s = series.add(db, "Математика", "tue", "10:00")
    series.generate(db, now_utc=NOW)
    eid = db.execute("SELECT id FROM events WHERE series_id=? ORDER BY start_utc "
                     "LIMIT 1", (s["id"],)).fetchone()[0]
    before = _earliest_fire(db, eid)
    series.update_participants(db, s["id"], subject_person_id=taya["id"],
                               now_utc=NOW)
    assert _earliest_fire(db, eid) < before


# ---- CLI ----

def test_cli_add_no_remind_one_off(db, capsys):
    _seed(db)
    assert cli.main(["cal", "add", "--title", "Карате", "--start",
                     "2030-01-08T10:00:00+00:00", "--for-person", "Тая",
                     "--no-remind", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["remind"] == 0
    assert _pending(db, payload["id"]) == 0


def test_cli_add_repeat_no_remind(db, capsys):
    _seed(db)
    assert cli.main(["cal", "add", "--title", "Робототехника", "--repeat",
                     "weekly", "--days", "tue,thu", "--start-time", "16:10",
                     "--end-time", "16:55", "--for-person", "Тая",
                     "--no-remind", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    sid = payload["series"]["id"]
    assert payload["series"]["remind"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM reminders r JOIN events e ON e.id=r.event_id "
        "WHERE e.series_id=?", (sid,)).fetchone()[0] == 0


def test_cli_update_remind_toggle(db, capsys):
    _seed(db)
    e = cal.add(db, "Врач", "2030-01-08T10:00:00+00:00")
    db.commit()
    assert cli.main(["cal", "update", str(e["id"]), "--no-remind", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["remind"] == 0
    assert _pending(db, e["id"]) == 0
    assert cli.main(["cal", "update", str(e["id"]), "--remind", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["remind"] == 1
    assert _pending(db, e["id"]) > 0


def test_cli_series_update_no_remind(db, capsys):
    _seed(db)
    s = series.add(db, "Актёрское мастерство", "tue,thu", "14:00")
    series.generate(db)
    db.commit()
    assert cli.main(["cal", "series", "update", str(s["id"]), "--no-remind",
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["remind"] is False
    assert series.get(db, s["id"])["remind"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM reminders r JOIN events e ON e.id=r.event_id "
        "WHERE e.series_id=? AND r.status='pending'", (s["id"],)).fetchone()[0] == 0
