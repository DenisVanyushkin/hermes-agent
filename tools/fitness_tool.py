"""Инструменты записи на групповые программы Invictus (платформа entryx.io).

Форма регистрации сверена с живым tools/legal_research_tool.py: реестр
импортируется как tools.registry, схема передаётся словарём, обработчик
принимает один словарь args. Сами инструменты остаются обычными функциями с
именованными параметрами — так их можно вызывать и из тестов, и из кода.
"""

import uuid
from datetime import date as date_cls
from datetime import datetime, time, timezone

from fitness.club_config import default_club_id, load_club_rules
from fitness.digest import render_schedule
from fitness.invictus_client import BookingRejected, InvictusClient, SessionDead
from fitness.rules import RuleStore, WatchRule
from tools.registry import registry

TOOLSET = "fitness_booking"
REGISTERED_NAMES: list[str] = []

_EMOJI = "🏋️"


def _client() -> InvictusClient:
    return InvictusClient()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _register(fn, description: str, parameters: dict) -> None:
    REGISTERED_NAMES.append(fn.__name__)
    registry.register(
        name=fn.__name__,
        toolset=TOOLSET,
        schema={
            "name": fn.__name__,
            "description": description,
            "parameters": parameters,
        },
        handler=lambda args, **kw: str(fn(**(args or {}))),
        requires_env=[],
        is_async=False,
        description=description,
        emoji=_EMOJI,
        max_result_size_chars=8000,
    )


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


# --- чтение ----------------------------------------------------------------


def fitness_schedule(date: str, club_id: str | None = None) -> str:
    """Расписание групповых программ на дату (YYYY-MM-DD), клубное время."""
    try:
        day = date_cls.fromisoformat(date)
    except ValueError:
        return f"Не разобрал дату {date!r}: нужен формат YYYY-MM-DD."
    try:
        slots = _client().schedule(day, day, club_id or default_club_id())
    except SessionDead as exc:
        return f"⚠️ Сессия Invictus недействительна ({exc}). Нужен новый захват токена."
    return render_schedule(slots)


def fitness_my_bookings() -> str:
    """Мои текущие записи на групповые программы."""
    try:
        info = _client().bookings_info()
    except SessionDead as exc:
        return f"⚠️ Сессия Invictus недействительна ({exc})."

    lines = []
    if info.is_banned(_now()):
        from fitness.models import CLUB_TZ

        local = info.banned_till.astimezone(CLUB_TZ)
        reason = info.ban_reason or "причина не указана"
        lines.append(f"⛔ Запись заблокирована до {local:%d.%m %H:%M} — {reason}.")
    if not info.bookings:
        lines.append("Активных записей нет.")
    else:
        lines.extend(
            f"• {b.local_start:%d.%m %H:%M} — {b.title}"
            + (" (лист ожидания)" if b.status == "waitlisted" else "")
            for b in sorted(info.bookings, key=lambda b: b.starts_at)
        )
    return "\n".join(lines)


# --- запись ----------------------------------------------------------------


def fitness_book(class_id: str) -> str:
    """Записаться на занятие по его class_id из fitness_schedule."""
    try:
        booking = _client().book(class_id)
    except BookingRejected as exc:
        return f"Записаться не удалось: {exc.reason}. {exc.detail}"
    except SessionDead as exc:
        return f"⚠️ Сессия Invictus недействительна ({exc})."
    return f"✅ Записал: «{booking.title}» {booking.local_start:%d.%m %H:%M}."


# --- правила автозаписи -----------------------------------------------------


def fitness_watch_add(
    title_pattern: str,
    kind: str = "recurring",
    weekday: int | None = None,
    at_time: str | None = None,
    target_date: str | None = None,
    club_id: str | None = None,
    trainer: str | None = None,
    window_minutes: int = 30,
    waitlist_ok: bool = True,
) -> str:
    """Создать правило автозаписи: повторяющееся (weekday+at_time) или разовое (target_date)."""
    if kind not in {"recurring", "oneshot"}:
        return "kind должен быть recurring или oneshot."
    if kind == "recurring" and weekday is None:
        return "Для повторяющегося правила нужен weekday (0=понедельник .. 6=воскресенье)."
    if kind == "oneshot" and not target_date:
        return "Для разового правила нужен target_date (YYYY-MM-DD)."
    try:
        rule = WatchRule(
            rule_id=uuid.uuid4().hex[:8],
            kind=kind,
            title_pattern=title_pattern,
            club_id=club_id or default_club_id(),
            weekday=weekday,
            at_time=time.fromisoformat(at_time) if at_time else None,
            window_minutes=window_minutes,
            trainer=trainer,
            waitlist_ok=waitlist_ok,
            target_date=date_cls.fromisoformat(target_date) if target_date else None,
            expires_at=None,
            active=True,
        )
    except ValueError as exc:
        return f"Не разобрал параметры правила: {exc}."
    RuleStore().add(rule)
    return f"Правило {rule.rule_id} создано: «{title_pattern}»."


def fitness_watch_list() -> str:
    """Показать правила автозаписи."""
    rules = RuleStore().load()
    if not rules:
        return "Правил автозаписи нет."
    return "\n".join(
        f"• {r.rule_id}: «{r.title_pattern}» "
        + (
            f"разовое {r.target_date.isoformat()}"
            if r.kind == "oneshot"
            else f"еженедельно, день {r.weekday}"
        )
        + (f" в {r.at_time.isoformat('minutes')}" if r.at_time else "")
        + ("" if r.active else " (выключено)")
        for r in rules
    )


def fitness_watch_remove(rule_id: str) -> str:
    """Удалить правило автозаписи по его id."""
    return (
        f"Правило {rule_id} удалено."
        if RuleStore().remove(rule_id)
        else f"Правило {rule_id} не найдено."
    )


_register(
    fitness_schedule,
    "Расписание групповых программ Invictus на дату (клубное время, Asia/Almaty)",
    _obj(
        {
            "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
            "club_id": {"type": "string", "description": "Идентификатор клуба (необязательно)"},
        },
        ["date"],
    ),
)
_register(
    fitness_my_bookings,
    "Мои записи на групповые программы Invictus и состояние блокировки записи",
    _obj({}),
)
_register(
    fitness_book,
    "Записаться на занятие Invictus по class_id из fitness_schedule",
    _obj(
        {"class_id": {"type": "string", "description": "Идентификатор занятия"}},
        ["class_id"],
    ),
)
_register(
    fitness_watch_add,
    "Создать правило автозаписи Invictus (повторяющееся или разовое)",
    _obj(
        {
            "title_pattern": {"type": "string", "description": "Подстрока названия занятия"},
            "kind": {"type": "string", "enum": ["recurring", "oneshot"]},
            "weekday": {
                "type": "integer",
                "description": "0=понедельник .. 6=воскресенье, клубное время",
            },
            "at_time": {"type": "string", "description": "Время занятия HH:MM, клубное"},
            "target_date": {"type": "string", "description": "YYYY-MM-DD для разового правила"},
            "club_id": {"type": "string"},
            "trainer": {"type": "string"},
            "window_minutes": {"type": "integer", "description": "Допуск по времени, минуты"},
            "waitlist_ok": {"type": "boolean", "description": "Вставать ли в лист ожидания"},
        },
        ["title_pattern"],
    ),
)
_register(fitness_watch_list, "Показать правила автозаписи Invictus", _obj({}))
_register(
    fitness_watch_remove,
    "Удалить правило автозаписи Invictus по его id",
    _obj({"rule_id": {"type": "string"}}, ["rule_id"]),
)
