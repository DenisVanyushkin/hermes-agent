"""Phase 5 Task 3: `fam tick meds-gen` -- midnight Asia/Almaty generation of
today's med_intakes rows from each active med's `times`, plus closing out
yesterday's still-pending intakes as `missed`.

Not a minute-tick (own systemd timer, OnCalendar 19:00 UTC = 00:00 Almaty,
Task 3's own step 5) but exercised here exactly like the other tick
entry points -- now_utc injectable, audits tick.meds_gen every run.
"""
from datetime import datetime, timezone

import pytest

from fam import audit, meds, tick

# 2026-07-20 00:00 Asia/Almaty (UTC+5, no DST) == 2026-07-19T19:00:00Z --
# the actual midnight-Almaty instant the systemd timer fires at.
MIDNIGHT_ALMATY_2026_07_20 = "2026-07-19T19:00:00+00:00"

# Same calendar instant, re-run later the same local day (still before the
# NEXT midnight) -- used for the idempotent-rerun / today-untouched checks.
LATER_SAME_LOCAL_DAY = "2026-07-20T10:00:00+00:00"  # 15:00 Almaty


def _two_meds(db):
    a = meds.add(db, "Магний", ["08:00", "20:00"], dose="1 таб")
    b = meds.add(db, "Витамин D", ["09:30"])
    db.commit()
    return a, b


def test_generates_one_intake_per_time_across_active_meds(db):
    a, b = _two_meds(db)

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts["generated"] == 3
    rows = db.execute(
        "SELECT med_id, plan_ts_utc, status, series_next_utc, taken_ts_utc "
        "FROM med_intakes ORDER BY med_id, plan_ts_utc"
    ).fetchall()
    assert len(rows) == 3
    for r in rows:
        assert r["status"] == "pending"
        assert r["taken_ts_utc"] is None
        # series_next_utc is seeded to plan_ts_utc at creation (T4 owns
        # moving it for persistent series).
        assert r["series_next_utc"] == r["plan_ts_utc"]


def test_plan_ts_utc_is_almaty_local_time_converted_to_utc(db):
    meds.add(db, "Магний", ["08:00", "20:00"])
    db.commit()

    tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    plan_ts = {
        r["plan_ts_utc"]
        for r in db.execute("SELECT plan_ts_utc FROM med_intakes").fetchall()
    }
    # 08:00 Almaty (UTC+5) on 2026-07-20 -> 03:00 UTC; 20:00 -> 15:00 UTC.
    assert plan_ts == {"2026-07-20T03:00:00+00:00", "2026-07-20T15:00:00+00:00"}


def test_disabled_med_is_skipped(db):
    med_id = meds.add(db, "Отключённое", ["08:00"])
    db.commit()
    meds.edit(db, med_id, enabled=False)
    db.commit()

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts["generated"] == 0
    assert db.execute("SELECT COUNT(*) FROM med_intakes").fetchone()[0] == 0


def test_rerun_same_day_does_not_duplicate(db):
    _two_meds(db)

    first = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)
    second = tick.meds_gen(db, now_utc=LATER_SAME_LOCAL_DAY)

    assert first["generated"] == 3
    assert second["generated"] == 0
    assert db.execute("SELECT COUNT(*) FROM med_intakes").fetchone()[0] == 3


def test_yesterdays_pending_intake_is_closed_as_missed(db):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    # 08:00 Almaty on 2026-07-19 -> 03:00 UTC, still pending going into the
    # 2026-07-20 midnight run.
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?)",
        (med_id, "2026-07-19T03:00:00+00:00", "pending",
         "2026-07-19T03:00:00+00:00", "2026-07-19T03:00:00+00:00"),
    )
    db.commit()

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts["missed"] == 1
    row = db.execute(
        "SELECT status FROM med_intakes WHERE plan_ts_utc=?",
        ("2026-07-19T03:00:00+00:00",),
    ).fetchone()
    assert row["status"] == "missed"


def test_todays_pending_intake_is_not_touched(db):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    # today's own generated pending row (15:00 Almaty on 2026-07-20 --
    # already >= the run's own start-of-today boundary).
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?)",
        (med_id, "2026-07-20T10:00:00+00:00", "pending",
         "2026-07-20T10:00:00+00:00", "2026-07-20T10:00:00+00:00"),
    )
    db.commit()

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts["missed"] == 0
    row = db.execute(
        "SELECT status FROM med_intakes WHERE plan_ts_utc=?",
        ("2026-07-20T10:00:00+00:00",),
    ).fetchone()
    assert row["status"] == "pending"


def test_non_pending_statuses_are_never_touched_by_missed_closeout(db):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    for status, plan_ts in (
        ("taken", "2026-07-19T03:00:00+00:00"),
        ("skipped", "2026-07-19T04:00:00+00:00"),
        ("missed", "2026-07-19T05:00:00+00:00"),
    ):
        db.execute(
            "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
            "series_next_utc, created_at) VALUES (?,?,?,?,?)",
            (med_id, plan_ts, status, plan_ts, plan_ts),
        )
    db.commit()

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts["missed"] == 0
    statuses = {
        r["plan_ts_utc"]: r["status"]
        for r in db.execute(
            "SELECT plan_ts_utc, status FROM med_intakes "
            "WHERE plan_ts_utc < '2026-07-20T00:00:00'"
        ).fetchall()
    }
    assert statuses == {
        "2026-07-19T03:00:00+00:00": "taken",
        "2026-07-19T04:00:00+00:00": "skipped",
        "2026-07-19T05:00:00+00:00": "missed",
    }


def test_audits_tick_meds_gen_with_generated_and_missed_counts(db):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?)",
        (med_id, "2026-07-19T03:00:00+00:00", "pending",
         "2026-07-19T03:00:00+00:00", "2026-07-19T03:00:00+00:00"),
    )
    db.commit()

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts == {"generated": 1, "missed": 1}
    rows = audit.query(db, since_utc=None, kind_prefix="tick.meds_gen",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == counts


def test_zero_meds_zero_stale_still_audits_tick_meds_gen(db):
    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    assert counts == {"generated": 0, "missed": 0}
    rows = audit.query(db, since_utc=None, kind_prefix="tick.meds_gen",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == {"generated": 0, "missed": 0}


def test_uses_real_now_when_not_given(db):
    # Pins that omitting now_utc doesn't raise and drives date_local off
    # the real wall clock, mirroring test_reminders_uses_real_now_when_not_given.
    real_now = datetime.now(timezone.utc)
    date_local = tick._today_almaty(real_now.isoformat(timespec="seconds"))
    meds.add(db, "Магний", ["08:00"])
    db.commit()

    counts = tick.meds_gen(db)

    assert counts["generated"] == 1
    row = db.execute("SELECT plan_ts_utc FROM med_intakes").fetchone()
    y, m, d = (int(x) for x in date_local.split("-"))
    expected_utc = datetime(y, m, d, 3, 0, 0, tzinfo=timezone.utc)  # 08:00 Almaty
    assert row["plan_ts_utc"] == expected_utc.isoformat(timespec="seconds")


def test_malformed_med_row_is_audited_and_other_meds_still_processed(db,
                                                                       monkeypatch):
    a, b = _two_meds(db)

    real_list = meds.list

    def bad_list(conn, include_disabled=False):
        rows = real_list(conn, include_disabled=include_disabled)
        # simulate a corrupted times entry bypassing meds._validate_times
        # (e.g. a manual DB edit) -- meds_gen's per-med guard must not let
        # this take down the whole tick.
        for r in rows:
            if r["id"] == a:
                r["times"] = ["not-a-time"]
        return rows

    monkeypatch.setattr(tick.meds, "list", bad_list)

    counts = tick.meds_gen(db, now_utc=MIDNIGHT_ALMATY_2026_07_20)

    # med b's single time still generated despite med a's bad row.
    assert counts["generated"] == 1
    rows = audit.query(db, since_utc=None, kind_prefix="tick.error", grep=None,
                        limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["where"] == "meds_gen"
    assert rows[0]["payload"]["med_id"] == a
