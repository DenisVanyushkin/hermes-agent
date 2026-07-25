"""Headless approval must resolve deterministically — never phantom-pending.

Incident 2026-07-12 (weather cron): a cron agent's terminal calls landed in
the gateway approval branch (stale platform contextvar on a recycled pool
thread + HERMES_CRON_SESSION not visible), found no notify callback, and got
``status: pending_approval`` — a dead end, because the ``_pending`` queue it
was filed into had no consumer at all. The agent thrashed for ~30 minutes and
failed the job. (That queue has since been deleted; the contract below is
asserted on the status the model actually receives.)

New contract: when the gateway branch has no notify callback registered,
resolve immediately:
- cron sessions (session key ``cron_*`` or HERMES_CRON_SESSION) follow
  ``approvals.cron_mode`` (approve → run, deny → definitive block);
- all other headless sessions get a definitive deny that tells the model
  approval is impossible here, instead of "asking the user".

This holds for every gate that can reach the notify-callback branch:
``check_all_command_guards`` (shell patterns), ``check_execute_code_guard``
(execute_code) and the shared ``_run_approval_gate`` core behind
``request_tool_approval`` (plugin ``pre_tool_call`` escalations) — the last of
which kept the dead ``submit_pending`` + ``status: approval_required`` pattern
until this suite grew its third gate.
"""

import pytest

import tools.approval as approval_module
from gateway.session_context import set_session_vars, clear_session_vars
from tools.approval import (
    check_all_command_guards,
    check_execute_code_guard,
    request_tool_approval,
    reset_current_session_key,
    set_current_session_key,
)

DANGEROUS_CMD = 'rm -rf /tmp/* 2>/dev/null; echo "Cleared /tmp"'
CRON_SESSION_KEY = "cron_482794186f87_20260712_040001"
INTERACTIVE_SESSION_KEY = "agent:main:telegram:dm:79564752:99554"


@pytest.fixture(autouse=True)
def _headless_gateway_context(monkeypatch):
    """Gateway-ish context with NO notify callback and NO cron env flag."""
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    # Simulate the stale interactive platform contextvar observed in the
    # incident: it makes _is_gateway_approval_context() return True.
    tokens = set_session_vars(platform="telegram", chat_id="x", chat_name="dm")
    approval_module._permanent_approved.clear()
    approval_module.clear_session(CRON_SESSION_KEY)
    approval_module.clear_session(INTERACTIVE_SESSION_KEY)
    yield
    clear_session_vars(tokens)
    approval_module._permanent_approved.clear()
    approval_module.clear_session(CRON_SESSION_KEY)
    approval_module.clear_session(INTERACTIVE_SESSION_KEY)


@pytest.fixture
def _cron_session():
    token = set_current_session_key(CRON_SESSION_KEY)
    yield
    reset_current_session_key(token)


@pytest.fixture
def _interactive_session():
    token = set_current_session_key(INTERACTIVE_SESSION_KEY)
    yield
    reset_current_session_key(token)


def test_cron_session_no_notifier_follows_cron_mode_approve(monkeypatch, _cron_session):
    monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "approve")
    result = check_all_command_guards(DANGEROUS_CMD, "docker", has_host_access=True)
    assert result["approved"] is True
    assert result.get("status") != "pending_approval"


def test_cron_session_no_notifier_follows_cron_mode_deny(monkeypatch, _cron_session):
    monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "deny")
    result = check_all_command_guards(DANGEROUS_CMD, "docker", has_host_access=True)
    assert result["approved"] is False
    assert result.get("approval_pending") is not True
    assert result.get("status") != "pending_approval"
    # The model must learn it cannot wait this out
    assert "cron" in result["message"].lower()


def test_non_cron_headless_no_notifier_denies_definitively(_interactive_session):
    result = check_all_command_guards(DANGEROUS_CMD, "docker", has_host_access=True)
    assert result["approved"] is False
    assert result.get("approval_pending") is not True
    assert result.get("status") != "pending_approval"
    msg = result["message"].lower()
    assert "no" in msg and "approv" in msg
    assert "do not retry" in msg


def test_execute_code_cron_session_follows_cron_mode_approve(monkeypatch, _cron_session):
    monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "approve")
    result = check_execute_code_guard("import os", "host")
    assert result["approved"] is True
    assert result.get("status") != "pending_approval"


def test_execute_code_non_cron_headless_denies_definitively(_interactive_session):
    result = check_execute_code_guard("import os", "host")
    assert result["approved"] is False
    assert result.get("approval_pending") is not True
    assert result.get("status") != "pending_approval"
    assert "do not retry" in result["message"].lower()


# ------------------------------------------------------------------
# Third gate: the shared _run_approval_gate core (plugin escalations)
# ------------------------------------------------------------------


def test_plugin_gate_cron_session_no_notifier_follows_cron_mode_approve(
    monkeypatch, _cron_session
):
    monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "approve")
    result = request_tool_approval(
        "browser_navigate", "navigates to an external URL", rule_key="ext-nav"
    )
    assert result["approved"] is True
    assert result.get("status") != "approval_required"


def test_plugin_gate_cron_session_no_notifier_follows_cron_mode_deny(
    monkeypatch, _cron_session
):
    monkeypatch.setattr(approval_module, "_get_cron_approval_mode", lambda: "deny")
    result = request_tool_approval(
        "browser_navigate", "navigates to an external URL", rule_key="ext-nav"
    )
    assert result["approved"] is False
    assert result.get("status") != "approval_required"
    assert result.get("approval_pending") is not True
    # The model must learn it cannot wait this out
    assert "cron" in result["message"].lower()


def test_plugin_gate_non_cron_headless_denies_definitively(_interactive_session):
    result = request_tool_approval(
        "browser_navigate", "navigates to an external URL", rule_key="ext-nav"
    )
    assert result["approved"] is False
    assert result.get("status") != "approval_required"
    assert result.get("approval_pending") is not True
    msg = result["message"].lower()
    assert "do not retry" in msg
    # Never claim a request was sent: nothing can deliver or answer it here.
    assert "asking the user for approval" not in msg


def test_plugin_gate_headless_deny_never_defers(_interactive_session):
    """The gate must resolve here, never park the request for a later answer.

    This used to assert the legacy ``_pending`` queue stayed empty. That queue
    had no consumer and has been deleted, so the guard is expressed against the
    only thing the model can observe: the returned status. A regression that
    re-introduces a parking lot has to answer with one of the deferral statuses
    below — which is precisely what the agent read as "wait" on 2026-07-12.
    """
    result = request_tool_approval(
        "browser_navigate", "external URL", rule_key="ext-nav"
    )

    assert result["approved"] is False
    assert result["status"] == "denied_no_approver"
    assert result["status"] not in ("approval_required", "pending_approval")
    assert result.get("approval_pending") is not True


def test_plugin_gate_headless_deny_carries_pattern_key(_interactive_session):
    """Callers key persistence off pattern_key, so the deny must still carry it."""
    result = request_tool_approval(
        "browser_navigate", "external URL", rule_key="ext-nav"
    )
    assert result["pattern_key"] == "plugin_rule:ext-nav"
