"""Task 9: gateway text-reply intercept for the ops gate.

Модель — tests/gateway/test_commit_approval_intercept.py. Ни один тест не
трогает remote: план состоит из локальных git-операций во временном репозитории.
"""

import subprocess
import time
import types
from pathlib import Path

import pytest

from gateway.run import GatewayRunner
from hermes_cli import commit_gate_service, ops_gate_service


def _source(platform="telegram", user_id="U123"):
    return types.SimpleNamespace(platform=platform, user_id=user_id)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")


def _branches(repo: Path) -> str:
    return _git(repo, "branch", "--list").stdout


def _create_op(branch: str = "ops-made-this") -> dict:
    return {
        "op_id": "git_branch_create",
        "risk": "mutate",
        "argv": ["git", "branch", branch],
        "description": f"создать ветку {branch}",
        "irreversible": None,
    }


def _delete_op(branch: str = "doomed") -> dict:
    return {
        "op_id": "git_branch_delete",
        "risk": "destroy",
        "argv": ["git", "branch", "-D", branch],
        "description": f"удалить ветку {branch}",
        "irreversible": "невлитые коммиты восстановимы только по SHA",
    }


def _record(repo: Path, plan: list[dict], task: str = "сделай это") -> None:
    ops_gate_service.record_pending(
        session_id="sess-1", repo_path=str(repo), plan=plan, original_task=task
    )


def test_no_pending_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    assert GatewayRunner._build_ops_approval_ack("выполни", _source()) is None


def test_ordinary_message_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [_create_op()])

    assert GatewayRunner._build_ops_approval_ack("почини баг", _source()) is None
    assert ops_gate_service.get_pending() is not None
    assert "ops-made-this" not in _branches(repo)


def test_execute_runs_the_plan_and_clears_the_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [_create_op()])

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert "git_branch_create" in ack
    assert "ops-made-this" in _branches(repo)
    assert ops_gate_service.get_pending() is None


def test_cancel_clears_the_marker_without_running_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [_create_op()])

    ack = GatewayRunner._build_ops_approval_ack("отмена", _source())

    assert ack is not None
    assert "отмен" in ack.lower()
    assert "ops-made-this" not in _branches(repo)
    assert ops_gate_service.get_pending() is None


def test_destroy_plan_is_not_run_by_a_bare_execute_and_stays_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "doomed")
    _record(repo, [_delete_op()])

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert "подтверждаю git_branch_delete" in ack
    assert "doomed" in _branches(repo)
    # Маркер обязан остаться: оператору ещё предстоит ответить.
    assert ops_gate_service.get_pending() is not None


def test_destroy_plan_runs_when_the_operation_id_is_echoed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "doomed")
    _record(repo, [_delete_op()])

    ack = GatewayRunner._build_ops_approval_ack("подтверждаю git_branch_delete", _source())

    assert ack is not None
    assert "git_branch_delete" in ack
    assert "doomed" not in _branches(repo)
    assert ops_gate_service.get_pending() is None


def test_confirmation_of_another_operation_does_not_run_the_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "doomed")
    _record(repo, [_delete_op()])

    ack = GatewayRunner._build_ops_approval_ack("подтверждаю git_tag_delete", _source())

    # Подтверждение не этого плана -- не ответ гейту: обычный путь продолжается.
    assert ack is None
    assert "doomed" in _branches(repo)
    assert ops_gate_service.get_pending() is not None


def test_execution_stops_at_the_first_non_zero_exit_and_clears_the_marker(tmp_path, monkeypatch):
    """Остальные операции могли опираться на упавшую — их не выполняем, а маркер
    снимаем: ответ на него прогнал бы уже выполненную часть плана заново."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [
        _create_op("first-ok"),
        # `git branch -D` несуществующей ветки -> ненулевой exit, без исключения.
        {"op_id": "git_branch_delete_missing", "risk": "mutate",
         "argv": ["git", "branch", "-D", "no-such-branch"],
         "description": "падающая операция", "irreversible": None},
        _create_op("never-created"),
    ])

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert "first-ok" in _branches(repo)
    assert "never-created" not in _branches(repo)
    assert "never-created" not in ack.split("Не выполнялись:")[0]
    assert "Не выполнялись:" in ack
    assert ops_gate_service.get_pending() is None


def test_a_raising_operation_still_clears_the_marker_and_acks(tmp_path, monkeypatch):
    """Не только OpsExecutionError: отсутствующий бинарник даёт FileNotFoundError.
    Если он утечёт наружу, оператор получит None при уже выполненной части плана,
    а взведённый маркер прогонит план повторно на следующее «выполни»."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [
        _create_op("ran-before-the-crash"),
        {"op_id": "missing_binary", "risk": "mutate",
         "argv": ["hermes-no-such-binary-xyz", "restart"],
         "description": "несуществующий бинарник", "irreversible": None},
        _create_op("after-the-crash"),
    ])

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert "missing_binary" in ack
    assert "ran-before-the-crash" in _branches(repo)
    assert "after-the-crash" not in _branches(repo)
    assert ops_gate_service.get_pending() is None


def test_plan_with_two_destroy_operations_is_refused_and_disarmed(tmp_path, monkeypatch):
    """Одно сообщение подтверждает ровно один id, поэтому такой план неотвечаем.
    Взведённый маркер, на который нельзя ответить, хуже отсутствующего."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "doomed")
    _record(repo, [
        _delete_op(),
        {"op_id": "git_tag_delete", "risk": "destroy", "argv": ["git", "tag", "-d", "v1"],
         "description": "удалить тег", "irreversible": "тег восстанавливать вручную"},
    ])

    ack = GatewayRunner._build_ops_approval_ack("подтверждаю git_branch_delete", _source())

    assert ack is not None
    assert "git_tag_delete" in ack
    assert "doomed" in _branches(repo)
    assert ops_gate_service.get_pending() is None


def test_empty_repo_path_falls_back_to_the_repo_root_not_the_process_cwd(tmp_path, monkeypatch):
    """Path("") -> "." -- это была бы произвольная текущая директория процесса.
    Пустой путь означает «неизвестно», поэтому берётся тот же фолбэк, что у
    коммитного гейта: корень репозитория, в котором лежит gateway/run.py."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    ops_gate_service.record_pending(
        session_id="sess-1", repo_path="", plan=[_create_op("must-not-exist")],
        original_task="сделай это",
    )
    seen = {}

    def _fake_execute(operation, *, cwd, **kwargs):
        seen["cwd"] = Path(cwd)
        return {"op_id": operation.op_id, "status": 0, "output": "", "truncated": False}

    monkeypatch.setattr("hermes_cli.ops_executor.execute_operation", _fake_execute)

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert seen["cwd"] not in (Path(""), Path("."))
    assert seen["cwd"].is_absolute()
    assert (seen["cwd"] / "gateway" / "run.py").exists()


def test_unresolvable_repo_path_is_refused_and_disarmed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    ops_gate_service.record_pending(
        session_id="sess-1", repo_path=str(tmp_path / "gone"),
        plan=[_create_op("must-not-exist")], original_task="сделай это",
    )
    monkeypatch.setattr(
        "hermes_cli.ops_executor.execute_operation",
        lambda *a, **k: pytest.fail("операция не должна выполняться без рабочей директории"),
    )

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert "Не выполняю" in ack
    assert ops_gate_service.get_pending() is None


def test_expired_marker_is_not_answerable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    ops_gate_service.record_pending(
        session_id="sess-1",
        repo_path=str(repo),
        plan=[_create_op("expired-plan")],
        original_task="сделай это",
        created_at=time.time() - ops_gate_service.PENDING_TTL_SECONDS - 60,
    )

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is None
    assert "expired-plan" not in _branches(repo)


def test_slack_non_operator_reply_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_OPERATOR_SLACK_UID", "U_OPERATOR")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [_create_op()])

    ack = GatewayRunner._build_ops_approval_ack(
        "выполни", _source(platform="slack", user_id="U_OTHER")
    )

    assert ack is None
    assert "ops-made-this" not in _branches(repo)
    assert ops_gate_service.get_pending() is not None


def test_slack_operator_reply_runs_the_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_OPERATOR_SLACK_UID", "U_OPERATOR")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _record(repo, [_create_op()])

    ack = GatewayRunner._build_ops_approval_ack(
        "выполни", _source(platform="slack", user_id="U_OPERATOR")
    )

    assert ack is not None
    assert "ops-made-this" in _branches(repo)
    assert ops_gate_service.get_pending() is None


def test_cancel_with_both_gates_pending_clears_both(tmp_path, monkeypatch):
    """«отмена» принимают оба парсера. Двусмысленная отмена = «пусть ничего не
    произойдёт», поэтому она обязана снять оба маркера, а не один."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n")
    commit_gate_service.record_pending(
        session_id="sess-1",
        workspace_path=str(repo),
        changed_files=["README.md"],
        commit_message="chore: update readme",
    )
    _record(repo, [_create_op()])

    ack = GatewayRunner._build_ops_approval_ack("отмена", _source())

    assert ack is not None
    assert ops_gate_service.get_pending() is None
    assert commit_gate_service.get_pending() is None
    assert "ops-made-this" not in _branches(repo)
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_execute_word_leaves_the_commit_gate_alone(tmp_path, monkeypatch):
    """Слова аппрува у гейтов не пересекаются: «выполни» трогает только ops."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n")
    commit_gate_service.record_pending(
        session_id="sess-1",
        workspace_path=str(repo),
        changed_files=["README.md"],
        commit_message="chore: update readme",
    )
    _record(repo, [_create_op()])

    ack = GatewayRunner._build_ops_approval_ack("выполни", _source())

    assert ack is not None
    assert ops_gate_service.get_pending() is None
    assert commit_gate_service.get_pending() is not None
