"""Рендер расписания и утреннего дайджеста. Всё время — клубное."""

from datetime import datetime

from fitness.models import CLUB_TZ, Booking, ClassSlot
from fitness.rules import WatchRule


def _spots(slot: ClassSlot) -> str:
    left = slot.spots_left
    if left is None:
        return ""
    return f" — свободно мест: {left}" if left else " — мест нет"


def render_schedule(slots: list[ClassSlot]) -> str:
    if not slots:
        return "На эту дату занятий нет."
    lines = []
    for slot in sorted(slots, key=lambda s: s.starts_at):
        mark = {"booked": "✅ ", "waitlisted": "⏳ "}.get(slot.my_status, "")
        trainer = f", {slot.trainer}" if slot.trainer else ""
        lines.append(
            f"{mark}{slot.local_start:%H:%M} — {slot.title}{trainer}{_spots(slot)}"
        )
    return "\n".join(lines)


def render_digest(*, bookings: list[Booking], slots: list[ClassSlot],
                  rules: list[WatchRule], now: datetime) -> str | None:
    """Утренний дайджест: только сегодняшние занятия, на которые есть моя запись.

    Весь список свободных занятий и предсказания автозаписи сознательно не
    показываем — владелец хочет видеть только то, куда уже записан. `slots` и
    `rules` остаются в сигнатуре ради совместимости с вызывающим кодом.
    """
    today = now.astimezone(CLUB_TZ).date()
    mine = [
        b for b in bookings
        if b.local_start.date() == today and b.starts_at >= now
    ]
    if not mine:
        return None
    rows = "\n".join(
        f"• {b.local_start:%H:%M} — {b.title}"
        for b in sorted(mine, key=lambda b: b.starts_at)
    )
    return f"Сегодня ты записан:\n{rows}"
