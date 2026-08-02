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

#: Отказ ядра в записи. Ищется в РЕЗУЛЬТАТАХ инструментов, а не в тексте
#: агента: собственное рассуждение про read-only -- это его слова, а нас
#: интересует ответ системы.
_WRITE_REFUSAL_MARKERS = ("read-only file system",)

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


def observed_write_refusals(messages: object) -> int:
    """Сколько раз системе отказали в записи за этот ход.

    Отказы приходят в результате инструмента и НИКОГДА не доходят до
    `errors.log`: они происходят внутри контейнера. Поэтому наблюдать их можно
    только здесь -- проверка «grep read-only в хостовом логе» пуста даже тогда,
    когда записи отклонялись весь ход.
    """
    count = 0
    for message in list(messages or []):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        lowered = content.lower()
        count += sum(lowered.count(marker) for marker in _WRITE_REFUSAL_MARKERS)
    return count


def render_execution_locus_block(sandbox_commands: list[str], write_refusals: int = 0) -> str:
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
    if write_refusals:
        # Ставится ПЕРЕД дисклеймером и рядом с командами: 2026-07-28 ответ
        # утверждал «сейчас в рабочем дереве изменения», когда все записи были
        # отклонены. Противоречие видно без ревьюера, если оба факта рядом.
        lines.append(
            f"Отклонено попыток записи в репозиторий: {write_refusals} "
            "(смонтирован :ro, правки возможны только через инженерный пайплайн)"
        )
    lines.append(
        "Наблюдения из песочницы не описывают состояние хоста: файлы, порты, "
        "сервисы и установленные пакеты там свои."
    )
    return "\n".join(lines)


#: Что видит конечный пользователь вместо инженерной объяснялки оборванного
#: хода. Молчание отвергнуто намеренно: «ничего не пришло» в чате читается как
#: «меня проигнорировали», адресат пишет повторно -- а ход мог отработать
#: наполовину. Строка предлагает ровно то действие, которое ему доступно.
SUPPRESSED_TURN_NOTICE = "Не получилось ответить с первого раза — напиши, пожалуйста, ещё раз 🙏"


def engineering_footers_suppressed(
    platform: str | None = None,
    *,
    fallback_platform: str | None = None,
) -> bool:
    """True, когда канал этого хода -- не инженерный.

    Блок про песочницу, отчёт о неудавшихся записях и объяснялка оборванного
    хода адресованы ревьюеру кода. На канале конечного пользователя они
    занимают больше места, чем сам ответ, и не описывают ничего, на что он
    может отреагировать.

    Платформа берётся по порядку: явный аргумент -> контекст сессии, который
    гейтвей выставляет на каждый ход -> `fallback_platform` (обычно
    `agent.platform`, для вызовов вне гейтвея). Контекст стоит выше атрибута
    агента намеренно: у субагента, порождённого внутри хода, свой агент, но
    адресат тот же самый.

    Список каналов -- `display.suppress_engineering_footers_platforms`,
    по умолчанию пуст: без правки конфига поведение прежнее.

    Любой отказ (нет конфига, мусор в значении, недоступен контекст сессии)
    даёт False. Сломанный конфиг обязан давать «слишком много инженерных
    подробностей у оператора», а не «анти-оверклейм тихо выключен».
    """
    try:
        key = str(platform or "").strip().lower()
        if not key:
            try:
                from gateway.session_context import get_session_env

                key = str(
                    get_session_env("HERMES_SESSION_PLATFORM", "") or ""
                ).strip().lower()
            except Exception:
                key = ""
        if not key:
            key = str(fallback_platform or "").strip().lower()
        if not key:
            return False

        from hermes_cli.config import cfg_get, load_config_readonly

        configured = cfg_get(
            load_config_readonly() or {},
            "display",
            "suppress_engineering_footers_platforms",
            default=None,
        )
        # Строка тоже итерируема, и `"w" in "whatsapp"` истинно -- принять её за
        # список значило бы подавлять по случайному совпадению буквы.
        if not isinstance(configured, (list, tuple, set, frozenset)):
            return False
        return key in {str(item).strip().lower() for item in configured}
    except Exception:
        return False
