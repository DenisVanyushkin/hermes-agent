"""cal.overlaps(): границы занятости для гвардрейла cal add/update.

Опорный кейс из прода: Интервизия 10:00–12:15 Алматы (05:00–07:15 UTC).
"""
import pytest

from fam import cal

BUSY_START = "2026-08-06T05:00:00+00:00"   # 10:00 Алматы
BUSY_END = "2026-08-06T07:15:00+00:00"     # 12:15 Алматы


def _busy(db, title="Интервизия", start=BUSY_START, end=BUSY_END):
    e = cal.add(db, title, start, end_utc=end)
    db.commit()
    return e


def test_interval_inside_conflicts(db):
    _busy(db)
    hits = cal.overlaps(db, "2026-08-06T06:00:00+00:00",
                        "2026-08-06T07:00:00+00:00")
    assert [h["title"] for h in hits] == ["Интервизия"]


def test_point_inside_conflicts(db):
    _busy(db)
    hits = cal.overlaps(db, "2026-08-06T06:00:00+00:00")
    assert len(hits) == 1


def test_point_exactly_at_end_is_clean(db):
    _busy(db)
    assert cal.overlaps(db, BUSY_END) == []


def test_back_to_back_after_is_clean(db):
    _busy(db)
    assert cal.overlaps(db, BUSY_END, "2026-08-06T08:00:00+00:00") == []


def test_back_to_back_before_is_clean(db):
    _busy(db)
    assert cal.overlaps(db, "2026-08-06T04:00:00+00:00", BUSY_START) == []


def test_equal_starts_conflict_even_for_two_points(db):
    _busy(db, title="ДР Таи", start=BUSY_START, end=None)
    hits = cal.overlaps(db, BUSY_START)
    assert [h["title"] for h in hits] == ["ДР Таи"]


def test_equal_start_with_interval_conflicts(db):
    _busy(db)
    hits = cal.overlaps(db, BUSY_START)
    assert len(hits) == 1


def test_stored_point_inside_new_interval_conflicts(db):
    _busy(db, title="Созвон", start="2026-08-06T06:00:00+00:00", end=None)
    hits = cal.overlaps(db, BUSY_START, BUSY_END)
    assert [h["title"] for h in hits] == ["Созвон"]


def test_non_active_events_ignored(db):
    e1 = _busy(db, title="Отменённое")
    e2 = _busy(db, title="Сделанное")
    cal.cancel(db, e1["id"])
    cal.done(db, e2["id"])
    db.commit()
    assert cal.overlaps(db, "2026-08-06T06:00:00+00:00") == []


def test_exclude_id_skips_the_event_itself(db):
    e = _busy(db)
    assert cal.overlaps(db, BUSY_START, BUSY_END, exclude_id=e["id"]) == []


def test_far_away_event_not_returned(db):
    _busy(db, start="2026-08-10T05:00:00+00:00", end="2026-08-10T07:00:00+00:00")
    assert cal.overlaps(db, BUSY_START, BUSY_END) == []


def test_results_sorted_by_start(db):
    _busy(db, title="Поздняя", start="2026-08-06T06:00:00+00:00",
          end="2026-08-06T08:00:00+00:00")
    _busy(db, title="Ранняя", start=BUSY_START, end=BUSY_END)
    hits = cal.overlaps(db, "2026-08-06T06:30:00+00:00")
    assert [h["title"] for h in hits] == ["Ранняя", "Поздняя"]


def test_end_before_start_raises(db):
    with pytest.raises(ValueError):
        cal.overlaps(db, BUSY_END, BUSY_START)


def test_naive_datetime_still_rejected(db):
    with pytest.raises(ValueError):
        cal.overlaps(db, "2026-08-06T10:00:00")
