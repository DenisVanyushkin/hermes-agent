"""✅/❌ reaction on the commit-gate message commits/discards the pending deliverable."""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hermes_cli import commit_gate_service
from plugins.platforms.slack.adapter import SlackAdapter

GATE_TEXT = "✅ ЗАДАЧА ВЫПОЛНЕНА: сделал штуку, изменённые файлы ниже."


def _adapter() -> SlackAdapter:
    adapter = SlackAdapter.__new__(SlackAdapter)
    adapter._bot_user_id = "UBOT"
    adapter._team_bot_user_ids = {}
    return adapter


def _gate_event(reaction: str, user: str = "UOP", channel: str = "C1", ts: str = "111.222") -> dict:
    return {
        "type": "reaction_added",
        "reaction": reaction,
        "user": user,
        "item": {"channel": channel, "ts": ts},
    }


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# --- gating (mocked service): operator, pending marker, gate-message match --

def test_non_operator_checkmark_is_noop():
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock()
    client.chat_postMessage = AsyncMock()
    adapter._get_client = lambda ch: client
    with (
        patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}),
        patch("plugins.platforms.slack.adapter.commit_gate_service.get_pending", return_value={"changed_files": ["a.py"]}),
    ):
        event = _gate_event("white_check_mark", user="UINTRUDER")
        asyncio.run(adapter._maybe_apply_commit_approval(event))
    client.conversations_history.assert_not_awaited()
    client.chat_postMessage.assert_not_awaited()


def test_no_pending_marker_is_noop():
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock()
    client.chat_postMessage = AsyncMock()
    adapter._get_client = lambda ch: client
    with (
        patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}),
        patch("plugins.platforms.slack.adapter.commit_gate_service.get_pending", return_value=None),
    ):
        event = _gate_event("white_check_mark")
        asyncio.run(adapter._maybe_apply_commit_approval(event))
    client.conversations_history.assert_not_awaited()
    client.chat_postMessage.assert_not_awaited()


def test_non_gate_message_is_noop_and_pending_intact():
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock(return_value={
        "messages": [{"ts": "111.222", "text": "just an ordinary chat message"}]
    })
    client.chat_postMessage = AsyncMock()
    adapter._get_client = lambda ch: client
    with (
        patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}),
        patch("plugins.platforms.slack.adapter.commit_gate_service.get_pending", return_value={"changed_files": ["a.py"]}),
        patch("plugins.platforms.slack.adapter.commit_gate_service.clear_pending") as clear_mock,
        patch("plugins.platforms.slack.adapter.commit_gate_service.apply_commit") as commit_mock,
    ):
        event = _gate_event("white_check_mark")
        asyncio.run(adapter._maybe_apply_commit_approval(event))
    client.chat_postMessage.assert_not_awaited()
    commit_mock.assert_not_called()
    clear_mock.assert_not_called()


def test_unrecognized_reaction_is_noop():
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock()
    adapter._get_client = lambda ch: client
    with patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP"}):
        event = _gate_event("eyes")
        asyncio.run(adapter._maybe_apply_commit_approval(event))
    client.conversations_history.assert_not_awaited()


# --- real git integration: ✅ commits, ❌ discards -------------------------

def test_checkmark_by_operator_on_gate_message_commits_real_repo(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "feature.py").write_text("print('new feature')\n")

    hermes_home = tmp_path / "home"
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock(return_value={
        "messages": [{"ts": "111.222", "text": GATE_TEXT}]
    })
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.000"})
    adapter._get_client = lambda ch: client

    with patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP", "HERMES_HOME": str(hermes_home)}):
        commit_gate_service.record_pending(
            session_id="s1",
            workspace_path=str(repo),
            changed_files=["feature.py"],
            commit_message="feat: add feature",
        )
        event = _gate_event("white_check_mark")
        asyncio.run(adapter._maybe_apply_commit_approval(event))
        assert commit_gate_service.get_pending() is None  # marker cleared

    client.chat_postMessage.assert_awaited()
    text = client.chat_postMessage.await_args.kwargs["text"]
    assert "Закоммичено" in text

    log = _git(repo, "log", "--oneline", "-1")
    assert "feat: add feature" in log.stdout
    status = _git(repo, "status", "--porcelain")
    assert status.stdout.strip() == ""  # working tree clean after commit


def test_x_reaction_on_gate_message_discards_real_repo(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "feature.py").write_text("print('discard me')\n")

    hermes_home = tmp_path / "home"
    adapter = _adapter()
    client = MagicMock()
    client.conversations_history = AsyncMock(return_value={
        "messages": [{"ts": "111.222", "text": GATE_TEXT}]
    })
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.000"})
    adapter._get_client = lambda ch: client

    with patch.dict(os.environ, {"HERMES_OPERATOR_SLACK_UID": "UOP", "HERMES_HOME": str(hermes_home)}):
        commit_gate_service.record_pending(
            session_id="s1",
            workspace_path=str(repo),
            changed_files=["feature.py"],
            commit_message="feat: add feature",
        )
        event = _gate_event("x")
        asyncio.run(adapter._maybe_apply_commit_approval(event))
        assert commit_gate_service.get_pending() is None  # marker cleared

    client.chat_postMessage.assert_awaited()
    text = client.chat_postMessage.await_args.kwargs["text"]
    assert "отклонены" in text

    status = _git(repo, "status", "--porcelain")
    assert status.stdout.strip() == ""  # feature.py stashed away, tree clean
    stash_list = _git(repo, "stash", "list")
    assert "commit-gate: discarded pending deliverable" in stash_list.stdout
