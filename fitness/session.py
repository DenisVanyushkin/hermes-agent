"""Хранение сессии Invictus.

Сессия подписывается человеком на ресепшне и невосполнима программно: здесь
есть продление и признание смерти, но нет и не должно появиться логина.

Ревизия 3: собственный user_id и срок жизни access-токена читаются из самого
JWT (claims `id` и `exp`), а не хранятся отдельными полями. Поля `expires_in` в
ответе /api/refresh нет вовсе, а хранимый рядом user_id можно было бы забыть
обновить при перезахвате — и «я записан» считалось бы по чужому идентификатору.
"""

import base64
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from fitness.store import JsonStore

REFRESH_MARGIN_SECONDS = 300
DEATH_NOTICE_COOLDOWN_HOURS = 24
SESSION_FILE = "session.json"


def _jwt_claims(token: str) -> dict:
    """Достаёт полезную нагрузку JWT без проверки подписи.

    Подпись намеренно не проверяется: секрет HS256 принадлежит серверу, и это
    не решение об авторизации — сервер валидирует токен на каждом вызове. Мы
    лишь читаем метаданные собственного credential'а.
    """
    try:
        payload = token.split(".")[1]
    except (AttributeError, IndexError):
        return {}
    # Сегменты JWT приходят без выравнивающих '=', а декодер их требует.
    padded = payload + "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def access_token_user_id(token: str) -> str:
    return str(_jwt_claims(token).get("id") or "")


def access_token_expiry(token: str) -> datetime | None:
    exp = _jwt_claims(token).get("exp")
    if exp is None:
        return None
    try:
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


@dataclass(frozen=True)
class Session:
    access_token: str
    refresh_token: str
    expires_at: datetime
    device_headers: dict[str, str] = field(default_factory=dict)
    captured_at: datetime | None = None
    dead_since: datetime | None = None
    death_reason: str | None = None
    last_death_notice_at: datetime | None = None

    @property
    def is_dead(self) -> bool:
        return self.dead_since is not None

    @property
    def user_id(self) -> str:
        """MongoDB ObjectId владельца из claim `id` access-токена."""
        return access_token_user_id(self.access_token)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class SessionStore:
    def __init__(self) -> None:
        self._json = JsonStore(SESSION_FILE)

    def load(self) -> Session | None:
        raw = self._json.read(default=None)
        if not raw:
            return None
        return Session(
            access_token=raw["access_token"],
            refresh_token=raw["refresh_token"],
            expires_at=_parse(raw["expires_at"]),
            device_headers=raw.get("device_headers", {}),
            captured_at=_parse(raw.get("captured_at")),
            dead_since=_parse(raw.get("dead_since")),
            death_reason=raw.get("death_reason"),
            last_death_notice_at=_parse(raw.get("last_death_notice_at")),
        )

    def save(self, session: Session) -> None:
        self._json.write(
            {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": _iso(session.expires_at),
                "device_headers": session.device_headers,
                "captured_at": _iso(session.captured_at),
                "dead_since": _iso(session.dead_since),
                "death_reason": session.death_reason,
                "last_death_notice_at": _iso(session.last_death_notice_at),
            }
        )

    def needs_refresh(self, session: Session, now: datetime) -> bool:
        return session.expires_at - now <= timedelta(seconds=REFRESH_MARGIN_SECONDS)

    def mark_dead(self, session: Session, now: datetime, *, reason: str) -> Session:
        dead = replace(session, dead_since=session.dead_since or now, death_reason=reason)
        self.save(dead)
        return dead

    def should_notify_death(self, session: Session, now: datetime) -> bool:
        if not session.is_dead:
            return False
        if session.last_death_notice_at is None:
            return True
        elapsed = now - session.last_death_notice_at
        return elapsed >= timedelta(hours=DEATH_NOTICE_COOLDOWN_HOURS)

    def note_death_notified(self, session: Session, now: datetime) -> Session:
        noted = replace(session, last_death_notice_at=now)
        self.save(noted)
        return noted
