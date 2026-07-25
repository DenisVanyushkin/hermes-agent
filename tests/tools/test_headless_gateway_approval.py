"""Headless gateway approvals (no notifier registered) follow cron_mode."""
from unittest.mock import patch as mock_patch

from tools.approval import _resolve_headless_gateway_approval

KW = dict(
    pattern_key="rm_recursive",
    pattern_keys=["rm_recursive"],
    description="recursive delete",
    display_target="rm -rf /tmp/x",
    target_label="command",
)


def test_smart_approve_allows(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._smart_approve", return_value="approve"),
    ):
        result = _resolve_headless_gateway_approval("cron_abc_1", **KW)
    assert result["approved"]


def test_smart_deny_blocks(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._smart_approve", return_value="deny"),
    ):
        result = _resolve_headless_gateway_approval("cron_abc_1", **KW)
    assert not result["approved"]
    assert result["status"] == "denied_no_approver"


def test_non_cron_headless_still_denies(monkeypatch):
    """A headless session that is not cron keeps the definitive deny."""
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    result = _resolve_headless_gateway_approval("agent:main:slack:...", **KW)
    assert not result["approved"]
