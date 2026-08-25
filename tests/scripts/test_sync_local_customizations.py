"""Интеграционные тесты синхронизации форка во временных репозиториях.

Скрипт ходит только в локальные пути: upstream и personal remote — это
bare-репозитории в tmp_path, токена нет, гейтвея нет.
"""

from __future__ import annotations

import os
import re
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


def _run_sync(world, extra_env=None, argv=()) -> subprocess.CompletedProcess:
    # HOME is redirected at a scratch dir on purpose. The script probes
    # "$HOME/.hermes/hermes-agent" for root-owned files and re-execs itself
    # under sudo when it finds any — so a single root-owned file in the real
    # live checkout (a stray `sudo git status` leaves one behind) turns every
    # test in this file red for a reason that has nothing to do with the test.
    home = world["fork"].parent / "fake-home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
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
        ["bash", str(SYNC), *argv],
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


def _boundary_recording_test_cmd(world) -> tuple[Path, Path]:
    """Как ``_inert_test_cmd``, но записывает свой argv.

    Обманка обязана остаться идентичной до и после слияния, иначе гейт увидит
    разные множества падений и упрётся в них вместо проверяемого здесь.
    """
    log = world["fork"].parent / "boundary-calls.log"
    script = world["fork"].parent / "boundary-recording-tests.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> ' + str(log) + "\n"
        "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
        "echo '1 failed, 5 passed in 2.00s'\n"
    )
    script.chmod(0o755)
    return script, log


def test_both_gate_runs_receive_the_fetched_upstream_sha(world):
    """Оба прогона меряют от одного полного SHA — того, который и слили.

    Граница решает, что считается тестом форка. Если один прогон получит ref, а
    другой полный SHA, или два разных SHA, сравнение «до и после» перестанет
    быть сравнением: разойдётся сенсор, а не поведение мержа. Ref к тому же
    может уехать между двумя прогонами, и тогда проверен будет один кандидат, а
    слит другой — поэтому здесь требуется именно 40-значный SHA и совпадение со
    вторым родителем получившегося merge-коммита.
    """
    _add_upstream_commit(world, "agent/new_module.py", "NEW = 1\n", "upstream feature")
    cmd, log = _boundary_recording_test_cmd(world)

    result = _run_sync(world, {"HERMES_SYNC_TEST_CMD": str(cmd)})
    assert result.returncode == 0, result.stderr

    calls = [line.split() for line in log.read_text().splitlines() if line.strip()]
    assert len(calls) == 2, f"expected one gate run before and one after: {calls}"

    boundaries, worktrees = set(), set()
    for call in calls:
        assert len(call) == 7, (
            f"expected `--selection-from <manifest> --attempt-root <root> --boundary <sha> <worktree>`: {call}"
        )
        assert call[0] == "--selection-from" and call[2] == "--attempt-root", (
            f"the manifest selection mode was not explicit: {call}"
        )
        assert call[4] == "--boundary", f"the boundary option moved unexpectedly: {call}"
        assert re.fullmatch(r"[0-9a-f]{40}", call[5]), (
            f"the boundary is not a full immutable SHA: {call}"
        )
        assert call[6] != call[5], f"the worktree is the boundary again: {call}"
        assert Path(call[1]).name == "gate-selection.json", call
        assert Path(call[3]).name == "attempts", call
        boundaries.add(call[5])
        worktrees.add(call[6])

    assert calls[0] == calls[1], f"the two runs did not receive identical argv: {calls}"
    assert len({call[1] for call in calls}) == 1, f"the two runs received different manifests: {calls}"
    assert len({call[3] for call in calls}) == 1, f"the two runs received different attempt roots: {calls}"

    assert len(boundaries) == 1, f"the two runs measured from different commits: {calls}"
    assert len(worktrees) == 1, f"the two runs used different worktrees: {calls}"

    fork = world["fork"]
    merge = _git(fork, "rev-list", "--merges", "-n1", "HEAD")
    assert _git(fork, "rev-parse", merge + "^2") == boundaries.pop(), (
        "the commit that was gated is not the commit that was merged"
    )


def test_no_upstream_changes_is_a_noop(world):
    result = _run_sync(world)
    assert result.returncode == 0, result.stderr
    assert "no upstream changes" in result.stdout.lower()


def _staged_test_cmd(tmp_path: Path, baseline: str, post: str) -> Path:
    """Обманка вместо pytest: первый вызов печатает baseline, второй — post."""
    marker = tmp_path / "run-count"
    script = tmp_path / "staged-tests.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'marker="{marker}"\n'
        'count=$(cat "$marker" 2>/dev/null || echo 0)\n'
        'echo $((count + 1)) > "$marker"\n'
        'if [ "$count" -eq 0 ]; then\n'
        f"cat <<'EOF'\n{baseline}\nEOF\n"
        "else\n"
        f"cat <<'EOF'\n{post}\nEOF\n"
        "fi\n"
    )
    script.chmod(0o755)
    return script


BASELINE_LOG = (
    "FAILED tests/known.py::test_flaky - AssertionError\n1 failed, 5 passed in 2.00s"
)
SAME_LOG = BASELINE_LOG
REGRESSED_LOG = (
    "FAILED tests/known.py::test_flaky - AssertionError\n"
    "FAILED tests/gate.py::test_ops_gate - AssertionError\n"
    "2 failed, 4 passed in 2.00s"
)


def test_a_merge_without_new_failures_lands(world, tmp_path):
    _add_upstream_commit(world, "agent/new_module.py", "NEW = 1\n", "upstream feature")
    cmd = _staged_test_cmd(tmp_path, BASELINE_LOG, SAME_LOG)

    result = _run_sync(world, {"HERMES_SYNC_TEST_CMD": str(cmd)})

    assert result.returncode == 0, result.stderr
    fork = world["fork"]
    assert (fork / "agent" / "new_module.py").exists()
    assert _git(fork, "rev-list", "--merges", "--count", "origin/main..HEAD") == "1"


def test_a_merge_that_breaks_tests_never_reaches_the_branch(world, tmp_path):
    _add_upstream_commit(world, "agent/new_module.py", "NEW = 1\n", "upstream feature")
    fork = world["fork"]
    before = _git(fork, "rev-parse", "HEAD")
    cmd = _staged_test_cmd(tmp_path, BASELINE_LOG, REGRESSED_LOG)

    result = _run_sync(world, {"HERMES_SYNC_TEST_CMD": str(cmd)})

    assert _git(fork, "rev-parse", "HEAD") == before, "ветка не должна двигаться"
    assert not (fork / "agent" / "new_module.py").exists()
    assert "tests/gate.py::test_ops_gate" in result.stdout
    assert _git(fork, "status", "--porcelain") == ""


def test_a_merge_that_cannot_run_is_not_reported_as_a_merge_tree_defect(world):
    """Не всякая неудача merge — расхождение с merge-tree.

    Прогон 2026-08-09 на клоне живого репозитория упал с «merge-tree reported
    a clean merge but git merge conflicted — this is a defect», а настоящей
    причиной было отсутствие git-identity. Сообщение уводило расследование в
    сторону несуществующего дефекта.
    """
    fork = world["fork"]
    _git(fork, "config", "--unset", "user.email")
    _git(fork, "config", "--unset", "user.name")
    _add_upstream_commit(world, "agent/new_module.py", "NEW = 1\n", "upstream feature")

    result = _run_sync(world)

    assert result.returncode != 0
    assert "could not run" in result.stderr
    assert "this is a defect" not in result.stderr


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


def test_post_update_only_publishes_a_landed_update_without_touching_upstream(world):
    """The finalizer fast-forwards an operator-approved merge itself, then
    needs only what follows a landed update: syntax check, runtime scripts,
    push, gateway restart, report. Bringing in newer upstream commits is the
    next scheduled sync's job — doing it here gated the push and the restart
    of an already-landed merge on an unrelated conflict set (2026-08-15).
    """
    fork = world["fork"]
    before = _git(fork, "rev-parse", "HEAD")
    (fork / "landed.py").write_text("LANDED = True\n")
    _git(fork, "add", "-A")
    _git(fork, "commit", "-qm", "landed by the finalizer")
    after = _git(fork, "rev-parse", "HEAD")
    # Upstream moved meanwhile; this mode must not even look at it.
    _add_upstream_commit(world, "agent/core.py", "VALUE = 2\n", "upstream moved on")
    _git(world["seed"], "push", "-q", "origin", "main")

    # A token makes the script actually push (without one it skips the push by
    # design); the personal remote is a local bare repo, so no auth happens.
    proc = _run_sync(world, extra_env={"GITHUB_TOKEN": "dummy"},
                     argv=["--post-update-only", before])

    assert proc.returncode == 0, proc.stderr
    assert _git(fork, "rev-parse", "HEAD") == after            # no upstream merge
    assert _git(world["personal"], "rev-parse", "local/customizations") == after
    assert "gateway restarted: yes" in proc.stdout
    # The report prints short SHAs.
    assert f"Before: {before[:7]}" in proc.stdout and f"After: {after[:7]}" in proc.stdout
    assert "landed.py" in proc.stdout                          # report lists what landed


def test_post_update_only_without_a_before_head_refuses(world):
    """The mode reports what landed between two points; without the first one
    there is nothing to report and nothing to verify."""
    proc = _run_sync(world, argv=["--post-update-only"])
    assert proc.returncode != 0
    assert "post-update-only" in proc.stderr
