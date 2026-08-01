"""series.iter_occurrences(): чистая сетка повторов (её же использует generate)."""
from datetime import datetime, timedelta

import pytest

from fam import cal, series


def _now(y=2026, m=8, d=1, hh=9):
    return datetime(y, m, d, hh, 0, tzinfo=cal.ALMATY)


def test_lists_matching_weekdays_within_horizon():
    now = _now()                      # суббота 01.08.2026
    horizon = (now + timedelta(weeks=2)).date()
    occ = series.iter_occurrences("mon,wed,fri", "10:00", "12:00", None,
                                  now, horizon)
    days = {datetime.fromisoformat(s).astimezone(cal.ALMATY).weekday()
            for s, _ in occ}
    assert days == {0, 2, 4}
    assert len(occ) == 6              # 2 недели × 3 дня


def test_end_time_none_gives_none_end():
    now = _now()
    occ = series.iter_occurrences("mon", "10:00", None, None,
                                  now, (now + timedelta(weeks=1)).date())
    assert occ and all(end is None for _, end in occ)


def test_until_local_caps_the_grid():
    now = _now()
    horizon = (now + timedelta(weeks=8)).date()
    until = (now + timedelta(days=10)).date().isoformat()
    occ = series.iter_occurrences("mon,wed,fri", "10:00", "12:00", until,
                                  now, horizon)
    assert all(
        datetime.fromisoformat(s).astimezone(cal.ALMATY).date().isoformat() <= until
        for s, _ in occ)


def test_occurrence_earlier_today_is_skipped():
    now = _now(2026, 8, 3, 14)         # понедельник, 14:00 — 10:00 уже прошло
    occ = series.iter_occurrences("mon", "10:00", "12:00", None,
                                  now, (now + timedelta(weeks=1)).date())
    first = datetime.fromisoformat(occ[0][0]).astimezone(cal.ALMATY)
    assert first.date() > now.date()


def test_times_convert_through_almaty():
    now = _now()
    occ = series.iter_occurrences("mon", "10:00", "12:00", None,
                                  now, (now + timedelta(weeks=1)).date())
    start, end = occ[0]
    assert start.endswith("+00:00") and start[11:16] == "05:00"   # 10:00 Алматы
    assert end[11:16] == "07:00"


def test_bad_weekday_raises():
    now = _now()
    with pytest.raises(ValueError):
        series.iter_occurrences("funday", "10:00", None, None,
                                now, (now + timedelta(weeks=1)).date())


def test_generate_matches_iter_occurrences(db):
    """Регресс на рефактор: generate() материализует ровно те слоты,
    которые отдаёт чистый генератор."""
    s = series.add(db, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00")
    db.commit()
    series.generate(db)
    db.commit()
    stored = [r["start_utc"] for r in db.execute(
        "SELECT start_utc FROM events WHERE series_id=? ORDER BY start_utc",
        (s["id"],))]
    now_local = datetime.now(cal.ALMATY)
    horizon = (now_local + timedelta(weeks=series.HORIZON_WEEKS)).date()
    expected = [start for start, _ in series.iter_occurrences(
        "mon,wed,fri", "10:00", "12:00", None, now_local, horizon)]
    assert stored == expected
