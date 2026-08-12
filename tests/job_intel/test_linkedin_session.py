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

# Структура снята с живой страницы 2026-08-10: LinkedIn перешёл на
# хэшированные имена классов (_3f0deaea, _79edae89), генерируемые сборкой и
# меняющиеся с каждым деплоем. Семантических классов (artdeco, global-nav,
# voyager, feed-identity-module) в разметке больше нет вовсе.
REDESIGNED_FEED = """
<html><body data-color-scheme="light" data-rehydrated="true">
  <header class="_3f0deaea _3e52c8e4"><nav class="_79edae89" data-testid="primary-nav">
    <a href="/feed/">Home</a><a href="/mynetwork/">My Network</a>
    <a href="/messaging/">Messaging</a><a href="/notifications/">Notifications</a>
  </nav></header>
  <main class="_303c51f5" data-testid="mainFeed">...</main>
</body></html>
"""

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
    отдали 20 и 7 вакансий и одновременно записали login_walls=1.

    Фикстура пересобрана на разметку после редизайна: прежняя опиралась на
    классы, которых у LinkedIn больше нет, и потому проверяла свойство на
    странице, которая не существует."""
    html = REDESIGNED_FEED.replace(
        "</body>", '<footer><a href="/uas/login">Sign in</a> · <span>Log in</span></footer></body>'
    )

    assert classify_auth_page("https://www.linkedin.com/feed/", html) == SESSION_OK


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
    """Одного testid достаточно: навигация на странице поиска другая."""
    html = REDESIGNED_FEED.replace('data-testid="primary-nav"', "")

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


# --- Маркеры после редизайна LinkedIn 2026-08 ----------------------------

GUEST_PAGE = """
<html><body>
  <a href="/uas/login">Sign in</a><a href="/signup">Join now</a>
  <main class="_303c51f5">Make the most of your professional life</main>
</body></html>
"""


def test_redesigned_feed_is_recognised_as_authenticated() -> None:
    """Прогон 2026-08-10 упал на этой странице: title был «Feed | LinkedIn»,
    кука жива, а классификатор вернул real_empty, потому что искал классы,
    которых в новой вёрстке нет."""
    assert classify_auth_page("https://www.linkedin.com/feed/", REDESIGNED_FEED) == SESSION_OK


def test_authenticated_nav_destinations_alone_are_enough() -> None:
    """Гостю LinkedIn не показывает ни My Network, ни Messaging, ни
    Notifications. Это свойство продукта, а не сборки, поэтому переживёт
    следующий редизайн — в отличие от имён классов."""
    html = REDESIGNED_FEED.replace('data-testid="primary-nav"', "").replace('data-testid="mainFeed"', "")

    assert classify_auth_page("https://www.linkedin.com/feed/", html) == SESSION_OK


def test_guest_page_is_not_mistaken_for_a_session() -> None:
    assert classify_auth_page("https://www.linkedin.com/", GUEST_PAGE) == SESSION_MISSING


def test_authwall_is_a_missing_session() -> None:
    assert classify_auth_page("https://www.linkedin.com/authwall?trk=x", "<html></html>") == SESSION_MISSING


def test_live_cookie_plus_unrecognised_page_is_not_a_dead_session() -> None:
    """Кука жива, признаков логин-стены и челленджа нет, а разметка не
    опознана. Это дрейф вёрстки, а не отсутствие сессии: склеив их, прогон
    2026-08-10 отказался работать при полностью живой авторизации."""
    verdict = resolve_session_state(cookie_state=SESSION_OK, page_state=REAL_EMPTY)

    assert verdict.state == SESSION_OK
    assert verdict.page_unrecognised is True


def test_unrecognised_page_without_a_cookie_stays_missing() -> None:
    verdict = resolve_session_state(cookie_state=SESSION_MISSING, page_state=REAL_EMPTY)

    assert verdict.state == SESSION_MISSING
    assert verdict.page_unrecognised is False


def test_recognised_feed_does_not_raise_the_drift_flag() -> None:
    verdict = resolve_session_state(cookie_state=SESSION_OK, page_state=SESSION_OK)

    assert verdict.state == SESSION_OK
    assert verdict.page_unrecognised is False


# --- Профиль ищется по сессии, а не по last_used ------------------------


def _profile_with(tmp_path: Path, name: str, cookies: list[tuple[str, str, int, int]]) -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    _make_cookie_db(directory / "Cookies", cookies)
    return directory


def test_profile_holding_the_session_wins_over_last_used(tmp_path: Path) -> None:
    """Ночной перезапуск десктопа 2026-08-12 открыл Default, last_used
    переключился туда, и резолвер увёл замер в профиль с одиннадцатью
    анонимными куками — при живой сессии в соседнем каталоге. Вопрос, на
    который надо отвечать, — «где лежит сессия», а не «что браузер открыл
    последним»."""
    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    _profile_with(tmp_path, "Default", [(".linkedin.com", "bcookie", future, 1)])
    _profile_with(tmp_path, "Profile 1", [(".www.linkedin.com", "li_at", future, 1)])
    (tmp_path / "Local State").write_text(
        _json.dumps({"profile": {"last_used": "Default"}}), encoding="utf-8"
    )

    assert resolve_profile_dir(tmp_path) == tmp_path / "Profile 1"


def test_last_used_is_the_fallback_when_no_profile_holds_a_session(tmp_path: Path) -> None:
    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    _profile_with(tmp_path, "Default", [(".linkedin.com", "bcookie", future, 1)])
    _profile_with(tmp_path, "Profile 1", [(".linkedin.com", "lidc", future, 1)])
    (tmp_path / "Local State").write_text(
        _json.dumps({"profile": {"last_used": "Profile 1"}}), encoding="utf-8"
    )

    assert resolve_profile_dir(tmp_path) == tmp_path / "Profile 1"


def test_default_is_the_last_resort(tmp_path: Path) -> None:
    (tmp_path / "Default").mkdir()

    assert resolve_profile_dir(tmp_path) == tmp_path / "Default"


def test_guest_page_beats_a_session_found_in_another_profile() -> None:
    """Страница прямо показывает гостя, а кука нашлась в другом профиле.
    Поиск пойдёт в том контексте, который открыт в браузере, поэтому живая
    кука на диске этого не отменяет. Без этого правила ночной перезапуск в
    Default отчитывался бы как здоровая сессия."""
    verdict = resolve_session_state(cookie_state=SESSION_OK, page_state=SESSION_MISSING)

    assert verdict.state == SESSION_MISSING


def test_module_prints_the_resolved_profile_name(tmp_path: Path) -> None:
    """Запускалка десктопа — bash, и ей нужен ответ строкой. Печатается имя
    каталога, а не путь: Chromium принимает --profile-directory именно так."""
    import subprocess, sys

    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    (tmp_path / "Profile 1").mkdir()
    _make_cookie_db(tmp_path / "Profile 1" / "Cookies", [(".www.linkedin.com", "li_at", future, 1)])

    out = subprocess.run(
        [sys.executable, "-m", "job_intel.linkedin_session", "--user-data-dir", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )

    assert out.stdout.strip() == "Profile 1"


# --- Нечитаемый профиль не равен профилю без сессии ----------------------

from job_intel.linkedin_session import resolve_profile


def test_unreadable_profile_is_reported_not_swallowed(tmp_path: Path) -> None:
    """Каталог Profile 1 имеет права drwx------ browser:browser. Под другим
    пользователем чтение падает, и `except: continue` превращал «не смог
    посмотреть» в «сессии тут нет»: резолвер от hermes отвечал Default, от
    root — Profile 1, на одних и тех же данных."""
    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    (tmp_path / "Default").mkdir()
    _make_cookie_db(tmp_path / "Default" / "Cookies", [(".linkedin.com", "bcookie", future, 1)])
    locked = tmp_path / "Profile 1"
    locked.mkdir()
    _make_cookie_db(locked / "Cookies", [(".www.linkedin.com", "li_at", future, 1)])
    (locked / "Cookies").chmod(0o000)

    try:
        resolution = resolve_profile(tmp_path)
    finally:
        (locked / "Cookies").chmod(0o600)

    assert "Profile 1" in resolution.unreadable


def test_resolution_names_the_reason(tmp_path: Path) -> None:
    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    (tmp_path / "Profile 1").mkdir()
    _make_cookie_db(tmp_path / "Profile 1" / "Cookies", [(".www.linkedin.com", "li_at", future, 1)])

    resolution = resolve_profile(tmp_path)

    assert resolution.reason == "session_cookie"
    assert resolution.path == tmp_path / "Profile 1"
    assert resolution.unreadable == ()


def test_fallback_to_default_names_itself(tmp_path: Path) -> None:
    (tmp_path / "Default").mkdir()

    resolution = resolve_profile(tmp_path)

    assert resolution.reason == "default"


def test_profile_dir_without_read_permission_is_reported(tmp_path: Path) -> None:
    """Каталог Profile 1 имеет права drwx------ browser:browser. Path.exists()
    при отказе в правах возвращает False, а не ошибку, поэтому профиль
    отсеивался ещё до попытки чтения: список нечитаемых оставался пустым, и
    отчёт выглядел полным, будучи неполным."""
    import os

    if os.geteuid() == 0:
        import pytest

        pytest.skip("root читает что угодно, права здесь ничего не значат")

    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    (tmp_path / "Default").mkdir()
    _make_cookie_db(tmp_path / "Default" / "Cookies", [(".linkedin.com", "bcookie", future, 1)])
    locked = tmp_path / "Profile 1"
    locked.mkdir()
    _make_cookie_db(locked / "Cookies", [(".www.linkedin.com", "li_at", future, 1)])
    locked.chmod(0o000)

    try:
        resolution = resolve_profile(tmp_path)
    finally:
        locked.chmod(0o700)

    assert "Profile 1" in resolution.unreadable
    assert resolution.reason != "session_cookie"


def test_one_unreadable_entry_does_not_blank_the_whole_scan(tmp_path: Path) -> None:
    """В каталоге профиля лежит SingletonSocket — симлинк, чей stat падает с
    PermissionError. Перебор со сплошным `except OSError` обнулял на нём весь
    список, и Profile 1 не доходил до проверки: отчёт сообщал, что нечитаемых
    профилей нет, хотя не посмотрел ни одного."""
    import os

    if os.geteuid() == 0:
        import pytest

        pytest.skip("root читает что угодно")

    future = _chromium_stamp(datetime(2027, 1, 1, tzinfo=timezone.utc))
    (tmp_path / "Default").mkdir()
    _make_cookie_db(tmp_path / "Default" / "Cookies", [(".linkedin.com", "bcookie", future, 1)])
    good = tmp_path / "Profile 1"
    good.mkdir()
    _make_cookie_db(good / "Cookies", [(".www.linkedin.com", "li_at", future, 1)])

    hostile = tmp_path / "SingletonSocket"
    hostile.mkdir()
    hostile.chmod(0o000)

    try:
        resolution = resolve_profile(tmp_path)
    finally:
        hostile.chmod(0o700)

    assert resolution.path == good
    assert resolution.reason == "session_cookie"


def test_only_chrome_profile_names_are_scanned(tmp_path: Path) -> None:
    """Посторонние записи в каталоге не профили и в список нечитаемых
    попадать не должны — иначе он забивается шумом и перестаёт читаться."""
    (tmp_path / "Default").mkdir()
    (tmp_path / "BrowserMetrics").mkdir()
    (tmp_path / "CaptchaProviders").mkdir()

    resolution = resolve_profile(tmp_path)

    assert resolution.unreadable == ()
