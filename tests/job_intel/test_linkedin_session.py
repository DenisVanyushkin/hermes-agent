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


# --- Задача 2: классификация страницы авторизации -------------------------

from job_intel.linkedin_session import (
    CHALLENGE_EMAIL_OTP,
    CHALLENGE_HARD,
    REAL_EMPTY,
    SessionVerdict,
    classify_auth_page,
    resolve_session_state,
)

FEED_HTML = """
<html><body>
  <nav><div class="global-nav__me">Me</div></nav>
  <footer><a href="/login">Sign in</a> · <a href="/uas/login">Log in</a></footer>
</body></html>
"""

LOGIN_HTML = "<html><body><h1>Sign in</h1><form action='/uas/login-submit'></form></body></html>"

OTP_HTML = """
<html><body><h1>Let's do a quick security check</h1>
<p>We sent a code to your email. Enter the code below.</p>
<input name="pin"></body></html>
"""

CAPTCHA_HTML = """
<html><body><h1>Security Verification</h1>
<div id="captcha-internal"></div><script src="/recaptcha/api.js"></script></body></html>
"""


def test_feed_is_authenticated_despite_sign_in_in_the_footer() -> None:
    """Ровно тот случай, на котором врал старый детектор: прогоны 291 и 307
    отдали 20 и 7 вакансий и одновременно записали login_walls=1."""
    assert classify_auth_page("https://www.linkedin.com/feed/", FEED_HTML) == SESSION_OK


def test_login_page_is_missing_cookie() -> None:
    assert classify_auth_page("https://www.linkedin.com/uas/login", LOGIN_HTML) == SESSION_MISSING


def test_email_code_page_is_an_otp_challenge() -> None:
    assert classify_auth_page("https://www.linkedin.com/checkpoint/lg/login-submit", OTP_HTML) == CHALLENGE_EMAIL_OTP


def test_captcha_page_is_a_hard_challenge_even_though_it_says_verification() -> None:
    """Жёсткий челлендж проверяется раньше почтового: страница капчи тоже
    содержит слово verification, и перепутать их — значит начать
    автоматически долбиться в проверку, которую нельзя проходить роботом."""
    assert classify_auth_page(
        "https://www.linkedin.com/checkpoint/challenge/", CAPTCHA_HTML
    ) == CHALLENGE_HARD


def test_second_authenticated_marker_also_counts() -> None:
    html = FEED_HTML.replace("global-nav__me", "feed-identity-module")
    assert classify_auth_page("https://www.linkedin.com/jobs/search/", html) == SESSION_OK


def test_unrecognised_page_is_real_empty() -> None:
    assert classify_auth_page("https://www.linkedin.com/jobs/search/", "<html></html>") == REAL_EMPTY


def test_verdict_prefers_the_page_over_the_cookie() -> None:
    verdict = resolve_session_state(cookie_state=SESSION_OK, page_state=CHALLENGE_EMAIL_OTP)
    assert verdict == SessionVerdict(state=CHALLENGE_EMAIL_OTP, cookie_mismatch=False)


def test_verdict_flags_mismatch_when_cookie_absent_but_page_authenticated() -> None:
    """Такого не бывает: если ленты нет куки, значит инвентарь снят не с того
    профиля. Флаг ловит ошибку конфигурации, а не состояние LinkedIn."""
    verdict = resolve_session_state(cookie_state=SESSION_MISSING, page_state=SESSION_OK)
    assert verdict == SessionVerdict(state=SESSION_OK, cookie_mismatch=True)


def test_verdict_falls_back_to_cookie_when_page_is_uninformative() -> None:
    verdict = resolve_session_state(cookie_state=SESSION_MISSING, page_state=REAL_EMPTY)
    assert verdict == SessionVerdict(state=SESSION_MISSING, cookie_mismatch=False)


# --- Задача: выбор профиля Chrome ----------------------------------------

import json as _json

from job_intel.linkedin_session import resolve_profile_dir


def test_last_used_profile_wins_over_default(tmp_path: Path) -> None:
    """Вход в аккаунт Google внутри браузера заставляет Chrome завести второй
    профиль, и сессия LinkedIn ложится в него. Жёстко прочитанный Default
    тогда пуст, а вывод «сессии нет» сделан по чужому профилю — это наблюдалось
    живьём 2026-08-09: Profile 1 держал li_at, Default не держал ничего."""
    (tmp_path / "Default").mkdir()
    (tmp_path / "Profile 1").mkdir()
    (tmp_path / "Local State").write_text(
        _json.dumps({"profile": {"last_used": "Profile 1"}}), encoding="utf-8"
    )

    assert resolve_profile_dir(tmp_path) == tmp_path / "Profile 1"


def test_default_is_used_when_local_state_is_absent(tmp_path: Path) -> None:
    (tmp_path / "Default").mkdir()

    assert resolve_profile_dir(tmp_path) == tmp_path / "Default"


def test_unreadable_local_state_falls_back_to_default(tmp_path: Path) -> None:
    """Битый Local State — это неизвестность, а не отсутствие профиля."""
    (tmp_path / "Default").mkdir()
    (tmp_path / "Local State").write_text("{ not json", encoding="utf-8")

    assert resolve_profile_dir(tmp_path) == tmp_path / "Default"


def test_named_profile_that_does_not_exist_falls_back_to_default(tmp_path: Path) -> None:
    (tmp_path / "Default").mkdir()
    (tmp_path / "Local State").write_text(
        _json.dumps({"profile": {"last_used": "Profile 7"}}), encoding="utf-8"
    )

    assert resolve_profile_dir(tmp_path) == tmp_path / "Default"
