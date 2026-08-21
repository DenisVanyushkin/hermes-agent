"""Точки входа для cron.

ВАЖНО: stdout `watch-tick` и `digest` — это буквально сообщения Амине в
WhatsApp: cron/scheduler.py при returncode == 0 отдаёт stdout прямо на
`deliver` джобы. Инженерный текст сюда печатать нельзя ни при каких
обстоятельствах — он идёт в очередь оператору (fitness/alerts.py), а
оттуда его выгребает отдельная джоба командой `alerts`.
"""

import sys
from datetime import datetime, timedelta, timezone

from fitness import alerts
from fitness.club_config import load_club_rules
from fitness.digest import render_digest
from fitness.engine import tick
from fitness.invictus_client import InvictusClient, SessionDead
from fitness.models import CLUB_TZ
from fitness.reminders import pending_reminders, render_reminder
from fitness.rules import RuleStore
from fitness.session import SessionStore
from fitness.store import JsonStore

REMINDERS_FILE = "reminders.json"


def _client() -> InvictusClient:
    return InvictusClient()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit_reminders(client, now: datetime) -> None:
    """Напоминание о приближающемся дедлайне отмены. Ничего не отменяет само."""
    state = JsonStore(REMINDERS_FILE)
    notified = set(state.read(default={"notified": []}).get("notified", []))
    club_rules = load_club_rules()
    due = pending_reminders(client.my_bookings(), club_rules, now, notified)
    for booking in due:
        print(render_reminder(booking, club_rules, now))
        notified.add(booking.class_id)
    if due:
        state.write({"notified": sorted(notified)}, mode=0o644)


def _queue_alerts(messages, now: datetime) -> None:
    """Инженерное — в очередь оператору, НИКОГДА в stdout: stdout этих
    точек входа доставляется Амине в WhatsApp (`deliver` джобы cron)."""
    for text in messages:
        alerts.push(text, now)


def cmd_watch_tick() -> int:
    now = _now()
    result = tick(
        client=_client(),
        rule_store=RuleStore(),
        club_rules=load_club_rules(),
        now=now,
        session_store=SessionStore(),
    )
    if result.messages:
        print("\n".join(result.messages))
    _queue_alerts(result.alerts, now)
    try:
        _emit_reminders(_client(), now)
    except SessionDead:
        # О смерти сессии уже сообщает tick(); второй раз шуметь незачем.
        pass
    return 0


def cmd_digest() -> int:
    now = _now()
    today = now.astimezone(CLUB_TZ).date()  # клубная дата, не серверная
    client = _client()
    try:
        slots = client.schedule(today, today + timedelta(days=1))
        bookings = client.my_bookings()
    except SessionDead as exc:
        # Не в stdout: дайджест доставляется Амине, а чинить сессию ей нечем.
        alerts.push(f"⚠️ Утренний дайджест Invictus не собран: сессия "
                    f"недействительна ({exc}). Нужен headless-логин.",
                    now, key="digest_session_dead")
        return 0
    text = render_digest(
        bookings=bookings,
        slots=slots,
        rules=RuleStore().load(),
        now=now,
    )
    if text:
        print(text)
    return 0


def cmd_status() -> int:
    session = SessionStore().load()
    if session is None:
        print("Сессия Invictus не захвачена.")
        return 0
    state = "мертва" if session.is_dead else "жива"
    print(f"Сессия {state}, истекает {session.expires_at.isoformat()}")
    return 0


def cmd_alerts() -> int:
    """Выгрести очередь инженерных алертов. Пустая очередь — ни строки."""
    due = alerts.drain(_now())
    if due:
        print("\n".join(due))
    return 0


COMMANDS = {"watch-tick": cmd_watch_tick, "digest": cmd_digest,
            "status": cmd_status, "alerts": cmd_alerts}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"неизвестная команда: {argv[0] if argv else '<нет>'}", file=sys.stderr)
        print(f"доступные: {', '.join(sorted(COMMANDS))}", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
