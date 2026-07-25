"""cron_mode semantics for the execute_code guard."""
from unittest.mock import patch as mock_patch

import pytest

from tools.approval import check_execute_code_guard


@pytest.fixture
def cron_env(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)


SCRIPT = "import shutil; shutil.rmtree('/var/lib/job-intel/state')"


def test_smart_denied_blocks(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._get_approval_mode", return_value="manual"),
        mock_patch("tools.approval._smart_approve", return_value="deny"),
    ):
        result = check_execute_code_guard(SCRIPT, "local", has_host_access=True)
    assert not result["approved"]
    assert result["cron_gate"] == "execute_code"


def test_smart_escalate_blocks(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._get_approval_mode", return_value="manual"),
        mock_patch("tools.approval._smart_approve", return_value="escalate"),
    ):
        result = check_execute_code_guard(SCRIPT, "local", has_host_access=True)
    assert not result["approved"]


def test_smart_approve_allows(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._get_approval_mode", return_value="manual"),
        mock_patch("tools.approval._smart_approve", return_value="approve"),
    ):
        result = check_execute_code_guard("print('hello')", "local", has_host_access=True)
    assert result["approved"]


def test_smart_sends_script_body_to_reviewer(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._get_approval_mode", return_value="manual"),
        mock_patch("tools.approval._smart_approve", return_value="approve") as smart,
    ):
        check_execute_code_guard(SCRIPT, "local", has_host_access=True)
    assert SCRIPT in smart.call_args[0][0]


def test_long_script_is_truncated_for_the_reviewer(cron_env):
    long_script = "x = 1\n" * 20000
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="smart"),
        mock_patch("tools.approval._get_approval_mode", return_value="manual"),
        mock_patch("tools.approval._smart_approve", return_value="approve") as smart,
    ):
        check_execute_code_guard(long_script, "local", has_host_access=True)
    assert len(smart.call_args[0][0]) <= 8192


def test_deny_mode_still_blocks(cron_env):
    with (
        mock_patch("tools.approval._get_cron_approval_mode", return_value="deny"),
        mock_patch("tools.approval._get_approval_mode", return_value="manual"),
    ):
        result = check_execute_code_guard(SCRIPT, "local", has_host_access=True)
    assert not result["approved"]
