from datetime import datetime, timedelta, timezone

from fitness.models import Booking, ClubRules
from fitness.reminders import pending_reminders, render_reminder

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
RULES = ClubRules(cancel_deadline_hours=2, no_show_penalty="блокировка записи на 3 дня")


def _booking(hours_ahead, status="booked", class_id="c1"):
    return Booking(
        class_id=class_id,
        title="Йога",
        starts_at=NOW + timedelta(hours=hours_ahead),
        status=status,
    )


def test_reminder_fires_inside_the_window_before_the_deadline():
    # дедлайн за 2 ч до занятия, окно напоминания открывается за 2.5 ч
    assert pending_reminders([_booking(2.4)], RULES, NOW, notified=set())


def test_reminder_does_not_fire_too_early():
    assert pending_reminders([_booking(5)], RULES, NOW, notified=set()) == []


def test_reminder_does_not_fire_after_the_deadline_has_passed():
    # до занятия 1 ч, дедлайн уже прошёл — напоминать поздно и бессмысленно
    assert pending_reminders([_booking(1)], RULES, NOW, notified=set()) == []


def test_already_notified_booking_is_skipped():
    assert pending_reminders([_booking(2.4)], RULES, NOW, notified={"c1"}) == []


def test_waitlisted_booking_is_not_reminded():
    # место не занято, санкция не грозит
    assert pending_reminders([_booking(2.4, status="waitlisted")], RULES, NOW,
                             notified=set()) == []


def test_unknown_deadline_falls_back_to_three_hours():
    rules = ClubRules()
    assert pending_reminders([_booking(3.4)], rules, NOW, notified=set())
    assert pending_reminders([_booking(6)], rules, NOW, notified=set()) == []


def test_message_states_the_deadline_and_the_real_sanction():
    text = render_reminder(_booking(2.4), RULES, NOW)
    assert "17:24" in text  # 12:00 UTC + 2.4 ч = 14:24 UTC = 19:24 Алматы, дедлайн 17:24
    assert "3 дня" in text
    assert "тренер" in text.lower()


def test_message_renders_class_time_in_club_timezone():
    assert "19:24" in render_reminder(_booking(2.4), RULES, NOW)
