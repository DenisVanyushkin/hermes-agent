"""Task 9: сообщение операторского гейта для плана операций."""

import subprocess
from pathlib import Path

from hermes_cli.ops_gate_message import render_ops_approval_message, resolve_operation_cwd


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    )


def _repo_with_run_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Главный чекаут и per-run воркtree на ветке hermes-run/*, как в проде."""
    repo = tmp_path / "main"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "initial commit")
    worktree = tmp_path / "runs" / "3d5f0f4d"
    _git(repo, "worktree", "add", "-q", "-b", "hermes-run/3d5f0f4d", str(worktree))
    return repo, worktree


def test_message_shows_argv_cwd_and_the_original_request():
    text = render_ops_approval_message({
        "repo_path": "/home/hermes/.hermes/hermes-agent",
        "original_task": "запушь текущую ветку в origin",
        "plan": [{
            "op_id": "git_push", "risk": "mutate",
            "argv": ["git", "push", "origin", "local/customizations"],
            "description": "опубликовать local/customizations", "irreversible": None,
        }],
    })
    assert "git push origin local/customizations" in text
    assert "/home/hermes/.hermes/hermes-agent" in text
    assert "запушь текущую ветку в origin" in text
    assert "выполни" in text


def test_destroy_plan_asks_for_the_operation_id_not_a_bare_yes():
    text = render_ops_approval_message({
        "repo_path": "/repo",
        "original_task": "удали ветку",
        "plan": [{
            "op_id": "git_branch_delete", "risk": "destroy",
            "argv": ["git", "branch", "-D", "old"],
            "description": "удалить ветку old",
            "irreversible": "незамердженные коммиты останутся только в reflog",
        }],
    })
    assert "подтверждаю git_branch_delete" in text
    assert "незамердженные коммиты" in text


def test_empty_repo_path_still_renders_a_concrete_directory():
    """Пустой cwd в сообщении = операция «где-то»; исполнение при этом всё равно
    произойдёт (интерцепт берёт тот же фолбэк), поэтому показывать надо его."""
    text = render_ops_approval_message({
        "repo_path": "",
        "original_task": "запушь ветку",
        "plan": [{
            "op_id": "git_push", "risk": "mutate",
            "argv": ["git", "push", "origin", "main"],
            "description": "опубликовать main", "irreversible": None,
        }],
    })

    fallback = resolve_operation_cwd("")
    assert "cwd:  \n" not in text and not text.rstrip().endswith("cwd:")
    assert f"cwd:  {fallback}" in text
    assert Path(fallback).is_absolute()
    # Фолбэк -- корень репозитория, тот же, что подставит интерцепт.
    assert (Path(fallback) / "hermes_cli" / "ops_gate_message.py").exists()


def test_resolve_operation_cwd_prefers_the_recorded_path():
    assert resolve_operation_cwd("/repo/x") == "/repo/x"
    assert resolve_operation_cwd(None) == resolve_operation_cwd("")


def test_run_worktree_resolves_to_the_main_checkout(tmp_path: Path):
    """Прогон живёт в per-run воркtree на ветке hermes-run/*, где исполнитель
    отказывается работать (refused_run_branch). Операции обязаны резолвиться в
    ГЛАВНЫЙ чекаут: только там `git push` публикует то, что просил оператор."""
    repo, worktree = _repo_with_run_worktree(tmp_path)

    assert resolve_operation_cwd(str(worktree)) == str(repo)


def test_main_checkout_resolves_to_itself(tmp_path: Path):
    """Резолв идемпотентен: маркер хранит уже отрезолвленный путь, интерцепт
    резолвит его второй раз -- и обязан получить то же самое."""
    repo, _worktree = _repo_with_run_worktree(tmp_path)

    assert resolve_operation_cwd(str(repo)) == str(repo)
    assert resolve_operation_cwd(resolve_operation_cwd(str(repo))) == str(repo)


def test_message_shows_the_main_checkout_not_the_run_worktree(tmp_path: Path):
    """`cwd:` в сообщении -- это обещание оператору. Показать воркtree прогона и
    выполнить в главном чекауте (или наоборот) -- разные директории под одним
    именем."""
    repo, worktree = _repo_with_run_worktree(tmp_path)
    text = render_ops_approval_message({
        "repo_path": str(worktree),
        "original_task": "запушь ветку",
        "plan": [{
            "op_id": "git_push", "risk": "mutate",
            "argv": ["git", "push", "origin", "main"],
            "description": "опубликовать main", "irreversible": None,
        }],
    })

    assert f"cwd:  {repo}" in text
    assert str(worktree) not in text
