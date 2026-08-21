"""Очередь инженерных сообщений оператору (Денису в Telegram).

Существует потому, что stdout кроновых точек входа fitness — это буквально
сообщения Амине в WhatsApp (`cron/scheduler.py` при успешном прогоне
доставляет stdout на `deliver` джобы), а stderr при returncode == 0
выбрасывается молча. Смешивать «✅ Записал тебя на бутcamp» и «refresh 401»
в одной трубе нельзя: у них разные адресаты.

Отдельная джоба (`python -m fitness alerts`, deliver в Telegram оператора)
выгребает эту очередь и доставляет её по адресу.

Дедуп по ключу с суточным окном: тик бежит каждые 5 минут, и мёртвая
сессия иначе превратилась бы в 288 одинаковых сообщений в сутки.
"""

from datetime import datetime, timedelta, timezone

from fitness.store import JsonStore

ALERTS_FILE = "operator-alerts.json"
DEFAULT_COOLDOWN_HOURS = 24


def _empty() -> dict:
    # Функция, а не модульная константа: константу мутировали бы вызовы push().
    return {"version": 1, "queue": [], "last_sent": {}}


def _load() -> dict:
    raw = JsonStore(ALERTS_FILE).read(default=None)
    if not isinstance(raw, dict):
        return _empty()
    queue = raw.get("queue")
    last_sent = raw.get("last_sent")
    return {
        "version": 1,
        "queue": [str(x) for x in queue] if isinstance(queue, list) else [],
        "last_sent": dict(last_sent) if isinstance(last_sent, dict) else {},
    }


def _save(state: dict) -> None:
    # 0o600: в тексте алерта может быть диагностика внутренностей.
    JsonStore(ALERTS_FILE).write(state, mode=0o600)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def push(text: str, now: datetime, *, key: str | None = None,
         cooldown_hours: float = DEFAULT_COOLDOWN_HOURS) -> bool:
    """Поставить алерт в очередь. False — подавлен дедупом или пустой."""
    text = (text or "").strip()
    if not text:
        return False
    key = key or text
    state = _load()
    previous = state["last_sent"].get(key)
    if previous:
        try:
            when = datetime.fromisoformat(str(previous))
        except ValueError:
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if now - when < timedelta(hours=cooldown_hours):
                return False
    state["queue"].append(text)
    state["last_sent"][key] = _iso(now)
    _save(state)
    return True


def pending(now: datetime) -> list[str]:
    """Посмотреть очередь, не забирая (диагностика и тесты)."""
    return list(_load()["queue"])


def drain(now: datetime) -> list[str]:
    """Забрать накопленное и очистить очередь. Журнал дедупа остаётся."""
    state = _load()
    queue = list(state["queue"])
    if queue:
        state["queue"] = []
        _save(state)
    return queue
