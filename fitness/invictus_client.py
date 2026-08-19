"""Синхронный клиент API мобильного приложения Invictus (платформа entryx.io).

Сознательно на stdlib urllib и синхронный: асинхронный клиент в host-процессе
гейтвея воюет с событийным циклом (грабли image_generate).

У клиента нет метода login(): сессия подписывается человеком на ресепшне.

Привязка к контракту API сосредоточена в ENDPOINTS и FIELDS. Появление
литерального имени поля API в теле функции — дефект: вендор может переименовать
поле, и правка должна быть в одном месте.
"""

import json
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from fitness.club_config import default_club_id
from fitness.models import CLUB_TZ, Booking, ClassSlot
from fitness.session import Session, SessionStore, access_token_expiry
from fitness.auth import (
    LoginError,
    MissingPhoneNumber,
    device_headers,
    load_or_create_device,
    set_phone_number,
)

BASE_URL = "https://entryx.io"  # F1, подтверждено захватом

ENDPOINTS = {
    "schedule": "/api/events",                       # GET,  F3
    "my_bookings": "/api/users/me/bookings-info",    # GET,  F9
    "book": "/api/eventAddParticipant",              # POST, F6
    "cancel": "/api/eventDeleteParticipant",         # POST, F7 — именно POST, не DELETE
    "waitlist": "/api/addParticipantToWaitlist",     # PUT,  F8 — именно PUT
    "refresh": "/api/refresh",                       # POST, F2
    "login": "/api/login",         # POST — запрос SMS-кода
    "check_sms": "/api/checkSms",  # POST — подтверждение кода, выдаёт токены
}

# Тело book/cancel/waitlist одинаковое: {"eventId": "<id события>"}.
# Ответ book: {"message": "ok"} — без id записи, поэтому write-then-verify обязателен.
# Ответ waitlist: полное событие, проверяется прямо по нему.
# Ответ refresh: {"accessToken": "<JWT>", "refreshToken": "<43 символа>"} — без expires_in.

FIELDS = {
    "slots_root": "docs",  # конверт mongoose-paginate-v2
    "class_id": "_id",  # id СОБЫТИЯ, не groupTraining._id
    "title": ("groupTraining", "name"),
    "trainer": ("coachId", "fullName"),
    "club_id": ("clubId", "_id"),
    "zone": ("zone", "name"),
    "starts_at": ("time", "start"),
    "ends_at": ("time", "end"),
    "capacity": "maxPerson",
    "participants": "participantsList",  # длина = занято; элементы — чужие id
    "participant_user": "user",
    "waitlist": "waitlist",  # массив голых id-строк
    "event_id": "eventId",  # тело book/cancel/waitlist
    "access_token": "accessToken",
    "refresh_token": "refreshToken",
    "phone_number": "phoneNumber",  # тело login/checkSms
    "otp_method": "otpMethod",
    "sms_code": "smsCode",          # тело checkSms
    "language": "language",
    "bookings_root": "records",  # /api/users/me/bookings-info
    "booking_id": "_id",
    "booking_title": "title",
    "booking_start": "startDate",
    "banned_till": "bookingsBannedTill",
    "ban_reason": "banReason",
}
# Полей taken, booking_opens_at и my_status в API НЕТ:
#   taken     = len(participantsList)
#   my_status = сверка собственного user_id с participantsList[].user и waitlist
#   booking_opens_at = None всегда; горизонт записи берётся из ClubRules (F11)

DEVICE_HEADERS_REQUIRED = ("x-device-id", "x-app-version", "x-platform")  # F1

# Таксономия отказов book/cancel не наблюдалась и через приложение недостижима:
# когда мест нет, кнопки записи просто нет. Любой ответ, отличный от 200, —
# отказ с reason="unknown". Коды НЕ угадываем (ревизия 3 плана).
REJECT_REASONS = ("unverified", "unknown")
RATE_LIMIT_RETRIES = 2
TIMEOUT_SECONDS = 20
FALLBACK_TOKEN_TTL_HOURS = 24  # если exp в токене не читается (F2: ровно 24 часа)


class SessionDead(RuntimeError):
    """Сессия не продлевается — нужен новый захват токена."""


class BookingRejected(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class BookingsInfo:
    """Ответ /api/users/me/bookings-info: записи плюс состояние блокировки."""

    bookings: list[Booking] = field(default_factory=list)
    banned_till: datetime | None = None
    ban_reason: str | None = None

    def is_banned(self, now: datetime) -> bool:
        return self.banned_till is not None and self.banned_till > now


class UrllibTransport:
    def request(self, method, url, *, headers, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b"{}"
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", "replace")}
            return exc.code, payload


def _err_ru(payload) -> str | None:
    """Человекочитаемое сообщение сервера: {"err": {"ru": "..."}}."""
    if isinstance(payload, dict):
        err = payload.get("err")
        if isinstance(err, dict):
            return err.get("ru")
    return None


def _parse_dt(value) -> datetime:
    """Разбирает момент времени по F12 (ISO 8601 с суффиксом Z, всегда UTC)."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # В захвате naive-значения не встречались; трактуем как клубное время.
        parsed = parsed.replace(tzinfo=CLUB_TZ)
    return parsed.astimezone(timezone.utc)


def _dig(row: dict, key):
    """Значение FIELDS — либо имя ключа, либо кортеж-путь по вложенным объектам."""
    if isinstance(key, tuple):
        node = row
        for part in key:
            if node is None:
                return None
            node = node.get(part)
        return node
    return row.get(key)


def _my_status(row: dict, my_user_id: str) -> str:
    """Сводит списки участников к одному значению.

    participantsList содержит идентификаторы других членов клуба. Он
    схлопывается здесь и дальше по коду не передаётся — ни в модели, ни в логи,
    ни в кэш.
    """
    participants = row.get(FIELDS["participants"]) or []
    waitlist = row.get(FIELDS["waitlist"]) or []
    if my_user_id and any(
        entry.get(FIELDS["participant_user"]) == my_user_id for entry in participants
    ):
        return "booked"
    if my_user_id and my_user_id in waitlist:
        return "waitlisted"
    return "none"


def parse_slots(payload: dict, *, my_user_id: str) -> list[ClassSlot]:
    """Разбирает /api/events."""
    slots = []
    for row in payload.get(FIELDS["slots_root"], []) or []:
        participants = row.get(FIELDS["participants"]) or []
        trainer = _dig(row, FIELDS["trainer"])
        slots.append(
            ClassSlot(
                class_id=str(row[FIELDS["class_id"]]),
                title=_dig(row, FIELDS["title"]),
                trainer=trainer.strip() if trainer else None,
                club_id=str(_dig(row, FIELDS["club_id"]) or ""),
                starts_at=_parse_dt(_dig(row, FIELDS["starts_at"])),
                ends_at=_parse_dt(_dig(row, FIELDS["ends_at"])),
                capacity=row.get(FIELDS["capacity"]),
                taken=len(participants),
                booking_opens_at=None,  # поля нет в API; горизонт берётся из ClubRules
                my_status=_my_status(row, my_user_id),
            )
        )
    return slots


def parse_bookings_info(payload: dict) -> BookingsInfo:
    """Разбирает /api/users/me/bookings-info."""
    bookings = [
        Booking(
            class_id=str(row[FIELDS["booking_id"]]),
            title=row.get(FIELDS["booking_title"]) or "",
            starts_at=_parse_dt(row[FIELDS["booking_start"]]),
            status="booked",
        )
        for row in payload.get(FIELDS["bookings_root"], []) or []
    ]
    banned_raw = payload.get(FIELDS["banned_till"])
    return BookingsInfo(
        bookings=bookings,
        banned_till=_parse_dt(banned_raw) if banned_raw else None,
        ban_reason=payload.get(FIELDS["ban_reason"]),
    )


def bookings_from_slots(slots: list[ClassSlot]) -> list[Booking]:
    """Пометка «я записан» в выдаче расписания (не источник «моих записей»)."""
    return [
        Booking(class_id=s.class_id, title=s.title, starts_at=s.starts_at, status=s.my_status)
        for s in slots
        if s.my_status in {"booked", "waitlisted"}
    ]


class InvictusClient:
    def __init__(
        self,
        *,
        transport=None,
        session_store: SessionStore | None = None,
        now: Callable[[], datetime] | None = None,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._transport = transport or UrllibTransport()
        self._sessions = session_store or SessionStore()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._base_url = base_url
        self._sleep = sleep or time_module.sleep

    def __repr__(self) -> str:  # токены наружу не выносим
        return f"<InvictusClient base_url={self._base_url!r}>"

    # --- публичный API -------------------------------------------------

    def schedule(
        self, day_from: date, day_to: date | None = None, club_id: str | None = None
    ) -> list[ClassSlot]:
        """Расписание за диапазон дат одним запросом.

        Границы приложение шлёт как сутки клубного времени, выраженные в UTC:
        00:00 Алматы = 19:00Z предыдущего дня.
        """
        day_to = day_to or day_from
        start = datetime.combine(day_from, time.min, tzinfo=CLUB_TZ).astimezone(timezone.utc)
        end = datetime.combine(day_to, time.max, tzinfo=CLUB_TZ).astimezone(timezone.utc)
        query = {
            "filter[fromDate]": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "filter[toDate]": end.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            "filter[clubId]": club_id or default_club_id(),
            "pagination[enabled]": "false",
            "pagination[page]": "1",
        }
        session = self._ensure_live_session()
        _, payload = self._call("GET", "schedule", query=query)
        return parse_slots(payload, my_user_id=session.user_id)

    def bookings_info(self) -> BookingsInfo:
        """Мои записи вместе с состоянием блокировки (bookingsBannedTill)."""
        _, payload = self._call("GET", "my_bookings")
        return parse_bookings_info(payload)

    def my_bookings(self) -> list[Booking]:
        return self.bookings_info().bookings

    def book(self, class_id: str) -> Booking:
        return self._write_and_verify("POST", "book", class_id, expect_present=True)

    def cancel(self, class_id: str) -> None:
        self._write_and_verify("POST", "cancel", class_id, expect_present=False)

    def join_waitlist(self, class_id: str) -> Booking:
        """PUT возвращает полное событие — постановка проверяется прямо по нему."""
        status, payload = self._call("PUT", "waitlist", body={FIELDS["event_id"]: class_id})
        if status >= 400:
            raise BookingRejected("unknown", json.dumps(payload, ensure_ascii=False)[:200])
        session = self._ensure_live_session()
        slots = parse_slots(
            {FIELDS["slots_root"]: [payload]}, my_user_id=session.user_id
        )
        if not slots or slots[0].my_status != "waitlisted":
            raise BookingRejected("unverified", "лист ожидания не подтвердился ответом")
        slot = slots[0]
        return Booking(
            class_id=slot.class_id,
            title=slot.title,
            starts_at=slot.starts_at,
            status="waitlisted",
        )

    # --- внутреннее ----------------------------------------------------

    def _write_and_verify(self, method: str, endpoint: str, class_id: str, *,
                          expect_present: bool):
        status, payload = self._call(
            method, endpoint, body={FIELDS["event_id"]: class_id}
        )
        if status >= 400:
            raise BookingRejected("unknown", json.dumps(payload, ensure_ascii=False)[:200])
        present = {b.class_id: b for b in self.my_bookings()}
        if expect_present:
            if class_id not in present:
                raise BookingRejected("unverified", "запись не подтвердилась перечитом")
            return present[class_id]
        if class_id in present:
            raise BookingRejected("unverified", "отмена не подтвердилась перечитом")
        return None

    def request_otp(self, phone_number: str | None = None) -> str:
        """POST /api/login — сервер шлёт SMS с кодом на номер аккаунта.

        Телефон/прокси не нужны: device-id фейковый и стабильный (auth), SMS
        уходит на номер, код диктует пользователь (см. login()).
        """
        device = load_or_create_device()
        if phone_number:
            set_phone_number(phone_number)
            number = phone_number
        else:
            number = device.get("phone_number")
        if not number:
            raise MissingPhoneNumber("нет номера телефона аккаунта Invictus")
        url = self._base_url.rstrip("/") + ENDPOINTS["login"]
        body = {
            FIELDS["phone_number"]: number,
            FIELDS["otp_method"]: "sms",
            FIELDS["language"]: "en",
        }
        status, payload = self._transport.request(
            "POST", url, headers=device_headers(device["device_id"]), body=body
        )
        if not 200 <= status < 300:
            raise LoginError(_err_ru(payload) or f"login {status}")
        return number

    def login(self, code: str) -> Session:
        """POST /api/checkSms — код из SMS в обмен на пару токенов.

        Собирает Session с фейковыми device-заголовками и чистыми dead-флагами,
        сохраняет в session.json. Не вызывает /api/refresh (не жжёт свежую пару).
        """
        device = load_or_create_device()
        number = device.get("phone_number")
        if not number:
            raise MissingPhoneNumber("нет номера телефона аккаунта Invictus")
        url = self._base_url.rstrip("/") + ENDPOINTS["check_sms"]
        headers = device_headers(device["device_id"])
        body = {FIELDS["phone_number"]: number, FIELDS["sms_code"]: code}
        status, payload = self._transport.request("POST", url, headers=headers, body=body)
        if not 200 <= status < 300:
            raise LoginError(_err_ru(payload) or f"checkSms {status}")
        access = payload[FIELDS["access_token"]]
        expires_at = access_token_expiry(access) or (
            self._now() + timedelta(hours=FALLBACK_TOKEN_TTL_HOURS)
        )
        session = Session(
            access_token=access,
            refresh_token=payload[FIELDS["refresh_token"]],
            expires_at=expires_at,
            device_headers=headers,
            captured_at=self._now(),
        )
        self._sessions.save(session)
        return session

    def _call(self, method: str, endpoint: str, *, query: dict | None = None, body=None):
        session = self._ensure_live_session()
        url = self._base_url.rstrip("/") + ENDPOINTS[endpoint]
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        def send(active: Session):
            headers = {
                "Authorization": f"Bearer {active.access_token}",
                **active.device_headers,
            }
            return self._transport.request(method, url, headers=headers, body=body)

        status, payload = send(session)
        for attempt in range(RATE_LIMIT_RETRIES):
            if status != 429:
                break
            self._sleep(2 ** attempt)
            status, payload = send(session)

        if status == 401:
            session = self._refresh(session)
            status, payload = send(session)
            if status == 401:
                self._sessions.mark_dead(session, self._now(), reason="401 после refresh")
                raise SessionDead("сессия отвергнута после обновления токена")
        return status, payload

    def _ensure_live_session(self) -> Session:
        session = self._sessions.load()
        if session is None:
            raise SessionDead("сессия не захвачена")
        if session.is_dead:
            raise SessionDead(session.death_reason or "сессия помечена мёртвой")
        if self._sessions.needs_refresh(session, self._now()):
            session = self._refresh(session)
        return session

    def _refresh(self, session: Session) -> Session:
        url = self._base_url.rstrip("/") + ENDPOINTS["refresh"]
        status, payload = self._transport.request(
            "POST",
            url,
            headers=dict(session.device_headers),
            body={FIELDS["refresh_token"]: session.refresh_token},
        )
        if status >= 400:
            self._sessions.mark_dead(session, self._now(), reason=f"refresh {status}")
            raise SessionDead(f"обновление токена отвергнуто: {status}")
        access = payload[FIELDS["access_token"]]
        # Срока жизни в ответе нет — читаем claim exp самого токена (F2).
        expires_at = access_token_expiry(access) or (
            self._now() + timedelta(hours=FALLBACK_TOKEN_TTL_HOURS)
        )
        refreshed = replace(
            session,
            access_token=access,
            # Свежий refresh-токен сохраняем всегда: сегодня ротация косметическая,
            # но вендор может ужесточить её без предупреждения.
            refresh_token=payload.get(FIELDS["refresh_token"]) or session.refresh_token,
            expires_at=expires_at,
        )
        self._sessions.save(refreshed)
        return refreshed
