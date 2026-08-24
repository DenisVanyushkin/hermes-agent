"""Отбор тестов форка — регрессия простоя 2026-08-22.

``run-fork-tests.sh`` считает набор как «свои тесты форка» плюс «тесты,
изменённые мержем». Второе слагаемое берётся из ``git diff --name-only`` без
``--diff-filter``, поэтому в набор попадают файлы, которые мерж **удалил**.
pytest, получив несуществующий путь, не пропускает его, а обрушивает весь
прогон: ``no tests ran in 0.01s``. Компаратор гейта не находит итоговой строки,
возвращает rc 2 «несравнимо», и мерж не приземляется — при том, что проверен он
не был ни разу.

Поэтому здесь проверяется не «набор непустой», а инвариант: **каждый путь в
наборе существует в том дереве, против которого набор будет запущен**. Его
нарушение гасит прогон целиком, а не один файл.

Набор читается не из нового флага, а из argv, который скрипт передаёт
интерпретатору: ``HERMES_PYTHON`` подменяется на фальшивый интерпретатор,
записывающий свои аргументы. Так тест работает против скрипта как он есть
сегодня, и падение указывает на удалённый путь, а не на незнакомую опцию.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run-fork-tests.sh"

UPSTREAM_REF = "refs/remotes/upstream/main"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, rel: str, body: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    """Форк, который вливает апстрим, удаливший один свой тест.

    Три файла и ровно один вид изменения — больше для этого инварианта не нужно,
    а меньше не позволяет отличить «удалённый» от «чужого».
    """
    repo = tmp_path / "fork"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    _write(repo, "tests/test_upstream_kept.py", "def test_kept():\n    pass\n")
    _write(repo, "tests/test_upstream_dropped.py", "def test_dropped():\n    pass\n")
    base = _commit(repo, "upstream base")

    # Ветка апстрима уходит вперёд и удаляет один из своих тестов.
    _git(repo, "checkout", "-q", "-b", "upstream-main")
    (repo / "tests/test_upstream_dropped.py").unlink()
    upstream_tip = _commit(repo, "upstream drops a test of its own")
    _git(repo, "update-ref", UPSTREAM_REF, upstream_tip)

    # Форк живёт своей жизнью от общей базы и заводит собственный тест.
    _git(repo, "checkout", "-q", "main")
    _git(repo, "reset", "-q", "--hard", base)
    _write(repo, "tests/test_fork_only.py", "def test_fork_only():\n    pass\n")
    _commit(repo, "fork adds a test of its own")

    # Мерж принимает удаление — путь остаётся в diff, но не в дереве.
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "merge",
        "-q",
        "--no-edit",
        "upstream-main",
    )
    return repo


@pytest.fixture()
def fake_python(tmp_path: Path) -> tuple[Path, Path]:
    """Интерпретатор, который ничего не исполняет, а записывает свой argv."""
    argv_file = tmp_path / "argv.json"
    interpreter = tmp_path / "fake-python"
    interpreter.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['FAKE_ARGV_FILE'], 'w', encoding='utf-8') as fh:\n"
        "    json.dump(sys.argv[1:], fh)\n",
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    return interpreter, argv_file


def _selection(world: Path, fake_python: tuple[Path, Path]) -> list[str]:
    interpreter, argv_file = fake_python
    env = {
        **os.environ,
        "HERMES_PYTHON": str(interpreter),
        "FAKE_ARGV_FILE": str(argv_file),
    }
    result = subprocess.run(
        ["bash", str(RUNNER), str(world)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert argv_file.exists(), (
        "the runner never reached the interpreter, so there is no selection to "
        f"inspect; rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    return [arg for arg in argv if arg.startswith("tests/") and arg.endswith(".py")]


def test_deleted_path_not_selected(world: Path, fake_python: tuple[Path, Path]) -> None:
    """Путь, удалённый мержем, не имеет права попасть в набор.

    Ассерт сформулирован через существование, а не через имя: набор, содержащий
    любой несуществующий путь, обрушит прогон целиком независимо от того, как
    этот путь туда попал.
    """
    selection = _selection(world, fake_python)

    missing = sorted(path for path in selection if not (world / path).exists())
    assert not missing, (
        "the selection names paths that do not exist in the tree under test; "
        "pytest aborts the whole run on the first of them instead of skipping "
        f"it: {missing}"
    )


def test_fork_own_test_is_selected(world: Path, fake_python: tuple[Path, Path]) -> None:
    """Обратная сторона: фильтр не должен вычистить собственные тесты форка."""
    selection = _selection(world, fake_python)

    assert "tests/test_fork_only.py" in selection, (
        "the fork's own test disappeared from the selection; a filter that "
        f"drops it makes the gate blind rather than safe: {selection}"
    )
