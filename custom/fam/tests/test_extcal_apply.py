"""Task 5: applying an `extcal.plan_changes()` Changeset to the DB
(`extcal.apply_changes`), and the invariants it exists to protect:

  - `rem.regenerate` never builds a reminder chain for an `owner='iphone'`
    row (invariant #2).
  - an imported event/plan's `place` is resolved when its free-text
    iCloud `LOCATION` matches a known `places` name/alias, and left
    None/NULL (never a raised error) when it doesn't -- the raw text is
    ALSO always stored verbatim in `notes` regardless (fix-round finding
    C1, Denis's decision, refined in the second fix round).
  - `apply_changes` never mutates an `owner='hermes'` row, even when a
    Changeset entry's `id` points at one (fix-round finding I2 -- a race
    with `cal adopt`/`disown` between fetch and apply).
  - re-applying the same Changeset is idempotent on both branches
    (fix-round finding I3).
  - `extcal` never reaches `gate.deliver` on any path (project-wide
    invariant #1, first actually testable once a module reached this far
    into cal.*/plans.*'s own call graph).

Every test here uses the `db` fixture (a fresh tmp-path sqlite file, see
conftest.py) -- never the live assistant.db. Changeset entries are built by
hand in the exact shape `extcal.plan_changes` documents/emits (see
test_extcal_reconcile.py's own fixtures for the same convention).
"""
from fam import audit, cal, extcal, gate, places, plans, rem


def _event_insert(uid, title, start_utc, end_utc=None, location="", seq=0,
                   href=None, etag=None, recurrence_id=None):
    return {
        "title": title, "start_utc": start_utc,
        "end_utc": end_utc or start_utc, "location": location,
        "external_uid": extcal._occurrence_key(uid, recurrence_id),
        "external_seq": seq, "owner": "iphone",
        "external_href": href, "external_etag": etag,
    }


def _plan_insert(uid, title, deadline, location="", href=None, etag=None):
    return {
        "title": title, "deadline": deadline, "location": location,
        "external_uid": extcal._occurrence_key(uid, None), "owner": "iphone",
        "external_href": href, "external_etag": etag,
    }


def _changeset(events_insert=(), events_update=(), events_cancel=(),
               plans_insert=(), plans_update=(), plans_drop=(),
               collisions=()):
    return {
        "events": {"insert": list(events_insert), "update": list(events_update),
                   "cancel": list(events_cancel)},
        "plans": {"insert": list(plans_insert), "update": list(plans_update),
                  "drop": list(plans_drop)},
        "collisions": list(collisions),
    }


def _reminder_count(conn, event_id):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM reminders WHERE event_id=?", (event_id,)
    ).fetchone()
    return row["n"]


def _get_event_by_uid(conn, external_uid):
    row = conn.execute(
        "SELECT * FROM events WHERE external_uid=?", (external_uid,)
    ).fetchone()
    return dict(row) if row else None


def _get_plan_by_uid(conn, external_uid):
    row = conn.execute(
        "SELECT * FROM plans WHERE external_uid=?", (external_uid,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------
# the main invariant: owner='iphone' never gets a reminder chain
# ---------------------------------------------------------------------

def test_insert_timed_event_is_owner_iphone_and_reminder_free(db):
    rem.seed_default_rules(db)
    db.commit()

    entry = _event_insert("uid-100@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00",
                           "2037-07-20T14:00:00+00:00", location="Invictus")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})

    assert counts["events_inserted"] == 1
    assert counts["errors"] == []
    row = _get_event_by_uid(db, entry["external_uid"])
    assert row is not None
    assert row["owner"] == "iphone"
    assert row["status"] == "active"
    assert _reminder_count(db, row["id"]) == 0


def test_insert_timed_event_does_not_touch_neighboring_hermes_event_reminders(db):
    rem.seed_default_rules(db)
    db.commit()

    hermes_event = cal.add(db, "Событие Гермеса", "2037-07-20T05:00:00+00:00")
    db.commit()
    hermes_reminder_count_before = _reminder_count(db, hermes_event["id"])
    assert hermes_reminder_count_before > 0  # sanity: default rule fired

    entry = _event_insert("uid-101@icloud.com", "Стоматолог",
                           "2037-07-21T09:00:00+00:00")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    assert counts["events_inserted"] == 1

    # the pre-existing Hermes-owned chain is completely untouched
    assert _reminder_count(db, hermes_event["id"]) == hermes_reminder_count_before

    imported = _get_event_by_uid(db, entry["external_uid"])
    assert _reminder_count(db, imported["id"]) == 0


def test_update_moves_event_time_without_creating_reminders(db):
    rem.seed_default_rules(db)
    db.commit()

    entry = _event_insert("uid-102@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00",
                           "2037-07-20T14:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    assert _reminder_count(db, row["id"]) == 0

    update_entry = {
        "id": row["id"],
        "changes": {"start_utc": ("2037-07-20T13:00:00+00:00",
                                   "2037-07-20T15:00:00+00:00")},
    }
    counts = extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    assert counts["events_updated"] == 1

    moved = cal.get(db, row["id"])
    assert moved["start_utc"] == "2037-07-20T15:00:00+00:00"
    assert _reminder_count(db, row["id"]) == 0


def test_regenerate_skips_owner_iphone_directly(db):
    # Direct unit-level pin of the invariant Task 5 adds to rem.regenerate
    # itself (not just through apply_changes) -- same style
    # test_regenerate_on_cancelled_event_clears_pending uses (raw SQL flip,
    # isolating regenerate()'s own contract from any other module's
    # cascading side effects).
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "Йога", "2037-07-20T13:00:00+00:00")
    db.commit()
    assert _reminder_count(db, e["id"]) > 0  # sanity: rules did fire once

    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (e["id"],))
    db.commit()

    created = rem.regenerate(db, e["id"])
    db.commit()
    assert created == 0
    assert _reminder_count(db, e["id"]) == 0


# ---------------------------------------------------------------------
# C1: place is NEVER resolved for an imported row -- raw text -> notes
# ---------------------------------------------------------------------

def test_insert_event_with_unresolvable_location_leaves_place_none_uses_notes(db):
    # This location text matches NOTHING in `places` -- under the FIRST
    # fix-round cut this raised UnknownRefError and the event never
    # imported at all. Now it must succeed unconditionally: place stays
    # NULL, the raw text lands in notes.
    entry = _event_insert("uid-200@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00",
                           location="Стоматология, Абая 150")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    assert counts["events_inserted"] == 1
    assert counts["errors"] == []

    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None
    assert row["notes"] == "Стоматология, Абая 150"


def test_insert_event_with_resolvable_location_sets_place_and_notes(db):
    # Refined C1 (Denis, second fix round): "точное совпадение с places
    # по-прежнему используем, когда оно есть" -- a location that DOES
    # match a known place is resolved and used, not discarded; notes still
    # carries the raw text either way (that's the field T6 must diff
    # against, per the module's Task 6 contract note -- not the place).
    invictus = places.add(db, "Invictus")
    db.commit()
    entry = _event_insert("uid-201@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00", location="Invictus")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    assert counts["events_inserted"] == 1

    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["place_id"] == invictus["id"]
    assert row["notes"] == "Invictus"


def test_insert_plan_with_unresolvable_location_leaves_place_none_uses_notes(db):
    entry = _plan_insert("uid-202@icloud.com", "Купить подарок", "2037-07-31",
                          location="Meга, 2 этаж")
    counts = extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    assert counts["plans_inserted"] == 1

    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None
    assert row["notes"] == "Meга, 2 этаж"


def test_insert_plan_with_resolvable_location_sets_place_and_notes(db):
    mega = places.add(db, "Мега")
    db.commit()
    entry = _plan_insert("uid-203@icloud.com", "Купить подарок", "2037-07-31",
                          location="Мега")
    counts = extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    assert counts["plans_inserted"] == 1

    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] == mega["id"]
    assert row["notes"] == "Мега"


def test_event_update_unresolvable_location_change_updates_notes_leaves_place_none(db):
    entry = _event_insert("uid-204@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00", location="Клиника А")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])

    update_entry = {
        "id": row["id"],
        "changes": {"location": ("Клиника А", "Клиника Б, каб. 12")},
    }
    counts = extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    assert counts["events_updated"] == 1

    after = cal.get(db, row["id"])
    assert after["place"] is None
    assert after["notes"] == "Клиника Б, каб. 12"


def test_event_update_location_change_resolves_new_place(db):
    invictus = places.add(db, "Invictus")
    db.commit()
    entry = _event_insert("uid-205@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00", location="где-то")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None  # "где-то" never resolves

    update_entry = {
        "id": row["id"],
        "changes": {"location": ("где-то", "Invictus")},
    }
    extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    after = cal.get(db, row["id"])
    assert after["place"]["id"] == invictus["id"]
    assert after["notes"] == "Invictus"


def test_event_update_location_change_clears_previously_resolved_place(db):
    invictus = places.add(db, "Invictus")
    db.commit()
    entry = _event_insert("uid-206@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00", location="Invictus")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["place_id"] == invictus["id"]

    # she edited the address on her phone to something Hermes has no
    # place for -- the STALE resolved place must not linger.
    update_entry = {
        "id": row["id"],
        "changes": {"location": ("Invictus", "Invictus, филиал на Достык")},
    }
    extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    after = cal.get(db, row["id"])
    assert after["place"] is None
    assert after["notes"] == "Invictus, филиал на Достык"


def test_plan_update_changes_title_deadline_and_location(db):
    entry = _plan_insert("uid-203@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])

    update_entry = {
        "id": row["id"],
        "changes": {
            "title": ("Купить подарок", "Купить подарок маме"),
            "deadline": ("2037-07-31", "2037-08-02"),
            "location": ("", "Мега, 2 этаж"),
        },
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert counts["plans_updated"] == 1

    after = plans.get(db, row["id"])
    assert after["title"] == "Купить подарок маме"
    assert after["deadline"] == "2037-08-02"
    assert after["place"] is None
    assert after["notes"] == "Мега, 2 этаж"


def test_plan_update_location_change_resolves_and_clears_place(db):
    mega = places.add(db, "Мега")
    db.commit()
    entry = _plan_insert("uid-204@icloud.com", "Купить подарок", "2037-07-31",
                          location="где-то")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None

    resolve_entry = {"id": row["id"],
                      "changes": {"location": ("где-то", "Мега")}}
    extcal.apply_changes(db, _changeset(plans_update=[resolve_entry]), {})
    after_resolve = plans.get(db, row["id"])
    assert after_resolve["place"]["id"] == mega["id"]

    clear_entry = {"id": row["id"],
                   "changes": {"location": ("Мега", "Мега, филиал 2")}}
    extcal.apply_changes(db, _changeset(plans_update=[clear_entry]), {})
    after_clear = plans.get(db, row["id"])
    assert after_clear["place"] is None
    assert after_clear["notes"] == "Мега, филиал 2"


# ---------------------------------------------------------------------
# I1: a plan update cascades recompute_road/regenerate when attached
# ---------------------------------------------------------------------

def test_plan_update_cascades_recompute_and_regenerate_when_attached(db, monkeypatch):
    hermes_event = cal.add(db, "Стоматолог", "2037-07-20T09:00:00+00:00")
    db.commit()
    entry = _plan_insert("uid-300@icloud.com", "Забрать анализы", "2037-07-19")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])
    assert plans.attach(db, row["id"], hermes_event["id"]) is True
    db.commit()

    calls = []
    monkeypatch.setattr(
        extcal.cal, "recompute_road",
        lambda conn, event_id: calls.append(("recompute", event_id)))
    monkeypatch.setattr(
        extcal.rem, "regenerate",
        lambda conn, event_id, now_utc=None: calls.append(("regen", event_id)))

    update_entry = {
        "id": row["id"],
        "changes": {"title": ("Забрать анализы", "Забрать анализы (готовы)")},
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert counts["plans_updated"] == 1
    assert ("recompute", hermes_event["id"]) in calls
    assert ("regen", hermes_event["id"]) in calls


def test_plan_update_does_not_cascade_when_not_attached(db, monkeypatch):
    entry = _plan_insert("uid-301@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])

    calls = []
    monkeypatch.setattr(
        extcal.cal, "recompute_road",
        lambda conn, event_id: calls.append(("recompute", event_id)))
    monkeypatch.setattr(
        extcal.rem, "regenerate",
        lambda conn, event_id, now_utc=None: calls.append(("regen", event_id)))

    update_entry = {"id": row["id"],
                     "changes": {"title": ("Купить подарок", "Купить подарок папе")}}
    extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert calls == []


# ---------------------------------------------------------------------
# I2: apply_changes never mutates an owner='hermes' row
# ---------------------------------------------------------------------

def test_event_update_pointing_at_hermes_row_is_refused(db):
    hermes = cal.add(db, "Событие Гермеса", "2037-07-20T05:00:00+00:00")
    db.commit()
    update_entry = {"id": hermes["id"],
                     "changes": {"title": ("Событие Гермеса", "Подмена")}}
    counts = extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    assert counts["events_updated"] == 0
    assert len(counts["errors"]) == 1

    after = cal.get(db, hermes["id"])
    assert after["title"] == "Событие Гермеса"


def test_event_cancel_pointing_at_hermes_row_is_refused(db):
    rem.seed_default_rules(db)
    db.commit()
    hermes = cal.add(db, "Событие Гермеса", "2037-07-20T05:00:00+00:00")
    db.commit()
    before = _reminder_count(db, hermes["id"])
    assert before > 0

    cancel_entry = {"id": hermes["id"], "external_uid": "irrelevant"}
    counts = extcal.apply_changes(db, _changeset(events_cancel=[cancel_entry]), {})
    assert counts["events_cancelled"] == 0
    assert len(counts["errors"]) == 1

    after = cal.get(db, hermes["id"])
    assert after["status"] == "active"
    assert _reminder_count(db, hermes["id"]) == before


def test_plan_update_pointing_at_hermes_row_is_refused(db):
    pid = plans.add(db, "План Гермеса", deadline="2037-08-01")
    db.commit()
    update_entry = {"id": pid, "changes": {"title": ("План Гермеса", "Подмена")}}
    counts = extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert counts["plans_updated"] == 0
    assert len(counts["errors"]) == 1

    after = plans.get(db, pid)
    assert after["title"] == "План Гермеса"


def test_plan_drop_pointing_at_hermes_row_is_refused(db):
    pid = plans.add(db, "План Гермеса", deadline="2037-08-01")
    db.commit()
    drop_entry = {"id": pid, "external_uid": "irrelevant"}
    counts = extcal.apply_changes(db, _changeset(plans_drop=[drop_entry]), {})
    assert counts["plans_dropped"] == 0
    assert len(counts["errors"]) == 1

    after = plans.get(db, pid)
    assert after["status"] == "open"


# ---------------------------------------------------------------------
# I3 / idempotency
# ---------------------------------------------------------------------

def test_reapplying_same_changeset_is_idempotent(db):
    event_entry = _event_insert("uid-400@icloud.com", "Йога",
                                 "2037-07-20T13:00:00+00:00")
    plan_entry = _plan_insert("uid-401@icloud.com", "Подарок", "2037-07-31")
    cs = _changeset(events_insert=[event_entry], plans_insert=[plan_entry])

    counts1 = extcal.apply_changes(db, cs, {})
    assert counts1["events_inserted"] == 1
    assert counts1["plans_inserted"] == 1
    assert counts1["errors"] == []

    counts2 = extcal.apply_changes(db, cs, {})
    # events: rejected by the partial UNIQUE index -> IntegrityError,
    # caught by the per-row guard -- no duplicate row, but an error entry.
    assert counts2["events_inserted"] == 0
    # plans: no unique index (I3) -- caught by the explicit SELECT-before-
    # insert guard instead -- a clean no-op, no error entry for it.
    assert counts2["plans_inserted"] == 0
    assert len(counts2["errors"]) == 1
    assert counts2["errors"][0]["branch"] == "events"

    events_n = db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE external_uid=?",
        (event_entry["external_uid"],)).fetchone()["n"]
    plans_n = db.execute(
        "SELECT COUNT(*) AS n FROM plans WHERE external_uid=?",
        (plan_entry["external_uid"],)).fetchone()["n"]
    assert events_n == 1
    assert plans_n == 1


def test_duplicate_external_uid_within_one_call_events_branch_inserts_once(db):
    e1 = _event_insert("uid-402@icloud.com", "Йога", "2037-07-20T13:00:00+00:00")
    e2 = dict(e1)
    e2["title"] = "Йога (дубль из другого календаря)"

    counts = extcal.apply_changes(db, _changeset(events_insert=[e1, e2]), {})
    assert counts["events_inserted"] == 1
    assert len(counts["errors"]) == 1

    n = db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE external_uid=?",
        (e1["external_uid"],)).fetchone()["n"]
    assert n == 1


def test_plans_insert_idempotency_guard_skips_without_recording_an_error(db):
    entry = _plan_insert("uid-403@icloud.com", "Подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})

    counts = extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    assert counts["plans_inserted"] == 0
    assert counts["errors"] == []

    n = db.execute(
        "SELECT COUNT(*) AS n FROM plans WHERE external_uid=?",
        (entry["external_uid"],)).fetchone()["n"]
    assert n == 1


# ---------------------------------------------------------------------
# cancel / drop
# ---------------------------------------------------------------------

def test_cancel_entry_marks_event_cancelled_and_clears_pending_reminders(db):
    rem.seed_default_rules(db)
    db.commit()
    entry = _event_insert("uid-500@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])

    cancel_entry = {"id": row["id"], "external_uid": entry["external_uid"]}
    counts = extcal.apply_changes(db, _changeset(events_cancel=[cancel_entry]), {})
    assert counts["events_cancelled"] == 1

    after = cal.get(db, row["id"])
    assert after["status"] == "cancelled"
    assert _reminder_count(db, row["id"]) == 0


def test_drop_entry_marks_plan_dropped(db):
    entry = _plan_insert("uid-501@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])

    drop_entry = {"id": row["id"], "external_uid": entry["external_uid"]}
    counts = extcal.apply_changes(db, _changeset(plans_drop=[drop_entry]), {})
    assert counts["plans_dropped"] == 1

    after = plans.get(db, row["id"])
    assert after["status"] == "dropped"


# ---------------------------------------------------------------------
# external identity + href/etag attachment
# ---------------------------------------------------------------------

def test_insert_persists_external_identity_columns(db):
    entry = _event_insert("uid-600@icloud.com", "Тренировка",
                           "2037-07-20T13:00:00+00:00", seq=3,
                           recurrence_id="2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["owner"] == "iphone"
    assert row["external_uid"] == entry["external_uid"]
    assert row["external_seq"] == 3


def test_insert_attaches_href_and_etag_when_present_on_entry(db):
    entry = _event_insert("uid-601@icloud.com", "Тренировка",
                           "2037-07-20T13:00:00+00:00",
                           href="/cal/1/uid-601.ics", etag='"abc123"')
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["external_href"] == "/cal/1/uid-601.ics"
    assert row["external_etag"] == '"abc123"'


def test_update_does_not_clobber_href_etag_when_absent_from_entry(db):
    entry = _event_insert("uid-602@icloud.com", "Тренировка",
                           "2037-07-20T13:00:00+00:00",
                           href="/cal/1/uid-602.ics", etag='"v1"')
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])

    update_entry = {
        "id": row["id"],
        "changes": {"title": ("Тренировка", "Тренировка (зал)")},
    }
    extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    after = db.execute("SELECT * FROM events WHERE id=?", (row["id"],)).fetchone()
    assert after["external_href"] == "/cal/1/uid-602.ics"
    assert after["external_etag"] == '"v1"'


# ---------------------------------------------------------------------
# audit: written for every mutation, and privacy-redacted
# ---------------------------------------------------------------------

def test_each_successful_mutation_writes_cal_ext_apply_audit(db):
    rem.seed_default_rules(db)
    db.commit()

    ins = _event_insert("uid-700@icloud.com", "A", "2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[ins]), {})
    row = _get_event_by_uid(db, ins["external_uid"])

    upd = {"id": row["id"], "changes": {"title": ("A", "B")}}
    extcal.apply_changes(db, _changeset(events_update=[upd]), {})

    cancel = {"id": row["id"], "external_uid": ins["external_uid"]}
    extcal.apply_changes(db, _changeset(events_cancel=[cancel]), {})

    plan_ins = _plan_insert("uid-701@icloud.com", "P", "2037-08-01")
    extcal.apply_changes(db, _changeset(plans_insert=[plan_ins]), {})

    rows = audit.query(db, None, "cal.ext.apply", None, limit=50)
    actions = {(r["payload"]["branch"], r["payload"]["action"]) for r in rows}
    assert ("events", "insert") in actions
    assert ("events", "update") in actions
    assert ("events", "cancel") in actions
    assert ("plans", "insert") in actions


def test_update_audit_redacts_title_and_location_values(db):
    ins = _event_insert("uid-702@icloud.com", "Секретное свидание",
                         "2037-07-20T13:00:00+00:00", location="Тайное место")
    extcal.apply_changes(db, _changeset(events_insert=[ins]), {})
    row = _get_event_by_uid(db, ins["external_uid"])

    upd = {
        "id": row["id"],
        "changes": {
            "title": ("Секретное свидание", "Другое название"),
            "location": ("Тайное место", "Новое тайное место"),
            "start_utc": ("2037-07-20T13:00:00+00:00", "2037-07-20T14:00:00+00:00"),
        },
    }
    extcal.apply_changes(db, _changeset(events_update=[upd]), {})

    audit_rows = audit.query(db, None, "cal.ext.apply", None, limit=50)
    update_rows = [r for r in audit_rows if r["payload"]["action"] == "update"]
    assert len(update_rows) == 1
    payload = update_rows[0]["payload"]
    assert payload["changes"]["title"] == "<redacted>"
    assert payload["changes"]["location"] == "<redacted>"
    # timestamps are WHEN, not WHAT -- kept as-is, useful for a human
    # skimming audit_log, not the "body" the design doc's rule is about.
    assert payload["changes"]["start_utc"] == [
        "2037-07-20T13:00:00+00:00", "2037-07-20T14:00:00+00:00"]
    # never a raw-text leak anywhere in the serialized payload
    assert "Другое название" not in str(payload)
    assert "Новое тайное место" not in str(payload)

    # the real content IS in the DB row itself -- redaction is audit-only.
    after = cal.get(db, row["id"])
    assert after["title"] == "Другое название"
    assert after["notes"] == "Новое тайное место"


# ---------------------------------------------------------------------
# per-row guard: one bad row rolls back, the rest still apply
# ---------------------------------------------------------------------

def test_bad_row_rolls_back_and_does_not_block_other_rows(db):
    good = _event_insert("uid-800@icloud.com", "Хорошее", "2037-07-20T13:00:00+00:00")
    bad = _event_insert("uid-801@icloud.com", "Плохое", "not-a-real-timestamp")

    counts = extcal.apply_changes(db, _changeset(events_insert=[bad, good]), {})

    assert counts["events_inserted"] == 1
    assert len(counts["errors"]) == 1
    assert counts["errors"][0]["external_uid"] == bad["external_uid"]

    assert _get_event_by_uid(db, good["external_uid"]) is not None
    assert _get_event_by_uid(db, bad["external_uid"]) is None  # rolled back

    error_rows = audit.query(db, None, "cal.ext.apply_error", None, limit=10)
    assert len(error_rows) == 1
    assert error_rows[0]["payload"]["external_uid"] == bad["external_uid"]


def test_bad_plan_update_row_rolls_back_and_leaves_plan_unchanged(db):
    entry = _plan_insert("uid-802@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])

    bad_update = {
        "id": row["id"],
        "changes": {
            "title": ("Купить подарок", "Купить подарок маме"),
            "deadline": ("2037-07-31", "not-a-real-date"),
        },
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[bad_update]), {})
    assert counts["plans_updated"] == 0
    assert len(counts["errors"]) == 1

    after = plans.get(db, row["id"])
    # neither the title nor the deadline changed -- the whole row-level
    # UPDATE never committed, not just the deadline half of it.
    assert after["title"] == "Купить подарок"
    assert after["deadline"] == "2037-07-31"


# ---------------------------------------------------------------------
# project-wide invariant: extcal never reaches gate.deliver
# ---------------------------------------------------------------------

def test_apply_changes_never_calls_gate_deliver(db, monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "deliver", lambda *a, **kw: calls.append((a, kw)))
    rem.seed_default_rules(db)
    db.commit()

    ins = _event_insert("uid-900@icloud.com", "A", "2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[ins]), {})
    event_row = _get_event_by_uid(db, ins["external_uid"])

    upd = {"id": event_row["id"], "changes": {"title": ("A", "B")}}
    extcal.apply_changes(db, _changeset(events_update=[upd]), {})

    plan_ins = _plan_insert("uid-901@icloud.com", "P", "2037-08-01")
    extcal.apply_changes(db, _changeset(plans_insert=[plan_ins]), {})
    plan_row = _get_plan_by_uid(db, plan_ins["external_uid"])

    plan_upd = {"id": plan_row["id"], "changes": {"title": ("P", "P2")}}
    extcal.apply_changes(db, _changeset(plans_update=[plan_upd]), {})

    plan_drop = {"id": plan_row["id"], "external_uid": plan_ins["external_uid"]}
    extcal.apply_changes(db, _changeset(plans_drop=[plan_drop]), {})

    cancel = {"id": event_row["id"], "external_uid": ins["external_uid"]}
    extcal.apply_changes(db, _changeset(events_cancel=[cancel]), {})

    assert calls == []


# ---------------------------------------------------------------------
# counts / malformed input
# ---------------------------------------------------------------------

def test_apply_changes_returns_counts_and_passes_through_collision_count(db):
    cs = _changeset(collisions=[{"branch": "events", "local_id": 1}])
    counts = extcal.apply_changes(db, cs, {})
    assert counts == {
        "events_inserted": 0, "events_updated": 0, "events_cancelled": 0,
        "plans_inserted": 0, "plans_updated": 0, "plans_dropped": 0,
        "collisions": 1, "errors": [],
    }


def test_malformed_changeset_is_a_noop_never_raises(db):
    for garbage in (None, "not a dict", 42, {}, {"events": "nope"},
                    {"events": {"insert": [None, "junk", 5]}},
                    {"collisions": 5}, {"collisions": "nope"}):
        counts = extcal.apply_changes(db, garbage, {})
        assert counts["events_inserted"] == 0
        assert counts["collisions"] == 0
        assert counts["errors"] == []
    assert db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 0
