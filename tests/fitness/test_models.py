from datetime import datetime, timezone

from fitness.models import CLUB_TZ, Booking, ClassSlot, ClubRules


def _slot(**kw):
    base = dict(
        class_id="c1",
        title="Функциональный тренинг",
        trainer="Иван",
        club_id="abay",
        starts_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc),
        capacity=20,
        taken=18,
        booking_opens_at=None,
        my_status="none",
    )
    base.update(kw)
    return ClassSlot(**base)


def test_local_start_renders_in_club_timezone():
    slot = _slot()
    assert slot.local_start.tzinfo.key == "Asia/Almaty"
    assert slot.local_start.hour == 19  # 14:00 UTC = 19:00 Алматы


def test_spots_left_counts_remaining_places():
    assert _slot().spots_left == 2


def test_spots_left_is_none_when_capacity_unknown():
    assert _slot(capacity=None).spots_left is None


def test_naive_datetime_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="tz-aware"):
        _slot(starts_at=datetime(2026, 8, 11, 14, 0))


def test_club_rules_default_to_unknown():
    rules = ClubRules()
    assert rules.cancel_deadline_hours is None
    assert rules.booking_opens_days_ahead is None


def test_booking_carries_status():
    booking = Booking(
        class_id="c1",
        title="Йога",
        starts_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        status="booked",
    )
    assert booking.status == "booked"
    assert CLUB_TZ.key == "Asia/Almaty"
