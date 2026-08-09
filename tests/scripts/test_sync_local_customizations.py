"""Интеграционные тесты синхронизации форка во временных репозиториях.

Скрипт ходит только в локальные пути: upstream и personal remote — это
bare-репозитории в tmp_path, токена нет, гейтвея нет.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC = REPO_ROOT / "scripts" / "sync-local-customizations.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def world(tmp_path: Path):
    """upstream (bare) → fork (рабочий) → personal (bare)."""
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    _git(upstream, "init", "-q", "--bare", "-b", "main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "agent").mkdir()
    (seed / "gateway").mkdir()
    (seed / "agent" / "core.py").write_text("VALUE = 1\n")
    (seed / "gateway" / "run.py").write_text("PORT = 8080\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "upstream base")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "-q", "origin", "main")

    fork = tmp_path / "fork"
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(fork)], check=True, capture_output=True
    )
    _git(fork, "config", "user.email", "t@t")
    _git(fork, "config", "user.name", "t")
    _git(fork, "checkout", "-qb", "local/customizations")
    (fork / "local_feature.py").write_text("LOCAL = True\n")
    # Заглушка вместо настоящего sync-runtime-scripts.sh: тот копирует файлы в
    # живой ~/.hermes/scripts, чему в тестах не место. Скрипт синхронизации
    # обязан его найти, иначе выходит с ошибкой ещё до пуша.
    (fork / "scripts").mkdir(exist_ok=True)
    helper = fork / "scripts" / "sync-runtime-scripts.sh"
    helper.write_text("#!/usr/bin/env bash\nexit 0\n")
    helper.chmod(0o755)
    _git(fork, "add", "-A")
    _git(fork, "commit", "-qm", "local customization")

    personal = tmp_path / "personal.git"
    personal.mkdir()
    _git(personal, "init", "-q", "--bare", "-b", "local/customizations")

    return {"upstream": upstream, "seed": seed, "fork": fork, "personal": personal}


def _inert_test_cmd(world) -> Path:
    """Обманка вместо прогона тестов: один и тот же лог до и после слияния.

    Нужна по умолчанию, иначе тесты про слияние упрутся в тестовый гейт:
    настоящий run-fork-tests.sh посчитает набор собственных тестов во
    временном репозитории, получит пустоту и честно откажется работать.
    Тесты самого гейта переопределяют эту команду явно.
    """
    script = world["fork"].parent / "inert-tests.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
        "echo '1 failed, 5 passed in 2.00s'\n"
    )
    script.chmod(0o755)
    return script


def _stub_hermes_bin(world) -> Path:
    """Заглушка вместо настоящего hermes.

    Без неё resolve_hermes_bin находит боевой бинарь и тест перезапускает
    ЖИВОЙ гейтвей — проверено на себе 2026-08-09. HERMES_BIN стоит первым в
    списке кандидатов, поэтому одной этой переменной достаточно.
    """
    script = world["fork"].parent / "hermes-stub.sh"
    script.write_text('#!/usr/bin/env bash\necho "hermes stub $*"\nexit 0\n')
    script.chmod(0o755)
    return script


def _run_sync(world, extra_env=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        {
            "HERMES_BIN": str(_stub_hermes_bin(world)),
            "HERMES_LOCAL_BRANCH": "local/customizations",
            "HERMES_UPSTREAM_REMOTE": "origin",
            "HERMES_UPSTREAM_BRANCH": "main",
            "HERMES_UPSTREAM_FETCH_URL": str(world["upstream"]),
            "HERMES_PERSONAL_REMOTE_URL": str(world["personal"]),
            "HERMES_ENV_FILE": "/dev/null",
            "HERMES_SYNC_TEST_CMD": str(_inert_test_cmd(world)),
            "SUDO_ASKPASS": "/bin/false",
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SYNC)],
        cwd=world["fork"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _add_upstream_commit(world, path: str, content: str, message: str) -> None:
    seed = world["seed"]
    target = seed / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", message)
    _git(seed, "push", "-q", "origin", "main")


def test_upstream_commits_arrive_as_a_merge_not_a_replay(world):
    _add_upstream_commit(world, "agent/new_module.py", "NEW = 1\n", "upstream feature")

    result = _run_sync(world)
    assert result.returncode == 0, result.stderr

    fork = world["fork"]
    assert (fork / "agent" / "new_module.py").exists()
    assert (fork / "local_feature.py").exists(), "локальная правка обязана уцелеть"
    merges = _git(fork, "rev-list", "--merges", "--count", "origin/main..HEAD")
    assert merges == "1", "обновление должно приезжать merge-коммитом"


def test_no_upstream_changes_is_a_noop(world):
    result = _run_sync(world)
    assert result.returncode == 0, result.stderr
    assert "no upstream changes" in result.stdout.lower()


def test_a_conflict_leaves_the_branch_untouched_and_reports_paths(world):
    fork = world["fork"]
    (fork / "gateway" / "run.py").write_text("PORT = 9090\n")
    _git(fork, "commit", "-qam", "local port change")
    before = _git(fork, "rev-parse", "HEAD")

    _add_upstream_commit(world, "gateway/run.py", "PORT = 7070\n", "upstream port change")

    result = _run_sync(world)

    assert result.returncode == 0, result.stderr
    assert _git(fork, "rev-parse", "HEAD") == before, "ветка не должна двигаться"
    assert "gateway/run.py" in result.stdout
    assert "conflict" in result.stdout.lower()
    assert _git(fork, "status", "--porcelain") == "", "рабочее дерево должно остаться чистым"


def test_another_hosts_commits_are_merged_in(world):
    """Резидентный агент пушит в общую ветку сам; его коммиты обязаны уцелеть.

    Заменяет TestPersonalRemoteIntegrationAfterHistoryRewrite: тот
    воспроизводил инцидент 2026-07-27, где скрипт принял собственную
    дорефрешенную линию за чужие коммиты. Переписывания истории больше нет,
    поэтому воспроизвести тот сценарий нечем — но гарантия та же: ни наша
    работа, ни чужая не теряются.
    """
    fork, personal = world["fork"], world["personal"]
    _git(fork, "remote", "add", "personal", str(personal))
    _git(fork, "push", "-q", "personal", "local/customizations")

    other = fork.parent / "other-host"
    subprocess.run(
        ["git", "clone", "-q", "-b", "local/customizations", str(personal), str(other)],
        check=True,
        capture_output=True,
    )
    _git(other, "config", "user.email", "o@o")
    _git(other, "config", "user.name", "o")
    (other / "from_other_host.py").write_text("OTHER = True\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "work from another host")
    _git(other, "push", "-q", "origin", "local/customizations")

    _add_upstream_commit(world, "agent/new_module.py", "NEW = 1\n", "upstream feature")

    result = _run_sync(world)
    assert result.returncode == 0, result.stderr
    assert (fork / "from_other_host.py").exists(), "коммит другого хоста потерян"
    assert (fork / "agent" / "new_module.py").exists()
    assert (fork / "local_feature.py").exists()
