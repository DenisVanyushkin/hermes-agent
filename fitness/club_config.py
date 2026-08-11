"""Правила и идентификаторы клуба как данные (F10, F11)."""

import os

from fitness.models import ClubRules

DEFAULT_CLUB_ID = "62212767097e5c317055385a"  # Invictus Fitness Gagarin

CLUB_RULES = ClubRules(
    booking_opens_days_ahead=7,   # окно до КОНЦА дня «сегодня + 7», не «сейчас + 168 ч»
    cancel_deadline_hours=2,
    max_active_bookings=None,     # в API не выражен и, вероятно, не существует
    no_show_penalty="блокировка записи на 3 дня",
)


def default_club_id() -> str:
    return os.environ.get("FITNESS_CLUB_ID") or DEFAULT_CLUB_ID


def load_club_rules() -> ClubRules:
    return CLUB_RULES
