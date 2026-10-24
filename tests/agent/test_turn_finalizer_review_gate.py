"""The review gate is wired into upstream's finalize_turn and build_turn_context.

The gate itself is tested in tests/hermes_cli (review_gate, review debt); these
tests prove the seams: the real finalize_turn hands the gate this turn's
messages and the dirty-path baseline taken at turn start, a blocking decision
replaces the answer and is persisted with the turn, and a broken gate never
breaks a turn.
"""

from types import SimpleNamespace

import pytest

import hermes_cli.review_gate as review_gate
from agent.local_finalizer import snapshot_dirty_baseline
from tests.agent.test_turn_context import _FakeAgent, _build, _stub_runtime_main  # noqa: F401 (autouse)
from tests.agent.test_turn_finalizer_engineering_footers import (  # noqa: F401 (autouse fixtures)
    _FooterAgent,
    _finalize,
    _no_plugin_hooks,
    _pristine_session_context,
)


def _decision(*, blocking):
    return SimpleNamespace(
        blocking=blocking,
        status="changes_requested" if blocking else "not_required",
        mode="enforce",
        reviewer_tier="code_review",
        reviewer_provider="openai-codex",
        reviewer_model="gpt-5.6-sol",
        changed_paths=["hermes_cli/x.py"],
        changed_path_count=1,
        packet={},
        packet_hash="abc123",
        automatic_review_invoked=True,
        automatic_review_verdict="changes_requested",
        reviewer_summary="summary",
        reviewer_findings=["finding"],
        required_changes=["change"],
        tests_required=["test"],
        approval_sensitive=False,
        user_override=False,
        review_error=None,
    )


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """Stub the decision, keep the real wiring and the real debt store (in tmp)."""
    calls = []
    cfg = {"blocking": False, "raise": False}

    def _evaluate(plan, messages, *, runtime_request=None, baseline_dirty_paths=None, state=None):
        calls.append({"messages": list(messages), "baseline": baseline_dirty_paths, "state": state})
        if cfg["raise"]:
            raise RuntimeError("reviewer exploded")
        return _decision(blocking=cfg["blocking"])

    monkeypatch.setattr(review_gate, "evaluate_review_gate", _evaluate)
    monkeypatch.setattr(review_gate, "render_review_gate_block_message", lambda d: "⛔ review gate blocked")
    monkeypatch.setattr(review_gate, "decision_to_dict", lambda d: {"status": d.status})
    monkeypatch.setattr(review_gate, "default_debt_root", lambda: tmp_path)
    monkeypatch.setattr(
        "hermes_cli.run_evidence.engineering_footers_suppressed", lambda *_a, **_kw: True,
    )
    return SimpleNamespace(calls=calls, cfg=cfg, debt_root=tmp_path)


def test_a_blocking_decision_replaces_the_answer_and_is_persisted(gate):
    gate.cfg["blocking"] = True
    agent = _FooterAgent(platform="telegram")
    agent._turn_dirty_baseline = ["already/dirty.py"]

    result = _finalize(agent, final_response="Всё починил, коммит готов")

    assert result["final_response"] == "⛔ review gate blocked"
    assert result["completed"] is False
    persisted = agent.persisted_messages
    assert persisted[-1]["content"] == "⛔ review gate blocked"
    assert persisted[-1]["_review_gate"]["status"] == "changes_requested"
    assert persisted[-1]["_review_gate"]["packet"] == {"status": "changes_requested"}
    # The gate saw this turn's tool calls and the baseline taken at turn start.
    (call,) = gate.calls
    assert call["baseline"] == ["already/dirty.py"]
    assert any(m.get("tool_calls") for m in call["messages"])


def test_a_passing_decision_leaves_the_turn_alone(gate):
    agent = _FooterAgent(platform="telegram")

    result = _finalize(agent, final_response="Добавил гречку")

    assert result["final_response"] == "Добавил гречку"
    assert result["completed"] is True
    assert "_review_gate" not in agent.persisted_messages[-1]
    assert len(gate.calls) == 1


def test_a_failing_gate_never_breaks_the_turn(gate):
    gate.cfg["raise"] = True
    agent = _FooterAgent(platform="telegram")

    result = _finalize(agent, final_response="Добавил гречку")

    assert result["final_response"] == "Добавил гречку"
    assert result["completed"] is True
    # The gate runs inside upstream's guarded persist step: an exception escaping
    # it would not fail the turn visibly, it would silently skip _persist_session.
    assert agent.persisted_messages is not None


def test_review_debt_is_loaded_for_the_gateway_session(gate):
    agent = _FooterAgent(platform="telegram")
    agent._gateway_session_key = "agent:main:telegram:dm:1"

    _finalize(agent, final_response="Добавил гречку")

    (call,) = gate.calls
    expected = review_gate.debt_key(gateway_session_key="agent:main:telegram:dm:1", session_id="sess-test")
    assert call["state"] is not None
    assert call["state"].task_key == expected


def test_an_empty_answer_is_not_reviewed(gate):
    _finalize(_FooterAgent(platform="telegram"), final_response="", exit_reason="empty_response")

    assert gate.calls == []


def test_build_turn_context_snapshots_the_dirty_baseline(monkeypatch):
    monkeypatch.setattr(review_gate, "snapshot_material_dirty_paths", lambda: ["wip/a.py"])
    agent = _FakeAgent()

    _build(agent)

    assert agent._turn_dirty_baseline == ["wip/a.py"]


def test_a_failing_snapshot_leaves_an_empty_baseline(monkeypatch):
    def _boom():
        raise OSError("not a git repo")

    monkeypatch.setattr(review_gate, "snapshot_material_dirty_paths", _boom)
    agent = SimpleNamespace()

    snapshot_dirty_baseline(agent)

    assert agent._turn_dirty_baseline == []
