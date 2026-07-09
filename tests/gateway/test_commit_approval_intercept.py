"""Task C2: gateway text-reply intercept for commit-gate approval/discard."""

import subprocess
import types
from pathlib import Path

import pytest

from gateway.run import GatewayRunner
from hermes_cli import commit_gate_service


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


def _write_pending(tmp_path, workspace, changed_files=("README.md",), commit_message="chore: thing"):
    monkeypatch_home = tmp_path / "hermes-home"
    commit_gate_service.record_pending(
        session_id="sess-1",
        workspace_path=str(workspace),
        changed_files=list(changed_files),
        commit_message=commit_message,
    )
    return monkeypatch_home


def test_no_pending_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    ack = GatewayRunner._build_commit_approval_ack("коммить", _source())
    assert ack is None


def test_pending_plus_commit_reply_commits_and_clears(tmp_path, monkeypatch):
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

    ack = GatewayRunner._build_commit_approval_ack("коммить", _source())

    assert ack is not None
    assert "Закоммичено" in ack
    log = _git(repo, "log", "-1", "--pretty=%s").stdout.strip()
    assert log == "chore: update readme"
    assert commit_gate_service.get_pending() is None


def test_pending_plus_discard_reply_stashes_and_clears(tmp_path, monkeypatch):
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

    ack = GatewayRunner._build_commit_approval_ack("отмена", _source())

    assert ack is not None
    assert "отклонены" in ack
    status = _git(repo, "status", "--porcelain").stdout.strip()
    assert status == ""
    assert commit_gate_service.get_pending() is None


def test_ordinary_message_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    commit_gate_service.record_pending(
        session_id="sess-1",
        workspace_path=str(repo),
        changed_files=["README.md"],
        commit_message="chore: thing",
    )

    ack = GatewayRunner._build_commit_approval_ack("почини баг", _source())

    assert ack is None
    assert commit_gate_service.get_pending() is not None


def test_slack_non_operator_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_OPERATOR_SLACK_UID", "U_OPERATOR")
    repo = tmp_path / "repo"
    _init_repo(repo)
    commit_gate_service.record_pending(
        session_id="sess-1",
        workspace_path=str(repo),
        changed_files=["README.md"],
        commit_message="chore: thing",
    )

    ack = GatewayRunner._build_commit_approval_ack("коммить", _source(platform="slack", user_id="U_OTHER"))

    assert ack is None
    assert commit_gate_service.get_pending() is not None


def test_slack_operator_commits(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_OPERATOR_SLACK_UID", "U_OPERATOR")
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n")
    commit_gate_service.record_pending(
        session_id="sess-1",
        workspace_path=str(repo),
        changed_files=["README.md"],
        commit_message="chore: update readme",
    )

    ack = GatewayRunner._build_commit_approval_ack("коммить", _source(platform="slack", user_id="U_OPERATOR"))

    assert ack is not None
    assert "Закоммичено" in ack
    assert commit_gate_service.get_pending() is None
