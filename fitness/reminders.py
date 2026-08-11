"""Напоминание перед дедлайном бесплатной отмены.

Существует потому, что санкция Invictus — блокировка записи на 3 дня, а значит
одна забытая тренировка останавливает всю автоматику, а не стоит одного занятия.
Автоматических действий не совершает: отмена остаётся решением человека.
"""

from datetime import datetime, timedelta

from fitness.models import Booking, ClubRules

REMINDER_LEAD_MINUTES = 30
FALLBACK_DEADLINE_HOURS = 3.0


def _deadline_hours(club_rules: ClubRules) -> float:
    if club_rules.cancel_deadline_hours is None:
        return FALLBACK_DEADLINE_HOURS
    return float(club_rules.cancel_deadline_hours)


def deadline_at(booking: Booking, club_rules: ClubRules) -> datetime:
    return booking.starts_at - timedelta(hours=_deadline_hours(club_rules))


def pending_reminders(bookings: list[Booking], club_rules: ClubRules, now: datetime,
                      notified: set[str]) -> list[Booking]:
    due = []
    for booking in bookings:
        if booking.status != "booked" or booking.class_id in notified:
            continue
        deadline = deadline_at(booking, club_rules)
        window_opens = deadline - timedelta(minutes=REMINDER_LEAD_MINUTES)
        if window_opens <= now <= deadline:
            due.append(booking)
    return due


def render_reminder(booking: Booking, club_rules: ClubRules, now: datetime) -> str:
    from fitness.models import CLUB_TZ

    deadline = deadline_at(booking, club_rules).astimezone(CLUB_TZ)
    sanction = club_rules.no_show_penalty or "блокировка записи на 3 дня"
    return (
        f"⏰ Ты записан: «{booking.title}» сегодня в {booking.local_start:%H:%M}.\n"
        f"Бесплатно отменить можно до {deadline:%H:%M}.\n"
        f"Пропуск, поздняя отмена или отсутствие отметки у тренера — {sanction}, "
        "и на эти дни встанет вся автозапись."
    )
