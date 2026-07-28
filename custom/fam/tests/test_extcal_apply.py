"""Task 5: applying an `extcal.plan_changes()` Changeset to the DB
(`extcal.apply_changes`), and the invariants it exists to protect:

  - `rem.regenerate` never builds a reminder chain for an `owner='iphone'`
    row (invariant #2).
  - an imported event/plan's `place` is resolved when its free-text
    iCloud `LOCATION` matches a known `places` name/alias, and left
    None/NULL (never a raised error) when it doesn't -- the raw text is
    ALSO always stored verbatim in the `external_location` COLUMN
    regardless (fix-round finding C1, Denis's decision; the dedicated
    column itself is fix-round 4).
  - `notes` is human-owned and `extcal` never reads or writes it: a note
    Amina (or Hermes on her behalf) attached to an imported row survives
    any number of syncs, any change of location, and the location being
    deleted on the phone -- fix-round 4 closes finding N1 by removing the
    shared column entirely instead of re-encoding a delimiter a fourth
    time.
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
from datetime import datetime, timezone

from fam import audit, cal, extcal, gate, places, plans, rem
from fam.extcal import ALMATY


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


def _occ(uid, title, start_utc, end_utc=None, all_day=False, location="",
         status=None, seq=0, has_alarm=False, recurrence_id=None):
    """An `expand()`-shaped Occurrence, same fixture shape
    test_extcal_reconcile.py uses -- needed here (not just there) by the
    fix-round-4 round-trip tests, which drive the REAL
    `plan_changes` -> `apply_changes` -> snapshot -> `plan_changes` loop
    against a real DB instead of hand-building the second changeset.
    """
    return {
        "uid": uid, "recurrence_id": recurrence_id, "title": title,
        "start_utc": start_utc, "end_utc": end_utc or start_utc,
        "all_day": all_day, "location": location, "status": status,
        "seq": seq, "has_alarm": has_alarm,
    }


def _all_day_utc(date_str):
    """The same VALUE=DATE -> UTC conversion `extcal._parse_dt_value` does:
    local Asia/Almaty midnight of `date_str`, in UTC (computed, never
    hardcoded -- Kazakhstan's UTC offset has changed within living
    memory)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(tzinfo=ALMATY).astimezone(timezone.utc).isoformat()


def _snapshot_from_db(conn):
    """`local_snapshot` in exactly the shape `plan_changes` documents,
    built the way Task 6 will: `dict(row)` over `SELECT *`, no mapping and
    no text parsing anywhere -- which is only possible because the raw
    iCloud LOCATION lives in its own `external_location` column
    (fix-round 4).
    """
    return {
        "events": [dict(r) for r in conn.execute("SELECT * FROM events")],
        "plans": [dict(r) for r in conn.execute("SELECT * FROM plans")],
    }


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
# C1 (+ fix-round 4): place is resolved when it matches, and the raw text
# ALWAYS lands in `external_location` -- never in the human-owned `notes`.
# ---------------------------------------------------------------------

def test_insert_event_with_unresolvable_location_stores_external_location(db):
    # This location text matches NOTHING in `places` -- under the FIRST
    # fix-round cut this raised UnknownRefError and the event never
    # imported at all. Now it must succeed unconditionally: place stays
    # NULL, the raw text lands in external_location, notes stays empty.
    entry = _event_insert("uid-200@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00",
                           location="Стоматология, Абая 150")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    assert counts["events_inserted"] == 1
    assert counts["errors"] == []

    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None
    assert row["external_location"] == "Стоматология, Абая 150"
    assert row["notes"] == ""


def test_insert_event_with_resolvable_location_sets_place_and_external_location(db):
    # Refined C1 (Denis, second fix round): "точное совпадение с places
    # по-прежнему используем, когда оно есть" -- a location that DOES
    # match a known place is resolved and used, not discarded;
    # external_location still carries the raw text either way (that's the
    # column T6 must diff against, per the module's Task 6 contract note --
    # not the place, whose name is Hermes's own canonical spelling).
    invictus = places.add(db, "Invictus")
    db.commit()
    entry = _event_insert("uid-201@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00", location="Invictus")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    assert counts["events_inserted"] == 1

    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["place_id"] == invictus["id"]
    assert row["external_location"] == "Invictus"
    assert row["notes"] == ""


def test_insert_event_without_location_leaves_external_location_null(db):
    # "no location on the phone" is ONE state in the DB, not two: NULL,
    # never an empty string (see _external_location_value).
    entry = _event_insert("uid-207@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    assert row["external_location"] is None


def test_insert_plan_with_unresolvable_location_stores_external_location(db):
    entry = _plan_insert("uid-202@icloud.com", "Купить подарок", "2037-07-31",
                          location="Meга, 2 этаж")
    counts = extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    assert counts["plans_inserted"] == 1

    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None
    assert row["external_location"] == "Meга, 2 этаж"
    assert row["notes"] == ""


def test_insert_plan_with_resolvable_location_sets_place_and_external_location(db):
    mega = places.add(db, "Мега")
    db.commit()
    entry = _plan_insert("uid-203@icloud.com", "Купить подарок", "2037-07-31",
                          location="Мега")
    counts = extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    assert counts["plans_inserted"] == 1

    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] == mega["id"]
    assert row["external_location"] == "Мега"
    assert row["notes"] == ""


def test_event_update_unresolvable_location_change_updates_external_location(db):
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
    assert after["external_location"] == "Клиника Б, каб. 12"
    assert after["notes"] == ""


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
    assert after["external_location"] == "Invictus"


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
    assert after["external_location"] == "Invictus, филиал на Достык"


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
    assert after["external_location"] == "Мега, 2 этаж"
    assert after["notes"] == ""


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
    assert after_clear["external_location"] == "Мега, филиал 2"


# ---------------------------------------------------------------------
# I1 / N2: a plan update cascades recompute_road/regenerate when
# attached AND the place actually changed -- never on an unrelated field
# (fix-round-2 narrowed the gate: title/deadline-only edits used to
# trigger a live-TomTom-risking recompute for nothing).
# ---------------------------------------------------------------------

def _patch_cascade_tracking(monkeypatch):
    calls = []
    monkeypatch.setattr(
        extcal.cal, "recompute_road",
        lambda conn, event_id: calls.append(("recompute", event_id)))
    monkeypatch.setattr(
        extcal.rem, "regenerate",
        lambda conn, event_id, now_utc=None: calls.append(("regen", event_id)))
    return calls


def test_plan_update_cascades_when_attached_and_place_actually_changes(db, monkeypatch):
    invictus = places.add(db, "Invictus")
    db.commit()
    hermes_event = cal.add(db, "Стоматолог", "2037-07-20T09:00:00+00:00")
    db.commit()
    entry = _plan_insert("uid-300@icloud.com", "Забрать анализы", "2037-07-19")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] is None
    assert plans.attach(db, row["id"], hermes_event["id"]) is True
    db.commit()

    calls = _patch_cascade_tracking(monkeypatch)

    update_entry = {
        "id": row["id"],
        "changes": {"location": ("", "Invictus")},
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert counts["plans_updated"] == 1
    assert plans.get(db, row["id"])["place"]["id"] == invictus["id"]
    assert ("recompute", hermes_event["id"]) in calls
    assert ("regen", hermes_event["id"]) in calls


def test_plan_update_does_not_cascade_on_title_only_change_even_when_attached(db, monkeypatch):
    hermes_event = cal.add(db, "Стоматолог", "2037-07-20T09:00:00+00:00")
    db.commit()
    entry = _plan_insert("uid-301@icloud.com", "Забрать анализы", "2037-07-19")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])
    assert plans.attach(db, row["id"], hermes_event["id"]) is True
    db.commit()

    calls = _patch_cascade_tracking(monkeypatch)

    update_entry = {
        "id": row["id"],
        "changes": {"title": ("Забрать анализы", "Забрать анализы (готовы)")},
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert counts["plans_updated"] == 1
    assert calls == []  # N2: title-only edit must never trigger a recompute


def test_plan_update_does_not_cascade_when_location_change_keeps_same_place(db, monkeypatch):
    mega = places.add(db, "Мега")
    db.commit()
    hermes_event = cal.add(db, "Стоматолог", "2037-07-20T09:00:00+00:00")
    db.commit()
    entry = _plan_insert("uid-302@icloud.com", "Купить подарок", "2037-07-31",
                          location="Мега")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])
    assert row["place_id"] == mega["id"]
    assert plans.attach(db, row["id"], hermes_event["id"]) is True
    db.commit()

    calls = _patch_cascade_tracking(monkeypatch)

    # the location TEXT changed (casing), but it still resolves to the
    # SAME place -- place_id doesn't actually move, so no cascade.
    update_entry = {"id": row["id"], "changes": {"location": ("Мега", "мега")}}
    extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert calls == []


def test_plan_update_does_not_cascade_when_not_attached(db, monkeypatch):
    invictus = places.add(db, "Invictus")
    db.commit()
    entry = _plan_insert("uid-303@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])

    calls = _patch_cascade_tracking(monkeypatch)

    # a genuine place change -- but this plan was never attached to
    # anything, so there is no event to recompute at all.
    update_entry = {"id": row["id"], "changes": {"location": ("", "Invictus")}}
    extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert plans.get(db, row["id"])["place"]["id"] == invictus["id"]
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
    # fix-round-2 finding N3: the FIRST fix round left this test checking
    # only two of the three zeroes it claimed ("zero new rows, zero
    # updates") -- the third, "zero extra audit noise", was actually
    # non-zero (a real cal.ext.apply_error for the events branch, since it
    # relied purely on the UNIQUE index + full rollback). Extending the
    # SELECT-before-insert guard to the events branch (matching plans, see
    # _apply_event_insert) makes this a TRUE zero on every axis now.
    event_entry = _event_insert("uid-400@icloud.com", "Йога",
                                 "2037-07-20T13:00:00+00:00")
    plan_entry = _plan_insert("uid-401@icloud.com", "Подарок", "2037-07-31")
    cs = _changeset(events_insert=[event_entry], plans_insert=[plan_entry])

    counts1 = extcal.apply_changes(db, cs, {})
    assert counts1["events_inserted"] == 1
    assert counts1["plans_inserted"] == 1
    assert counts1["errors"] == []

    audit_before = audit.query(db, None, "cal.ext.apply", None, limit=50)

    counts2 = extcal.apply_changes(db, cs, {})
    assert counts2["events_inserted"] == 0
    assert counts2["plans_inserted"] == 0
    assert counts2["errors"] == []  # true zero -- not "expected 1", zero

    events_n = db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE external_uid=?",
        (event_entry["external_uid"],)).fetchone()["n"]
    plans_n = db.execute(
        "SELECT COUNT(*) AS n FROM plans WHERE external_uid=?",
        (plan_entry["external_uid"],)).fetchone()["n"]
    assert events_n == 1
    assert plans_n == 1

    # zero cal.ext.apply_error rows ever; the only NEW cal.ext.apply
    # entries from the second pass are the two expected, explicitly
    # audited "insert_skipped_duplicate" breadcrumbs (not silence, but not
    # a real re-insert or an error either) -- pinned explicitly rather than
    # just asserting "no error", per the review's "либо зафиксируй
    # ожидаемые записи в ассерте теста явно".
    assert audit.query(db, None, "cal.ext.apply_error", None, limit=10) == []
    audit_after = audit.query(db, None, "cal.ext.apply", None, limit=50)
    new_count = len(audit_after) - len(audit_before)
    assert new_count == 2
    # audit.query orders newest-first (id DESC) -- the newest `new_count`
    # rows are exactly what this second apply_changes() call just wrote.
    new_actions = {(r["payload"]["branch"], r["payload"]["action"])
                   for r in audit_after[:new_count]}
    assert new_actions == {("events", "insert_skipped_duplicate"),
                            ("plans", "insert_skipped_duplicate")}


def test_duplicate_external_uid_within_one_call_events_branch_inserts_once(db):
    e1 = _event_insert("uid-402@icloud.com", "Йога", "2037-07-20T13:00:00+00:00")
    e2 = dict(e1)
    e2["title"] = "Йога (дубль из другого календаря)"

    counts = extcal.apply_changes(db, _changeset(events_insert=[e1, e2]), {})
    assert counts["events_inserted"] == 1
    # fix-round-2 (N3): events now use the same SELECT-before-insert guard
    # plans already had -- the second (duplicate-key) entry is a clean,
    # audited skip, not an IntegrityError-driven rollback.
    assert counts["errors"] == []

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
    assert after["external_location"] == "Новое тайное место"


def test_plan_update_audit_redacts_title_and_location_values(db):
    # fix-round-2 finding N4: the events-branch redaction test above had
    # no plans-branch mirror, even though `_apply_plan_update` calls the
    # exact same `_audit_safe_changes` helper.
    ins = _plan_insert("uid-703@icloud.com", "Секретный план", "2037-08-01",
                        location="Тайное место плана")
    extcal.apply_changes(db, _changeset(plans_insert=[ins]), {})
    row = _get_plan_by_uid(db, ins["external_uid"])

    upd = {
        "id": row["id"],
        "changes": {
            "title": ("Секретный план", "Другое название плана"),
            "location": ("Тайное место плана", "Новое тайное место плана"),
            "deadline": ("2037-08-01", "2037-08-05"),
        },
    }
    extcal.apply_changes(db, _changeset(plans_update=[upd]), {})

    audit_rows = audit.query(db, None, "cal.ext.apply", None, limit=50)
    update_rows = [r for r in audit_rows
                   if r["payload"]["branch"] == "plans"
                   and r["payload"]["action"] == "update"]
    assert len(update_rows) == 1
    payload = update_rows[0]["payload"]
    assert payload["changes"]["title"] == "<redacted>"
    assert payload["changes"]["location"] == "<redacted>"
    assert payload["changes"]["deadline"] == ["2037-08-01", "2037-08-05"]
    assert "Другое название плана" not in str(payload)
    assert "Новое тайное место плана" not in str(payload)

    after = plans.get(db, row["id"])
    assert after["title"] == "Другое название плана"
    assert after["external_location"] == "Новое тайное место плана"


def test_raw_location_text_never_reaches_audit_log_on_any_path(db):
    # Privacy sweep (fix-round 4): the design doc's rule is "тела VEVENT
    # целиком не логируются, только UID и счётчики", and
    # `_audit_safe_changes` covers extcal's OWN update entries -- but until
    # fix-round 4 the raw location ALSO travelled as `notes` through
    # `cal.add`/`cal.update`/`plan.add`, each of which logs its own audit
    # payload with the notes value in it verbatim, entirely outside
    # `_audit_safe_changes`' reach. Now that nothing passes the location
    # as notes at all, the raw text must appear in NO audit row from ANY
    # path: insert, update, or plan insert/update.
    secret = "Клиника интимного здоровья, Абая 150"
    secret_plan = "Аптека на Розыбакиева, 3 этаж"
    ins = _event_insert("uid-720@icloud.com", "Врач",
                         "2037-07-20T13:00:00+00:00", location=secret)
    extcal.apply_changes(db, _changeset(events_insert=[ins]), {})
    row = _get_event_by_uid(db, ins["external_uid"])
    upd = {"id": row["id"], "changes": {"location": (secret, secret + ", каб. 4")}}
    extcal.apply_changes(db, _changeset(events_update=[upd]), {})

    plan_ins = _plan_insert("uid-721@icloud.com", "Забрать лекарство",
                             "2037-08-01", location=secret_plan)
    extcal.apply_changes(db, _changeset(plans_insert=[plan_ins]), {})
    plan_row = _get_plan_by_uid(db, plan_ins["external_uid"])
    plan_upd = {"id": plan_row["id"],
                "changes": {"location": (secret_plan, secret_plan + " (новый)")}}
    extcal.apply_changes(db, _changeset(plans_update=[plan_upd]), {})

    # every audit row this DB has, whatever its kind -- cal.add,
    # cal.update, plan.add, rem.regenerate, cal.ext.apply, ...
    all_rows = db.execute("SELECT kind, payload FROM audit_log").fetchall()
    assert all_rows  # the sweep would be vacuous on an empty table
    blob = " ".join(f"{r['kind']} {r['payload']}" for r in all_rows)
    assert "Абая 150" not in blob
    assert "Розыбакиева" not in blob

    # ...while the values themselves really are on the rows.
    assert cal.get(db, row["id"])["external_location"] == secret + ", каб. 4"
    assert plans.get(db, plan_row["id"])["external_location"] == \
        secret_plan + " (новый)"


# ---------------------------------------------------------------------
# fix-round 4 (finding N1, closed by removing the mechanism): `notes` is
# human-owned, `external_location` is machine-owned, and the two never
# meet. Rounds 1-3 all kept the raw location INSIDE `notes` and only
# traded one failure mode for the next (wholesale overwrite -> a readable
# marker a human can quote -> invisible markers an LLM agent driving
# `fam cal update --notes` drops on a rewrite). These tests assert the
# property those rounds were reaching for, now that it holds structurally:
# nothing here parses, merges, or delimits anything.
# ---------------------------------------------------------------------

def test_human_note_survives_a_location_change_on_an_event(db):
    entry = _event_insert("uid-704@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00", location="Клиника А")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])

    # a human (Amina, or Hermes on her behalf) attaches a note directly,
    # NOT through extcal -- the exact scenario the review's N1 repro
    # describes. Note this is a WHOLESALE replacement of the column, which
    # is precisely what `fam cal update --notes` does.
    cal.update(db, row["id"], notes="взяла паспорт")
    db.commit()

    update_entry = {
        "id": row["id"],
        "changes": {"location": ("Клиника А", "Клиника Б, каб. 12")},
    }
    extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})

    after = cal.get(db, row["id"])
    assert after["notes"] == "взяла паспорт"  # byte-for-byte, not merged
    assert after["external_location"] == "Клиника Б, каб. 12"


def test_human_note_survives_the_location_being_deleted_on_the_phone(db):
    entry = _event_insert("uid-705@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00", location="Клиника А")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    cal.update(db, row["id"], notes="взяла паспорт")
    db.commit()

    # she deleted the location on the phone entirely: external_location
    # must go back to NULL (no stale address left behind), notes must not
    # so much as flinch.
    update_entry = {"id": row["id"], "changes": {"location": ("Клиника А", "")}}
    extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})

    after = cal.get(db, row["id"])
    assert after["notes"] == "взяла паспорт"
    assert after["external_location"] is None


def test_human_note_survives_a_location_change_on_a_plan(db):
    entry = _plan_insert("uid-706@icloud.com", "Купить подарок", "2037-07-31",
                          location="Мега, 2 этаж")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = _get_plan_by_uid(db, entry["external_uid"])

    # plans.py has no generic update() -- raw SQL here stands in for
    # "however a human note ends up on this column".
    db.execute("UPDATE plans SET notes=? WHERE id=?", ("нужен чек", row["id"]))
    db.commit()

    update_entry = {
        "id": row["id"],
        "changes": {"location": ("Мега, 2 этаж", "Мега, 3 этаж")},
    }
    extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})

    after = plans.get(db, row["id"])
    assert after["notes"] == "нужен чек"
    assert after["external_location"] == "Мега, 3 этаж"


def test_human_note_survives_many_syncs_and_set_clear_set_cycles(db):
    # The accumulation/erosion failure every earlier round eventually hit:
    # repeated syncs, with the location set, cleared, and set again, and a
    # human note present the whole time. Nothing may accumulate (no second
    # copy of anything), nothing may erode (the note is byte-identical at
    # the end), and the note's content is deliberately hostile -- it
    # contains round 2's retired readable marker pair AND round 3's
    # invisible marker code points, both of which are now just ordinary,
    # completely inert characters in a column extcal never reads.
    # written with explicit \uXXXX escapes, never literal invisible glyphs:
    # a literal zero-width character in a source file is unverifiable by
    # reading it and at real risk of mangling across editors/transfers.
    hostile_note = (
        "заметка от Амины:\n[extcal:location]\n"
        "это моя личная заметка, не место!\n[/extcal:location]\n"
        "и немного невидимых: "
        "\u200b\u200c\u200d\u2060\u2063 \u200b\u200c\u200d\u2060\u2064\n"
        "конец"
    )
    entry = _event_insert("uid-707@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00", location="Клиника А")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = _get_event_by_uid(db, entry["external_uid"])
    cal.update(db, row["id"], notes=hostile_note)
    db.commit()

    for _ in range(3):
        # same location re-synced (an unchanged LOCATION still shows up as
        # an update entry when some OTHER field changed)
        extcal.apply_changes(db, _changeset(events_update=[
            {"id": row["id"], "changes": {"location": ("Клиника А", "Клиника А")}}]), {})
        # cleared on the phone, then set again
        extcal.apply_changes(db, _changeset(events_update=[
            {"id": row["id"], "changes": {"location": ("Клиника А", "")}}]), {})
        extcal.apply_changes(db, _changeset(events_update=[
            {"id": row["id"], "changes": {"location": ("", "Клиника А")}}]), {})

    after = cal.get(db, row["id"])
    assert after["notes"] == hostile_note  # byte-for-byte after 9 updates
    assert after["external_location"] == "Клиника А"  # exactly one value


def test_extcal_module_has_no_notes_marker_machinery_left(db):
    # Fix-round 4 deleted the delimiter mechanism outright rather than
    # re-encoding it a fourth time; this pins that it stays deleted, so a
    # future edit can't quietly reintroduce a shared-column convention.
    for name in ("_LOC_BLOCK_BEGIN", "_LOC_BLOCK_END", "_LOC_BLOCK_RE",
                 "_strip_location_block", "_merge_notes_with_location"):
        assert not hasattr(extcal, name), f"{name} is back"


# ---------------------------------------------------------------------
# fix-round 4: the value is READABLE from outside, and a full
# plan_changes -> apply_changes -> snapshot -> plan_changes round trip
# settles to zero changes.
# ---------------------------------------------------------------------

def test_cal_get_and_plans_get_expose_external_location(db):
    # This is how Amina gets the address back: the agent runs
    # `fam cal show <id> --json` / `fam plan list --json`, which serialize
    # exactly these dicts, so it can answer "а где стоматология?" with the
    # text off her own phone even though no `places` entry matched.
    ev = _event_insert("uid-800@icloud.com", "Стоматолог",
                        "2037-07-20T13:00:00+00:00",
                        location="Стоматология, Абая 150")
    pl = _plan_insert("uid-801@icloud.com", "Забрать анализы", "2037-07-31",
                       location="Инвитро, Сейфуллина 500")
    extcal.apply_changes(
        db, _changeset(events_insert=[ev], plans_insert=[pl]), {})

    event_row = _get_event_by_uid(db, ev["external_uid"])
    plan_row = _get_plan_by_uid(db, pl["external_uid"])

    got_event = cal.get(db, event_row["id"])
    assert got_event["external_location"] == "Стоматология, Абая 150"
    got_plan = plans.get(db, plan_row["id"])
    assert got_plan["external_location"] == "Инвитро, Сейфуллина 500"

    # and through the list-shaped read paths the digest/day views use
    day = cal.day(db, "2037-07-20")
    assert [e["external_location"] for e in day if e["id"] == event_row["id"]] == \
        ["Стоматология, Абая 150"]
    open_plans = plans.list_open(db)
    assert [p["external_location"] for p in open_plans if p["id"] == plan_row["id"]] == \
        ["Инвитро, Сейфуллина 500"]


def test_round_trip_timed_event_settles_to_zero_changes(db):
    # The real idempotency question, end to end: plan_changes decides,
    # apply_changes writes, T6's snapshot (`dict(row)` over `SELECT *`)
    # reads back, plan_changes decides again on the SAME occurrence. With
    # the raw text in its own column this MUST be empty -- with it hidden
    # in `notes` behind a delimiter, every round of this task had to trust
    # that a parse and a re-serialize agreed.
    occ = _occ("uid-900@icloud.com", "Стоматолог", "2037-07-20T13:00:00+00:00",
                "2037-07-20T14:00:00+00:00", location="Стоматология, Абая 150")
    now = "2037-07-19T06:00:00+00:00"

    cs1 = extcal.plan_changes([occ], {"events": [], "plans": []}, now)
    assert len(cs1["events"]["insert"]) == 1
    counts1 = extcal.apply_changes(db, cs1, {})
    assert counts1["events_inserted"] == 1
    assert counts1["errors"] == []

    cs2 = extcal.plan_changes([occ], _snapshot_from_db(db), now)
    assert cs2["events"]["insert"] == []
    assert cs2["events"]["update"] == []
    assert cs2["events"]["cancel"] == []
    counts2 = extcal.apply_changes(db, cs2, {})
    assert counts2["events_inserted"] == 0
    assert counts2["events_updated"] == 0
    assert counts2["errors"] == []


def test_round_trip_all_day_plan_settles_to_zero_changes(db):
    start = _all_day_utc("2037-07-31")
    occ = _occ("uid-901@icloud.com", "Купить подарок", start, end_utc=start,
                all_day=True, location="Мега, 2 этаж")
    now = "2037-07-20T06:00:00+00:00"

    cs1 = extcal.plan_changes([occ], {"events": [], "plans": []}, now)
    assert len(cs1["plans"]["insert"]) == 1
    assert extcal.apply_changes(db, cs1, {})["plans_inserted"] == 1

    cs2 = extcal.plan_changes([occ], _snapshot_from_db(db), now)
    assert cs2["plans"]["insert"] == []
    assert cs2["plans"]["update"] == []
    assert cs2["plans"]["drop"] == []


def test_round_trip_is_stable_even_with_a_human_note_attached(db):
    # A human note on the row must not make the sync think anything
    # changed -- the diff has no reason to look at `notes` at all now, and
    # this pins that it doesn't.
    occ = _occ("uid-902@icloud.com", "Стоматолог", "2037-07-20T13:00:00+00:00",
                "2037-07-20T14:00:00+00:00", location="Стоматология, Абая 150")
    now = "2037-07-19T06:00:00+00:00"
    extcal.apply_changes(
        db, extcal.plan_changes([occ], {"events": [], "plans": []}, now), {})

    event_id = _snapshot_from_db(db)["events"][0]["id"]
    cal.update(db, event_id, notes="взяла паспорт и полис")
    db.commit()

    cs = extcal.plan_changes([occ], _snapshot_from_db(db), now)
    assert cs["events"]["update"] == []
    assert cs["events"]["insert"] == []
    assert cs["events"]["cancel"] == []


def test_round_trip_detects_a_real_location_edit_on_the_phone(db):
    # The other half of the same contract: the round trip must be quiet on
    # no change, and must NOT be quiet on a real one -- otherwise "zero
    # updates" would be a vacuous property.
    occ = _occ("uid-903@icloud.com", "Стоматолог", "2037-07-20T13:00:00+00:00",
                "2037-07-20T14:00:00+00:00", location="Клиника А")
    now = "2037-07-19T06:00:00+00:00"
    extcal.apply_changes(
        db, extcal.plan_changes([occ], {"events": [], "plans": []}, now), {})

    moved = dict(occ, location="Клиника Б, каб. 12")
    cs = extcal.plan_changes([moved], _snapshot_from_db(db), now)
    assert len(cs["events"]["update"]) == 1
    assert cs["events"]["update"][0]["changes"]["location"] == (
        "Клиника А", "Клиника Б, каб. 12")

    extcal.apply_changes(db, cs, {})
    event_id = _snapshot_from_db(db)["events"][0]["id"]
    assert cal.get(db, event_id)["external_location"] == "Клиника Б, каб. 12"
    # ...and it settles immediately afterwards
    assert extcal.plan_changes([moved], _snapshot_from_db(db), now)[
        "events"]["update"] == []


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
