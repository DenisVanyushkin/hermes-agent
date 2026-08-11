"""Доменные модели интеграции с Invictus.

Все моменты времени внутри системы — tz-aware UTC. Наружу рендерится CLUB_TZ.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

CLUB_TZ = ZoneInfo("Asia/Almaty")


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} должен быть tz-aware datetime")
    return value


@dataclass(frozen=True)
class ClassSlot:
    class_id: str
    title: str
    trainer: str | None
    club_id: str
    starts_at: datetime
    ends_at: datetime
    capacity: int | None
    taken: int | None
    booking_opens_at: datetime | None
    my_status: str  # "none" | "booked" | "waitlisted"

    def __post_init__(self) -> None:
        _require_aware(self.starts_at, "starts_at")
        _require_aware(self.ends_at, "ends_at")
        if self.booking_opens_at is not None:
            _require_aware(self.booking_opens_at, "booking_opens_at")

    @property
    def spots_left(self) -> int | None:
        if self.capacity is None or self.taken is None:
            return None
        return max(0, self.capacity - self.taken)

    @property
    def local_start(self) -> datetime:
        return self.starts_at.astimezone(CLUB_TZ)


@dataclass(frozen=True)
class Booking:
    class_id: str
    title: str
    starts_at: datetime
    status: str  # "booked" | "waitlisted"

    def __post_init__(self) -> None:
        _require_aware(self.starts_at, "starts_at")

    @property
    def local_start(self) -> datetime:
        return self.starts_at.astimezone(CLUB_TZ)


@dataclass(frozen=True)
class ClubRules:
    """Правила клуба как данные. None означает «неизвестно» и включает fail-closed."""

    booking_opens_days_ahead: int | None = None
    cancel_deadline_hours: float | None = None
    max_active_bookings: int | None = None
    no_show_penalty: str | None = None
