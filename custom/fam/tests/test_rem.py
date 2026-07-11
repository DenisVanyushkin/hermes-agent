import json

from fam import audit, cal, people, places, rem


def _seed_people(db):
    people.add(db, "Тая", slug="taya")
    people.add(db, "Амина", slug="amina")
    people.add(db, "Денис", slug="denis")
    db.commit()


# ---- leave_at arithmetic ----

def test_leave_at_event_override_beats_place(db):
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.execute("UPDATE events SET travel_min=5 WHERE id=?", (e["id"],))
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:55:00+00:00"


def test_leave_at_place_fallback(db):
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:40:00+00:00"


def test_leave_at_no_place_is_zero(db):
    _seed_people(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == event["start_utc"] == "2026-07-15T05:00:00+00:00"


# Carry-over from T2 review, finding 2: an explicit event-level travel_min=0
# must still win over a nonzero place travel_min -- pins the "is None" (not
# "is falsy") precedence check in leave_at().
def test_leave_at_event_travel_zero_overrides_nonzero_place(db):
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.execute("UPDATE events SET travel_min=0 WHERE id=?", (e["id"],))
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == event["start_utc"] == "2026-07-15T05:00:00+00:00"


# ---- applicable_rules scoping ----

def test_applicable_rules_taya_scope_only_when_participant(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    e_no_taya = cal.add(db, "Без Таи", "2026-07-15T05:00:00+00:00",
                         participants=["Амина"])
    e_with_taya = cal.add(db, "С Таей", "2026-07-15T06:00:00+00:00",
                           participants=["Тая"])
    db.commit()

    scopes_no = {r["scope"] for r in
                 rem.applicable_rules(db, cal.get(db, e_no_taya["id"]))}
    scopes_with = {r["scope"] for r in
                   rem.applicable_rules(db, cal.get(db, e_with_taya["id"]))}

    # e_no_taya's participant is Амина (slug=amina), so slug:amina's
    # (empty-stages) reserve rule legitimately matches too -- scope
    # matching doesn't care that the rule currently has 0 stages.
    assert scopes_no == {"default", "slug:amina"}
    assert scopes_with == {"default", "slug:taya"}
    for r in rem.applicable_rules(db, cal.get(db, e_with_taya["id"])):
        assert isinstance(r["stages"], list)


def test_applicable_rules_skips_disabled(db):
    _seed_people(db)
    db.execute(
        "INSERT INTO reminder_rules(scope, stages, enabled, created_at) "
        "VALUES (?,?,?,?)",
        ("default", "[]", 0, "2026-01-01T00:00:00+00:00"),
    )
    e = cal.add(db, "Событие", "2026-07-20T05:00:00+00:00")
    db.commit()

    assert rem.applicable_rules(db, cal.get(db, e["id"])) == []


# Carry-over from T2 review, finding 3: a rule row with a malformed stages
# column (e.g. hand-edited by an admin) must never raise out of
# applicable_rules -- a bad rule must not break event creation. It is
# skipped and audited instead.
def test_applicable_rules_skips_malformed_stages_and_audits(db):
    _seed_people(db)
    row = db.execute(
        "INSERT INTO reminder_rules(scope, stages, enabled, created_at) "
        "VALUES (?,?,?,?)",
        ("default", "not-json", 1, "2026-01-01T00:00:00+00:00"),
    )
    bad_rule_id = row.lastrowid
    db.commit()

    rules = rem.applicable_rules(db, {"participants": []})

    assert rules == []
    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.rule_error",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"]["rule_id"] == bad_rule_id


def test_broken_rule_stages_does_not_break_event_creation(db):
    _seed_people(db)
    db.execute(
        "INSERT INTO reminder_rules(scope, stages, enabled, created_at) "
        "VALUES (?,?,?,?)",
        ("default", "{not valid json", 1, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()

    # must not raise despite the malformed rule -- cal.add's regenerate
    # hook calls applicable_rules internally.
    e = cal.add(db, "Событие", "2026-07-20T05:00:00+00:00")
    db.commit()

    assert e["id"] is not None
    rows = audit.query(db, since_utc=None, kind_prefix="rem.rule_error",
                        grep=None, limit=10)
    assert len(rows) >= 1


# ---- seed_default_rules ----

def test_seed_default_rules_idempotent_and_audited_once(db):
    rem.seed_default_rules(db)
    db.commit()
    rem.seed_default_rules(db)  # second call: no-op, no re-insert
    db.commit()

    rows = db.execute(
        "SELECT scope FROM reminder_rules ORDER BY scope").fetchall()
    assert [r["scope"] for r in rows] == ["default", "slug:amina", "slug:taya"]

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.seed",
                              grep=None, limit=10)
    assert len(audit_rows) == 3


def test_seed_default_rules_stage_content(db):
    rem.seed_default_rules(db)
    db.commit()

    default_stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope='default'"
    ).fetchone()["stages"])
    assert default_stages == [
        {"anchor": "start", "offset_min": -60, "label": "скоро событие"},
        {"anchor": "leave_at", "offset_min": 0, "label": "пора выходить"},
    ]

    taya_stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope='slug:taya'"
    ).fetchone()["stages"])
    assert taya_stages == [
        {"anchor": "leave_at", "offset_min": -45, "label": "Тае пора собираться"},
    ]

    # slug:amina ships as an inert reserve -- empty stages, no reminders
    # generated until an admin populates it.
    amina_stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope='slug:amina'"
    ).fetchone()["stages"])
    assert amina_stages == []


# ---- regenerate ----

def test_past_stage_not_created(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    now = "2026-07-20T05:00:00+00:00"
    start = "2026-07-20T05:30:00+00:00"  # 30 min from "now"
    e = cal.add(db, "Скоро", start)
    db.commit()

    created = rem.regenerate(db, e["id"], now_utc=now)
    db.commit()

    rows = db.execute(
        "SELECT * FROM reminders WHERE event_id=?", (e["id"],)).fetchall()
    # -60min stage would fire at 04:30 (past) -> skipped;
    # leave_at (offset 0, no travel) fires at start 05:30 (future) -> created
    assert created == 1
    assert len(rows) == 1
    assert rows[0]["label"] == "пора выходить"
    assert rows[0]["fire_at_utc"] == "2026-07-20T05:30:00+00:00"


def test_regenerate_on_reschedule_moves_pending_keeps_sent(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    now = "2026-07-19T00:00:00+00:00"
    start1 = "2026-07-20T05:00:00+00:00"
    e = cal.add(db, "Событие", start1)
    db.commit()

    created = rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    assert created == 2

    rows = db.execute(
        "SELECT * FROM reminders WHERE event_id=? ORDER BY fire_at_utc",
        (e["id"],)).fetchall()
    assert len(rows) == 2

    # simulate the earlier stage having already fired and been sent
    sent_row = dict(rows[0])
    db.execute(
        "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
        (now, sent_row["id"]))
    db.commit()
    other_pending_fire_at = rows[1]["fire_at_utc"]

    # reschedule the event forward
    start2 = "2026-07-21T05:00:00+00:00"
    cal.update(db, e["id"], start_utc=start2)
    db.commit()

    created2 = rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    assert created2 == 2

    all_rows = [dict(r) for r in db.execute(
        "SELECT * FROM reminders WHERE event_id=?", (e["id"],)).fetchall()]
    by_status = {}
    for r in all_rows:
        by_status.setdefault(r["status"], []).append(r)

    # sent row survived untouched
    assert len(by_status.get("sent", [])) == 1
    assert by_status["sent"][0]["id"] == sent_row["id"]
    assert by_status["sent"][0]["fire_at_utc"] == sent_row["fire_at_utc"]

    # old pending row's fire time is gone; two new pending rows reflect
    # the new start (fire_at moved, not just row identities)
    pending = by_status.get("pending", [])
    assert len(pending) == 2
    fire_times = sorted(p["fire_at_utc"] for p in pending)
    assert other_pending_fire_at not in fire_times
    assert fire_times == [
        "2026-07-21T04:00:00+00:00",  # start - 60min
        "2026-07-21T05:00:00+00:00",  # leave_at (travel 0) == start
    ]


def test_regenerate_on_cancelled_event_clears_pending(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    now = "2026-07-19T00:00:00+00:00"
    e = cal.add(db, "Событие", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e["id"], now_utc=now)
    db.commit()

    # Flip the event's status directly via SQL rather than cal.cancel() --
    # cal.cancel() now (Task 3) also fires rem.cancel_chain, which would
    # transition these pending rows to 'cancelled' itself and mask what
    # this test is actually pinning: regenerate()'s own contract that a
    # non-active event yields a clean delete-and-no-recreate.
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (e["id"],))
    db.commit()

    created = rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    assert created == 0

    rows = db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],)).fetchall()
    assert rows == []


# ---- ack_chain / cancel_chain ----

def test_ack_chain_counts_and_audit(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    e = cal.add(db, "Событие", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e["id"], now_utc=now)
    db.commit()

    count = rem.ack_chain(db, e["id"])
    db.commit()
    assert count == 2

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],)).fetchall()}
    assert statuses == {"acked"}

    rows = audit.query(db, since_utc=None, kind_prefix="rem.ack", grep=None,
                        limit=1)
    assert rows[0]["payload"]["event_id"] == e["id"]
    assert rows[0]["payload"]["count"] == 2


def test_cancel_chain_counts_and_audit(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    e = cal.add(db, "Событие", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e["id"], now_utc=now)
    db.commit()

    count = rem.cancel_chain(db, e["id"])
    db.commit()
    assert count == 2

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],)).fetchall()}
    assert statuses == {"cancelled"}

    rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_chain",
                        grep=None, limit=1)
    assert rows[0]["payload"]["event_id"] == e["id"]
    assert rows[0]["payload"]["count"] == 2


# ---- list_reminders / list_rules (CLI-facing queries) ----

def test_list_reminders_filters_by_event_and_due(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    e1 = cal.add(db, "Раз", "2026-07-20T05:00:00+00:00")
    e2 = cal.add(db, "Два", "2026-07-21T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e1["id"], now_utc=now)
    rem.regenerate(db, e2["id"], now_utc=now)
    db.commit()

    only_e1 = rem.list_reminders(db, event_id=e1["id"])
    assert len(only_e1) == 2
    assert {r["event_id"] for r in only_e1} == {e1["id"]}

    # nothing is due yet (all fire_at_utc are in the future relative to `now`)
    assert rem.list_reminders(db, due=True, now_utc=now) == []

    # push one of e1's stages into the past relative to `now` and confirm
    # it (and only it) shows up as due
    past_row = only_e1[0]
    db.execute("UPDATE reminders SET fire_at_utc=? WHERE id=?",
               ("2026-07-18T00:00:00+00:00", past_row["id"]))
    db.commit()

    due = rem.list_reminders(db, due=True, now_utc=now)
    assert [r["id"] for r in due] == [past_row["id"]]


def test_list_rules_surfaces_malformed_stages_without_raising(db):
    rem.seed_default_rules(db)
    row = db.execute(
        "INSERT INTO reminder_rules(scope, stages, enabled, created_at) "
        "VALUES (?,?,?,?)",
        ("slug:broken", "not-json", 1, "2026-01-01T00:00:00+00:00"),
    )
    bad_id = row.lastrowid
    db.commit()

    rules = rem.list_rules(db)
    by_id = {r["id"]: r for r in rules}

    assert len(rules) == 4  # default, slug:taya, slug:amina, slug:broken
    assert by_id[bad_id]["stages_error"] is True
    assert by_id[bad_id]["stages"] == "not-json"  # left raw, not raised
