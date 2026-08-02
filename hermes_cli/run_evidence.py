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
import logging

logger = logging.getLogger(__name__)

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
#: «меня проигнорировали».
#:
#: Строка НЕ зовёт повторить просьбу. Английский оригинал советовал `continue`
#: -- ВОЗОБНОВИТЬ незаконченное; буквальный русский перевод («напиши ещё раз»)
#: советует ПОВТОРИТЬ, а ход мог отработать наполовину: на пути бытового CRUD
#: (`fam shop add`) повтор кладёт позицию в список второй раз. Поэтому здесь
#: только предложение посмотреть на результат -- действие, которое не может
#: ничего испортить.
SUPPRESSED_TURN_NOTICE = "Ответ мог оборваться — проверь, пожалуйста, результат 🙏"

#: Имя контекстной переменной, которой планировщик помечает аудиторию задания
#: (`gateway/session_context.py`). Читается по имени намеренно: импорт
#: `cron.scheduler` втянул бы весь планировщик в турн-путь ради одной строки.
CRON_AUDIENCE_CONTEXT_VAR = "HERMES_CRON_AUDIENCE"

#: Аудитория, при которой результат задания читает конечный пользователь.
CRON_AUDIENCE_END_USER = "end_user"

#: Формы порчи конфига, о которых уже предупредили в этом процессе. Предикат
#: зовётся на каждый ход, а `logger.warning` на каждый ход -- это не сигнал,
#: а фон, который учатся пролистывать.
_WARNED_CONFIG_SHAPES: set[str] = set()


def _warn_once(shape: str, message: str, *args: object) -> None:
    if shape in _WARNED_CONFIG_SHAPES:
        return
    _WARNED_CONFIG_SHAPES.add(shape)
    logger.warning(message, *args)


def cron_end_user_turn() -> bool:
    """True, когда этот ход выполняет крон ради конечного пользователя.

    У крона канал ХОДА и адресат ДОСТАВКИ -- разные вещи. Ход не выполняется
    ни на какой платформе (`platform=""` в контексте сессии, `platform="cron"`
    у агента), а результат уходит туда, куда указывает `deliver:` задания.
    Признак кладёт `cron/scheduler.py` в контекстную переменную на время
    задания, взяв его у `resolve_cron_audience` -- у того же механизма, что уже
    решает, доставлять ли конечному пользователю технический текст отказа.

    Любой отказ (переменной нет, контекст недоступен) -- False, «не подавлять».
    """
    try:
        from gateway.session_context import get_session_env

        value = str(get_session_env(CRON_AUDIENCE_CONTEXT_VAR, "") or "").strip().lower()
        return value == CRON_AUDIENCE_END_USER
    except Exception:
        return False


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

    Отдельная ветка -- крон: у него канал хода и адресат доставки не совпадают,
    поэтому список платформ его не ловит и «cron» в этот список писать нельзя
    (там оказались бы и операторские задания). Аудитория задания приходит
    контекстной переменной, см. `cron_end_user_turn`.

    Любой отказ (нет конфига, мусор в значении, недоступен контекст сессии)
    даёт False. Сломанный конфиг обязан давать «слишком много инженерных
    подробностей у оператора», а не «анти-оверклейм тихо выключен».
    """
    try:
        # Крон проверяется первым: платформы у его хода нет вовсе, а список
        # каналов ниже отвечает на другой вопрос.
        if cron_end_user_turn():
            return True

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

        try:
            _cfg = load_config_readonly() or {}
        except Exception as read_err:
            # Отдельный warning, а не общий except ниже: конфиг, который вообще
            # не читается, оператор чинит иначе, чем опечатку в одном ключе.
            _warn_once(
                "read-failed",
                "display.suppress_engineering_footers_platforms not read (%s: %s); "
                "engineering footers stay on every channel until config.yaml loads",
                type(read_err).__name__,
                read_err,
            )
            return False

        configured = cfg_get(
            _cfg,
            "display",
            "suppress_engineering_footers_platforms",
            default=None,
        )
        # Ключа нет -- фича просто выключена, это нормальный путь и молчит.
        if configured is None:
            return False
        # Строка тоже итерируема, и `"w" in "whatsapp"` истинно -- принять её за
        # список значило бы подавлять по случайному совпадению буквы. Форма
        # руками создаётся легко (потерянное тире списка:
        # `suppress_engineering_footers_platforms: whatsapp`), а симптом у
        # оператора один -- «футеры всё ещё приходят», поэтому она говорит.
        if not isinstance(configured, (list, tuple, set, frozenset)):
            _warn_once(
                f"not-a-list:{type(configured).__name__}",
                "display.suppress_engineering_footers_platforms must be a list, got %r "
                "-- ignoring it; engineering footers are NOT suppressed anywhere. "
                "Write it as `suppress_engineering_footers_platforms: [whatsapp]`",
                configured,
            )
            return False
        return key in {str(item).strip().lower() for item in configured}
    except Exception:
        return False
