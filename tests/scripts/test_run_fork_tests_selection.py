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


def test_a_path_missing_from_the_working_tree_is_refused(
    world: Path, fake_python: tuple[Path, Path]
) -> None:
    """Расхождение дерева с HEAD — отказ, а не запись в лог.

    ``--diff-filter=d`` снимает удалённое мержем ещё на входе, поэтому сюда
    доходит только другой случай: путь числится в ``HEAD``, а на диске его нет.
    Кандидаты берутся из ``ls-tree HEAD``, значит такой путь войдёт в набор, и
    это означает, что чекаут не соответствует проверяемому коммиту.

    Логировать и продолжать здесь нельзя. Оставшиеся тесты пройдут, компаратор
    получит нормальную итоговую строку, а ``dropped_missing`` не читает ни один
    потребитель — сенсор уменьшится, и гейт отчитается о чистом прогоне.
    Видимость для человека отказа не заменяет.
    """
    (world / "tests/test_fork_only.py").unlink()

    result = _print_selection(world, fake_python)

    assert result.returncode == 2, (
        "a checkout that does not match its HEAD must stop the gate, not just "
        f"annotate the log; rc={result.returncode} stdout={result.stdout!r}"
    )
    assert result.stdout.strip() == "", (
        f"a refused run still emitted a selection: {result.stdout!r}"
    )
    assert "dropped_missing=1" in result.stderr, (
        f"the missing path was not accounted for; stderr={result.stderr!r}"
    )
    assert "tests/test_fork_only.py" in result.stderr, (
        f"the missing path was not named; stderr={result.stderr!r}"
    )


def test_double_dash_separator_still_yields_the_worktree(
    world: Path, fake_python: tuple[Path, Path]
) -> None:
    """`--` снимает маркер, а не проглатывает путь за ним."""
    interpreter, argv_file = fake_python
    env = {
        **os.environ,
        "HERMES_PYTHON": str(interpreter),
        "FAKE_ARGV_FILE": str(argv_file),
        "HERMES_UPSTREAM_REMOTE": "upstream",
        "HERMES_UPSTREAM_BRANCH": "main",
    }
    result = subprocess.run(
        ["bash", str(RUNNER), "--print-selection", "--", str(world)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert "tests/test_fork_only.py" in result.stdout.splitlines(), (
        f"the worktree after `--` was not used; stdout={result.stdout!r}"
    )


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["FIRST", "SECOND"], id="two_worktrees"),
        pytest.param(["FIRST", "--print-selection"], id="option_after_worktree"),
        pytest.param([], id="no_worktree"),
    ],
)
def test_ambiguous_argv_is_refused(world: Path, argv: list[str]) -> None:
    """Лишний argv отвергается, а не разрешается в пользу последнего.

    Прежде из ``/first /second`` брался второй, и первый исчезал молча. Набор
    тестов решает, поедет ли обновление в прод; тихий выбор одного из двух
    путей — не та неоднозначность, которую стоит терпеть в этой позиции.
    """
    concrete = [str(world) if arg == "FIRST" else arg for arg in argv]
    concrete = [str(world / "elsewhere") if arg == "SECOND" else arg for arg in concrete]

    result = subprocess.run(
        ["bash", str(RUNNER), *concrete], capture_output=True, text=True
    )

    assert result.returncode == 2, (
        f"ambiguous argv {concrete} was accepted; rc={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout.strip() == "", (
        f"a refused run still emitted output: {result.stdout!r}"
    )


def _tests_in(repo: Path, rev: str) -> set[str]:
    listing = _git(repo, "ls-tree", "-r", "--name-only", rev, "tests/")
    return {line for line in listing.splitlines() if line.endswith(".py")}


def _fork_only(repo: Path, head: str, boundary: str) -> set[str]:
    """Набор «наших» тестов так, как его считает раннер: HEAD минус граница."""
    return _tests_in(repo, head) - _tests_in(repo, boundary)


@pytest.fixture()
def drifted_world(tmp_path: Path) -> tuple[Path, str]:
    """Ref апстрима отстал от коммита, который форк реально влил.

    Это D3 в миниатюре. В бою ``upstream/main`` отставал на 752 коммита, и
    из-за этого около 105 апстримовых тестовых файлов попадали в набор как
    «свои»: всё, что апстрим добавил после последнего fetch, выглядит для
    ``comm`` как файл, которого у апстрима нет.
    """
    repo = tmp_path / "drifted"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    _write(repo, "tests/test_upstream_kept.py", "def test_kept():\n    pass\n")
    base = _commit(repo, "upstream base")
    # Ref остаётся здесь и дальше не двигается — именно он и отстанет.
    _git(repo, "update-ref", UPSTREAM_REF, base)

    _git(repo, "checkout", "-q", "-b", "upstream-main")
    _write(repo, "tests/test_upstream_new.py", "def test_new():\n    pass\n")
    merged_upstream = _commit(repo, "upstream adds a test after our last fetch")

    _git(repo, "checkout", "-q", "main")
    _write(repo, "tests/test_fork_only.py", "def test_fork_only():\n    pass\n")
    _commit(repo, "fork adds a test of its own")
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
    return repo, merged_upstream


def test_boundary_is_explicit(
    drifted_world: tuple[Path, str], fake_python: tuple[Path, Path]
) -> None:
    """Без явной границы раннер обязан отказать, а не взять отставший ref.

    Молчаливое использование ref — не неточность, а подмена смысла: гейт
    объявляет чужие тесты своими и гоняет их как сенсор форка. Отказ здесь
    дешевле, чем отчёт, посчитанный не от той границы.
    """
    repo, merged_upstream = drifted_world
    interpreter, argv_file = fake_python

    stale = _fork_only(repo, "HEAD", UPSTREAM_REF)
    exact = _fork_only(repo, "HEAD", merged_upstream)
    assert stale != exact, (
        "the fixture does not distinguish the stale ref from the merged commit, "
        f"so it cannot prove anything: {sorted(stale)}"
    )

    result = subprocess.run(
        ["bash", str(RUNNER), "--print-selection", str(repo)],
        env={
            **os.environ,
            "HERMES_PYTHON": str(interpreter),
            "FAKE_ARGV_FILE": str(argv_file),
            "HERMES_UPSTREAM_REMOTE": "upstream",
            "HERMES_UPSTREAM_BRANCH": "main",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, (
        "the runner accepted an implicit boundary and computed a selection from "
        f"the stale ref: {len(stale)} files ({sorted(stale)}) instead of the "
        f"{len(exact)} it would get from the commit actually merged "
        f"({sorted(exact)}); rc={result.returncode} stdout={result.stdout!r}"
    )
    assert result.stdout.strip() == "", (
        f"a refused run still emitted a selection: {result.stdout!r}"
    )
