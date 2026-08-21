"""Детерминированный движок автозаписи.

Никакого LLM: тик считает целевые занятия по правилам, проверяет ограничения и
пишет. Всякая запись подтверждается перечитом (это делает клиент).

Ревизия 3 плана изменила три вещи против первоначальной редакции:
  * `max_active_bookings` перестал быть условием работы — в API его нет и,
    вероятно, не существует; требовать неизвестный факт как fail-closed условие
    значит выключить автозапись навсегда;
  * перед любыми попытками записи читается `bookingsBannedTill` — иначе движок
    трое суток молча долбится в API после санкции клуба;
  * заполненное занятие не пробуется на запись вовсе: приложение в этом случае
    не показывает кнопку, серверное поведение неизвестно, и цена ошибки —
    трёхдневная блокировка. Идём сразу в лист ожидания, если правило разрешает.
"""

import copy
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from fitness.invictus_client import BookingRejected, SessionDead
from fitness.models import CLUB_TZ, Booking, ClubRules
from fitness.rules import RuleStore, WatchRule, is_expired, rule_matches
from fitness.session import SessionStore
from fitness.store import JsonStore, is_paused

DAILY_AUTOBOOK_LIMIT = 2
FAILURE_NOTICE_HOURS = 12
LAZY_MINUTE_OF_HOUR = 5  # в первые 5 минут часа проходим по всем дням-кандидатам
ASSUMED_CLASS_MINUTES = 60  # у Booking нет времени окончания — считаем занятие часовым
STATE_FILE = "state.json"

_DEFAULT_STATE = {"attempts": {}, "autobooked": {}, "waitlisted": [], "ban_notified_till": None}


@dataclass
class TickResult:
    """Результат тика с РАЗДЕЛЁННЫМИ аудиториями.

    `messages` уходят Амине в WhatsApp (stdout кроновой джобы доставляется
    на её `deliver` как есть), `alerts` — оператору в Telegram через
    отдельную очередь. Класть инженерный текст в `messages` = писать
    живому человеку про refresh-токены.
    """

    booked: list[Booking] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)   # Амине, в WhatsApp
    alerts: list[str] = field(default_factory=list)     # оператору, в Telegram
    api_calls: int = 0


def _load_state() -> dict:
    # deepcopy обязателен: dict(...) копирует только верхний уровень, и вложенные
    # attempts/autobooked остались бы теми же объектами, что в _DEFAULT_STATE —
    # то есть тик мутировал бы модульную константу и таскал состояние между
    # запусками внутри одного долгоживущего процесса.
    state = JsonStore(STATE_FILE).read(default=copy.deepcopy(_DEFAULT_STATE))
    for key, value in _DEFAULT_STATE.items():
        state.setdefault(key, copy.deepcopy(value))
    return state


def _save_state(state: dict) -> None:
    JsonStore(STATE_FILE).write(state, mode=0o644)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _candidate_days(rules: list[WatchRule], club_rules: ClubRules, now: datetime) -> list:
    """Дни внутри горизонта записи, на которых может сработать хоть одно правило."""
    today = now.astimezone(CLUB_TZ).date()
    days = []
    for offset in range(club_rules.booking_opens_days_ahead + 1):
        day = today + timedelta(days=offset)
        for rule in rules:
            if rule.kind == "oneshot":
                if rule.target_date == day:
                    days.append(day)
                    break
            elif rule.weekday is None or rule.weekday == day.weekday():
                days.append(day)
                break
    return days


def _days_to_query(rules: list[WatchRule], club_rules: ClubRules, now: datetime, *, lazy: bool):
    """Экономия обращений к API: вне ленивого прохода смотрим только кромку горизонта.

    Кромка — это день, который прямо сейчас входит в окно записи; именно там
    идёт гонка за местами. Разовые правила внутри горизонта проверяются всегда:
    их мало, и они срочные по определению.
    """
    candidates = _candidate_days(rules, club_rules, now)
    if lazy:
        return sorted(set(candidates))
    edge = now.astimezone(CLUB_TZ).date() + timedelta(days=club_rules.booking_opens_days_ahead)
    eager = [day for day in candidates if day == edge]
    eager += [
        rule.target_date
        for rule in rules
        if rule.kind == "oneshot" and rule.target_date in candidates
    ]
    return sorted(set(eager))


def _render_ban(info, now: datetime) -> str:
    local = info.banned_till.astimezone(CLUB_TZ)
    reason = info.ban_reason or "причина не указана"
    return (
        f"⛔ Запись в Invictus заблокирована до {local:%d.%m %H:%M} — {reason}. "
        "Автозапись приостановлена до конца блокировки."
    )


def tick(*, client, rule_store: RuleStore, club_rules: ClubRules, now: datetime,
         session_store: SessionStore) -> TickResult:
    result = TickResult()

    session = session_store.load()
    if session is None or session.is_dead:
        if session is not None and session_store.should_notify_death(session, now):
            session_store.note_death_notified(session, now)
            result.alerts.append(
                "⚠️ Сессия Invictus недействительна — автозапись остановлена. "
                "Нужен headless-логин: fitness_login_request → код из SMS → "
                "fitness_login_confirm."
            )
        return result

    if is_paused():
        return result

    # Fail-closed только по фактам, которые в принципе познаваемы (ревизия 3).
    if club_rules.booking_opens_days_ahead is None or club_rules.cancel_deadline_hours is None:
        result.alerts.append(
            "⚠️ Правила клуба не заданы — автозапись выключена fail-closed."
        )
        return result

    rules = [r for r in rule_store.load() if r.active and not is_expired(r, now)]
    if not rules:
        return result

    lazy = now.minute < LAZY_MINUTE_OF_HOUR
    days = _days_to_query(rules, club_rules, now, lazy=lazy)
    if not days:
        return result

    state = _load_state()

    # Блокировка проверяется ДО расписания и до любых попыток записи.
    info = client.bookings_info()
    result.api_calls += 1
    if info.is_banned(now):
        marker = info.banned_till.isoformat()
        if state.get("ban_notified_till") != marker:
            state["ban_notified_till"] = marker
            result.messages.append(_render_ban(info, now))
        _save_state(state)
        return result
    if state.get("ban_notified_till") is not None:
        state["ban_notified_till"] = None

    # Горизонт — это КОНЕЦ дня-кромки в клубном времени, а не «сейчас плюс N суток»:
    # клуб открывает запись на день целиком, и вечернее занятие на кромке иначе
    # отсекалось бы как «за горизонтом».
    edge_day = now.astimezone(CLUB_TZ).date() + timedelta(days=club_rules.booking_opens_days_ahead)
    horizon = datetime.combine(edge_day, time.max, tzinfo=CLUB_TZ)

    # Один запрос на минимальный интервал, покрывающий дни-кандидаты.
    wanted_days = set(days)
    slots = client.schedule(min(days), max(days))
    result.api_calls += 1
    slots = [s for s in slots if s.local_start.date() in wanted_days]

    today_key = now.astimezone(CLUB_TZ).date().isoformat()
    booked_today = state["autobooked"].get(today_key, 0)
    waitlisted = set(state.get("waitlisted") or [])
    active_bookings = [b for b in info.bookings if b.starts_at >= now]

    for slot in sorted(slots, key=lambda s: s.starts_at):
        if slot.starts_at > horizon or slot.starts_at <= now:
            continue
        matched = next((r for r in rules if rule_matches(r, slot)), None)
        if matched is None or slot.my_status != "none":
            continue

        opens = slot.booking_opens_at
        if opens is not None and opens > now:
            continue
        if opens is None and not lazy:
            continue

        # Мест нет — запись не пробуем, только лист ожидания.
        if slot.spots_left == 0:
            if matched.waitlist_ok and slot.class_id not in waitlisted:
                client.join_waitlist(slot.class_id)
                waitlisted.add(slot.class_id)
                result.messages.append(
                    f"Мест нет — встал в лист ожидания: «{slot.title}» "
                    f"{slot.local_start:%d.%m %H:%M}."
                )
            continue

        if booked_today >= DAILY_AUTOBOOK_LIMIT:
            continue
        if any(_overlaps(slot.starts_at, slot.ends_at, b.starts_at,
                         b.starts_at + timedelta(minutes=ASSUMED_CLASS_MINUTES))
               for b in active_bookings):
            result.messages.append(
                f"Пропустил «{slot.title}» {slot.local_start:%d.%m %H:%M} — "
                "конфликт с существующей записью."
            )
            continue

        key = f"{matched.rule_id}:{slot.class_id}"
        attempt = state["attempts"].setdefault(key, {"tries": 0, "notified": False})
        attempt["tries"] += 1

        try:
            booking = client.book(slot.class_id)
        except BookingRejected as exc:
            if not attempt["notified"] and \
                    slot.starts_at - now <= timedelta(hours=FAILURE_NOTICE_HOURS):
                attempt["notified"] = True
                result.messages.append(
                    f"Не удалось записаться на «{slot.title}» "
                    f"{slot.local_start:%d.%m %H:%M}: {exc.reason}."
                )
            continue
        except SessionDead as exc:
            # Обычно смерть уже зафиксировал клиент, но полагаться на это нельзя:
            # признание смерти — обязанность того, кто её увидел.
            dead = session_store.load()
            if dead is not None and not dead.is_dead:
                dead = session_store.mark_dead(dead, now, reason=str(exc))
            if dead and session_store.should_notify_death(dead, now):
                session_store.note_death_notified(dead, now)
                result.alerts.append("⚠️ Сессия Invictus умерла во время автозаписи.")
            state["waitlisted"] = sorted(waitlisted)
            _save_state(state)
            return result

        booked_today += 1
        state["autobooked"][today_key] = booked_today
        active_bookings.append(booking)
        result.booked.append(booking)
        result.messages.append(
            f"✅ Записал: «{slot.title}» {slot.local_start:%d.%m %H:%M}"
            + (f", {slot.trainer}" if slot.trainer else "")
        )
        if matched.kind == "oneshot":
            rule_store.remove(matched.rule_id)
            rules = [r for r in rules if r.rule_id != matched.rule_id]

    state["waitlisted"] = sorted(waitlisted)
    _save_state(state)
    return result
