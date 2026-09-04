"""Pending-ack snapshot for the gateway's turn context.

Why this exists: on 2026-07-23 the daily session reset landed between the
09:00 medication reminder and Amina's 09:25 "Готово". The fresh session
started with history=0, the agent never called a tool, and intake #3 stayed
`pending` -- so the system re-nagged her at 09:45. Chat history is not a
durable place to keep "there is an open question"; the DB already is. This
module snapshots the open questions to a small JSON file the gateway reads
on every turn, so the context survives resets, restarts and compression.
"""
import json
import os
import stat

import pytest

from fam import acks, meds


def _insert_intake(db, med_id, plan_ts_utc, status="pending",
                    deferred_until_utc=None):
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, taken_ts_utc, status, "
        "series_next_utc, deferred_until_utc, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (med_id, plan_ts_utc, None, status, deferred_until_utc,
         deferred_until_utc, plan_ts_utc),
    )
    return cur.lastrowid


NOW = "2026-07-23T04:25:00+00:00"  # 09:25 Almaty -- the "Готово" reply


# ---- build ----

def test_build_includes_a_due_pending_intake(db):
    med_id = meds.add(db, "мисол", ["09:00"], dose="1 таблетка")
    db.commit()
    intake_id = _insert_intake(db, med_id, "2026-07-23T04:00:00+00:00")
    db.commit()

    snap = acks.build(db, cfg={"target": "whatsapp:+77011102626"}, now_utc=NOW)

    assert snap["target"] == "whatsapp:+77011102626"
    assert len(snap["items"]) == 1
    item = snap["items"][0]
    assert item["kind"] == "med_intake"
    assert item["id"] == intake_id
    assert item["name"] == "мисол"
    assert item["dose"] == "1 таблетка"
    assert item["due_local"] == "09:00"
    assert item["ack_cmd"] == f"fam med taken {intake_id}"
    assert item["skip_cmd"] == f"fam med skip {intake_id}"


def test_build_excludes_intakes_not_due_yet(db):
    """A dose scheduled for tonight is not an open question yet."""
    med_id = meds.add(db, "мисол", ["21:00"])
    db.commit()
    _insert_intake(db, med_id, "2026-07-23T16:00:00+00:00")  # 21:00 Almaty
    db.commit()

    assert acks.build(db, now_utc=NOW)["items"] == []


def test_build_excludes_stale_intakes(db):
    """Yesterday's unanswered dose must not haunt every turn forever."""
    med_id = meds.add(db, "мисол", ["09:00"])
    db.commit()
    _insert_intake(db, med_id, "2026-07-22T04:00:00+00:00")
    db.commit()

    assert acks.build(db, now_utc=NOW)["items"] == []


def test_build_excludes_resolved_intakes(db):
    med_id = meds.add(db, "мисол", ["09:00"])
    db.commit()
    _insert_intake(db, med_id, "2026-07-23T04:00:00+00:00", status="taken")
    db.commit()

    assert acks.build(db, now_utc=NOW)["items"] == []


def test_build_orders_by_due_time(db):
    med_id = meds.add(db, "мисол", ["07:00", "09:00"])
    db.commit()
    later = _insert_intake(db, med_id, "2026-07-23T04:00:00+00:00")
    earlier = _insert_intake(db, med_id, "2026-07-23T02:00:00+00:00")
    db.commit()

    ids = [i["id"] for i in acks.build(db, now_utc=NOW)["items"]]
    assert ids == [earlier, later]


def test_build_stamps_generated_at(db):
    snap = acks.build(db, now_utc=NOW)
    assert snap["generated_at"] == NOW


def test_build_shows_deferred_time_not_plan_time(db):
    """T2.5: a dose deferred to 20:00 must show the deferred time, not
    its original 09:00 plan_ts_utc -- otherwise a fresh/reset session
    re-nags at the old (already-answered-by-deferral) time."""
    med_id = meds.add(db, "мисол", ["09:00"], dose="1 таблетка")
    db.commit()
    intake_id = _insert_intake(
        db, med_id, "2026-07-23T04:00:00+00:00",  # 09:00 Almaty
        deferred_until_utc="2026-07-23T15:00:00+00:00",  # 20:00 Almaty
    )
    db.commit()

    snap = acks.build(db, now_utc=NOW)

    assert len(snap["items"]) == 1
    item = snap["items"][0]
    assert item["due_local"] == "20:00"
    assert item["deferred"] is True


def test_build_ignores_expired_deferral(db):
    """A deferred_until_utc already in the past (edge case: clock passed
    it without an ack) must not keep masking the original due time --
    falls back to plan-time behavior, no regression."""
    med_id = meds.add(db, "мисол", ["09:00"])
    db.commit()
    _insert_intake(
        db, med_id, "2026-07-23T04:00:00+00:00",
        deferred_until_utc="2026-07-23T04:10:00+00:00",  # before NOW (09:25)
    )
    db.commit()

    item = acks.build(db, now_utc=NOW)["items"][0]
    assert item["due_local"] == "09:00"
    assert not item.get("deferred")


def test_build_non_deferred_intake_unaffected(db):
    """Regression: a normal due dose (no deferral) keeps its plan-time
    due_local and carries no 'deferred' key."""
    med_id = meds.add(db, "мисол", ["09:00"], dose="1 таблетка")
    db.commit()
    _insert_intake(db, med_id, "2026-07-23T04:00:00+00:00")
    db.commit()

    item = acks.build(db, now_utc=NOW)["items"][0]
    assert item["due_local"] == "09:00"
    assert not item.get("deferred")


# ---- write ----

def test_write_creates_readable_json(db, tmp_path):
    med_id = meds.add(db, "мисол", ["09:00"], dose="1 таблетка")
    db.commit()
    intake_id = _insert_intake(db, med_id, "2026-07-23T04:00:00+00:00")
    db.commit()
    path = tmp_path / "pending-acks.json"

    acks.write(db, cfg={"target": "whatsapp:+77011102626"}, path=path,
               now_utc=NOW)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert [i["id"] for i in data["items"]] == [intake_id]
    assert data["target"] == "whatsapp:+77011102626"


def test_write_is_owner_only(db, tmp_path):
    """The snapshot names medications -- same sensitivity as the DB."""
    path = tmp_path / "pending-acks.json"
    acks.write(db, path=path, now_utc=NOW)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_write_clears_resolved_items(db, tmp_path):
    """An empty snapshot must overwrite a stale one, not be skipped --
    otherwise the gateway keeps injecting an already-answered question."""
    med_id = meds.add(db, "мисол", ["09:00"])
    db.commit()
    intake_id = _insert_intake(db, med_id, "2026-07-23T04:00:00+00:00")
    db.commit()
    path = tmp_path / "pending-acks.json"
    acks.write(db, path=path, now_utc=NOW)
    assert json.loads(path.read_text(encoding="utf-8"))["items"]

    meds.take(db, intake_id)
    db.commit()
    acks.write(db, path=path, now_utc=NOW)

    assert json.loads(path.read_text(encoding="utf-8"))["items"] == []


def test_write_never_raises_on_unwritable_path(db, tmp_path):
    """A broken snapshot must never break a tick or a med ack."""
    path = tmp_path / "no-such-dir" / "pending-acks.json"
    acks.write(db, path=path, now_utc=NOW)  # must not raise


def test_write_is_atomic(db, tmp_path):
    """No half-written file: readers see either the old or the new one."""
    path = tmp_path / "pending-acks.json"
    acks.write(db, path=path, now_utc=NOW)
    acks.write(db, path=path, now_utc=NOW)
    assert json.loads(path.read_text(encoding="utf-8"))["items"] == []
    assert not list(tmp_path.glob("*.tmp*"))


S3_NOW = "2026-09-04T12:00:00+00:00"
S3_EXACTLY_INSIDE = "2026-09-04T10:00:00+00:00"
S3_AFTER_WINDOW = "2026-09-04T09:59:00+00:00"
S3_INSIDE = "2026-09-04T11:00:00+00:00"


def _event_with_sent_reminders(
    db, *, status="active", sent_times=(S3_INSIDE,),
    created_at="2026-09-01T00:00:00+00:00",
    start_utc="2026-09-10T00:00:00+00:00",
):
    event = db.execute(
        "INSERT INTO events(title,start_utc,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?)",
        ("S3 событие", start_utc, status, created_at, created_at),
    )
    event_id = event.lastrowid
    for index, sent_at in enumerate(sent_times, start=1):
        reminder = db.execute(
            "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
            "VALUES(?,?,?,?,?,?)",
            (event_id, "leave", sent_at, "sent", created_at, sent_at),
        )
        db.execute(
            "INSERT INTO sent_messages("
            "wa_message_id,kind,ref_id,event_id,created_at) VALUES(?,?,?,?,?)",
            (f"wa-rem-{event_id}-{index}", "reminder", reminder.lastrowid,
             event_id, sent_at),
        )
    db.commit()
    return event_id


@pytest.mark.parametrize(
    ("case", "status", "sent_times", "expected"),
    [
        ("exactly_inside", "active", (S3_EXACTLY_INSIDE,), True),
        ("after_window", "active", (S3_AFTER_WINDOW,), False),
        ("cancelled", "cancelled", (S3_INSIDE,), False),
        ("done", "done", (S3_INSIDE,), False),
        ("active_without_pending", "active", (S3_INSIDE,), True),
    ],
)
def test_open_resolution_candidates_window_matrix(
    db, case, status, sent_times, expected
):
    from fam import rem

    event_id = _event_with_sent_reminders(
        db, status=status, sent_times=sent_times
    )

    candidates = rem.open_resolution_candidates(
        db, now_utc=S3_NOW, max_age_min=120
    )

    assert bool([item for item in candidates if item["ref_id"] == event_id]) is expected, case


def test_open_resolution_candidates_anchors_window_at_latest_outbound(
    db,
):
    from fam import rem

    event_id = _event_with_sent_reminders(
        db,
        sent_times=("2026-09-04T09:59:00+00:00", S3_INSIDE),
        created_at="2026-09-01T00:00:00+00:00",
        start_utc="2026-09-10T00:00:00+00:00",
    )

    candidates = rem.open_resolution_candidates(
        db, now_utc=S3_NOW, max_age_min=120
    )

    matching = [item for item in candidates if item["ref_id"] == event_id]
    assert len(matching) == 1
    assert matching[0]["wa_message_ids"] == [
        f"wa-rem-{event_id}-1", f"wa-rem-{event_id}-2"
    ]


def test_active_chains_keeps_pending_only_contract_for_s3_event(db):
    from fam import rem

    event_id = _event_with_sent_reminders(db, sent_times=(S3_INSIDE,))

    assert rem.active_chains(db) == []
    assert [
        item["ref_id"]
        for item in rem.open_resolution_candidates(
            db, now_utc=S3_NOW, max_age_min=120
        )
    ] == [event_id]
