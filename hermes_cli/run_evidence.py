"""Доказательства прогона: где выполнялась проверка и что стало с обещанным.

Оба блока отвечают на вопросы, которые в разборе 2026-07-28 никто не задал
вовремя. Воспроизведение: агент объявил причину установленной, имея одну
проверку, выполненную В ПЕСОЧНИЦЕ, где ломающийся код не запускается вовсе.
Обещания: из пяти одобренных владельцем пунктов три сделаны, два молча выпали,
один заменён на свою противоположность -- и ревью не могло это поймать, потому
что отсутствующий пункт в дифф не попадает.

Место исполнения берётся из факта, а не со слов агента. Вывод «воспроизведён ли
сбой» остаётся за агентом и живёт в его прозе -- механически он не выводится.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Инструменты, чьи команды физически исполняются ВНУТРИ Docker-песочницы.
#: Отдельный набор, а не переиспользование MUTATING_TOOL_NAMES: там про то,
#: меняет ли инструмент состояние, здесь -- про то, ГДЕ он выполняется.
#: Хостовые ops-операции сюда не входят: они исполняются финализатором, в
#: messages вызовами инструментов не появляются и выводятся своим блоком.
SANDBOX_TOOL_NAMES = frozenset({"terminal", "execute_code"})

_MAX_SHOWN = 5
_MAX_COMMAND_CHARS = 120

_OUTCOMES = {"done": "сделано", "skipped": "не сделано", "changed": "сделано иначе"}


@dataclass(frozen=True)
class ReproductionRecord:
    command: str
    ran_on: str
    observed: str
    reproduced: bool | None

    def __post_init__(self) -> None:
        if self.ran_on not in _RAN_ON:
            raise ValueError("invalid_ran_on")


def render_reproduction_block(record: ReproductionRecord | None) -> str:
    """Блок о воспроизведении. Отсутствие записи -- тоже содержимое, не пустота."""
    if record is None:
        return "*Воспроизведение*\nВоспроизведение не выполнялось."
    return "\n".join([
        "*Воспроизведение*",
        f"Команда: `{record.command}`",
        f"Выполнена: {_RAN_ON[record.ran_on]}",
        f"Наблюдалось: {record.observed}",
        f"Вывод: {_REPRODUCED[record.reproduced]}",
    ])


def observed_sandbox_commands(messages: object) -> list[str]:
    """Команды этого хода, выполненные в песочнице, в порядке вызова.

    Читается из уже имеющихся сообщений хода -- ничего не надо ни объявлять,
    ни просить у модели. Заявлению агента о том, где он проверял, верить
    нельзя: 2026-07-28 он сообщил `EXIT=0` как доказательство работоспособности,
    получив его в контейнере, где ломающийся код не запускается вовсе.
    """
    import json as _json

    found: list[str] = []
    for message in list(messages or []):
        if not isinstance(message, dict):
            continue
        for call in list(message.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name") not in SANDBOX_TOOL_NAMES:
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = _json.loads(arguments)
                except (ValueError, TypeError):
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            text = str(
                arguments.get("command") or arguments.get("code") or ""
            ).strip().splitlines()
            if text:
                found.append(text[0][:_MAX_COMMAND_CHARS])
    return found


def render_execution_locus_block(sandbox_commands: list[str]) -> str:
    """Где выполнялись проверки этого хода.

    Пустой блок, когда команд не было вовсе: приписка «ничего не выполнялось» к
    ходу, который ничего и не собирался выполнять, -- шум, а шум учатся
    пролистывать вместе с сигналом.
    """
    if not sandbox_commands:
        return ""
    lines = [
        "*Где выполнялись проверки*",
        f"В песочнице (окружение контейнера, не хост): {len(sandbox_commands)}",
    ]
    for command in sandbox_commands[:_MAX_SHOWN]:
        lines.append(f"- `{command}`")
    if len(sandbox_commands) > _MAX_SHOWN:
        lines.append(f"- …и ещё {len(sandbox_commands) - _MAX_SHOWN}")
    lines.append(
        "Наблюдения из песочницы не описывают состояние хоста: файлы, порты, "
        "сервисы и установленные пакеты там свои."
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class PromiseItem:
    text: str
    outcome: str | None
    note: str = ""

    def __post_init__(self) -> None:
        if self.outcome is not None and self.outcome not in _OUTCOMES:
            raise ValueError("invalid_outcome")


def unaccounted_promises(items: list[PromiseItem]) -> list[PromiseItem]:
    """Пункты, по которым прогон не отчитался.

    «Не сделано» и «сделано иначе» -- полноправные исходы, но только с
    причиной: без неё это не решение, а умолчание, то есть ровно тот случай,
    ради которого реестр и заводится.
    """
    return [
        item
        for item in items
        if item.outcome is None or (item.outcome != "done" and not item.note.strip())
    ]


def render_promise_block(items: list[PromiseItem]) -> str:
    if not items:
        return ""
    lines = ["*Что стало с одобренными пунктами*"]
    for item in items:
        label = _OUTCOMES.get(item.outcome or "", "не отчитано")
        suffix = f" — {item.note.strip()}" if item.note.strip() else ""
        lines.append(f"- {item.text}: {label}{suffix}")
    return "\n".join(lines)
