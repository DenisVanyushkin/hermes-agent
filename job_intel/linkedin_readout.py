"""Снимок двух фактов: откуда namespace выходит наружу и жива ли сессия.

Это не фаза приборов, а две команды: без них единственный доступный сигнал —
found_count в суточной сводке, который читается сутками.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from job_intel.linkedin_session import (
    CookieRecord,
    read_cookie_inventory,
    resolve_profile,
    session_state_from_cookies,
)

EXIT_IP_URL = "https://api.ipify.org"
EXIT_IP_TIMEOUT_SECONDS = 10


def probe_exit_ip(*, url: str = EXIT_IP_URL, timeout: int = EXIT_IP_TIMEOUT_SECONDS) -> str | None:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 — фиксированный адрес
            return response.read().decode("utf-8").strip() or None
    except Exception:
        return None


def current_netns() -> str | None:
    """Имя сетевого пространства имён, в котором выполняется этот процесс.

    Сравнивает inode `/proc/self/ns/net` с именованными namespace в
    `/var/run/netns`. Возвращает None для хоста — это не ошибка, а факт,
    который обязан попасть в отчёт.
    """
    try:
        own = Path("/proc/self/ns/net").stat().st_ino
    except OSError:
        return None
    named = Path("/var/run/netns")
    if not named.is_dir():
        return None
    for entry in named.iterdir():
        try:
            if entry.stat().st_ino == own:
                return entry.name
        except OSError:
            continue
    return None


def build_report(
    *,
    exit_ip: str | None,
    inventory: Sequence[CookieRecord],
    now: datetime,
    netns: str | None = None,
    profile_dir: str | None = None,
    profile_reason: str | None = None,
    unreadable_profiles: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "checked_at": now.isoformat(),
        "exit_ip": exit_ip,
        "exit_reachable": exit_ip is not None,
        # exit_ip измеряет выход процесса, который запустил readout, а не
        # браузера. Снаружи namespace это адрес хоста, и без пометки его
        # прочитают как «туннель не работает».
        "netns": netns,
        "exit_ip_attributable": netns is not None,
        # Chrome заводит второй профиль при входе в аккаунт Google, и
        # сессия ложится в него. Состояние, прочитанное из чужого
        # профиля, — не факт о сессии, поэтому каталог называется.
        "profile_dir": profile_dir,
        # Как выбран профиль и что прочитать не удалось: состояние,
        # полученное после нечитаемого профиля с сессией, — факт о
        # правах доступа, а не о сессии.
        "profile_reason": profile_reason,
        "unreadable_profiles": list(unreadable_profiles),
        "session_state": session_state_from_cookies(inventory, now=now),
        "cookies": [
            {
                "name": record.name,
                "host": record.host,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            }
            for record in inventory
        ],
    }


def render_report(report: dict[str, Any]) -> str:
    head = (
        f"{report['session_state']} "
        f"exit={report['exit_ip'] or 'UNREACHABLE'} "
        f"netns={report.get('netns') or 'host'} "
        f"profile={report.get('profile_dir') or '?'}"
    )
    lines = [head]
    for cookie in report["cookies"]:
        lines.append(f"  {cookie['name']:<12} {cookie['host']:<20} expires={cookie['expires_at']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Снимок выхода и сессии LinkedIn")
    parser.add_argument("--profile", required=True, type=Path, help="каталог профиля Chromium")
    parser.add_argument("--json", action="store_true", help="вывести отчёт как JSON")
    args = parser.parse_args(argv)

    resolution = resolve_profile(args.profile)
    cookie_db = resolution.path / "Cookies"
    inventory = read_cookie_inventory(cookie_db) if cookie_db.exists() else []
    report = build_report(
        exit_ip=probe_exit_ip(),
        inventory=inventory,
        now=datetime.now(timezone.utc),
        netns=current_netns(),
        profile_dir=resolution.path.name,
        profile_reason=resolution.reason,
        unreadable_profiles=resolution.unreadable,
    )
    print(json.dumps(report, indent=2) if args.json else render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
