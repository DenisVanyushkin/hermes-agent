"""Состояние сессии LinkedIn из фактов, а не из подстрок.

Детектор, который этот модуль заменяет (`_looks_like_login_wall`), искал
"sign in" в 169 КБ разметки и потому одинаково срабатывал на здоровом прогоне
с двадцатью вакансиями и на мёртвом с нулём. Здесь используются только
свидетельства присутствия: наличие сессионной куки и маркеры, встречающиеся
исключительно на залогиненной странице.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
SESSION_COOKIE = "li_at"

# Имена профилей Chrome: Default и «Profile N».
_PROFILE_NAME = re.compile(r"Profile \d+")

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


def _cookie_db_state(profile_dir: Path) -> str:
    """`present`, `absent` или `unreadable` для куки-базы профиля.

    `Path.exists()` здесь не годится: при отказе в правах он возвращает False,
    а не ошибку, из-за чего профиль отсеивался ещё до попытки чтения и список
    нечитаемых оставался пустым — отчёт выглядел полным, будучи неполным.
    Каталоги профилей имеют права drwx------ browser:browser, так что под
    любым другим пользователем это происходит всегда.
    """
    try:
        (profile_dir / "Cookies").stat()
    except PermissionError:
        return "unreadable"
    except OSError:
        return "absent"
    return "present"


def _profile_candidates(user_data_dir: Path) -> tuple[list[Path], list[str]]:
    """Каталоги профилей с куки-базой и имена тех, что прочитать не удалось.

    Порядок устойчив: Default первым, остальные по алфавиту.
    """
    candidates: list[Path] = []
    unreadable: list[str] = []

    def classify(directory: Path) -> None:
        state = _cookie_db_state(directory)
        if state == "present":
            candidates.append(directory)
        elif state == "unreadable":
            unreadable.append(directory.name)

    try:
        names = sorted(entry.name for entry in user_data_dir.iterdir())
    except OSError:
        names = []

    # Перебираются только каталоги с именами профилей Chrome. Рядом лежат
    # посторонние записи вроде SingletonSocket — симлинка в недоступную цель,
    # на котором `is_dir()` кидает PermissionError. Прежний сплошной перебор
    # обрывался на нём целиком и возвращал пустой список, из-за чего профиль
    # с сессией не доходил до проверки, а отчёт сообщал, что нечитаемых
    # профилей нет — не посмотрев ни одного.
    ordered = ["Default"] + [n for n in names if _PROFILE_NAME.fullmatch(n)]
    seen: set[str] = set()
    for name in ordered:
        if name in seen:
            continue
        seen.add(name)
        directory = user_data_dir / name
        try:
            if not directory.is_dir():
                continue
        except OSError:
            unreadable.append(name)
            continue
        classify(directory)
    return candidates, unreadable


@dataclass(frozen=True)
class ProfileResolution:
    """Каталог профиля и то, как он был выбран.

    Причина и список нечитаемых профилей — часть ответа, а не служебная
    деталь: `session_missing_cookie`, полученное после того, как профиль с
    сессией не удалось прочитать, есть факт о правах доступа, а не о сессии.
    """

    path: Path
    reason: str
    unreadable: tuple[str, ...] = ()


def resolve_profile(user_data_dir: Path) -> ProfileResolution:
    """Каталог профиля Chrome, в котором лежит сессия LinkedIn.

    Отвечает на вопрос «где сессия», а не «что браузер открыл последним».
    Разница стоила ложной тревоги 2026-08-12: ночной перезапуск десктопа
    открыл Default, `last_used` переключился туда, и замер ушёл в профиль с
    одиннадцатью анонимными куками — при живой сессии в соседнем каталоге.
    Причина второго профиля — вход в аккаунт Google внутри браузера: Chrome
    заводит под него отдельный профиль, и логин LinkedIn ложится туда.

    Порядок: профиль с сессионной кукой, затем `last_used`, затем Default.
    Каждая ступень — возврат к менее точному ответу, но не молчаливая
    подмена: вызывающий получает каталог и печатает его имя в отчёте.
    """
    candidates, unreadable = _profile_candidates(user_data_dir)
    for candidate in candidates:
        try:
            inventory = read_cookie_inventory(candidate / "Cookies")
        except Exception:
            # Каталог профиля имеет права drwx------ browser:browser, поэтому
            # под другим пользователем чтение падает. Пропустить молча —
            # значит выдать «сессии тут нет» вместо «не смог посмотреть»:
            # резолвер от hermes отвечал Default, от root — Profile 1, на
            # одних и тех же данных.
            unreadable.append(candidate.name)
            continue
        if any(record.name == SESSION_COOKIE for record in inventory):
            return ProfileResolution(candidate, "session_cookie", tuple(unreadable))

    state_path = user_data_dir / "Local State"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        last_used = str(state.get("profile", {}).get("last_used") or "").strip()
    except Exception:
        last_used = ""
    if last_used:
        candidate = user_data_dir / last_used
        if candidate.is_dir():
            return ProfileResolution(candidate, "last_used", tuple(unreadable))
    return ProfileResolution(user_data_dir / "Default", "default", tuple(unreadable))


def resolve_profile_dir(user_data_dir: Path) -> Path:
    """Тонкая обёртка для вызывающих, которым нужен только каталог."""
    return resolve_profile(user_data_dir).path


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
#
# Прежний набор (global-nav__me, feed-identity-module,
# nav-item__profile-member-photo) умер вместе с редизайном: 2026-08-10 живая
# лента с title «Feed | LinkedIn» не содержала ни одного из них, как и слов
# artdeco, global-nav и voyager вообще. LinkedIn перешёл на хэшированные
# имена классов вида _3f0deaea, которые генерирует сборка и которые меняются
# с каждым деплоем. Опираться на имена классов больше нельзя в принципе.
_AUTHENTICATED_MARKERS = (
    'data-testid="mainfeed"',
    'data-testid="primary-nav"',
)

# Пункты навигации, которых у гостя нет. Это свойство продукта, а не сборки,
# поэтому переживёт следующий редизайн: страница без сессии не предлагает ни
# сети контактов, ни сообщений, ни уведомлений. Требуется не меньше двух,
# чтобы случайная ссылка в подвале не сошла за признак авторизации.
_AUTHENTICATED_NAV = ("/mynetwork", "/messaging", "/notifications")
_AUTHENTICATED_NAV_MIN = 2

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

_LOGIN_URL_MARKERS = ("/uas/login", "/login", "/authwall")
# Разметка гостевой страницы: приглашение войти или зарегистрироваться.
_GUEST_MARKERS = ('href="/uas/login"', 'href="/signup"')


@dataclass(frozen=True)
class SessionVerdict:
    state: str
    cookie_mismatch: bool = False
    page_unrecognised: bool = False


def classify_auth_page(url: str, html: str) -> str:
    lowered_url = (url or "").lower()
    lowered_html = (html or "").lower()
    if any(marker in lowered_html for marker in _AUTHENTICATED_MARKERS):
        return SESSION_OK
    if sum(item in lowered_html for item in _AUTHENTICATED_NAV) >= _AUTHENTICATED_NAV_MIN:
        return SESSION_OK
    if any(marker in lowered_html for marker in _GUEST_MARKERS):
        return SESSION_MISSING
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
    if page_state == SESSION_MISSING:
        # Страница прямо показывает гостя или логин-форму. Живая кука,
        # найденная в другом профиле, этого не отменяет: поиск пойдёт в том
        # контексте, который открыт в браузере. Без этого правила ночной
        # перезапуск в Default отчитывался бы как здоровая сессия.
        return SessionVerdict(SESSION_MISSING)
    if cookie_state == SESSION_MISSING:
        return SessionVerdict(SESSION_MISSING)
    # Кука жива, логин-стены и челленджа нет, а разметку опознать не удалось.
    # Это дрейф вёрстки, а не отсутствие сессии. Склеив эти два случая, прогон
    # 2026-08-10 отказался работать при полностью живой авторизации: LinkedIn
    # сменил разметку, и «страницу не узнал» было прочитано как «сессии нет».
    return SessionVerdict(SESSION_OK, page_unrecognised=True)


def main(argv: "Sequence[str] | None" = None) -> int:
    """Печатает имя каталога профиля, в котором лежит сессия LinkedIn.

    Нужен запускалке десктопа: она на bash и умеет читать только строку.
    Печатается имя, а не путь — Chromium принимает --profile-directory
    именно в таком виде.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Профиль Chrome с сессией LinkedIn")
    parser.add_argument("--user-data-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    resolution = resolve_profile(args.user_data_dir)
    if resolution.unreadable:
        # В stderr, чтобы stdout остался пригодным для подстановки в аргумент.
        print(
            "предупреждение: не удалось прочитать профили: "
            + ", ".join(resolution.unreadable)
            + " — выбор мог быть сделан по неполным данным",
            file=sys.stderr,
        )
    print(resolution.path.name)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
