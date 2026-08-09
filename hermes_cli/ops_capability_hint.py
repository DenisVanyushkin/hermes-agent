"""Ответ на вопрос «а есть ли вообще чем это сделать».

Когда инженер объявляет блокировку, он часто пишет точный рецепт: «выполнить
`hermes lsp install pyright`». Каталог операций -- потолок его способностей, и
если нужной операции там нет, задача невыполнима принципиально, а не из-за
плохой формулировки. Модуль сопоставляет упомянутые команды с CATALOG, чтобы
финальное сообщение говорило «нет операции в каталоге», а не «дай уточнение и
повтори».

Здесь ничего не исполняется и не предлагается к исполнению: гейт апрува
поднимает только propose_ops. Это чтение текста ради одной строки объяснения.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from hermes_cli.ops_catalog import CATALOG

MAX_HINTS = 6
MAX_COMMAND_CHARS = 120

# Двоичные файлы, с которых может начинаться хостовая команда. Набор фиксирован
# намеренно: разбирать произвольный текст как команды -- значит регулярно врать
# «нет операции в каталоге» про то, что командой вовсе не было.
_KNOWN_BINARIES = frozenset(
    {"hermes", "git", "docker", "systemctl", "journalctl", "ss", "stat", "pip", "npm", "apt", "apt-get"}
)

_SHELL_NOISE = ("sudo", "-n", "env")
# Хвост, на котором команда заканчивается в живой речи: запятая, точка, союз.
_TAIL_PUNCTUATION = ",.;:!?)»\"'"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./=@:+-]+")
_BACKTICK_RE = re.compile(r"`([^`\n]{1,400})`")


@dataclass(frozen=True)
class CommandHint:
    """Упомянутая команда и операция каталога, которая её покрывает."""

    command: str
    op_id: str | None
    risk: str | None = None


def _strip_noise(tokens: list[str]) -> list[str]:
    while tokens and tokens[0] in _SHELL_NOISE:
        tokens.pop(0)
    return tokens


def _match(tokens: list[str]) -> tuple[str, str] | None:
    """Самая длинная сигнатура-приставка выигрывает.

    Иначе `git push --force-with-lease` опознавалась бы как обычный git_push --
    destroy выдавали бы за mutate, а это ровно та разница, ради которой
    destroy-операции требуют отдельного эхо-подтверждения.
    """
    best: tuple[str, str] | None = None
    best_length = 0
    for op_id, operation in CATALOG.items():
        signature = list(operation.signature)
        if len(signature) > len(tokens) or len(signature) <= best_length:
            continue
        if tokens[: len(signature)] == signature:
            best = (op_id, operation.risk)
            best_length = len(signature)
    return best


def _candidates_from_text(text: str) -> list[list[str]]:
    """Команды из бэктиков, а при их отсутствии -- из голой прозы."""
    fragments = _BACKTICK_RE.findall(text)
    if fragments:
        return [_TOKEN_RE.findall(fragment) for fragment in fragments]

    found: list[list[str]] = []
    tokens: list[str] = []
    for raw in text.split():
        stripped = raw.strip(_TAIL_PUNCTUATION + "`")
        clean = _TOKEN_RE.fullmatch(stripped) is not None
        if clean and (stripped in _KNOWN_BINARIES or stripped in _SHELL_NOISE):
            # Начало новой команды: предыдущую закрываем, эту открываем.
            if tokens:
                found.append(tokens)
            tokens = [stripped]
        elif tokens and clean:
            tokens.append(stripped)
        elif tokens:
            # Слово, командой быть не могущее (кириллица, кавычки) -- конец команды.
            found.append(tokens)
            tokens = []
        if tokens and raw != stripped:
            # Команду закрыла пунктуация: «...status, затем...».
            found.append(tokens)
            tokens = []
    if tokens:
        found.append(tokens)
    return found


def analyze_commands(texts: Iterable[str]) -> list[CommandHint]:
    hints: list[CommandHint] = []
    seen: set[str] = set()
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        for tokens in _candidates_from_text(text):
            tokens = _strip_noise(list(tokens))
            if not tokens or tokens[0] not in _KNOWN_BINARIES:
                continue
            command = " ".join(tokens)[:MAX_COMMAND_CHARS].rstrip()
            if command in seen:
                continue
            seen.add(command)
            matched = _match(tokens)
            hints.append(
                CommandHint(
                    command=command,
                    op_id=matched[0] if matched else None,
                    risk=matched[1] if matched else None,
                )
            )
            if len(hints) >= MAX_HINTS:
                return hints
    return hints


def has_capability_gap(hints: Iterable[CommandHint]) -> bool:
    return any(hint.op_id is None for hint in hints)


def hint_lines(hints: Iterable[CommandHint]) -> list[str]:
    lines: list[str] = []
    for hint in hints:
        if hint.op_id is None:
            lines.append(f"- `{hint.command}` — нет операции в каталоге")
        else:
            lines.append(f"- `{hint.command}` — есть операция {hint.op_id} ({hint.risk})")
    return lines
