"""cron_mode semantics for the shared approval gate (plugin escalations)."""
from unittest.mock import patch as mock_patch

import pytest

from tools.approval import request_tool_approval


@pytest.fixture
def cron_env(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)


def test_smart_deny_blocks_plugin_escalation(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval.is_approved", return_value=False),
        mock_patch("tools.approval._smart_approve", return_value="deny"),
    ):
        result = request_tool_approval(
            tool_name="dangerous_plugin_tool",
            reason="plugin requested a destructive action",
        )
    assert not result["approved"]


def test_smart_escalate_blocks_plugin_escalation(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval.is_approved", return_value=False),
        mock_patch("tools.approval._smart_approve", return_value="escalate"),
    ):
        result = request_tool_approval(
            tool_name="dangerous_plugin_tool",
            reason="plugin requested a destructive action",
        )
    assert not result["approved"]


def test_smart_approve_allows(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval.is_approved", return_value=False),
        mock_patch("tools.approval._smart_approve", return_value="approve"),
    ):
        result = request_tool_approval(
            tool_name="benign_plugin_tool",
            reason="plugin requested a file read",
        )
    assert result["approved"]
