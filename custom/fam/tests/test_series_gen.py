"""series.generate: materialize occurrences (Asia/Almaty), idempotent, horizon."""
from datetime import datetime, timedelta, timezone

from fam import series, cal, people, places

# 2026-07-13 is a Monday (Almaty). Midnight Almaty = 2026-07-12T19:00:00+00:00.
NOW = "2026-07-13T00:00:00+05:00"


def _weekdays_of(db, sid):
    rows = db.execute(
        "SELECT start_utc FROM events WHERE series_id=? ORDER BY start_utc",
        (sid,)).fetchall()
    out = []
    for r in rows:
        dt = datetime.fromisoformat(r["start_utc"]).astimezone(cal.ALMATY)
        out.append(dt.weekday())
    return out


def test_generates_only_on_configured_weekdays(db):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00")
    db.commit()
    n = series.generate(db, now_utc=NOW, horizon_weeks=2)
    db.commit()
    assert n > 0
    # every occurrence is Mon(0)/Wed(2)/Fri(4)
    assert set(_weekdays_of(db, s["id"])) <= {0, 2, 4}
    # first occurrence: Mon 2026-07-13 10:00 Almaty = 05:00 UTC
    first = db.execute(
        "SELECT start_utc, end_utc FROM events WHERE series_id=? "
        "ORDER BY start_utc LIMIT 1", (s["id"],)).fetchone()
    assert first["start_utc"] == "2026-07-13T05:00:00+00:00"
    assert first["end_utc"] == "2026-07-13T07:00:00+00:00"


def test_generation_is_idempotent(db):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00"); db.commit()
    n1 = series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    count1 = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=?",
                        (s["id"],)).fetchone()["c"]
    n2 = series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    count2 = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=?",
                        (s["id"],)).fetchone()["c"]
    assert n1 == count1 and n2 == 0 and count2 == count1


def test_rolling_horizon_extends(db):
    s = series.add(db, "Тренировка", "mon", "10:00"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    c1 = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=?",
                    (s["id"],)).fetchone()["c"]
    # advance now by 3 weeks -> new Mondays appear, old ones untouched
    later = "2026-08-03T00:00:00+05:00"
    series.generate(db, now_utc=later, horizon_weeks=2); db.commit()
    c2 = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=?",
                    (s["id"],)).fetchone()["c"]
    assert c2 > c1


def test_until_truncates(db):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00",
                   until_local="2026-07-17"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=8); db.commit()
    starts = [r["start_utc"] for r in db.execute(
        "SELECT start_utc FROM events WHERE series_id=? ORDER BY start_utc",
        (s["id"],))]
    # only Mon13, Wed15, Fri17 (<= until 2026-07-17)
    assert len(starts) == 3
    assert starts[-1] == "2026-07-17T05:00:00+00:00"


def test_cancelled_series_generates_nothing(db):
    s = series.add(db, "Тренировка", "mon", "10:00"); db.commit()
    series.cancel(db, s["id"]); db.commit()
    n = series.generate(db, now_utc=NOW, horizon_weeks=4); db.commit()
    assert n == 0


def test_participants_copied_to_occurrences(db):
    people.add(db, "Амина"); db.commit()
    s = series.add(db, "Тренировка", "mon", "10:00", participants=["Амина"])
    db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    ev = db.execute("SELECT id FROM events WHERE series_id=? LIMIT 1",
                    (s["id"],)).fetchone()
    parts = db.execute(
        "SELECT person_id FROM event_participants WHERE event_id=?",
        (ev["id"],)).fetchall()
    assert len(parts) == 1


def test_cancelled_occurrence_is_not_resurrected(db):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    ev = db.execute("SELECT id, start_utc FROM events WHERE series_id=? "
                    "ORDER BY start_utc LIMIT 1", (s["id"],)).fetchone()
    db.execute("UPDATE events SET status=\x27cancelled\x27 WHERE id=?", (ev["id"],))
    db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    # the slot stays a single cancelled tombstone, not recreated
    same = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=? AND "
                      "start_utc=?", (s["id"], ev["start_utc"])).fetchone()["c"]
    assert same == 1


def test_reschedule_occurrence_leaves_tombstone_no_dup(db):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2); db.commit()
    occ = db.execute(
        "SELECT * FROM events WHERE series_id=? AND status='active' "
        "ORDER BY start_utc LIMIT 1", (s["id"],)).fetchone()
    old_start = occ["start_utc"]
    new_start = (datetime.fromisoformat(old_start)
                 + timedelta(hours=2)).isoformat(timespec="seconds")
    cal.update(db, occ["id"], start_utc=new_start)
    db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=2)  # regen that used to duplicate
    db.commit()
    at_old = db.execute(
        "SELECT status FROM events WHERE series_id=? AND start_utc=?",
        (s["id"], old_start)).fetchall()
    assert [r["status"] for r in at_old] == ["cancelled"]   # tombstone only
    moved = db.execute("SELECT * FROM events WHERE id=?", (occ["id"],)).fetchone()
    assert moved["start_utc"] == new_start and moved["series_id"] == s["id"]
    ts = db.execute(
        "SELECT 1 FROM audit_log WHERE kind='cal.series.tombstone'").fetchone()
    assert ts is not None


def test_series_cancel_deletes_future_untouched(db):
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00"); db.commit()
    series.generate(db, now_utc=NOW, horizon_weeks=4); db.commit()
    before = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=?",
                        (s["id"],)).fetchone()["c"]
    assert before > 0
    # cancel relative to a now BEFORE all occurrences -> all are future
    removed = series.cancel(db, s["id"], now_utc=NOW); db.commit()
    remaining = db.execute("SELECT COUNT(*) c FROM events WHERE series_id=? AND "
                           "status=\x27active\x27", (s["id"],)).fetchone()["c"]
    assert removed == before and remaining == 0
