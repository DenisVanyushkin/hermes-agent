"""Task 5: applying an `extcal.plan_changes()` Changeset to the DB
(`extcal.apply_changes`), and the main invariant it exists to protect:
`rem.regenerate` never builds a reminder chain for an `owner='iphone'` row.

Every test here uses the `db` fixture (a fresh tmp-path sqlite file, see
conftest.py) -- never the live assistant.db. Changeset entries are built by
hand in the exact shape `extcal.plan_changes` documents/emits (see
test_extcal_reconcile.py's own fixtures for the same convention), so these
tests exercise apply_changes()'s actual contract, not a stand-in for it.
"""
import pytest

from fam import audit, cal, extcal, places, plans, rem


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


# ---------------------------------------------------------------------
# the main invariant: owner='iphone' never gets a reminder chain
# ---------------------------------------------------------------------

def test_insert_timed_event_is_owner_iphone_and_reminder_free(db):
    rem.seed_default_rules(db)
    db.commit()
    places.add(db, "Invictus")
    db.commit()

    entry = _event_insert("uid-100@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00",
                           "2037-07-20T14:00:00+00:00", location="Invictus")
    counts = extcal.apply_changes(db, _changeset(events_insert=[entry]), {})

    assert counts["events_inserted"] == 1
    assert counts["errors"] == []
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()
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

    imported = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()
    assert _reminder_count(db, imported["id"]) == 0


def test_update_moves_event_time_without_creating_reminders(db):
    rem.seed_default_rules(db)
    db.commit()

    entry = _event_insert("uid-102@icloud.com", "Йога",
                           "2037-07-20T13:00:00+00:00",
                           "2037-07-20T14:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()
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
# all-day -> plans
# ---------------------------------------------------------------------

def test_insert_all_day_occurrence_creates_open_plan_with_deadline(db):
    entry = _plan_insert("uid-200@icloud.com", "Купить подарок", "2037-07-31")
    counts = extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    assert counts["plans_inserted"] == 1

    row = db.execute(
        "SELECT * FROM plans WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()
    assert row is not None
    assert row["owner"] == "iphone"
    assert row["status"] == "open"
    assert row["deadline"] == "2037-07-31"


def test_drop_entry_marks_plan_dropped(db):
    entry = _plan_insert("uid-201@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM plans WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()

    drop_entry = {"id": row["id"], "external_uid": entry["external_uid"]}
    counts = extcal.apply_changes(db, _changeset(plans_drop=[drop_entry]), {})
    assert counts["plans_dropped"] == 1

    after = plans.get(db, row["id"])
    assert after["status"] == "dropped"


def test_plan_update_changes_title_deadline_and_location(db):
    places.add(db, "Дом")
    db.commit()
    entry = _plan_insert("uid-202@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM plans WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()

    update_entry = {
        "id": row["id"],
        "changes": {
            "title": ("Купить подарок", "Купить подарок маме"),
            "deadline": ("2037-07-31", "2037-08-02"),
            "location": ("", "Дом"),
        },
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[update_entry]), {})
    assert counts["plans_updated"] == 1

    after = plans.get(db, row["id"])
    assert after["title"] == "Купить подарок маме"
    assert after["deadline"] == "2037-08-02"
    assert after["place"]["name"] == "Дом"


# ---------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------

def test_cancel_entry_marks_event_cancelled_and_clears_pending_reminders(db):
    rem.seed_default_rules(db)
    db.commit()
    entry = _event_insert("uid-300@icloud.com", "Стоматолог",
                           "2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()

    cancel_entry = {"id": row["id"], "external_uid": entry["external_uid"]}
    counts = extcal.apply_changes(db, _changeset(events_cancel=[cancel_entry]), {})
    assert counts["events_cancelled"] == 1

    after = cal.get(db, row["id"])
    assert after["status"] == "cancelled"
    assert _reminder_count(db, row["id"]) == 0


# ---------------------------------------------------------------------
# external identity + href/etag attachment
# ---------------------------------------------------------------------

def test_insert_persists_external_identity_columns(db):
    entry = _event_insert("uid-400@icloud.com", "Тренировка",
                           "2037-07-20T13:00:00+00:00", seq=3,
                           recurrence_id="2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()
    assert row["owner"] == "iphone"
    assert row["external_uid"] == entry["external_uid"]
    assert row["external_seq"] == 3


def test_insert_attaches_href_and_etag_when_present_on_entry(db):
    entry = _event_insert("uid-401@icloud.com", "Тренировка",
                           "2037-07-20T13:00:00+00:00",
                           href="/cal/1/uid-401.ics", etag='"abc123"')
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()
    assert row["external_href"] == "/cal/1/uid-401.ics"
    assert row["external_etag"] == '"abc123"'


def test_update_does_not_clobber_href_etag_when_absent_from_entry(db):
    entry = _event_insert("uid-402@icloud.com", "Тренировка",
                           "2037-07-20T13:00:00+00:00",
                           href="/cal/1/uid-402.ics", etag='"v1"')
    extcal.apply_changes(db, _changeset(events_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()

    update_entry = {
        "id": row["id"],
        "changes": {"title": ("Тренировка", "Тренировка (зал)")},
    }
    extcal.apply_changes(db, _changeset(events_update=[update_entry]), {})
    after = db.execute("SELECT * FROM events WHERE id=?", (row["id"],)).fetchone()
    assert after["external_href"] == "/cal/1/uid-402.ics"
    assert after["external_etag"] == '"v1"'


# ---------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------

def test_each_successful_mutation_writes_cal_ext_apply_audit(db):
    rem.seed_default_rules(db)
    db.commit()

    ins = _event_insert("uid-500@icloud.com", "A", "2037-07-20T13:00:00+00:00")
    extcal.apply_changes(db, _changeset(events_insert=[ins]), {})
    row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (ins["external_uid"],)
    ).fetchone()

    upd = {"id": row["id"], "changes": {"title": ("A", "B")}}
    extcal.apply_changes(db, _changeset(events_update=[upd]), {})

    cancel = {"id": row["id"], "external_uid": ins["external_uid"]}
    extcal.apply_changes(db, _changeset(events_cancel=[cancel]), {})

    plan_ins = _plan_insert("uid-501@icloud.com", "P", "2037-08-01")
    extcal.apply_changes(db, _changeset(plans_insert=[plan_ins]), {})

    rows = audit.query(db, None, "cal.ext.apply", None, limit=50)
    actions = {(r["payload"]["branch"], r["payload"]["action"]) for r in rows}
    assert ("events", "insert") in actions
    assert ("events", "update") in actions
    assert ("events", "cancel") in actions
    assert ("plans", "insert") in actions


# ---------------------------------------------------------------------
# per-row guard: one bad row rolls back, the rest still apply
# ---------------------------------------------------------------------

def test_bad_row_rolls_back_and_does_not_block_other_rows(db):
    good = _event_insert("uid-600@icloud.com", "Хорошее", "2037-07-20T13:00:00+00:00")
    # "Неизвестное место" resolves to nothing -- cal.add raises
    # UnknownRefError before any insert for THIS entry only.
    bad = _event_insert("uid-601@icloud.com", "Плохое", "2037-07-20T14:00:00+00:00",
                         location="Неизвестное место")

    counts = extcal.apply_changes(
        db, _changeset(events_insert=[bad, good]), {})

    assert counts["events_inserted"] == 1
    assert len(counts["errors"]) == 1
    assert counts["errors"][0]["external_uid"] == bad["external_uid"]

    good_row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (good["external_uid"],)
    ).fetchone()
    assert good_row is not None

    bad_row = db.execute(
        "SELECT * FROM events WHERE external_uid=?", (bad["external_uid"],)
    ).fetchone()
    assert bad_row is None  # rolled back -- no half-written row left behind

    error_rows = audit.query(db, None, "cal.ext.apply_error", None, limit=10)
    assert len(error_rows) == 1
    assert error_rows[0]["payload"]["external_uid"] == bad["external_uid"]


def test_bad_plan_update_row_rolls_back_and_leaves_plan_unchanged(db):
    entry = _plan_insert("uid-602@icloud.com", "Купить подарок", "2037-07-31")
    extcal.apply_changes(db, _changeset(plans_insert=[entry]), {})
    row = db.execute(
        "SELECT * FROM plans WHERE external_uid=?", (entry["external_uid"],)
    ).fetchone()

    bad_update = {
        "id": row["id"],
        "changes": {
            "title": ("Купить подарок", "Купить подарок маме"),
            "location": ("", "Совсем неизвестное место"),
        },
    }
    counts = extcal.apply_changes(db, _changeset(plans_update=[bad_update]), {})
    assert counts["plans_updated"] == 0
    assert len(counts["errors"]) == 1

    after = plans.get(db, row["id"])
    # neither the title nor the place_id changed -- the whole row-level
    # UPDATE never committed, not just the place half of it.
    assert after["title"] == "Купить подарок"
    assert after["place"] is None


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
                    {"events": {"insert": [None, "junk", 5]}}):
        counts = extcal.apply_changes(db, garbage, {})
        assert counts["events_inserted"] == 0
        assert counts["errors"] == []
    assert db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 0
