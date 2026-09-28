"""Schedule-only events (remind=0) do not take Amina's slot.

Live 2026-09-26: recording Taya's Robotics series tripped the overlap guard on
Amina's own cosmetologist visit, and Acting on a client session. Taya is at
school then; Amina is not going anywhere, so nobody is double-booked. With the
guard as it was, the agent would ask Amina "keep both?" for every club.
"""
from datetime import datetime, timedelta, timezone

from fam import cal, cli


def _local(days_ahead, hh, mm=0):
    d = (datetime.now(timezone.utc).astimezone(cal.ALMATY)
         + timedelta(days=days_ahead)).date()
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00+05:00"


def _acks(db):
    return db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='cal.overlap_ack'").fetchone()[0]


def _amina_busy(db, days_ahead=3):
    e = cal.add(db, "Косметолог", _local(days_ahead, 14, 15),
                end_utc=_local(days_ahead, 15, 15))
    db.commit()
    return e


def _club(db, days_ahead=3):
    e = cal.add(db, "Робототехника", _local(days_ahead, 14, 20),
                end_utc=_local(days_ahead, 15, 5), remind=False)
    db.commit()
    return e


def test_overlaps_ignores_schedule_only_candidates(db):
    club = _club(db)
    busy = _amina_busy(db)
    hits = cal.overlaps(db, _local(3, 14, 30), _local(3, 15, 0))
    assert [h["id"] for h in hits] == [busy["id"]]
    assert club["id"] not in [h["id"] for h in hits]


def test_add_regular_event_over_a_club_needs_no_confirmation(db, capsys):
    _club(db)
    rc = cli.main(["cal", "add", "--title", "Маникюр",
                   "--start", _local(3, 14, 30), "--end", _local(3, 15, 0)])
    assert rc == 0, capsys.readouterr().err
    assert _acks(db) == 0


def test_add_schedule_only_event_over_amina_needs_no_confirmation(db, capsys):
    _amina_busy(db)
    rc = cli.main(["cal", "add", "--title", "Карате", "--start", _local(3, 14, 20),
                   "--end", _local(3, 15, 5), "--no-remind"])
    assert rc == 0, capsys.readouterr().err
    assert _acks(db) == 0


def test_series_schedule_only_over_amina_needs_no_confirmation(db, capsys):
    for day in range(1, 8):
        _amina_busy(db, days_ahead=day)
    rc = cli.main(["cal", "add", "--title", "Актёрское мастерство", "--repeat",
                   "weekly", "--days", "mon,tue,wed,thu,fri,sat,sun",
                   "--start-time", "14:30", "--end-time", "15:15", "--no-remind"])
    assert rc == 0, capsys.readouterr().err
    assert _acks(db) == 0


def test_update_moving_a_club_onto_amina_needs_no_confirmation(db, capsys):
    _amina_busy(db, days_ahead=4)
    club = _club(db, days_ahead=3)
    rc = cli.main(["cal", "update", str(club["id"]), "--start", _local(4, 14, 20)])
    assert rc == 0, capsys.readouterr().err
    assert _acks(db) == 0
    moved = cal.get(db, club["id"])
    assert moved["end_local"] == _local(4, 15, 5)   # duration kept on the move


def test_regular_events_still_guarded(db, capsys):
    _amina_busy(db)
    rc = cli.main(["cal", "add", "--title", "Маникюр",
                   "--start", _local(3, 14, 30), "--end", _local(3, 15, 0)])
    assert rc == 2
    assert "Косметолог" in capsys.readouterr().err


def test_regular_series_still_guarded_by_amina_event(db, capsys):
    for day in range(1, 8):
        _amina_busy(db, days_ahead=day)
    rc = cli.main(["cal", "add", "--title", "Бассейн", "--repeat", "weekly",
                   "--days", "mon,tue,wed,thu,fri,sat,sun",
                   "--start-time", "14:30", "--end-time", "15:15"])
    assert rc == 2
    assert "Косметолог" in capsys.readouterr().err
