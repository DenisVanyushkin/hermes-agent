"""Доказательства прогона: где выполнялась проверка и что стало с обещанным.

Оба блока отвечают на вопросы, которые в разборе 2026-07-28 никто не задал
вовремя. Воспроизведение: агент объявил причину установленной, имея одну
проверку, выполненную В ПЕСОЧНИЦЕ, где ломающийся код не запускается вовсе.
Обещания: из пяти одобренных владельцем пунктов три сделаны, два молча выпали,
один заменён на свою противоположность -- и ревью не могло это поймать, потому
что отсутствующий пункт в дифф не попадает.

Модуль намеренно только про данные и рендер: где взять запись и куда приклеить
блок -- решает вызывающая сторона.
"""
from __future__ import annotations

from dataclasses import dataclass

_RAN_ON = {"host": "на хосте", "sandbox": "в песочнице"}

_REPRODUCED = {
    True: "сбой воспроизведён",
    False: "сбой не воспроизведён",
    None: "результат неоднозначен",
}

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
