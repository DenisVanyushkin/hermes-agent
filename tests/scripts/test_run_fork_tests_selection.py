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

import itertools
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
    """Интерпретатор, который ничего не исполняет, а записывает свой argv.

    Пишет строкой на вызов, а не перезаписывает файл: так тест видит, что
    раннер позвал интерпретатор **ровно один раз**, и красный результат не
    может оказаться следствием двух прогонов, наложившихся друг на друга.
    """
    argv_file = tmp_path / "argv.jsonl"
    interpreter = tmp_path / "fake-python"
    interpreter.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['FAKE_ARGV_FILE'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    return interpreter, argv_file


def _selection(world: Path, fake_python: tuple[Path, Path]) -> list[str]:
    """Набор, который раннер реально передал pytest.

    Извлекается срезом по контракту вызова — аргументы между парой ``-m pytest``
    и первой опцией раннера, — а не фильтром «похоже на путь к тесту». Срез
    доказывает саму форму вызова: если раннер начнёт передавать пути иначе,
    тест это заметит, а фильтр по внешнему виду промолчал бы.
    """
    interpreter, argv_file = fake_python
    env = {
        **os.environ,
        "HERMES_PYTHON": str(interpreter),
        "FAKE_ARGV_FILE": str(argv_file),
        # Границу называем явно: иначе отказ по недостижимому upstream-ref
        # завершил бы раннер до pytest и затмил проверяемый ассерт.
        "HERMES_UPSTREAM_REMOTE": "upstream",
        "HERMES_UPSTREAM_BRANCH": "main",
    }
    result = subprocess.run(
        ["bash", str(RUNNER), str(world)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "the runner failed before or during the interpreter call, so the "
        f"selection under test is not the one it would really use; rc="
        f"{result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert argv_file.exists(), (
        "the runner never reached the interpreter — the fake was not used, so "
        f"there is no selection to inspect; stderr={result.stderr!r}"
    )
    invocations = [
        json.loads(line)
        for line in argv_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(invocations) == 1, (
        "expected exactly one interpreter call; more than one means the "
        f"selection below is a mix of runs: {invocations}"
    )

    argv = invocations[0]
    assert argv[:2] == ["-m", "pytest"], (
        f"the runner no longer invokes pytest as a module; argv={argv}"
    )
    rest = argv[2:]
    selection = list(itertools.takewhile(lambda arg: not arg.startswith("-"), rest))
    assert len(selection) < len(rest), (
        "no runner-owned pytest option follows the paths, so the slice cannot "
        f"be trusted to end where the selection ends; argv={argv}"
    )
    return selection


def test_deleted_path_not_selected(world: Path, fake_python: tuple[Path, Path]) -> None:
    """Путь, удалённый мержем, не имеет права попасть в набор.

    Ассерт сформулирован через существование, а не через имя: набор, содержащий
    любой несуществующий путь, обрушит прогон целиком независимо от того, как
    этот путь туда попал.
    """
    selection = _selection(world, fake_python)

    # Прежде чем требовать отсутствия лишнего, убеждаемся, что нужное есть:
    # красный список ниже не должен оказаться следствием пустой фикстуры.
    assert "tests/test_fork_only.py" in selection, (
        f"the fixture produced no fork-owned selection at all: {selection}"
    )

    missing = sorted(path for path in selection if not (world / path).exists())
    assert missing == [], (
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


def _print_selection(
    world: Path, fake_python: tuple[Path, Path]
) -> subprocess.CompletedProcess[str]:
    interpreter, argv_file = fake_python
    env = {
        **os.environ,
        "HERMES_PYTHON": str(interpreter),
        "FAKE_ARGV_FILE": str(argv_file),
        "HERMES_UPSTREAM_REMOTE": "upstream",
        "HERMES_UPSTREAM_BRANCH": "main",
    }
    return subprocess.run(
        ["bash", str(RUNNER), "--print-selection", str(world)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_print_selection_emits_only_paths_on_stdout(
    world: Path, fake_python: tuple[Path, Path]
) -> None:
    """stdout — протокол, и в нём не может быть ничего, кроме путей.

    Смысл проверки практический: набор забирают редиректом в файл манифеста.
    Одна диагностическая строка, попавшая в stdout, станет там «ещё одним
    путём», и потребитель манифеста упрётся в файл, которого нет.
    """
    result = _print_selection(world, fake_python)

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    lines = result.stdout.splitlines()
    assert lines, "the selection came back empty"

    not_paths = [line for line in lines if not (world / line).is_file()]
    assert not_paths == [], (
        "stdout carries lines that are not existing paths, so redirecting it "
        f"into a manifest would poison the manifest: {not_paths}"
    )
    assert "fork test selection:" in result.stderr, (
        "the diagnostic line vanished instead of moving to stderr; a selection "
        "that shrinks silently is a gate that reports a clean run"
    )
    assert "fork test selection:" not in result.stdout


def test_print_selection_does_not_run_the_tests(
    world: Path, fake_python: tuple[Path, Path]
) -> None:
    """Спросить набор должно быть дёшево — иначе его снова никто не проверит."""
    _, argv_file = fake_python
    result = _print_selection(world, fake_python)

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert not argv_file.exists(), (
        "--print-selection reached the interpreter, so asking for the selection "
        "costs a full test run"
    )


def test_print_selection_reports_a_path_missing_from_the_working_tree(
    world: Path, fake_python: tuple[Path, Path]
) -> None:
    """Фильтр существования стережёт не удаления мержем, а расхождение дерева.

    ``--diff-filter=d`` снимает удалённое ещё на входе, поэтому здесь нужен
    случай, до которого он не достаёт: файл числится в ``HEAD``, а на диске его
    нет. Так бывает, когда рабочее дерево гейта разошлось с коммитом. Набор
    берётся из ``ls-tree HEAD``, то есть путь в него попадёт, и без фильтра
    существования pytest снова обрушил бы прогон целиком.

    И отброшенное обязано быть названо: сенсор, молча уменьшившийся на файл,
    даёт «прогон чистый» вместо «проверено не всё».
    """
    (world / "tests/test_fork_only.py").unlink()

    result = _print_selection(world, fake_python)

    assert "dropped_missing=1" in result.stderr, (
        f"the missing path was not accounted for; stderr={result.stderr!r}"
    )
    assert "tests/test_fork_only.py" in result.stderr, (
        f"the missing path was not named; stderr={result.stderr!r}"
    )
    assert "tests/test_fork_only.py" not in result.stdout.splitlines(), (
        "the missing path still reached the selection protocol"
    )
