"""approvals.cron_mode: smart on upstream's three cron gates (tools/approval_cron_smart.py).

Real entry points (check_all_command_guards, request_tool_approval,
check_execute_code_guard) and the real config read; only the reviewer is a
stand-in. smart allows on an explicit APPROVE and blocks on everything else;
deny never asks; approve never asks.
"""
import pytest

from tools.approval import check_all_command_guards, check_execute_code_guard, request_tool_approval

DANGEROUS = "rm -rf /tmp/stuff"


@pytest.fixture
def cron(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    for name in ("HERMES_INTERACTIVE", "HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK", "HERMES_YOLO_MODE"):
        monkeypatch.delenv(name, raising=False)

    def configure(cron_mode, verdict=None, *, raises=False):
        cfg = {"approvals": {"cron_mode": cron_mode, "mode": "manual"}}
        monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda *a, **k: cfg)
        asked = []

        def reviewer(subject, description):
            asked.append(subject)
            if raises:
                raise RuntimeError("reviewer down")
            return verdict
        monkeypatch.setattr("tools.approval_smart._smart_approve", reviewer)
        return asked
    return configure


GATES = {
    "terminal": lambda: check_all_command_guards(DANGEROUS, "local"),
    "flagged_action": lambda: request_tool_approval("dangerous_tool", "deletes a production table"),
    "execute_code": lambda: check_execute_code_guard("import os; os.system('rm -rf /tmp/x')", "local"),
}


@pytest.mark.parametrize("gate", GATES)
def test_smart_approve_allows_after_one_review(cron, gate):
    asked = cron("smart", "approve")
    assert GATES[gate]()["approved"] is True
    assert len(asked) == 1


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("verdict", ["deny", "escalate", "garbage", None])
def test_anything_but_approve_blocks(cron, gate, verdict):
    asked = cron("smart", verdict)
    result = GATES[gate]()
    assert result["approved"] is False
    assert result.get("status") not in ("approval_required", "pending_approval")
    assert result.get("approval_pending") is not True
    assert len(asked) == 1


@pytest.mark.parametrize("gate", GATES)
def test_a_failing_reviewer_blocks(cron, gate):
    cron("smart", raises=True)
    assert GATES[gate]()["approved"] is False


@pytest.mark.parametrize("gate", GATES)
def test_deny_never_asks_the_reviewer(cron, gate):
    asked = cron("deny", "approve")
    assert GATES[gate]()["approved"] is False
    assert asked == []


@pytest.mark.parametrize("gate", GATES)
def test_approve_never_asks_the_reviewer(cron, gate):
    asked = cron("approve", "deny")
    assert GATES[gate]()["approved"] is True
    assert asked == []


def test_hardline_blocks_never_reach_the_reviewer(cron):
    asked = cron("smart", "approve")
    assert check_all_command_guards("rm -rf /", "local")["approved"] is False
    assert asked == []


def test_smart_is_case_and_space_insensitive(cron):
    asked = cron("  SMART ", "approve")
    assert check_all_command_guards(DANGEROUS, "local")["approved"] is True
    assert len(asked) == 1


def test_upstreams_mode_getter_still_reads_smart_as_deny(cron):
    """Every upstream comparison stays closed: only the fork's hook can open a gate."""
    from tools.approval_context import _get_cron_approval_mode

    cron("smart", "approve")
    assert _get_cron_approval_mode() == "deny"


@pytest.mark.parametrize("gate", GATES)
def test_cron_smart_never_opens_a_single_query_session(cron, monkeypatch, gate):
    """-q has its own binary single_query_mode; cron's reviewer must not reach it."""
    asked = cron("smart", "approve")
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "1")
    assert GATES[gate]()["approved"] is False
    assert asked == []
