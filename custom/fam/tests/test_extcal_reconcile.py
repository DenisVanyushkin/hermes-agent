"""Task 4: reconciliation (`extcal.plan_changes`).

Pure decision-layer tests -- no DB, no network, no system clock. Every
Occurrence fixture here is built by hand in the exact shape `expand()`
documents (`{uid, recurrence_id, title, start_utc, end_utc, all_day,
location, status, seq, has_alarm}`), and every `local_snapshot` fixture is
built by hand in the shape documented on `plan_changes` itself
(`{"events": [EventRow, ...], "plans": [PlanRow, ...]}` -- note the local
side's location key is `external_location`, schema v12's own column) --
neither touches
`cal.py`/`plans.py`/`db.py` at all, per the task boundary.
"""
from datetime import datetime, timezone

from fam import extcal
from fam.extcal import ALMATY

NOW = "2026-07-28T06:00:00+00:00"


def _occ(uid, title, start_utc, end_utc=None, all_day=False, location="",
         status=None, seq=0, has_alarm=False, recurrence_id=None):
    return {
        "uid": uid, "recurrence_id": recurrence_id, "title": title,
        "start_utc": start_utc, "end_utc": end_utc or start_utc,
        "all_day": all_day, "location": location, "status": status,
        "seq": seq, "has_alarm": has_alarm,
    }


def _all_day_utc(date_str):
    """The same VALUE=DATE -> UTC conversion `extcal._parse_dt_value` does:
    local Asia/Almaty midnight of `date_str`, in UTC."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(tzinfo=ALMATY).astimezone(timezone.utc).isoformat()


def _snap(events=None, plans=None):
    return {"events": events or [], "plans": plans or []}


def _event_row(id, uid, title, start_utc, end_utc=None, location="",
                status="active", owner="iphone", external_seq=0, recurrence_id=None):
    # `uid` (plain ICS uid, or None for a hermes row that never carries one)
    # is run through the SAME `_occurrence_key` encoding plan_changes itself
    # uses, so a fixture row genuinely links against a remote Occurrence
    # built from the same raw uid/recurrence_id -- exercising the real
    # length-prefixed contract instead of a hand-rolled stand-in for it.
    external_uid = extcal._occurrence_key(uid, recurrence_id) if uid else None
    # `external_location` (not `location`) is the snapshot key `_event_diff`
    # reads -- schema v12's own column name, so a real snapshot row is just
    # `dict(row)` over `SELECT * FROM events` with no mapping (Task 5
    # fix-round 4: the raw iCloud LOCATION text lives in its own
    # machine-owned column, never in the human-owned `notes`).
    return {
        "id": id, "owner": owner, "external_uid": external_uid,
        "external_seq": external_seq, "status": status, "title": title,
        "start_utc": start_utc, "end_utc": end_utc or start_utc,
        "external_location": location,
    }


def _plan_row(id, uid, title, deadline, location="", status="open",
              owner="iphone", recurrence_id=None):
    external_uid = extcal._occurrence_key(uid, recurrence_id) if uid else None
    return {
        "id": id, "owner": owner, "external_uid": external_uid,
        "status": status, "title": title, "deadline": deadline,
        "external_location": location,
    }


def _empty_changeset_shape(cs):
    assert set(cs.keys()) == {"events", "plans", "collisions"}
    assert set(cs["events"].keys()) == {"insert", "update", "cancel"}
    assert set(cs["plans"].keys()) == {"insert", "update", "drop"}


# ---------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------

def test_new_timed_occurrence_is_event_insert():
    occ = _occ("uid-1@icloud.com", "Йога", "2026-07-28T13:00:00+00:00",
                "2026-07-28T14:00:00+00:00", location="Invictus")
    cs = extcal.plan_changes([occ], _snap(), NOW)
    _empty_changeset_shape(cs)
    assert cs["events"]["insert"] == [{
        "title": "Йога", "start_utc": "2026-07-28T13:00:00+00:00",
        "end_utc": "2026-07-28T14:00:00+00:00", "location": "Invictus",
        "external_uid": extcal._occurrence_key("uid-1@icloud.com", None),
        "external_seq": 0, "owner": "iphone",
    }]
    assert cs["events"]["update"] == cs["events"]["cancel"] == []
    assert cs["plans"]["insert"] == cs["plans"]["update"] == cs["plans"]["drop"] == []
    assert cs["collisions"] == []


def test_new_single_day_all_day_occurrence_is_plan_insert_with_start_date():
    # No real DTEND -- 1h default duration (fam convention, see
    # _finalize_component) -- deadline is the START date.
    start = _all_day_utc("2026-07-31")
    occ = _occ("uid-2@icloud.com", "Купить подарок", start,
                end_utc=None, all_day=True)
    occ["end_utc"] = start  # explicit: same instant, <=1h "duration"
    cs = extcal.plan_changes([occ], _snap(), NOW)
    assert len(cs["plans"]["insert"]) == 1
    ins = cs["plans"]["insert"][0]
    assert ins["deadline"] == "2026-07-31"
    assert ins["owner"] == "iphone"
    assert ins["external_uid"] == extcal._occurrence_key("uid-2@icloud.com", None)
    assert cs["events"]["insert"] == []


def test_new_multi_day_all_day_occurrence_deadline_is_end_date():
    # RFC 5545 DTEND is end-EXCLUSIVE for all-day: a 3-day trip Jul 1-3
    # carries DTEND=Jul 4. deadline must land on Jul 3 (the real last day),
    # not Jul 4 (the exclusive DTEND date) and not Jul 1 (the start date).
    start = _all_day_utc("2026-07-01")
    end = _all_day_utc("2026-07-04")
    occ = _occ("uid-3@icloud.com", "Поездка", start, end, all_day=True)
    cs = extcal.plan_changes([occ], _snap(), NOW)
    assert len(cs["plans"]["insert"]) == 1
    assert cs["plans"]["insert"][0]["deadline"] == "2026-07-03"


# ---------------------------------------------------------------------
# update
# ---------------------------------------------------------------------

def test_changed_time_title_and_location_is_event_update():
    existing = _event_row(5, "uid-4@icloud.com", "Йога",
                           "2026-07-28T13:00:00+00:00",
                           "2026-07-28T14:00:00+00:00", location="Invictus")
    occ = _occ("uid-4@icloud.com", "Йога (зал)", "2026-07-28T13:30:00+00:00",
                "2026-07-28T14:30:00+00:00", location="Invictus 2")
    cs = extcal.plan_changes([occ], _snap(events=[existing]), NOW)
    assert cs["events"]["insert"] == [] and cs["events"]["cancel"] == []
    assert len(cs["events"]["update"]) == 1
    upd = cs["events"]["update"][0]
    assert upd["id"] == 5
    assert upd["changes"]["title"] == ("Йога", "Йога (зал)")
    assert upd["changes"]["start_utc"] == (
        "2026-07-28T13:00:00+00:00", "2026-07-28T13:30:00+00:00")
    assert upd["changes"]["location"] == ("Invictus", "Invictus 2")


def test_unchanged_occurrence_produces_no_update():
    existing = _event_row(6, "uid-5@icloud.com", "Тренировка",
                           "2026-07-28T13:00:00+00:00",
                           "2026-07-28T14:00:00+00:00", location="Invictus")
    occ = _occ("uid-5@icloud.com", "Тренировка", "2026-07-28T13:00:00+00:00",
                "2026-07-28T14:00:00+00:00", location="Invictus")
    cs = extcal.plan_changes([occ], _snap(events=[existing]), NOW)
    assert cs["events"]["update"] == []
    assert cs["events"]["insert"] == [] and cs["events"]["cancel"] == []


def test_changed_deadline_is_plan_update():
    existing = _plan_row(7, "uid-6@icloud.com", "Купить подарок", "2026-07-31")
    new_deadline_start = _all_day_utc("2026-08-02")
    occ = _occ("uid-6@icloud.com", "Купить подарок", new_deadline_start,
                end_utc=new_deadline_start, all_day=True)
    cs = extcal.plan_changes([occ], _snap(plans=[existing]), NOW)
    assert len(cs["plans"]["update"]) == 1
    upd = cs["plans"]["update"][0]
    assert upd["id"] == 7
    assert upd["changes"]["deadline"] == ("2026-07-31", "2026-08-02")


# ---------------------------------------------------------------------
# cancel / drop (disappeared from the phone)
# ---------------------------------------------------------------------

def test_disappeared_uid_is_event_cancel():
    existing = _event_row(8, "uid-7@icloud.com", "Стоматолог",
                           "2026-07-30T09:00:00+00:00")
    cs = extcal.plan_changes([], _snap(events=[existing]), NOW)
    assert cs["events"]["cancel"] == [
        {"id": 8, "external_uid": extcal._occurrence_key("uid-7@icloud.com", None)}]
    assert cs["events"]["insert"] == [] and cs["events"]["update"] == []


def test_disappeared_uid_is_plan_drop():
    existing = _plan_row(9, "uid-8@icloud.com", "Забрать посылку", "2026-07-30")
    cs = extcal.plan_changes([], _snap(plans=[existing]), NOW)
    assert cs["plans"]["drop"] == [
        {"id": 9, "external_uid": extcal._occurrence_key("uid-8@icloud.com", None)}]


def test_disappearance_suppressed_for_stale_past_row_out_of_window_floor():
    # A row whose start is well before "now - 1 day" naturally scrolls out
    # of the CalDAV read window on its own (see _time_range) -- its absence
    # from remote_occurrences is NOT evidence it was deleted on the phone,
    # so it must NOT be cancelled.
    existing = _event_row(10, "uid-9@icloud.com", "Старое событие",
                           "2026-01-01T09:00:00+00:00")
    cs = extcal.plan_changes([], _snap(events=[existing]), NOW)
    assert cs["events"]["cancel"] == []


def test_disappearance_suppressed_when_now_utc_is_unusable():
    # Fail-closed: an unparseable/missing now_utc must not itself become the
    # reason something gets cancelled/dropped.
    existing = _event_row(11, "uid-10@icloud.com", "Событие",
                           "2026-07-30T09:00:00+00:00")
    cs = extcal.plan_changes([], _snap(events=[existing]), "garbage-not-a-date")
    assert cs["events"]["cancel"] == []
    cs2 = extcal.plan_changes([], _snap(events=[existing]), None)
    assert cs2["events"]["cancel"] == []


# ---------------------------------------------------------------------
# guard: owner='hermes' rows are never a write target
# ---------------------------------------------------------------------

def test_hermes_owned_rows_never_appear_in_any_list():
    hermes_event = _event_row(20, None, "Тренировка", "2026-08-01T05:00:00+00:00",
                               owner="hermes")
    hermes_plan = _plan_row(21, None, "Купить продукты", "2026-08-05", owner="hermes")
    # Remote batch is entirely unrelated -- nothing should ever touch 20/21.
    occ = _occ("uid-11@icloud.com", "Другое дело", "2026-08-03T10:00:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[hermes_event], plans=[hermes_plan]), NOW)

    touched_event_ids = ({u["id"] for u in cs["events"]["update"]}
                          | {c["id"] for c in cs["events"]["cancel"]})
    touched_plan_ids = ({u["id"] for u in cs["plans"]["update"]}
                         | {d["id"] for d in cs["plans"]["drop"]})
    assert 20 not in touched_event_ids
    assert 21 not in touched_plan_ids
    # And the unrelated occurrence still inserts normally (guard doesn't
    # accidentally swallow real work).
    assert len(cs["events"]["insert"]) == 1


def test_hermes_owned_row_not_fuzzy_eligible_is_untouched_and_remote_still_inserts():
    # Same external_uid as the remote occurrence would compute (a
    # hypothetical corruption/edge case) -- still invisible to key-matching,
    # since events_by_key is built from owner='iphone' rows only.
    hermes_event = _event_row(22, "uid-12@icloud.com", "Совсем другое",
                               "2026-08-10T05:00:00+00:00", owner="hermes")
    occ = _occ("uid-12@icloud.com", "Йога", "2026-08-01T13:00:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[hermes_event]), NOW)
    assert len(cs["events"]["insert"]) == 1
    assert cs["events"]["update"] == [] and cs["events"]["cancel"] == []
    assert cs["collisions"] == []


# ---------------------------------------------------------------------
# tombstone: a previously cancelled/dropped row never resurrects
# ---------------------------------------------------------------------

def test_tombstoned_event_does_not_resurrect_on_reappearance():
    existing = _event_row(30, "uid-13@icloud.com", "Стоматолог",
                           "2026-07-30T09:00:00+00:00", status="cancelled")
    # Same uid reappears, "active" again (e.g. a sync-token replay) --
    # must NOT be updated or re-inserted.
    occ = _occ("uid-13@icloud.com", "Стоматолог", "2026-07-30T09:00:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[existing]), NOW)
    assert cs["events"]["insert"] == []
    assert cs["events"]["update"] == []
    assert cs["events"]["cancel"] == []  # already terminal -- no repeat cancel either


def test_tombstoned_plan_does_not_resurrect_on_reappearance():
    existing = _plan_row(31, "uid-14@icloud.com", "Забрать посылку",
                          "2026-07-30", status="dropped")
    start = _all_day_utc("2026-07-30")
    occ = _occ("uid-14@icloud.com", "Забрать посылку", start, end_utc=start, all_day=True)
    cs = extcal.plan_changes([occ], _snap(plans=[existing]), NOW)
    assert cs["plans"]["insert"] == []
    assert cs["plans"]["update"] == []
    assert cs["plans"]["drop"] == []


def test_tombstoned_row_disappearing_again_is_not_double_cancelled():
    existing = _event_row(32, "uid-15@icloud.com", "Стоматолог",
                           "2026-07-30T09:00:00+00:00", status="cancelled")
    cs = extcal.plan_changes([], _snap(events=[existing]), NOW)
    assert cs["events"]["cancel"] == []


# ---------------------------------------------------------------------
# fuzzy match -> collisions, never a duplicate insert
# ---------------------------------------------------------------------

def test_fuzzy_match_within_15_minutes_and_casefold_title_is_collision():
    hermes_event = _event_row(40, None, "Тренировка", "2026-08-01T05:00:00+00:00",
                               owner="hermes")
    occ = _occ("uid-16@icloud.com", "тренировка", "2026-08-01T05:10:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[hermes_event]), NOW)
    assert cs["events"]["insert"] == []
    assert len(cs["collisions"]) == 1
    coll = cs["collisions"][0]
    assert coll["branch"] == "events"
    assert coll["local_id"] == 40
    assert coll["remote_uid"] == "uid-16@icloud.com"


def test_fuzzy_match_cyrillic_case_insensitive_title():
    hermes_event = _event_row(41, None, "ЙОГА", "2026-08-02T13:00:00+00:00",
                               owner="hermes")
    occ = _occ("uid-17@icloud.com", "йога", "2026-08-02T13:05:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[hermes_event]), NOW)
    assert cs["events"]["insert"] == []
    assert len(cs["collisions"]) == 1
    assert cs["collisions"][0]["local_id"] == 41


def test_fuzzy_match_beyond_15_minutes_is_not_a_collision():
    hermes_event = _event_row(42, None, "Тренировка", "2026-08-01T05:00:00+00:00",
                               owner="hermes")
    occ = _occ("uid-18@icloud.com", "тренировка", "2026-08-01T05:20:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[hermes_event]), NOW)
    assert cs["collisions"] == []
    assert len(cs["events"]["insert"]) == 1


def test_fuzzy_match_different_title_is_not_a_collision():
    hermes_event = _event_row(43, None, "Тренировка", "2026-08-01T05:00:00+00:00",
                               owner="hermes")
    occ = _occ("uid-19@icloud.com", "Стоматолог", "2026-08-01T05:05:00+00:00")
    cs = extcal.plan_changes([occ], _snap(events=[hermes_event]), NOW)
    assert cs["collisions"] == []
    assert len(cs["events"]["insert"]) == 1


def test_fuzzy_match_plan_same_deadline_and_casefold_title_is_collision():
    hermes_plan = _plan_row(44, None, "Купить Подарок", "2026-07-31", owner="hermes")
    start = _all_day_utc("2026-07-31")
    occ = _occ("uid-20@icloud.com", "купить подарок", start, end_utc=start, all_day=True)
    cs = extcal.plan_changes([occ], _snap(plans=[hermes_plan]), NOW)
    assert cs["plans"]["insert"] == []
    assert len(cs["collisions"]) == 1
    assert cs["collisions"][0]["branch"] == "plans"
    assert cs["collisions"][0]["local_id"] == 44


def test_fuzzy_claim_sets_are_independent_across_events_and_plans_tables():
    # Review finding C1: events and plans have INDEPENDENT autoincrement
    # PKs, so id=3 in `events` and id=3 in `plans` are two unrelated rows
    # that both routinely exist at once. A single shared "claimed" set keyed
    # on the bare id would let claiming the hermes EVENT (id=3) wrongly
    # suppress claiming the entirely different hermes PLAN (id=3) later in
    # the SAME call, turning what should be a second `collisions` entry into
    # a silent duplicate insert.
    hermes_event = _event_row(3, None, "Тренировка", "2026-08-01T05:00:00+00:00",
                               owner="hermes")
    hermes_plan = _plan_row(3, None, "Купить подарок", "2026-07-31", owner="hermes")
    timed_occ = _occ("uid-40@icloud.com", "тренировка", "2026-08-01T05:05:00+00:00")
    all_day_start = _all_day_utc("2026-07-31")
    all_day_occ = _occ("uid-41@icloud.com", "купить подарок", all_day_start,
                        end_utc=all_day_start, all_day=True)
    cs = extcal.plan_changes([timed_occ, all_day_occ],
                              _snap(events=[hermes_event], plans=[hermes_plan]), NOW)

    assert cs["events"]["insert"] == []
    assert cs["plans"]["insert"] == []
    assert len(cs["collisions"]) == 2
    branches = {c["branch"]: c["local_id"] for c in cs["collisions"]}
    assert branches == {"events": 3, "plans": 3}


# ---------------------------------------------------------------------
# timed -> events, all-day -> plans (branch routing + crossing)
# ---------------------------------------------------------------------

def test_timed_and_all_day_route_to_different_branches_in_one_batch():
    timed = _occ("uid-21@icloud.com", "Йога", "2026-07-28T13:00:00+00:00")
    all_day_start = _all_day_utc("2026-07-29")
    all_day = _occ("uid-22@icloud.com", "Купить подарок", all_day_start,
                    end_utc=all_day_start, all_day=True)
    cs = extcal.plan_changes([timed, all_day], _snap(), NOW)
    assert len(cs["events"]["insert"]) == 1
    assert cs["events"]["insert"][0]["title"] == "Йога"
    assert len(cs["plans"]["insert"]) == 1
    assert cs["plans"]["insert"][0]["title"] == "Купить подарок"


def test_item_that_became_all_day_drops_the_stale_event_row():
    # Same uid used to be timed (owner='iphone' event row on file); it
    # reappears as an all-day item -- old event row must be cancelled and
    # the item re-homed as a plan, not left duplicated in both tables.
    stale_event = _event_row(50, "uid-23@icloud.com", "Дело",
                              "2026-07-28T13:00:00+00:00")
    start = _all_day_utc("2026-07-29")
    occ = _occ("uid-23@icloud.com", "Дело", start, end_utc=start, all_day=True)
    cs = extcal.plan_changes([occ], _snap(events=[stale_event]), NOW)
    assert cs["events"]["cancel"] == [
        {"id": 50, "external_uid": extcal._occurrence_key("uid-23@icloud.com", None)}]
    assert len(cs["plans"]["insert"]) == 1


def test_item_that_became_timed_drops_the_stale_plan_row():
    stale_plan = _plan_row(51, "uid-24@icloud.com", "Дело", "2026-07-29")
    occ = _occ("uid-24@icloud.com", "Дело", "2026-07-29T13:00:00+00:00")
    cs = extcal.plan_changes([occ], _snap(plans=[stale_plan]), NOW)
    assert cs["plans"]["drop"] == [
        {"id": 51, "external_uid": extcal._occurrence_key("uid-24@icloud.com", None)}]
    assert len(cs["events"]["insert"]) == 1


# ---------------------------------------------------------------------
# recurring occurrence linking (uid + recurrence_id)
# ---------------------------------------------------------------------

def test_recurring_instances_share_uid_but_link_independently_by_recurrence_id():
    rid1 = "2026-08-03T05:00:00+00:00"
    rid2 = "2026-08-10T05:00:00+00:00"
    existing = _event_row(60, "uid-25@icloud.com", "Тренировка",
                           "2026-08-03T05:00:00+00:00", recurrence_id=rid1)
    occ_unchanged = _occ("uid-25@icloud.com", "Тренировка", "2026-08-03T05:00:00+00:00",
                          recurrence_id=rid1)
    occ_new_instance = _occ("uid-25@icloud.com", "Тренировка", "2026-08-10T05:00:00+00:00",
                             recurrence_id=rid2)
    cs = extcal.plan_changes([occ_unchanged, occ_new_instance], _snap(events=[existing]), NOW)
    assert cs["events"]["update"] == []  # rid1 instance unchanged
    assert len(cs["events"]["insert"]) == 1  # rid2 is a brand-new instance
    assert cs["events"]["insert"][0]["external_uid"] == extcal._occurrence_key(
        "uid-25@icloud.com", rid2)


def test_occurrence_key_is_unambiguous_across_uid_and_recurrence_id_boundary():
    # Review finding I3: a bare "uid::recurrence_id" join is ambiguous --
    # uid="A::B" with no recurrence_id must NEVER produce the same key as
    # uid="A" with recurrence_id="B". The length-prefixed encoding must
    # keep them distinct.
    key_embedded_separator = extcal._occurrence_key("A::B", None)
    key_split_across_fields = extcal._occurrence_key("A", "B")
    assert key_embedded_separator != key_split_across_fields


def test_moved_recurring_instance_is_update_not_new_row():
    rid1 = "2026-08-03T05:00:00+00:00"
    existing = _event_row(61, "uid-26@icloud.com", "Тренировка",
                           "2026-08-03T05:00:00+00:00", recurrence_id=rid1)
    # The instance moved to a new time, but RECURRENCE-ID (the key) still
    # names its ORIGINAL slot -- must link to the same row as an update.
    moved = _occ("uid-26@icloud.com", "Тренировка", "2026-08-03T07:00:00+00:00",
                  recurrence_id=rid1)
    cs = extcal.plan_changes([moved], _snap(events=[existing]), NOW)
    assert cs["events"]["insert"] == []
    assert len(cs["events"]["update"]) == 1
    assert cs["events"]["update"][0]["id"] == 61
    assert cs["events"]["update"][0]["changes"]["start_utc"] == (
        "2026-08-03T05:00:00+00:00", "2026-08-03T07:00:00+00:00")


# ---------------------------------------------------------------------
# intra-batch dedup (review finding I2)
# ---------------------------------------------------------------------

def test_duplicate_occurrence_within_one_batch_produces_single_insert():
    # Same event visible through two of her subscribed calendars at once
    # (extcal_read_calendars == [] reads all of them) -- both readings are
    # byte-identical Occurrences carrying the same uid. Must not queue two
    # inserts with the same external_uid (schema v12's partial UNIQUE index
    # on that column would only catch this later, at T5's write time).
    occ_from_cal_a = _occ("uid-30@icloud.com", "Йога", "2026-07-28T13:00:00+00:00")
    occ_from_cal_b = _occ("uid-30@icloud.com", "Йога", "2026-07-28T13:00:00+00:00")
    cs = extcal.plan_changes([occ_from_cal_a, occ_from_cal_b], _snap(), NOW)
    assert len(cs["events"]["insert"]) == 1


def test_duplicate_all_day_occurrence_within_one_batch_produces_single_insert():
    start = _all_day_utc("2026-07-31")
    occ_from_cal_a = _occ("uid-31@icloud.com", "Купить подарок", start, end_utc=start,
                           all_day=True)
    occ_from_cal_b = _occ("uid-31@icloud.com", "Купить подарок", start, end_utc=start,
                           all_day=True)
    cs = extcal.plan_changes([occ_from_cal_a, occ_from_cal_b], _snap(), NOW)
    assert len(cs["plans"]["insert"]) == 1


def test_duplicate_occurrence_against_an_existing_row_produces_at_most_one_update():
    existing = _event_row(70, "uid-32@icloud.com", "Йога", "2026-07-28T13:00:00+00:00")
    occ_a = _occ("uid-32@icloud.com", "Йога (зал)", "2026-07-28T13:00:00+00:00")
    occ_b = _occ("uid-32@icloud.com", "Йога (зал)", "2026-07-28T13:00:00+00:00")
    cs = extcal.plan_changes([occ_a, occ_b], _snap(events=[existing]), NOW)
    assert len(cs["events"]["update"]) == 1
    assert cs["events"]["insert"] == []


# ---------------------------------------------------------------------
# garbage input never crashes the function
# ---------------------------------------------------------------------

def test_garbage_remote_occurrences_do_not_crash():
    garbage = [
        None, "not a dict", 42, {},
        {"uid": None, "title": "no uid"},
        {"uid": "uid-x", "start_utc": None, "title": "no start"},
        {"uid": "uid-y", "start_utc": "not-a-real-timestamp", "title": "bad time"},
    ]
    cs = extcal.plan_changes(garbage, _snap(), NOW)
    _empty_changeset_shape(cs)
    assert cs["events"]["insert"] == []
    assert cs["plans"]["insert"] == []


def test_garbage_local_snapshot_does_not_crash():
    occ = _occ("uid-27@icloud.com", "Дело", "2026-07-28T13:00:00+00:00")
    for bad_snapshot in (None, {}, {"events": "not a list"},
                          {"events": [None, "nope", 5], "plans": [{"owner": "mystery"}]}):
        cs = extcal.plan_changes([occ], bad_snapshot, NOW)
        _empty_changeset_shape(cs)


def test_completely_garbage_call_does_not_raise():
    cs = extcal.plan_changes("not a list either", "not a dict either", object())
    _empty_changeset_shape(cs)
    assert cs["events"] == {"insert": [], "update": [], "cancel": []}
    assert cs["plans"] == {"insert": [], "update": [], "drop": []}
    assert cs["collisions"] == []
