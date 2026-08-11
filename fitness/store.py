"""JSON-хранилище состояния интеграции.

Путь вычисляется лениво: модуль, читающий HERMES_HOME на импорте, заставляет
тесты писать в живое состояние.

Каталог намеренно `~/.hermes/state/fitness/`, а не `~/.hermes/fitness/`:
каталог данных, чьё имя совпадает с именем python-пакета, становится неявным
namespace-пакетом (PEP 420) и затеняет настоящий пакет.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

PAUSE_FLAG = "PAUSED"


def state_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "state" / "fitness"


def is_paused() -> bool:
    return (state_dir() / PAUSE_FLAG).exists()


class JsonStore:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def path(self) -> Path:
        return state_dir() / self.name

    def read(self, default: Any) -> Any:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write(self, payload: Any, *, mode: int = 0o600) -> None:
        directory = state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{self.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            # Права выставляются ДО подмены: иначе файл на мгновение виден с
            # дефолтным режимом, а внутри session.json лежит живой bearer-токен.
            os.chmod(tmp, mode)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
