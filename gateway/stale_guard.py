"""Конфиг-гейт, бюджет авторестартов и тексты алертов для stale-code guard.

Бюджет обязателен: на этой машине post-commit хук автопушит каждый коммит, а
куратор коммитит патчи сам, поэтому сбой в детекторе мог бы поставить
гейтвей в петлю рестартов. Метки времени лежат в файле — иначе рестарт
обнулял бы собственный счётчик и петля стала бы вечной.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "check_every_minutes": 5,
    "idle_timeout_minutes": 10,
    "max_auto_restarts_per_hour": 2,
}
_BUDGET_FILENAME = "gateway-auto-restarts.json"
_WINDOW_SECONDS = 3600.0


def _positive_int(value, default: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return out if out > 0 else default


def get_stale_guard_config(config) -> dict | None:
    """Разобрать ``gateway.stale_code_guard``. None == фича выключена.

    Выключено по умолчанию: на VPS этого блока нет, и там ничего не должно
    измениться.
    """
    try:
        block = (config or {}).get("gateway", {}).get("stale_code_guard")
    except AttributeError:
        return None
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return None

    watch = block.get("watch_files")
    if not isinstance(watch, list) or not all(isinstance(x, str) for x in watch):
        watch = []

    return {
        "check_every_minutes": _positive_int(
            block.get("check_every_minutes"), _DEFAULTS["check_every_minutes"]
        ),
        "idle_timeout_minutes": _positive_int(
            block.get("idle_timeout_minutes"), _DEFAULTS["idle_timeout_minutes"]
        ),
        "max_auto_restarts_per_hour": _positive_int(
            block.get("max_auto_restarts_per_hour"),
            _DEFAULTS["max_auto_restarts_per_hour"],
        ),
        "watch_files": watch,
    }


def budget_path(hermes_home) -> Path:
    return Path(hermes_home) / _BUDGET_FILENAME


def _read_marks(hermes_home) -> list[float]:
    try:
        raw = budget_path(hermes_home).read_text(encoding="utf-8")
        marks = json.loads(raw)
    except (OSError, ValueError):
        return []
    if not isinstance(marks, list):
        return []
    return [float(m) for m in marks if isinstance(m, (int, float))]


def _write_marks(hermes_home, marks: list[float]) -> None:
    """Атомарная запись меток. Бросает при любой проблеме записи.

    Через временный файл рядом + ``os.replace``: рестарт посреди записи иначе
    оставил бы обрезанный JSON, ``_read_marks`` прочитал бы его как пустой, и
    единственное, что переживает рестарт, обнулилось бы.
    """
    path = budget_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(marks))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_auto_restart(hermes_home, now: float) -> bool:
    """Записать метку авторестарта. Никогда не бросает.

    Возвращает True, только если метка реально легла на диск. Бюджет —
    единственное, что удерживает гейтвей от петли рестартов, поэтому потеря
    записи трактуется как «рестартовать нельзя» (fail closed), а не как
    безобидный сбой учёта.
    """
    try:
        marks = [m for m in _read_marks(hermes_home) if now - m < _WINDOW_SECONDS]
        marks.append(now)
        _write_marks(hermes_home, marks)
        return True
    except Exception:  # noqa: BLE001 — учёт бюджета не должен ронять тик
        logger.error(
            "stale-guard: не удалось записать бюджет авторестартов (%s) — "
            "авторестарт запрещён",
            budget_path(hermes_home),
            exc_info=True,
        )
        return False


def budget_writable(hermes_home) -> bool:
    """Проба записи файла бюджета: перезаписать его собственным содержимым.

    Зовётся при вооружении сторожа и перед каждым авторестартом: если
    HERMES_HOME смонтирован read-only / диск полон / права чужие, бюджет
    молча терялся бы и гейтвей рестартовал бы каждые ``check_every_minutes``
    вечно.
    """
    try:
        _write_marks(hermes_home, _read_marks(hermes_home))
        return True
    except Exception:  # noqa: BLE001
        logger.error(
            "stale-guard: файл бюджета %s недоступен для записи",
            budget_path(hermes_home),
            exc_info=True,
        )
        return False


def auto_restart_allowed(hermes_home, now: float, max_per_hour: int) -> bool:
    recent = [m for m in _read_marks(hermes_home) if now - m < _WINDOW_SECONDS]
    return len(recent) < max_per_hour


def format_skew_alert(changed: list[str], boot_time_label: str) -> str:
    head = ", ".join(changed[:2])
    tail = f" (+{len(changed) - 2})" if len(changed) > 2 else ""
    return "\n".join(
        [
            "🟠 Гермес: процесс устарел",
            f"Изменились на диске: {head}{tail}",
            f"Загружены в память: {boot_time_label}",
            "Рестартну, как только никто не пишет.",
        ]
    )


def format_budget_exhausted_alert(max_per_hour: int) -> str:
    return "\n".join(
        [
            "🔴 Гермес: авторестарт отключён",
            f"Исчерпан бюджет ({max_per_hour} за час) — дерево продолжает ехать.",
            "Больше сам рестартовать не буду до перезапуска процесса.",
            "Дальше руками: hermes gateway restart",
        ]
    )


def format_budget_unwritable_alert(path) -> str:
    return "\n".join(
        [
            "🔴 Гермес: авторестарт отключён",
            f"Не могу вести бюджет рестартов: {path} не пишется.",
            "Без бюджета рестартовать опасно (петля). Дальше руками: hermes gateway restart",
        ]
    )
