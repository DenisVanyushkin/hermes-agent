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
