from datetime import datetime, time, timedelta, timezone

from fitness.digest import render_digest, render_schedule
from fitness.models import Booking, ClassSlot
from fitness.rules import WatchRule

NOW = datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc)  # 07:00 Алматы


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


def test_schedule_is_rendered_in_club_time():
    text = render_schedule([_slot()])
    assert "19:00" in text  # 14:00 UTC
    assert "Функциональный тренинг" in text


def test_schedule_shows_remaining_spots():
    assert "2" in render_schedule([_slot()])


def test_schedule_marks_my_bookings():
    assert "✅" in render_schedule([_slot(my_status="booked")])


def test_schedule_marks_waitlisted_slots():
    assert "⏳" in render_schedule([_slot(my_status="waitlisted")])


def test_schedule_hides_spot_count_when_unknown():
    text = render_schedule([_slot(capacity=None, taken=None)])
    assert "мест" not in text.lower() or "?" in text


def test_schedule_is_sorted_by_start_time():
    late = _slot(class_id="late", title="Поздняя",
                 starts_at=datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc),
                 ends_at=datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc))
    text = render_schedule([late, _slot()])
    assert text.index("Функциональный") < text.index("Поздняя")


def test_empty_schedule_says_so():
    assert "нет" in render_schedule([]).lower()


def test_digest_is_none_when_nothing_to_say():
    assert render_digest(bookings=[], slots=[], rules=[], now=NOW) is None


def test_digest_lists_my_bookings():
    booking = Booking(
        class_id="c1",
        title="Йога",
        starts_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        status="booked",
    )
    text = render_digest(bookings=[booking], slots=[], rules=[], now=NOW)
    assert "Йога" in text
    assert "19:00" in text


def test_digest_shows_only_my_bookings_not_free_classes():
    booking = Booking(
        class_id="c1",
        title="Йога",
        starts_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        status="booked",
    )
    free_slot = _slot(class_id="free", title="Бокс", my_status="none", capacity=20, taken=1)
    text = render_digest(bookings=[booking], slots=[free_slot], rules=[], now=NOW)
    assert "Йога" in text
    assert "Бокс" not in text
    assert "есть места" not in text.lower()


def test_digest_is_none_when_only_free_slots_available():
    free_slot = _slot(class_id="free", title="Бокс", my_status="none", capacity=20, taken=1)
    assert render_digest(bookings=[], slots=[free_slot], rules=[], now=NOW) is None


def test_digest_ignores_rules_without_a_booking():
    rule = WatchRule(
        rule_id="r1", kind="recurring", title_pattern="функционал", club_id=None,
        weekday=1, at_time=time(19, 0), window_minutes=30, trainer=None,
        waitlist_ok=True, target_date=None,
        expires_at=NOW + timedelta(days=30), active=True,
    )
    assert render_digest(bookings=[], slots=[_slot()], rules=[rule], now=NOW) is None


def test_digest_only_covers_today():
    tomorrow_booking = Booking(
        class_id="c1", title="Йога",
        starts_at=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
        status="booked",
    )
    assert render_digest(bookings=[tomorrow_booking], slots=[], rules=[], now=NOW) is None


def test_digest_skips_bookings_that_already_started():
    past_booking = Booking(
        class_id="c1", title="Йога",
        starts_at=NOW - timedelta(hours=1),
        status="booked",
    )
    assert render_digest(bookings=[past_booking], slots=[], rules=[], now=NOW) is None
