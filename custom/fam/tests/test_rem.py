import json

import pytest

from fam import audit, cal, people, places, rem


def _seed_people(db):
    people.add(db, "Тая", slug="taya")
    people.add(db, "Амина", slug="amina")
    people.add(db, "Денис", slug="denis")
    db.commit()


@pytest.fixture()
def conn_with_taya_event(db):
    """A db with rules seeded and one future active event whose sole
    participant is Тая -- applicable_rules() precedence gives it just the
    slug:taya chain (build_stages(60): a mix of prepare and leave kinds),
    per test_regenerate_writes_stage_kind.
    """
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "С Таей", "2037-07-20T05:00:00+00:00",
                participants=["Тая"])
    db.commit()
    return db, e


@pytest.fixture()
def conn_with_future_default_event(db):
    """A db with the pre-2c rule content seeded (old 2-stage default/taya
    shape) and one future active event with no slug-scoped participant,
    so it picks up the (pre-migration) default rule -- migrate_rules_2c
    then reseeds default/slug:taya to the 2c escalation chains and must
    regenerate this event's reminders against the new content.
    """
    _seed_people(db)
    rem._seed_rule(db, "default", [
        {"anchor": "start", "offset_min": -60, "label": "скоро событие"},
        {"anchor": "leave_at", "offset_min": 0, "label": "пора выходить"},
    ])
    rem._seed_rule(db, "slug:taya", [
        {"anchor": "leave_at", "offset_min": -45, "label": "Тае пора собираться"},
    ])
    rem._seed_rule(db, "slug:amina", [])
    db.commit()
    e = cal.add(db, "Без Таи", "2037-07-20T05:00:00+00:00",
                participants=["Денис"])
    db.commit()
    return db, e


# ---- leave_at arithmetic ----

def test_leave_at_event_override_beats_place(db):
    # 3a road priority: travel_min_road unset here, so falls through to the
    # manual event override -- this rung of the ladder is unaffected by 3a.
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.execute("UPDATE events SET travel_min=5 WHERE id=?", (e["id"],))
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:55:00+00:00"


def test_leave_at_place_fallback(db):
    # 3a road priority: no travel_min_road, no manual travel_min -- falls
    # through to the place's travel_min, same as pre-3a.
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:40:00+00:00"


def test_leave_at_no_place_is_zero(db):
    # 3a road priority: no road, no manual, no place -- bottom rung, 0.
    _seed_people(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == event["start_utc"] == "2026-07-15T05:00:00+00:00"


# Carry-over from T2 review, finding 2: an explicit event-level travel_min=0
# must still win over a nonzero place travel_min -- pins the "is None" (not
# "is falsy") precedence check in leave_at().
def test_leave_at_event_travel_zero_overrides_nonzero_place(db):
    # 3a road priority: travel_min_road unset -- falls through to manual;
    # the "is None" (not "is falsy") check still governs this rung.
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.execute("UPDATE events SET travel_min=0 WHERE id=?", (e["id"],))
    db.commit()

    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == event["start_utc"] == "2026-07-15T05:00:00+00:00"


# ---- leave_at 3a road priority ladder ----
# Priority: travel_min_road (computed, with traffic) beats travel_min
# (manual override) beats place.travel_min beats 0. One event mutated
# through all four rungs, per file convention (see the four tests above
# for the pre-3a manual/place/zero rungs individually).

def test_leave_at_road_beats_manual_beats_place_beats_zero(db):
    _seed_people(db)
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", place="Клиника")
    db.commit()

    # Rung 1: place.travel_min only -> 20 min.
    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:40:00+00:00"

    # Rung 2: manual travel_min beats place.
    db.execute("UPDATE events SET travel_min=5 WHERE id=?", (e["id"],))
    db.commit()
    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:55:00+00:00"

    # Rung 3: computed travel_min_road beats manual travel_min.
    db.execute("UPDATE events SET travel_min_road=35 WHERE id=?", (e["id"],))
    db.commit()
    event = cal.get(db, e["id"])
    assert rem.leave_at(db, event) == "2026-07-15T04:25:00+00:00"

    # Rung 4: travel_min_road=0 still wins over nonzero manual/place (the
    # "is None" not "is falsy" check applies at every rung).
    db.execute("UPDATE events SET travel_min_road=0 WHERE id=?", (e["id"],))
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
    # 2c precedence: slug:taya has non-empty stages, so it REPLACES
    # default rather than stacking with it (was {"default", "slug:taya"}).
    assert scopes_with == {"slug:taya"}
    for r in rem.applicable_rules(db, cal.get(db, e_with_taya["id"])):
        assert isinstance(r["stages"], list)


# ---- applicable_rules precedence (2c) ----

def test_applicable_rules_slug_rule_suppresses_default(db):
    # A specific (slug-scoped) rule with non-empty stages replaces the
    # default rule rather than stacking with it.
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    e = cal.add(db, "С Таей", "2026-07-15T06:00:00+00:00",
                participants=["Тая"])
    db.commit()

    rules = rem.applicable_rules(db, cal.get(db, e["id"]))
    scopes = {r["scope"] for r in rules}
    assert scopes == {"slug:taya"}


def test_applicable_rules_default_when_no_slug_match(db):
    # An event with no slug-scoped participants (or none with a matching
    # rule) falls back to the default rule.
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    e = cal.add(db, "Без слагов", "2026-07-15T05:00:00+00:00",
                participants=["Денис"])
    db.commit()

    rules = rem.applicable_rules(db, cal.get(db, e["id"]))
    assert {r["scope"] for r in rules} == {"default"}


def test_applicable_rules_empty_slug_rule_does_not_claim_precedence(db):
    # slug:amina is seeded with empty stages (inert reserve) -- an event
    # with only Амина as participant must still get the default chain,
    # not zero reminders.
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    e = cal.add(db, "Только Амина", "2026-07-15T05:00:00+00:00",
                participants=["Амина"])
    db.commit()

    rules = rem.applicable_rules(db, cal.get(db, e["id"]))
    assert {r["scope"] for r in rules} == {"default", "slug:amina"}


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

    # 2c: default/taya stages come from build_stages() (escalation
    # chains) rather than a hand-written two-stage list -- see
    # test_build_stages_lead_30_countdown_wins_collision/lead_60 below for
    # the canonical shape pin; this test just confirms seeding wires them.
    default_stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope='default'"
    ).fetchone()["stages"])
    assert default_stages == rem.build_stages(30)

    taya_stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope='slug:taya'"
    ).fetchone()["stages"])
    assert taya_stages == rem.build_stages(60)

    # slug:amina ships as an inert reserve -- empty stages, no reminders
    # generated until an admin populates it.
    amina_stages = json.loads(db.execute(
        "SELECT stages FROM reminder_rules WHERE scope='slug:amina'"
    ).fetchone()["stages"])
    assert amina_stages == []


# ---- migrate_rules_2c ----

def test_migrate_rules_2c_reseeds_and_regenerates(conn_with_future_default_event):
    conn, event = conn_with_future_default_event
    rem.migrate_rules_2c(conn)
    rules = {r["scope"]: r["stages"] for r in rem.list_rules(conn)}
    assert rules["default"] == rem.build_stages(30)
    assert rules["slug:taya"] == rem.build_stages(60)
    # future active event is regenerated against the new escalation chain
    rems = rem.list_reminders(conn, event_id=event["id"])
    assert len(rems) == 4                    # D=30: 30/25/15/0

    audit_rows = audit.query(conn, since_utc=None, kind_prefix="rem.migrate_2c",
                              grep=None, limit=10)
    assert {r["payload"]["scope"] for r in audit_rows} == {"default", "slug:taya"}

    # repeat call is a no-op (meta-гвард rules_version='2c')
    before = [dict(r) for r in rems]
    regenerated = rem.migrate_rules_2c(conn)
    assert regenerated == 0
    assert [dict(r) for r in rem.list_reminders(conn, event_id=event["id"])] == before


def test_migrate_rules_2c_returns_regenerated_count(conn_with_future_default_event):
    conn, event = conn_with_future_default_event
    regenerated = rem.migrate_rules_2c(conn)
    assert regenerated == 1


# ---- regenerate ----

def test_regenerate_writes_stage_kind(conn_with_taya_event):
    conn, event = conn_with_taya_event
    rem.regenerate(conn, event["id"])
    kinds = {r["kind"] for r in rem.list_reminders(conn, event_id=event["id"])}
    assert kinds == {"prepare", "leave"}


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
        "SELECT * FROM reminders WHERE event_id=? ORDER BY fire_at_utc",
        (e["id"],)).fetchall()
    # default (2c) = build_stages(30): offsets -30/-25/-15/0 from leave_at
    # (== start, no travel). -30min stage fires exactly at `now` (05:00) --
    # fire_dt <= now_dt is skipped, not just strictly past -> the other
    # three (-25/-15/0) are created.
    assert created == 3
    assert len(rows) == 3
    assert [r["fire_at_utc"] for r in rows] == [
        "2026-07-20T05:05:00+00:00",
        "2026-07-20T05:15:00+00:00",
        "2026-07-20T05:30:00+00:00",
    ]
    assert rows[-1]["label"] == "пора выходить"
    assert rows[-1]["fire_at_utc"] == "2026-07-20T05:30:00+00:00"


def test_regenerate_on_reschedule_moves_pending_keeps_sent(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()

    now = "2026-07-19T00:00:00+00:00"
    start1 = "2026-07-20T05:00:00+00:00"
    e = cal.add(db, "Событие", start1)
    db.commit()

    # default (2c) = build_stages(30): 4 stages at offsets -30/-25/-15/0
    # from leave_at (== start1, no travel) -> all 4 future relative to now.
    created = rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    assert created == 4

    rows = db.execute(
        "SELECT * FROM reminders WHERE event_id=? ORDER BY fire_at_utc",
        (e["id"],)).fetchall()
    assert len(rows) == 4

    # simulate the earliest stage having already fired and been sent
    sent_row = dict(rows[0])
    db.execute(
        "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
        (now, sent_row["id"]))
    db.commit()
    other_pending_fire_ats = {r["fire_at_utc"] for r in rows[1:]}

    # reschedule the event forward
    start2 = "2026-07-21T05:00:00+00:00"
    cal.update(db, e["id"], start_utc=start2)
    db.commit()

    created2 = rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    assert created2 == 4

    all_rows = [dict(r) for r in db.execute(
        "SELECT * FROM reminders WHERE event_id=?", (e["id"],)).fetchall()]
    by_status = {}
    for r in all_rows:
        by_status.setdefault(r["status"], []).append(r)

    # sent row survived untouched
    assert len(by_status.get("sent", [])) == 1
    assert by_status["sent"][0]["id"] == sent_row["id"]
    assert by_status["sent"][0]["fire_at_utc"] == sent_row["fire_at_utc"]

    # old pending rows' fire times are gone; four new pending rows reflect
    # the new start (fire_at moved, not just row identities)
    pending = by_status.get("pending", [])
    assert len(pending) == 4
    fire_times = sorted(p["fire_at_utc"] for p in pending)
    assert not (set(fire_times) & other_pending_fire_ats)
    assert fire_times == [
        "2026-07-21T04:30:00+00:00",  # leave_at - 30min
        "2026-07-21T04:35:00+00:00",  # leave_at - 25min
        "2026-07-21T04:45:00+00:00",  # leave_at - 15min
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
    assert count == 4  # default (2c) = build_stages(30), 4 stages

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],)).fetchall()}
    assert statuses == {"acked"}

    rows = audit.query(db, since_utc=None, kind_prefix="rem.ack", grep=None,
                        limit=1)
    assert rows[0]["payload"]["event_id"] == e["id"]
    assert rows[0]["payload"]["count"] == 4


def test_ack_scope_prepare_leaves_leave_stages(conn_with_taya_event):
    conn, event = conn_with_taya_event
    rem.regenerate(conn, event["id"])
    count = rem.ack_chain(conn, event["id"], scope="prepare")
    assert count == 3                                    # D=60: три prepare
    left = rem.list_reminders(conn, event_id=event["id"])
    assert {r["kind"] for r in left if r["status"] == "pending"} == {"leave"}
    assert {r["kind"] for r in left if r["status"] == "acked"} == {"prepare"}


def test_ack_scope_all_default_unchanged(conn_with_taya_event):
    conn, event = conn_with_taya_event
    rem.regenerate(conn, event["id"])
    assert rem.ack_chain(conn, event["id"]) == 6
    assert all(r["status"] == "acked"
               for r in rem.list_reminders(conn, event_id=event["id"]))


def test_ack_chain_unknown_scope_raises(conn_with_taya_event):
    conn, event = conn_with_taya_event
    rem.regenerate(conn, event["id"])
    with pytest.raises(ValueError):
        rem.ack_chain(conn, event["id"], scope="bogus")


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
    assert count == 4  # default (2c) = build_stages(30), 4 stages

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],)).fetchall()}
    assert statuses == {"cancelled"}

    rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_chain",
                        grep=None, limit=1)
    assert rows[0]["payload"]["event_id"] == e["id"]
    assert rows[0]["payload"]["count"] == 4


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
    assert len(only_e1) == 4  # default (2c) = build_stages(30), 4 stages
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


# ---- active_chains (Task 11: reminder-reaction ack fix) ----
#
# A conversational reaction ("уже выходим") arrives in a turn that never
# saw the reminder that triggered it -- reminders are delivered out-of-band
# by the tick (a separate `hermes send`), not by this conversation. The
# skill resolves "which event?" via `fam rem active`, i.e. active_chains():
# distinct events that still have >=1 pending reminder ("a chain in
# progress"). sent/acked/cancelled rows never make an event show up here on
# their own -- only a still-pending row does.

def test_active_chains_mixed_sent_and_pending_counts(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    e = cal.add(db, "Событие", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e["id"], now_utc=now)
    db.commit()

    rows = db.execute(
        "SELECT id, fire_at_utc FROM reminders WHERE event_id=? "
        "ORDER BY fire_at_utc", (e["id"],)).fetchall()
    # default (2c) = build_stages(30): 4 stages
    assert len(rows) == 4
    # simulate the earliest stage having already fired and been sent by
    # the tick -- exactly the "mark stage-0 sent" setup the E2E smoke test
    # uses.
    db.execute("UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
               (now, rows[0]["id"]))
    db.commit()

    chains = rem.active_chains(db)

    assert len(chains) == 1
    c = chains[0]
    assert c["event_id"] == e["id"]
    assert c["title"] == "Событие"
    assert c["pending_count"] == 3
    assert c["sent_count"] == 1
    assert c["start_local"] == cal._to_local_iso(e["start_utc"])
    assert c["next_fire_local"] == cal._to_local_iso(rows[1]["fire_at_utc"])


def test_active_chains_fully_acked_event_absent(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    e = cal.add(db, "Подтверждено", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    rem.ack_chain(db, e["id"])
    db.commit()

    assert rem.active_chains(db) == []


def test_active_chains_cancelled_event_absent(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    e = cal.add(db, "Отменено", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e["id"], now_utc=now)
    db.commit()
    rem.cancel_chain(db, e["id"])
    db.commit()

    assert rem.active_chains(db) == []


def test_active_chains_orders_by_next_fire_ascending(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    now = "2026-07-19T00:00:00+00:00"
    # intentionally added out of chronological order, so a correct result
    # pins the ORDER BY rather than incidentally matching insertion order.
    e_later = cal.add(db, "Позже", "2026-07-25T05:00:00+00:00")
    e_sooner = cal.add(db, "Раньше", "2026-07-20T05:00:00+00:00")
    db.commit()
    rem.regenerate(db, e_later["id"], now_utc=now)
    rem.regenerate(db, e_sooner["id"], now_utc=now)
    db.commit()

    chains = rem.active_chains(db)

    assert [c["event_id"] for c in chains] == [e_sooner["id"], e_later["id"]]


def test_active_chains_empty_when_no_reminders_at_all(db):
    # no reminder_rules seeded -> applicable_rules() matches nothing ->
    # cal.add()'s regenerate hook creates 0 reminders for this event.
    _seed_people(db)
    cal.add(db, "Без напоминаний", "2026-07-20T05:00:00+00:00")
    db.commit()

    assert rem.active_chains(db) == []


def test_active_chains_empty_on_fresh_db(db):
    assert rem.active_chains(db) == []


def test_build_stages_lead_60_matches_denis_spec():
    stages = rem.build_stages(60)
    assert [(s["offset_min"], s["label"], s["kind"]) for s in stages] == [
        (-60, "пора собираться", "prepare"),
        (-55, "уже начали собираться?", "prepare"),
        (-45, "не отвлекаемся, собираемся", "prepare"),
        (-30, "выходить через полчаса", "leave"),
        (-15, "выходить через 15 минут", "leave"),
        (0, "пора выходить", "leave"),
    ]
    assert all(s["anchor"] == "leave_at" for s in stages)


def test_build_stages_lead_30_countdown_wins_collision():
    # D-15 == 15 совпадает с countdown-15 — побеждает countdown-лейбл
    stages = rem.build_stages(30)
    assert [(s["offset_min"], s["label"], s["kind"]) for s in stages] == [
        (-30, "пора собираться", "prepare"),
        (-25, "уже начали собираться?", "prepare"),
        (-15, "выходить через 15 минут", "leave"),
        (0, "пора выходить", "leave"),
    ]


def test_build_stages_short_lead_degrades_gracefully():
    stages = rem.build_stages(15)
    assert [(s["offset_min"], s["label"], s["kind"]) for s in stages] == [
        (-15, "пора собираться", "prepare"),
        (-10, "уже начали собираться?", "prepare"),
        (0, "пора выходить", "leave"),
    ]
"""Task 4 (phase 7): prep_min precedence over reminder_rules. Appended to
test_rem.py -- these tests assume _seed_people/conn_with_taya_event
fixtures already defined above in the same module.
"""


def test_prep_min_overrides_default(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "Стрижка", "2037-07-20T05:00:00+00:00",
                participants=["Денис"], prep_min=120)
    db.commit()

    # reminders table has no offset_min column -- derive offsets from
    # fire_at_utc vs leave_at instead, mirroring existing tests' pattern.
    leave = rem.leave_at(db, cal.get(db, e["id"]))
    leave_dt = rem._parse_utc(leave)
    fires = db.execute(
        "SELECT fire_at_utc, rule_id FROM reminders WHERE event_id=? "
        "ORDER BY fire_at_utc", (e["id"],)).fetchall()
    offsets = {round((leave_dt - rem._parse_utc(r["fire_at_utc"])).total_seconds() / 60)
               for r in fires}
    assert offsets == {120, 115, 105, 30, 15, 0}
    # every synthesized reminder from a prep_min chain has rule_id=None
    # (no reminder_rules row backs it) per the brief's schema check.
    assert all(r["rule_id"] is None for r in fires)


def test_prep_min_overrides_taya(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "С Таей", "2037-07-20T05:00:00+00:00",
                participants=["Тая"], prep_min=90)
    db.commit()

    leave_dt = rem._parse_utc(rem.leave_at(db, cal.get(db, e["id"])))
    fires = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=?", (e["id"],)).fetchall()
    offsets = {round((leave_dt - rem._parse_utc(r["fire_at_utc"])).total_seconds() / 60)
               for r in fires}
    # build_stages(90) shape, NOT build_stages(60) (Taya's slug default).
    assert offsets == set(
        off for off, _ in [(s["offset_min"] * -1, None) for s in rem.build_stages(90)])


def test_no_prep_min_taya_unchanged(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "С Таей", "2037-07-20T05:00:00+00:00",
                participants=["Тая"])
    db.commit()

    leave_dt = rem._parse_utc(rem.leave_at(db, cal.get(db, e["id"])))
    fires = db.execute(
        "SELECT fire_at_utc, rule_id FROM reminders WHERE event_id=?", (e["id"],)).fetchall()
    offsets = {round((leave_dt - rem._parse_utc(r["fire_at_utc"])).total_seconds() / 60)
               for r in fires}
    expected = {off for off, _ in [(s["offset_min"] * -1, None) for s in rem.build_stages(60)]}
    assert offsets == expected
    # regression: without prep_min, reminders still come from a real rule row.
    assert all(r["rule_id"] is not None for r in fires)


def test_update_prep_min_regenerates(db):
    _seed_people(db)
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "Стрижка", "2037-07-20T05:00:00+00:00",
                participants=["Денис"])
    db.commit()
    before = db.execute(
        "SELECT rule_id FROM reminders WHERE event_id=? AND status='pending'",
        (e["id"],)).fetchall()
    assert before  # sanity: default rule produced pending reminders
    assert all(r["rule_id"] is not None for r in before)  # rule-based, pre-update

    cal.update(db, e["id"], prep_min=45)
    db.commit()

    after = db.execute(
        "SELECT fire_at_utc, rule_id FROM reminders WHERE event_id=? AND status='pending'",
        (e["id"],)).fetchall()
    # regenerated from the synthetic prep_min chain (rule_id=None), not
    # the pre-update rule-based reminders -- fire_at set differs (build_
    # stages(45) vs default's build_stages(30)) and rule_id is now NULL.
    assert after
    assert all(r["rule_id"] is None for r in after)
    leave_dt = rem._parse_utc(rem.leave_at(db, cal.get(db, e["id"])))
    offsets = {round((leave_dt - rem._parse_utc(r["fire_at_utc"])).total_seconds() / 60)
               for r in after}
    assert offsets == {off for off, _ in
                        [(s["offset_min"] * -1, None) for s in rem.build_stages(45)]}


