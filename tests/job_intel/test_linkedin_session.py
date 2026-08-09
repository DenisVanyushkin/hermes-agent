from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_intel.linkedin_session import (
    CookieRecord,
    SESSION_MISSING,
    SESSION_OK,
    read_cookie_inventory,
    session_state_from_cookies,
)

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _chromium_stamp(moment: datetime) -> int:
    return int((moment - CHROMIUM_EPOCH).total_seconds() * 1_000_000)


def _make_cookie_db(path: Path, rows: list[tuple[str, str, int, int]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE cookies ("
        " creation_utc INTEGER, host_key TEXT, name TEXT, value TEXT,"
        " expires_utc INTEGER, is_persistent INTEGER)"
    )
    conn.executemany(
        "INSERT INTO cookies (creation_utc, host_key, name, value, expires_utc, is_persistent)"
        " VALUES (0, ?, ?, 'SECRET', ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_inventory_reports_names_hosts_and_expiry_but_never_values(tmp_path: Path) -> None:
    future = datetime(2027, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "Cookies"
    _make_cookie_db(db, [(".linkedin.com", "li_at", _chromium_stamp(future), 1)])

    inventory = read_cookie_inventory(db)

    assert inventory == [
        CookieRecord(host=".linkedin.com", name="li_at", expires_at=future, persistent=True)
    ]
    assert not hasattr(inventory[0], "value")


def test_inventory_filters_foreign_hosts(tmp_path: Path) -> None:
    db = tmp_path / "Cookies"
    _make_cookie_db(
        db,
        [
            (".linkedin.com", "li_at", 0, 0),
            (".example.com", "session", 0, 0),
        ],
    )

    names = [record.name for record in read_cookie_inventory(db)]

    assert names == ["li_at"]


def test_session_ok_when_li_at_present_and_unexpired() -> None:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    inventory = [
        CookieRecord(".linkedin.com", "li_at", now + timedelta(days=30), True),
    ]

    assert session_state_from_cookies(inventory, now=now) == SESSION_OK


def test_missing_when_only_anonymous_cookies_remain() -> None:
    """Точное состояние профиля на 2026-08-09: bcookie, lidc, li_gc и
    JSESSIONID живы, li_at нет. Это отзыв сессии, а не челлендж."""
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    inventory = [
        CookieRecord(".linkedin.com", "bcookie", now + timedelta(days=300), True),
        CookieRecord(".linkedin.com", "lidc", now + timedelta(days=1), True),
        CookieRecord(".linkedin.com", "li_gc", now + timedelta(days=100), True),
        CookieRecord(".www.linkedin.com", "JSESSIONID", None, False),
    ]

    assert session_state_from_cookies(inventory, now=now) == SESSION_MISSING


def test_expired_li_at_counts_as_missing() -> None:
    """Просроченную куку браузер не отправит, значит для LinkedIn её нет.
    Отдельного состояния она не заслуживает."""
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    inventory = [CookieRecord(".linkedin.com", "li_at", now - timedelta(seconds=1), True)]

    assert session_state_from_cookies(inventory, now=now) == SESSION_MISSING
