"""Рендер расписания и утреннего дайджеста. Всё время — клубное."""

from datetime import datetime, timedelta

from fitness.models import CLUB_TZ, Booking, ClassSlot
from fitness.rules import WatchRule, rule_matches


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
    today = now.astimezone(CLUB_TZ).date()
    mine = [b for b in bookings if b.local_start.date() == today and b.starts_at >= now]
    today_slots = [s for s in slots if s.local_start.date() == today and s.starts_at >= now]
    free = [s for s in today_slots if s.my_status == "none" and (s.spots_left or 0) > 0]

    horizon = now + timedelta(days=1)
    pending = [
        (rule, slot)
        for slot in slots
        if now < slot.starts_at <= horizon and slot.my_status == "none"
        for rule in rules
        if rule.active and rule_matches(rule, slot)
    ]

    if not mine and not free and not pending:
        return None

    blocks = []
    if mine:
        rows = "\n".join(
            f"• {b.local_start:%H:%M} — {b.title}"
            for b in sorted(mine, key=lambda b: b.starts_at)
        )
        blocks.append(f"Сегодня ты записан:\n{rows}")
    if free:
        rows = "\n".join(
            f"• {s.local_start:%H:%M} — {s.title}{_spots(s)}"
            for s in sorted(free, key=lambda s: s.starts_at)[:10]
        )
        blocks.append(f"Есть места сегодня:\n{rows}")
    if pending:
        rows = "\n".join(
            f"• {slot.local_start:%d.%m %H:%M} — {slot.title} (правило {rule.rule_id})"
            for rule, slot in sorted(pending, key=lambda pair: pair[1].starts_at)[:10]
        )
        blocks.append(f"Автозапись отработает в ближайшие сутки:\n{rows}")
    return "\n\n".join(blocks)
