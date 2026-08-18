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
from fitness.auth import LoginError, MissingPhoneNumber
from fitness.models import CLUB_TZ
from fitness.rules import RuleStore, WatchRule
from tools.registry import registry

TOOLSET = "fitness_booking"
REGISTERED_NAMES: list[str] = []

_EMOJI = "🏋️"


def _client() -> InvictusClient:
    return InvictusClient()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _schema(name: str, description: str, parameters: dict) -> dict:
    return {"name": name, "description": description, "parameters": parameters}


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


def fitness_cancel(class_id: str, confirm_penalty: bool = False) -> str:
    """Отменить запись. При отмене после дедлайна клуба требуется confirm_penalty=True.

    Гейт информирующий: он выносит цену отмены на поверхность, чтобы решение
    принимал человек, а не модель по умолчанию. Цена здесь — не одно занятие, а
    блокировка записи на 3 дня, то есть остановка всей автоматики.
    """
    try:
        client = _client()
        booking = next((b for b in client.my_bookings() if b.class_id == class_id), None)
    except SessionDead as exc:
        return f"⚠️ Сессия Invictus недействительна ({exc})."
    if booking is None:
        return f"Запись {class_id} не найдена среди активных."

    rules = load_club_rules()
    hours_left = (booking.starts_at - _now()).total_seconds() / 3600

    if not confirm_penalty:
        if rules.cancel_deadline_hours is None:
            # Доказать, что отмена бесплатна, нечем — считаем её платной.
            return (
                f"Дедлайн бесплатной отмены неизвестен, поэтому отмена «{booking.title}» "
                f"{booking.local_start:%d.%m %H:%M} может стоить санкции клуба. "
                "Подтвердить — вызвать повторно с confirm_penalty=True."
            )
        if hours_left < rules.cancel_deadline_hours:
            penalty = rules.no_show_penalty or "санкция по правилам клуба"
            return (
                f"До занятия «{booking.title}» {booking.local_start:%d.%m %H:%M} осталось "
                f"{hours_left:.1f} ч, дедлайн бесплатной отмены — "
                f"{rules.cancel_deadline_hours} ч. "
                f"Отмена сейчас: {penalty}. "
                "Подтвердить — вызвать повторно с confirm_penalty=True."
            )

    try:
        client.cancel(class_id)
    except BookingRejected as exc:
        return f"Отменить не удалось: {exc.reason}. {exc.detail}"
    except SessionDead as exc:
        return f"⚠️ Сессия Invictus недействительна ({exc})."
    return f"Отменил запись: «{booking.title}» {booking.local_start:%d.%m %H:%M}."


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




# --- регистрация -----------------------------------------------------------
#
# Вызовы registry.register(...) обязаны стоять ЛИТЕРАЛЬНО на верхнем уровне
# модуля: discover_builtin_tools() парсит файл через ast и импортирует его,
# только если найдёт среди statement'ов модуля выражение вида
# `registry.register(...)` (_module_registers_tools). Обёртка-хелпер прячет
# вызов внутрь функции — файл перестаёт считаться тулфайлом, молча не
# импортируется, и тулсет не появляется ни в одной платформе. Тесты этого не
# ловят: они импортируют модуль напрямую, минуя дискавери.

def fitness_login_request(phone_number: str | None = None, person_name: str | None = None) -> str:
    """Запросить SMS-код Invictus. phone_number — только чтобы сменить номер."""
    try:
        number = _client().request_otp(phone_number)
    except MissingPhoneNumber:
        return "Нужен номер телефона аккаунта Invictus — продиктуй его."
    except LoginError as exc:
        return f"⚠️ Не удалось запросить код: {exc}"
    who = f"{person_name}, " if person_name else ""
    return f"{who}код отправлен по SMS на номер …{number[-4:]}. Продиктуй его — я введу."


def fitness_login_confirm(code: str) -> str:
    """Подтвердить код из SMS и сохранить сессию Invictus."""
    try:
        session = _client().login(code)
    except MissingPhoneNumber:
        return "Сначала запроси код: fitness_login_request."
    except LoginError as exc:
        return f"Код не подошёл ({exc}). Запроси новый через fitness_login_request."
    local = session.expires_at.astimezone(CLUB_TZ).strftime("%d.%m %H:%M")
    return f"✅ Готово. Сессия Invictus активна до {local} (клубное время)."


registry.register(
    name="fitness_schedule",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_schedule",
        "Расписание групповых программ Invictus на дату (клубное время, Asia/Almaty)",
        _obj(
            {
                "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
                "club_id": {"type": "string", "description": "Идентификатор клуба"},
            },
            ["date"],
        ),
    ),
    handler=lambda args, **kw: fitness_schedule(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_my_bookings",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_my_bookings",
        "Мои записи на групповые программы Invictus и состояние блокировки записи",
        _obj({}),
    ),
    handler=lambda args, **kw: fitness_my_bookings(),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_book",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_book",
        "Записаться на занятие Invictus по class_id из fitness_schedule",
        _obj({"class_id": {"type": "string", "description": "Идентификатор занятия"}},
             ["class_id"]),
    ),
    handler=lambda args, **kw: fitness_book(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_cancel",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_cancel",
        "Отменить запись на занятие Invictus. После дедлайна бесплатной отмены "
        "требует confirm_penalty=True: санкция клуба — блокировка записи на 3 дня",
        _obj(
            {
                "class_id": {"type": "string", "description": "Идентификатор занятия"},
                "confirm_penalty": {
                    "type": "boolean",
                    "description": "Подтверждение отмены со штрафом (после дедлайна)",
                },
            },
            ["class_id"],
        ),
    ),
    handler=lambda args, **kw: fitness_cancel(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_watch_add",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_watch_add",
        "Создать правило автозаписи Invictus (повторяющееся или разовое)",
        _obj(
            {
                "title_pattern": {"type": "string", "description": "Подстрока названия"},
                "kind": {"type": "string", "enum": ["recurring", "oneshot"]},
                "weekday": {
                    "type": "integer",
                    "description": "0=понедельник .. 6=воскресенье, клубное время",
                },
                "at_time": {"type": "string", "description": "Время занятия HH:MM, клубное"},
                "target_date": {"type": "string", "description": "YYYY-MM-DD, разовое правило"},
                "club_id": {"type": "string"},
                "trainer": {"type": "string"},
                "window_minutes": {"type": "integer", "description": "Допуск по времени"},
                "waitlist_ok": {"type": "boolean", "description": "Вставать ли в лист ожидания"},
            },
            ["title_pattern"],
        ),
    ),
    handler=lambda args, **kw: fitness_watch_add(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_watch_list",
    toolset=TOOLSET,
    schema=_schema("fitness_watch_list", "Показать правила автозаписи Invictus", _obj({})),
    handler=lambda args, **kw: fitness_watch_list(),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_watch_remove",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_watch_remove",
        "Удалить правило автозаписи Invictus по его id",
        _obj({"rule_id": {"type": "string"}}, ["rule_id"]),
    ),
    handler=lambda args, **kw: fitness_watch_remove(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_login_request",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_login_request",
        "Запросить SMS-код для входа в Invictus. phone_number укажи только чтобы "
        "залогинить другой номер; person_name — имя для обращения в ответе",
        _obj(
            {
                "phone_number": {"type": "string", "description": "Номер аккаунта (только для смены)"},
                "person_name": {"type": "string", "description": "Имя для обращения"},
            }
        ),
    ),
    handler=lambda args, **kw: fitness_login_request(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_login_confirm",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_login_confirm",
        "Подтвердить вход в Invictus кодом из SMS (после fitness_login_request)",
        _obj({"code": {"type": "string", "description": "Код из SMS"}}, ["code"]),
    ),
    handler=lambda args, **kw: fitness_login_confirm(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

REGISTERED_NAMES.extend(
    [
        "fitness_schedule",
        "fitness_my_bookings",
        "fitness_book",
        "fitness_cancel",
        "fitness_watch_add",
        "fitness_watch_list",
        "fitness_watch_remove",
        "fitness_login_request",
        "fitness_login_confirm",
    ]
)
