"""Чтение одноразового кода из почты.

Переписано с `_read_headhunter_otp_from_gmail`, где было четыре дефекта:
переменные окружения, которых нет ни в одном файле; расширение выборки до
всего INBOX при неудачном поиске; паттерн, берущий первое 4–8-значное число из
тела письма; и один `except Exception`, из-за которого «креды не настроены»
было неотличимо от «код не подошёл». Последний и объясняет, почему тракт два
месяца отчитывался об отсутствии попыток вместо отсутствия конфигурации.
"""
from __future__ import annotations

import imaplib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import message_from_bytes
from email.message import Message
from email.utils import parsedate_to_datetime

# Код привязан к слову-метке: без привязки любой номер заказа в письме
# считается одноразовым кодом.
LINKEDIN_CODE_PATTERN = re.compile(r"(?:code|код)\D{0,40}(?P<code>\d{6})", re.IGNORECASE)

STATUS_OK = "ok"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_IMAP_ERROR = "imap_error"
STATUS_NO_FRESH_MESSAGE = "no_fresh_message"
STATUS_NO_CODE_IN_MESSAGE = "no_code_in_message"


@dataclass(frozen=True)
class MailOtpConfig:
    address: str
    password: str
    host: str
    port: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "MailOtpConfig | None":
        address = (env.get("EMAIL_ADDRESS") or env.get("JOB_INTEL_GMAIL_ADDRESS") or "").strip()
        password = (env.get("EMAIL_PASSWORD") or env.get("JOB_INTEL_GMAIL_APP_PASSWORD") or "").strip()
        if not address or not password:
            return None
        return cls(
            address=address,
            password=password,
            host=(env.get("EMAIL_IMAP_HOST") or "imap.gmail.com").strip(),
            port=int(env.get("EMAIL_IMAP_PORT") or 993),
        )


@dataclass(frozen=True)
class OtpResult:
    code: str | None
    status: str


def _message_text(message: Message) -> str:
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="ignore"))
    else:
        payload = message.get_payload(decode=True)
        if payload:
            parts.append(payload.decode(message.get_content_charset() or "utf-8", errors="ignore"))
    subject = message.get("Subject") or ""
    if subject:
        parts.append(str(subject))
    return "\n".join(parts)


def read_otp(
    config: MailOtpConfig | None,
    *,
    sender: str,
    subject_hint: str,
    pattern: re.Pattern[str],
    max_age: timedelta,
    now: datetime,
    client_factory: Callable[[str, int], object] = imaplib.IMAP4_SSL,
) -> OtpResult:
    # У client_factory есть умолчание, чтобы вызывающий мог передать read_otp
    # как otp_reader без обёртки; тесты подменяют его целиком.
    if config is None:
        return OtpResult(None, STATUS_NOT_CONFIGURED)
    saw_fresh_message = False
    try:
        with client_factory(config.host, config.port) as client:  # type: ignore[attr-defined]
            client.login(config.address, config.password)
            client.select("INBOX")
            status, data = client.search(None, f'(FROM "{sender}" SUBJECT "{subject_hint}")')
            if status != "OK":
                # Расширять поиск на весь INBOX нельзя: именно так прежняя
                # реализация начинала искать код в произвольных письмах.
                return OtpResult(None, STATUS_IMAP_ERROR)
            ids = [item for item in (data[0].split() if data and data[0] else []) if item]
            for message_id in reversed(ids[-10:]):
                fetch_status, payload = client.fetch(message_id, "(RFC822)")
                if fetch_status != "OK" or not payload or not payload[0]:
                    continue
                message = message_from_bytes(payload[0][1])
                try:
                    sent_at = parsedate_to_datetime(message.get("Date") or "")
                except (TypeError, ValueError):
                    continue
                if sent_at is None or (now - sent_at) > max_age:
                    continue
                saw_fresh_message = True
                match = pattern.search(_message_text(message))
                if match:
                    return OtpResult(match.group("code"), STATUS_OK)
    except Exception:
        return OtpResult(None, STATUS_IMAP_ERROR)
    return OtpResult(None, STATUS_NO_CODE_IN_MESSAGE if saw_fresh_message else STATUS_NO_FRESH_MESSAGE)
