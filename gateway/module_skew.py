"""Детект того, что файлы загруженных модулей изменились на диске.

``gateway/code_skew.py`` сравнивает ревизию git и отвечает на вопрос
«рискованно ли прямо сейчас» для разовых операций (/model). Здесь другой,
более узкий вопрос: **устарел ли модуль, который процесс уже держит в
памяти**. Разница принципиальна на машинах, где HEAD двигают часто и по
поводам, не касающимся кода (автопуш-хук, кураторские патчи, коммиты в
docs/): по HEAD пришлось бы рестартовать каждые двадцать минут, по файлам —
только когда действительно разъехалось.

Побочно ловится случай, недостижимый ни для какого preload: старый валидатор
в памяти против свежего YAML на диске (конфиги читаются с диска на каждом
обращении). Для этого есть ``watch_files``.

Замерено на этом дереве: 583 модуля из репозитория, stat-проход 3.0 мс,
sha256 всех 583 файлов (26.9 MiB) — 118 мс разово на старте.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# path -> (mtime_ns, size, sha256_hex). Пустой словарь == снимок не снят.
_snapshot: dict[Path, tuple[int, int, str]] = {}
_snapshot_taken = False


def reset_snapshot() -> None:
    """Сбросить состояние (используется тестами)."""
    global _snapshot_taken
    _snapshot.clear()
    _snapshot_taken = False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> tuple[int, int, str] | None:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size, _sha256(path))
    except OSError:
        return None


# Компоненты пути, по которым файл считается ЧУЖИМ, даже если лежит внутри
# корня репозитория. На проде venv живёт прямо в корне
# (``<repo>/venv/lib/python3.x/site-packages``), и без этого фильтра снимок
# накрывал бы сотни файлов зависимостей: ``pip install -e .`` — штатный шаг
# деплоя — переписывает их и выглядел бы как скос кода гейтвея.
_FOREIGN_PATH_PARTS = frozenset({"site-packages", "site-python", ".venv", "venv"})


def _is_foreign_path(path: Path) -> bool:
    return any(part in _FOREIGN_PATH_PARTS for part in path.parts)


def _repo_module_files(project_root: Path) -> list[Path]:
    """Файлы модулей из sys.modules, лежащие внутри корня репозитория.

    Файлы venv/site-packages исключаются: см. ``_FOREIGN_PATH_PARTS``.
    """
    found = []
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        try:
            path = Path(filename).resolve()
        except Exception:  # noqa: BLE001 — OSError, RuntimeError (петля
            # симлинков), ValueError (NUL в пути): один битый путь не имеет
            # права ронять весь снимок и выключать детектор навсегда.
            continue
        if project_root in path.parents and not _is_foreign_path(path):
            found.append(path)
    return found


def take_snapshot(project_root: Path, watch_files: list[str] | None = None) -> int:
    """Снять снимок. Зовётся на старте, пока sys.modules совпадает с диском.

    Возвращает число охваченных файлов. Никогда не бросает.
    """
    global _snapshot_taken
    try:
        root = Path(project_root).resolve()
        _snapshot.clear()

        for path in _repo_module_files(root):
            fp = _fingerprint(path)
            if fp is not None:
                _snapshot[path] = fp

        for rel in watch_files or []:
            path = (root / rel).resolve()
            fp = _fingerprint(path)
            if fp is not None:  # несуществующий путь молча игнорируем
                _snapshot[path] = fp

        _snapshot_taken = True
        return len(_snapshot)
    except Exception:  # noqa: BLE001 — детектор не имеет права ронять старт
        logger.warning("module-skew snapshot failed", exc_info=True)
        _snapshot.clear()
        _snapshot_taken = False
        return 0


def detect_module_skew(project_root: Path) -> list[str]:
    """Пути (относительно корня) файлов, чьё СОДЕРЖИМОЕ изменилось с снимка.

    Два шага: дешёвый ``stat`` по всему снимку, затем sha256 только по
    разошедшимся. Второй шаг обязателен — ``git pull``/``git reset``
    переписывает файлы и двигает mtime даже когда содержимое то же, и без
    проверки хешей гейтвей рестартовал бы на пустом месте.

    Файлы, появившиеся в sys.modules ПОСЛЕ снимка, скосом не считаются: они
    только что прочитаны с текущего диска, — но добавляются в снимок здесь же,
    чтобы их последующая правка на диске уже ловилась (ленивые импорты вроде
    ``run_agent`` иначе оставались бы невидимыми навсегда).

    Известное ограничение: правка, сохранившая И mtime, И размер, первым
    (stat-)шагом не отсеивается и до sha256 не доходит — осознанный размен на
    дешёвый проход по всему снимку каждые несколько минут.
    """
    if not _snapshot_taken:
        return []
    try:
        root = Path(project_root).resolve()
        changed = []
        for path, (mtime_ns, size, sha) in _snapshot.items():
            try:
                st = path.stat()
            except OSError:
                changed.append(path)  # файл исчез — тоже расхождение
                continue
            if st.st_mtime_ns == mtime_ns and st.st_size == size:
                continue
            try:
                if _sha256(path) != sha:
                    changed.append(path)
            except OSError:
                changed.append(path)

        # Модули, доехавшие в sys.modules после снимка (ленивые импорты),
        # фиксируем текущим состоянием диска: с этого момента их правка
        # будет считаться скосом.
        for path in _repo_module_files(root):
            if path in _snapshot:
                continue
            fp = _fingerprint(path)
            if fp is not None:
                _snapshot[path] = fp

        out = []
        for path in changed:
            try:
                out.append(path.relative_to(root).as_posix())
            except ValueError:
                out.append(str(path))
        return sorted(out)
    except Exception:  # noqa: BLE001 — детектор не имеет права ронять тик
        logger.warning("module-skew detection failed", exc_info=True)
        return []
