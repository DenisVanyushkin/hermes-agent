"""Доказательства прогона: где физически выполнялись его проверки.

Отвечает на вопрос, который в разборе 2026-07-28 никто не задал вовремя. Агент
объявил причину установленной, имея одну проверку -- `EXIT=0` от
`SKIP_LIVE_COLLECTION=1 python3 -m job_intel doctor`. Она выполнялась В
ПЕСОЧНИЦЕ, где браузерного окружения нет вовсе и медленный путь не запускается,
то есть не воспроизводила ровно ничего. Ни агент, ни ревьюер этого не заметили,
потому что в ответе не было сказано, где команда запускалась.

Место берётся из факта, а не со слов агента: `terminal` и `execute_code`
исполняются внутри контейнера, а их вызовы и так лежат в сообщениях хода.
Вывод «воспроизведён ли сбой» остаётся за агентом и живёт в его прозе -- это
суждение, из вызовов оно не следует.

Второй вопрос того разбора -- что стало с каждым одобренным пунктом -- сюда не
входит намеренно. Он тоже про смысл, а не про наблюдаемый факт, и живёт правилом
ревьюера `unaccounted_promised_item` (`prompts/subagents/hermes_code_reviewer.md`).
Детерминированный реестр на его месте покупался бы отпиской вида
`outcome=skipped, note="не успел"`, закрывающей проверку без содержания.
"""
from __future__ import annotations

import json

#: Инструменты, чьи команды физически исполняются ВНУТРИ Docker-песочницы.
#: Отдельный набор, а не переиспользование `MUTATING_TOOL_NAMES`: там про то,
#: меняет ли инструмент состояние, здесь -- про то, ГДЕ он выполняется.
#: Хостовые ops-операции сюда не входят: они исполняются финализатором, в
#: сообщениях вызовами инструментов не появляются и выводятся своим блоком.
SANDBOX_TOOL_NAMES = frozenset({"terminal", "execute_code"})

_MAX_SHOWN = 5
_MAX_COMMAND_CHARS = 120


def observed_sandbox_commands(messages: object) -> list[str]:
    """Команды этого хода, выполненные в песочнице, в порядке вызова.

    Читается из уже имеющихся сообщений хода -- ничего не надо ни объявлять, ни
    просить у модели. Терпимо к любому мусору в структуре: блок про честность
    отчёта не имеет права уронить сам отчёт.
    """
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
                    arguments = json.loads(arguments)
                except (ValueError, TypeError):
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            first_line = str(
                arguments.get("command") or arguments.get("code") or ""
            ).strip().splitlines()
            if first_line:
                found.append(first_line[0][:_MAX_COMMAND_CHARS])
    return found


def render_execution_locus_block(sandbox_commands: list[str]) -> str:
    """Где выполнялись проверки этого хода.

    Пустая строка, когда команд не было вовсе: приписка «ничего не выполнялось»
    к ходу, который ничего и не собирался выполнять, -- шум, а шум учатся
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
