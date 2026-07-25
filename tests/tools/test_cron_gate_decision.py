"""Unit tests for the shared cron/headless approval decision resolver."""
from unittest.mock import patch as mock_patch

import pytest

from tools.approval import resolve_cron_gate_decision

KW = dict(
    gate="terminal",
    subject_text="rm -rf /tmp/x",
    description="recursive delete",
    deny_message="BLOCKED: no user present.",
)


class TestApproveMode:
    def test_approve_allows_and_marks_auto(self):
        result = resolve_cron_gate_decision(mode="approve", **KW)
        assert result["approved"]
        assert result["cron_auto_approved"]
        assert result["cron_mode"] == "approve"


class TestDenyMode:
    def test_deny_blocks_with_supplied_message(self):
        result = resolve_cron_gate_decision(mode="deny", **KW)
        assert not result["approved"]
        assert result["message"] == "BLOCKED: no user present."


class TestSmartMode:
    def test_smart_approve_allows(self):
        with mock_patch("tools.approval._smart_approve", return_value="approve"):
            result = resolve_cron_gate_decision(mode="smart", **KW)
        assert result["approved"]
        assert result["smart_approved"]

    def test_smart_deny_blocks(self):
        with mock_patch("tools.approval._smart_approve", return_value="deny"):
            result = resolve_cron_gate_decision(mode="smart", **KW)
        assert not result["approved"]
        assert result["smart_denied"]
        assert "smart approval" in result["message"].lower()

    def test_smart_escalate_blocks(self):
        with mock_patch("tools.approval._smart_approve", return_value="escalate"):
            result = resolve_cron_gate_decision(mode="smart", **KW)
        assert not result["approved"]
        assert result["smart_escalated"]

    def test_smart_unknown_verdict_blocks(self):
        """Anything the aux path can return that is not 'approve'/'deny' fails closed."""
        with mock_patch("tools.approval._smart_approve", return_value=None):
            result = resolve_cron_gate_decision(mode="smart", **KW)
        assert not result["approved"]

    def test_smart_exception_blocks(self):
        with mock_patch("tools.approval._smart_approve", side_effect=RuntimeError("boom")):
            result = resolve_cron_gate_decision(mode="smart", **KW)
        assert not result["approved"]
        assert result["smart_escalated"]

    def test_smart_passes_subject_and_description_to_llm(self):
        with mock_patch("tools.approval._smart_approve", return_value="approve") as smart:
            resolve_cron_gate_decision(mode="smart", **{**KW, "subject_text": "ls -la"})
        smart.assert_called_once_with("ls -la", "recursive delete")


class TestModeResolution:
    def test_mode_none_reads_config(self):
        with mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"):
            result = resolve_cron_gate_decision(**KW)
        assert not result["approved"]

    def test_unknown_mode_fails_closed(self):
        result = resolve_cron_gate_decision(mode="banana", **KW)
        assert not result["approved"]
