from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")


@pytest.fixture(autouse=True)
def _clear_module_cache():
    # Ensure a fresh import per test so monkeypatched env vars are re-read cleanly.
    import sys

    sys.modules.pop("hermes_cli.commit_gate_service", None)
    yield
    sys.modules.pop("hermes_cli.commit_gate_service", None)


def _module():
    return importlib.import_module("hermes_cli.commit_gate_service")


def test_record_get_clear_pending_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    module = _module()

    assert module.get_pending() is None

    module.record_pending(
        session_id="sess-1",
        workspace_path="/repo",
        changed_files=["a.py", "b.py"],
        commit_message="feat: thing",
    )

    pending = module.get_pending()
    assert pending is not None
    assert pending["session_id"] == "sess-1"
    assert pending["workspace_path"] == "/repo"
    assert pending["changed_files"] == ["a.py", "b.py"]
    assert pending["commit_message"] == "feat: thing"
    assert pending["status"] == "awaiting_commit"

    module.clear_pending()
    assert module.get_pending() is None
    # Clearing again (already absent) must not raise.
    module.clear_pending()


def test_get_pending_returns_none_when_status_not_awaiting_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    module = _module()

    path = module._pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"status": "done"}')

    assert module.get_pending() is None


def test_get_pending_returns_none_on_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    module = _module()

    path = module._pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")

    assert module.get_pending() is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("коммить", "commit"),
        ("Коммить", "commit"),
        ("коммит", "commit"),
        ("закоммить", "commit"),
        ("commit", "commit"),
        ("коммить и запушь", "commit_push"),
        ("commit and push", "commit_push"),
        ("запушь", None),  # push word alone, no commit word -> not an unambiguous commit reply
        ("отмена", "discard"),
        ("отмени", "discard"),
        ("cancel", "discard"),
        ("discard", "discard"),
        ("сбрось", "discard"),
        ("не коммить", "discard"),
        ("почини баг", None),
        ("спасибо", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_commit_reply(text, expected, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _module()
    assert module.parse_commit_reply(text) == expected


def test_parse_commit_reply_rejects_overly_long_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _module()
    long_text = "коммить " + ("x" * 40)
    assert len(long_text) > 40
    assert module.parse_commit_reply(long_text) is None


def test_apply_commit_commits_modified_and_new_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _module()
    repo = tmp_path / "repo"
    _init_repo(repo)

    (repo / "README.md").write_text("hello world\n")
    (repo / "new_file.py").write_text("print('hi')\n")

    result = module.apply_commit(
        repo=repo,
        changed_files=["README.md", "new_file.py"],
        commit_message="chore: update readme and add file",
        push=False,
    )

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["sha"]
    assert result["pushed"] is False

    log = _git(repo, "log", "-1", "--pretty=%s").stdout.strip()
    assert log == "chore: update readme and add file"

    status = _git(repo, "status", "--porcelain").stdout.strip()
    assert status == ""


def test_apply_commit_with_no_changed_files_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _module()
    repo = tmp_path / "repo"
    _init_repo(repo)

    result = module.apply_commit(repo=repo, changed_files=[], commit_message="chore: nothing", push=False)
    assert result["ok"] is False
    assert result["committed"] is False


def test_apply_discard_stashes_modified_and_new_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _module()
    repo = tmp_path / "repo"
    _init_repo(repo)

    (repo / "README.md").write_text("changed\n")
    (repo / "untracked.py").write_text("print('new')\n")

    result = module.apply_discard(repo=repo, changed_files=["README.md", "untracked.py"])
    assert result["ok"] is True

    status = _git(repo, "status", "--porcelain").stdout.strip()
    assert status == ""
    assert not (repo / "untracked.py").exists()

    stash_list = _git(repo, "stash", "list").stdout.strip()
    assert "commit-gate: discarded pending deliverable" in stash_list


def test_apply_discard_with_no_changed_files_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _module()
    repo = tmp_path / "repo"
    _init_repo(repo)

    result = module.apply_discard(repo=repo, changed_files=[])
    assert result["ok"] is True
    assert "nothing to discard" in result["detail"]


def test_is_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()

    monkeypatch.setenv("HERMES_OPERATOR_SLACK_UID", "U123")
    assert module.is_operator("U123") is True
    assert module.is_operator("U999") is False
    assert module.is_operator("") is False
    assert module.is_operator(None) is False

    monkeypatch.delenv("HERMES_OPERATOR_SLACK_UID", raising=False)
    assert module.is_operator("U123") is False


def test_is_gate_message() -> None:
    module = _module()
    assert module.is_gate_message("✅ ЗАДАЧА ВЫПОЛНЕНА — ревьюер одобрил, жду твоего «коммить»") is True
    assert module.is_gate_message("some other message") is False
    assert module.is_gate_message("") is False
    assert module.is_gate_message(None) is False
