"""Состояние сессии LinkedIn из фактов, а не из подстрок.

Детектор, который этот модуль заменяет (`_looks_like_login_wall`), искал
"sign in" в 169 КБ разметки и потому одинаково срабатывал на здоровом прогоне
с двадцатью вакансиями и на мёртвом с нулём. Здесь используются только
свидетельства присутствия: наличие сессионной куки и маркеры, встречающиеся
исключительно на залогиненной странице.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
SESSION_COOKIE = "li_at"

SESSION_OK = "session_ok"
SESSION_MISSING = "session_missing_cookie"


@dataclass(frozen=True)
class CookieRecord:
    host: str
    name: str
    expires_at: datetime | None
    persistent: bool


def _chromium_time(value: int | None) -> datetime | None:
    if not value:
        return None
    return CHROMIUM_EPOCH + timedelta(microseconds=int(value))


def read_cookie_inventory(cookie_db: Path, *, host_filter: str = "linkedin") -> list[CookieRecord]:
    """Метаданные куки из профиля Chromium.

    Значения не читаются: вызывающему нужны присутствие и срок, а чтение
    секрета, который не нужен, превращает диагностику в обязательство.
    База копируется перед чтением — живой профиль держит её заблокированной.
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copy(cookie_db, copy)
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT host_key, name, expires_utc, is_persistent FROM cookies"
            ).fetchall()
        finally:
            conn.close()
    return [
        CookieRecord(
            host=host,
            name=name,
            expires_at=_chromium_time(expires),
            persistent=bool(persistent),
        )
        for host, name, expires, persistent in rows
        if host_filter in (host or "")
    ]


def session_state_from_cookies(inventory: Sequence[CookieRecord], *, now: datetime) -> str:
    for record in inventory:
        if record.name != SESSION_COOKIE:
            continue
        if record.expires_at is not None and record.expires_at <= now:
            return SESSION_MISSING
        return SESSION_OK
    return SESSION_MISSING


CHALLENGE_EMAIL_OTP = "challenge_email_otp"
CHALLENGE_HARD = "challenge_hard"
REAL_EMPTY = "real_empty"

SESSION_STATES = (
    SESSION_OK,
    SESSION_MISSING,
    CHALLENGE_EMAIL_OTP,
    CHALLENGE_HARD,
    REAL_EMPTY,
)

# Маркеры, встречающиеся только на залогиненной странице. Свидетельство
# присутствия, а не отсутствия: их нельзя случайно найти в футере.
_AUTHENTICATED_MARKERS = (
    "global-nav__me",
    "feed-identity-module",
    "nav-item__profile-member-photo",
)

# Проверяется раньше почтового челленджа: страница капчи тоже содержит слово
# verification, а перепутать их — значит начать автоматически проходить
# проверку, которую проходить роботом нельзя.
_HARD_CHALLENGE_MARKERS = (
    "/checkpoint/challenge",
    "security verification",
    "verify your identity",
    "recaptcha",
    "captcha-internal",
)

_EMAIL_OTP_MARKERS = (
    "we sent a code",
    "enter the code",
    "verification code",
    "код подтверждения",
)

_LOGIN_URL_MARKERS = ("/uas/login", "/login")


@dataclass(frozen=True)
class SessionVerdict:
    state: str
    cookie_mismatch: bool = False


def classify_auth_page(url: str, html: str) -> str:
    lowered_url = (url or "").lower()
    lowered_html = (html or "").lower()
    if any(marker in lowered_html for marker in _AUTHENTICATED_MARKERS):
        return SESSION_OK
    if any(marker in lowered_url or marker in lowered_html for marker in _HARD_CHALLENGE_MARKERS):
        return CHALLENGE_HARD
    if any(marker in lowered_html for marker in _EMAIL_OTP_MARKERS):
        return CHALLENGE_EMAIL_OTP
    if any(marker in lowered_url for marker in _LOGIN_URL_MARKERS):
        return SESSION_MISSING
    return REAL_EMPTY


def resolve_session_state(*, cookie_state: str, page_state: str) -> SessionVerdict:
    """Кука говорит, что держит браузер; страница — что LinkedIn с этим сделал.

    Расхождение осмысленно: живая кука при странице челленджа означает
    челлендж, а отсутствие куки при ленте означает, что инвентарь снят не с
    того профиля — состояние LinkedIn тут ни при чём, поэтому это флаг, а не
    шестое состояние.
    """
    mismatch = cookie_state == SESSION_MISSING and page_state == SESSION_OK
    if page_state == SESSION_OK:
        return SessionVerdict(SESSION_OK, mismatch)
    if page_state in (CHALLENGE_HARD, CHALLENGE_EMAIL_OTP):
        return SessionVerdict(page_state)
    if cookie_state == SESSION_MISSING:
        return SessionVerdict(SESSION_MISSING)
    return SessionVerdict(page_state)
