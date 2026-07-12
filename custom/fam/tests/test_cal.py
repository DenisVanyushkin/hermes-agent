import pytest
from fam import audit, cal, people, places, rem

def _seed(db):
    people.add(db, "Тая", slug="taya")
    people.add(db, "Денис", slug="denis")
    places.add(db, "Клиника Дента", aliases=["стоматолог"])
    db.commit()

def _seed_rules(db):
    rem.seed_default_rules(db)
    db.commit()

def test_add_resolves_refs_and_roundtrips(db):
    _seed(db)
    e = cal.add(db, "Тае к стоматологу", "2026-07-15T05:00:00+00:00",
                place="стоматолог", participants=["Тая"], transport="car")
    db.commit()
    got = cal.get(db, e["id"])
    assert got["place"]["name"] == "Клиника Дента"
    assert [p["name"] for p in got["participants"]] == ["Тая"]
    assert got["start_local"].startswith("2026-07-15T10:00")  # Almaty = UTC+5

def test_add_unknown_person_raises_without_insert(db):
    _seed(db)
    with pytest.raises(cal.UnknownRefError):
        cal.add(db, "Обед", "2026-07-15T07:00:00+00:00", participants=["Айгуль"])
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

def test_group_participant_expands(db):
    _seed(db)
    for n in ("Мадина", "Салтанат"): people.add(db, n)
    g = people.add(db, "татешки", kind="group")
    people.add_member(db, "татешки", "Мадина"); people.add_member(db, "татешки", "Салтанат")
    e = cal.add(db, "Чай", "2026-07-16T09:00:00+00:00", participants=["татешки"])
    db.commit()
    names = {p["name"] for p in cal.get(db, e["id"])["participants"]}
    assert names == {"Мадина", "Салтанат"}

def test_day_query_uses_almaty_boundaries(db):
    _seed(db)
    cal.add(db, "Утро", "2026-07-15T01:00:00+00:00")   # 06:00 Almaty 15-го
    cal.add(db, "Ночь-до", "2026-07-14T18:00:00+00:00") # 23:00 Almaty 14-го
    db.commit()
    titles = [e["title"] for e in cal.day(db, "2026-07-15")]
    assert titles == ["Утро"]

def test_cancel_hides_from_day(db):
    _seed(db)
    e = cal.add(db, "Отменить", "2026-07-15T05:00:00+00:00"); db.commit()
    cal.cancel(db, e["id"]); db.commit()
    assert cal.day(db, "2026-07-15") == []

# --- Finding 1: update() audit payload must be UTC-normalized ---

def test_update_audit_payload_is_utc_normalized(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    cal.update(db, e["id"], start_utc="2026-07-15T11:00:00+05:00")
    db.commit()

    rows = audit.query(db, since_utc=None, kind_prefix="cal.update", grep=None, limit=1)
    payload = rows[0]["payload"]
    # 11:00+05:00 == 06:00 UTC
    assert payload["start_utc"] == "2026-07-15T06:00:00+00:00"

    got = cal.get(db, e["id"])
    assert got["start_utc"] == "2026-07-15T06:00:00+00:00"

# --- Finding 2: update() must reject unknown fields ---

def test_update_rejects_unknown_field(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()

    with pytest.raises(ValueError):
        cal.update(db, e["id"], bogus="x")

    got = cal.get(db, e["id"])
    assert got["title"] == "Событие"

    rows = audit.query(db, since_utc=None, kind_prefix="cal.update", grep=None, limit=10)
    assert rows == []

# --- Finding 4: update() participant add/rm + unknown-person coverage ---

def test_update_add_and_remove_participant_roundtrip(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()

    cal.update(db, e["id"], add_person=["Тая"])
    db.commit()
    got = cal.get(db, e["id"])
    assert [p["name"] for p in got["participants"]] == ["Тая"]

    cal.update(db, e["id"], rm_person=["Тая"])
    db.commit()
    got = cal.get(db, e["id"])
    assert got["participants"] == []

def test_update_unknown_person_in_add_person_raises_without_mutation(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", participants=["Тая"])
    db.commit()

    with pytest.raises(cal.UnknownRefError):
        cal.update(db, e["id"], add_person=["Незнакомец"])

    got = cal.get(db, e["id"])
    assert [p["name"] for p in got["participants"]] == ["Тая"]

def test_to_utc_iso_rejects_naive_datetime():
    with pytest.raises(ValueError):
        cal._to_utc_iso("2026-07-15T10:00:00")

# --- Task 3: regeneration hooks (rem.regenerate/cancel_chain wired into cal) ---
# Dates are fixed far in the future (2099) since these hooks call
# rem.regenerate() with the real clock (no now_utc override), unlike the
# rem.py-level tests which pin "now" explicitly.

def test_add_creates_reminder_instances(db):
    _seed(db)
    _seed_rules(db)

    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    rows = db.execute(
        "SELECT * FROM reminders WHERE event_id=? ORDER BY fire_at_utc",
        (e["id"],)).fetchall()
    # default rule (2c) = build_stages(30): leave_at-30/-25/-15/0
    # (== start, no place/travel)
    assert len(rows) == 4
    assert {r["status"] for r in rows} == {"pending"}

def test_update_notes_does_not_regenerate(db):
    _seed(db)
    _seed_rules(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    before = {r["id"]: r["fire_at_utc"] for r in db.execute(
        "SELECT id, fire_at_utc FROM reminders WHERE event_id=?", (e["id"],))}

    cal.update(db, e["id"], notes="просто заметка")
    db.commit()

    after = {r["id"]: r["fire_at_utc"] for r in db.execute(
        "SELECT id, fire_at_utc FROM reminders WHERE event_id=?", (e["id"],))}

    # same row ids, same fire_at_utc -- update(notes=...) must not touch
    # the reminder chain at all.
    assert before == after

def test_update_start_utc_regenerates(db):
    _seed(db)
    _seed_rules(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    cal.update(db, e["id"], start_utc="2099-01-02T05:00:00+00:00")
    db.commit()

    rows = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
        "ORDER BY fire_at_utc", (e["id"],)).fetchall()
    fire_times = [r["fire_at_utc"] for r in rows]
    # default rule (2c) = build_stages(30): leave_at-30/-25/-15/0
    assert fire_times == [
        "2099-01-02T04:30:00+00:00",
        "2099-01-02T04:35:00+00:00",
        "2099-01-02T04:45:00+00:00",
        "2099-01-02T05:00:00+00:00",  # leave_at (no travel) == start
    ]

def test_update_travel_min_regenerates_leave_at_stage(db):
    _seed(db)
    _seed_rules(db)
    pl = places.add(db, "Клиника2")
    db.execute("UPDATE places SET travel_min=10 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00", place="Клиника2")
    db.commit()

    cal.update(db, e["id"], travel_min=30)
    db.commit()

    # every default (2c) stage anchors on leave_at -- pin the "пора
    # выходить" (offset 0) stage, whose fire_at_utc == leave_at exactly,
    # to check the travel_min override without depending on row order.
    row = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
        "AND anchor='leave_at' AND label='пора выходить'", (e["id"],)).fetchone()
    assert row["fire_at_utc"] == "2099-01-01T04:30:00+00:00"  # start - 30min override

def test_update_place_change_regenerates(db):
    _seed(db)
    _seed_rules(db)
    pl1 = places.add(db, "Место1")
    pl2 = places.add(db, "Место2")
    db.execute("UPDATE places SET travel_min=10 WHERE id=?", (pl1["id"],))
    db.execute("UPDATE places SET travel_min=50 WHERE id=?", (pl2["id"],))
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00", place="Место1")
    db.commit()

    cal.update(db, e["id"], place="Место2")
    db.commit()

    # pin the "пора выходить" (offset 0) stage -- fire_at_utc == leave_at.
    row = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
        "AND anchor='leave_at' AND label='пора выходить'", (e["id"],)).fetchone()
    assert row["fire_at_utc"] == "2099-01-01T04:10:00+00:00"  # start - 50min

def test_update_participants_change_regenerates_taya_stage(db):
    _seed(db)
    _seed_rules(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) c FROM reminders WHERE event_id=?", (e["id"],)
    ).fetchone()["c"] == 4  # default rule (2c, 4 stages), Тая not yet a participant

    cal.update(db, e["id"], add_person=["Тая"])
    db.commit()

    rule_ids = {r["rule_id"] for r in db.execute(
        "SELECT DISTINCT rule_id FROM reminders WHERE event_id=? AND status='pending'",
        (e["id"],))}
    # 2c precedence: slug:taya (non-empty stages) replaces default rather
    # than stacking with it, so only its rule_id is represented now
    # (was 2: default + slug:taya).
    assert len(rule_ids) == 1
    labels = {r["label"] for r in db.execute(
        "SELECT label FROM reminders WHERE event_id=? AND status='pending'", (e["id"],))}
    # slug:taya (2c) = build_stages(60): D=60's prepare-stage label
    assert "пора собираться" in labels

def test_cancel_cancels_pending_reminder_chain(db):
    _seed(db)
    _seed_rules(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    cal.cancel(db, e["id"])
    db.commit()

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],))}
    assert statuses == {"cancelled"}

# --- Fix round 1: update()'s "_material_changed" signal (mail hook dedup) ---
# The mail hook (cli.py's _maybe_email_event) needs to know whether a
# `cal update` changed a field the .ics email actually reflects
# (title/start_utc/end_utc/place/participants/travel_min) -- a notes-only
# edit must not re-send. update() computes and exposes this as a transient
# "_material_changed" key on its returned dict, reusing the same
# before/after snapshots it already takes for reminder-chain regen
# detection (a superset: adds end_utc and title, which are not
# regen-relevant but ARE mail-relevant -- see cal.py's
# _MAIL_TRIGGER_COLUMNS).

def test_update_notes_only_is_not_material_changed(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    updated = cal.update(db, e["id"], notes="просто заметка")
    assert updated["_material_changed"] is False

def test_update_title_only_is_material_changed(db):
    # Product decision (Denis, phase-2b final review Minor #7),
    # superseding the earlier spec-literal reading that excluded title:
    # a title-only rename IS material -- title feeds the .ics SUMMARY,
    # and the stable UID means the admin's calendar entry just updates
    # its name on the re-sent email (without it, the entry silently
    # keeps the stale title).
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    updated = cal.update(db, e["id"], title="Новое название")
    assert updated["_material_changed"] is True

def test_update_start_utc_is_material_changed(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    updated = cal.update(db, e["id"], start_utc="2026-07-15T06:00:00+00:00")
    assert updated["_material_changed"] is True

def test_update_end_utc_only_is_material_changed(db):
    # end_utc is NOT a reminder-regen trigger column, but IS mail-material
    # -- the one field where the two sets diverge, pinning that mail's
    # signal isn't just a re-use of the regen flag itself.
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    updated = cal.update(db, e["id"], end_utc="2026-07-15T07:00:00+00:00")
    assert updated["_material_changed"] is True

def test_update_place_change_is_material_changed(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    updated = cal.update(db, e["id"], place="Клиника Дента")
    assert updated["_material_changed"] is True

def test_update_travel_min_change_is_material_changed(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника Дента")
    db.commit()
    updated = cal.update(db, e["id"], travel_min=15)
    assert updated["_material_changed"] is True

def test_update_add_person_is_material_changed(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    updated = cal.update(db, e["id"], add_person=["Тая"])
    assert updated["_material_changed"] is True

def test_update_rm_person_is_material_changed(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", participants=["Тая"])
    db.commit()
    updated = cal.update(db, e["id"], rm_person=["Тая"])
    assert updated["_material_changed"] is True

def test_update_no_op_participant_add_is_not_material_changed(db):
    # add_person for someone already a participant: participant set is
    # unchanged (INSERT OR IGNORE), so this must not read as material.
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", participants=["Тая"])
    db.commit()
    updated = cal.update(db, e["id"], add_person=["Тая"])
    assert updated["_material_changed"] is False

def test_done_cancels_pending_reminder_chain(db):
    _seed(db)
    _seed_rules(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    cal.done(db, e["id"])
    db.commit()

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],))}
    assert statuses == {"cancelled"}
