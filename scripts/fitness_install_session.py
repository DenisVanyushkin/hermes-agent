"""Установка сессии Invictus в состояние текущего хоста.

Сессию подписывает человек на ресепшне, программно она невосполнима — поэтому
здесь только установка захваченной пары токенов, никакого логина.

Секреты читаются из файлов или из stdin и никогда не печатаются и не передаются
через argv: argv виден любому пользователю в `ps`.

Использование:
    python scripts/fitness_install_session.py --refresh-file /tmp/refresh.txt \\
                                              --device-id-file /tmp/devid.txt
    python scripts/fitness_install_session.py            # спросит интерактивно
    python scripts/fitness_install_session.py --headers-file /tmp/dev_headers.json

Проверка после установки — тем же клиентом, только чтение.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fitness.models import CLUB_TZ  # noqa: E402
from fitness.session import Session, SessionStore  # noqa: E402
from fitness.store import state_dir  # noqa: E402

DEFAULT_HEADERS = {
    "x-app-version": "4.2.0",
    "x-platform": "ios",
    "user-agent": "Invictus/612 CFNetwork/3860.700.1 Darwin/25.6.0",
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
}


def _read(path: str | None, prompt: str) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return input(prompt).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-file", help="файл с refresh-токеном (43 символа)")
    ap.add_argument("--device-id-file", help="файл с x-device-id (UUID)")
    ap.add_argument("--headers-file", help="JSON с заголовками устройства из захвата")
    ap.add_argument("--access-file", help="файл с access-токеном (необязательно)")
    ap.add_argument("--no-verify", action="store_true", help="не ходить в сеть после записи")
    args = ap.parse_args()

    headers = dict(DEFAULT_HEADERS)
    device_id = ""
    if args.headers_file:
        raw = json.loads(Path(args.headers_file).read_text(encoding="utf-8"))
        for key, value in raw.items():
            if key.lower() == "authorization":
                continue  # протухший bearer из захвата в сессию не кладём
            headers[key] = value
        device_id = str(raw.get("x-device-id") or "")

    if not device_id:
        device_id = _read(args.device_id_file, "x-device-id (UUID): ")
    headers["x-device-id"] = device_id

    refresh = _read(args.refresh_file, "refresh-токен (43 символа): ")
    access = _read(args.access_file, "") if args.access_file else ""

    problems = []
    if len(refresh) != 43:
        problems.append(f"refresh-токен длиной {len(refresh)}, ожидается 43")
    if len(device_id) != 36:
        problems.append(f"x-device-id длиной {len(device_id)}, ожидается 36")
    if problems:
        for p in problems:
            print(f"⚠️  {p}", file=sys.stderr)
        print("Проверь, что скопировал значения целиком.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    store = SessionStore()
    store.save(
        Session(
            access_token=access,
            refresh_token=refresh,
            # Намеренно в прошлом: клиент продлится на первом вызове и запишет
            # настоящий срок из claim exp самого токена.
            expires_at=now - timedelta(minutes=1),
            device_headers=headers,
            captured_at=now,
        )
    )
    print(f"Сессия записана: {state_dir() / 'session.json'} (режим 600)")

    if args.no_verify:
        return 0

    from fitness.invictus_client import InvictusClient, SessionDead

    client = InvictusClient()
    try:
        today = now.astimezone(CLUB_TZ).date()
        slots = client.schedule(today, today)
    except SessionDead as exc:
        print(f"❌ Сессия не ожила: {exc}", file=sys.stderr)
        print(
            "Чаще всего это НЕ мёртвый токен, а неверный x-device-id: без него "
            "сервер отвечает 401 с текстом «Сессия недействительна».",
            file=sys.stderr,
        )
        return 1

    session = store.load()
    print(f"✅ Работает: занятий сегодня {len(slots)}, "
          f"user_id {'вычислен' if session.user_id else 'ПУСТО'}, "
          f"токен действителен до {session.expires_at.isoformat()}")
    info = client.bookings_info()
    print(f"   записей: {len(info.bookings)}, блокировка: "
          f"{info.banned_till.isoformat() if info.banned_till else 'нет'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
