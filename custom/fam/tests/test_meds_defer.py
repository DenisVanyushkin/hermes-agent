"""Task 1 (meds-defer): defer() pushes a pending dose's series_next_utc
out without acking it -- status stays 'pending' (unlike take()/skip()
tested in test_meds_ack.py). Uses the shared `db` fixture (conftest.py)
for an isolated per-test sqlite file, same convention as test_meds_ack.py.
"""
import json

import pytest

from fam import meds


def _mk_intake(db, status="pending", plan="2026-07-24T04:00:00Z"):
    med_id = meds.add(db, "мисол", ["09:00"], remaining=10, threshold=1)
    cur = db.execute(
        "INSERT INTO med_intakes (med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?)",
        (med_id, plan, status, plan, "2026-07-24T04:00:00Z"),
    )
    return med_id, cur.lastrowid


def test_defer_pending_moves_series_next_and_keeps_pending(db):
    med_id, iid = _mk_intake(db)
    now = "2026-07-24T04:10:00Z"          # 09:10 Almaty
    until = "2026-07-24T15:00:00Z"        # 20:00 Almaty
    out = meds.defer(db, iid, until, now_utc=now)

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?", (iid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] == until
    assert out["intake_id"] == iid
    assert out["med_id"] == med_id
    assert out["until_utc"] == until


@pytest.mark.parametrize("status", ["taken", "skipped", "missed"])
def test_defer_non_pending_raises(db, status):
    _, iid = _mk_intake(db, status=status)
    with pytest.raises(ValueError):
        meds.defer(db, iid, "2026-07-24T15:00:00Z", now_utc="2026-07-24T04:10:00Z")


def test_defer_past_time_raises(db):
    _, iid = _mk_intake(db)
    with pytest.raises(ValueError):
        meds.defer(db, iid, "2026-07-24T04:00:00Z", now_utc="2026-07-24T04:10:00Z")


def test_defer_after_almaty_midnight_raises(db):
    _, iid = _mk_intake(db)
    # 2026-07-25T00:00 Almaty == 2026-07-24T19:00Z -> "tomorrow", forbidden
    with pytest.raises(ValueError):
        meds.defer(db, iid, "2026-07-24T19:30:00Z", now_utc="2026-07-24T04:10:00Z")


def test_defer_writes_audit(db):
    med_id, iid = _mk_intake(db)
    meds.defer(db, iid, "2026-07-24T15:00:00Z", now_utc="2026-07-24T04:10:00Z")

    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='meds.defer' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["payload"])
    assert payload["intake_id"] == iid
    assert payload["med_id"] == med_id
    assert payload["until_utc"] == "2026-07-24T15:00:00Z"


def test_defer_twice_moves_again(db):
    _, iid = _mk_intake(db)
    meds.defer(db, iid, "2026-07-24T15:00:00Z", now_utc="2026-07-24T04:10:00Z")
    meds.defer(db, iid, "2026-07-24T16:00:00Z", now_utc="2026-07-24T15:10:00Z")

    row = db.execute(
        "SELECT series_next_utc FROM med_intakes WHERE id=?", (iid,)
    ).fetchone()
    assert row["series_next_utc"] == "2026-07-24T16:00:00Z"
