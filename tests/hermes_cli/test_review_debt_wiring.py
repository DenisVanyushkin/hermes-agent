"""The debt has to reach the gate, or it is just bookkeeping.

evaluate_review_gate() decides `status` and `review_required` from the current
turn alone. These tests pin the two properties that make debt matter: while it
stands the gate cannot report not_required, and an automatic verdict updates it.
"""
from pathlib import Path

import pytest

from hermes_cli.profile_execution import build_role_execution_plan
from hermes_cli.review_gate import (
    ReviewGateState,
    evaluate_review_gate,
)

OBSERVE = {"review_gate": {"mode": "observe", "auto_review_in_observe": False}}


def _plan():
    return build_role_execution_plan("поправь tools/approval.py")


def test_quiet_turn_with_debt_cannot_report_not_required():
    """The 07:16 shape: nothing edited this turn, findings still outstanding."""
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])

    decision = evaluate_review_gate(
        _plan(), [], config=OBSERVE, changed_paths=[], state=state
    )

    assert decision.review_required is True
    assert decision.status != "not_required"


def test_quiet_turn_without_debt_is_unchanged():
    """No debt must mean exactly the old behaviour."""
    decision = evaluate_review_gate(
        _plan(), [], config=OBSERVE, changed_paths=[], state=ReviewGateState(task_key="s1")
    )
    assert decision.review_required is False
    assert decision.status == "not_required"


def test_omitting_the_state_keeps_the_previous_behaviour():
    """The parameter is additive; callers that do not pass it see no change."""
    decision = evaluate_review_gate(_plan(), [], config=OBSERVE, changed_paths=[])
    assert decision.review_required is False
    assert decision.status == "not_required"


def test_settled_debt_stops_forcing_review():
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.record_verdict("approved", changed_paths=["tools/approval.py"])

    decision = evaluate_review_gate(
        _plan(), [], config=OBSERVE, changed_paths=[], state=state
    )
    assert decision.review_required is False


def test_an_explicit_changes_requested_verdict_records_debt():
    state = ReviewGateState(task_key="s1")

    evaluate_review_gate(
        _plan(), [], config=OBSERVE,
        changed_paths=["tools/approval.py"], verdict="changes_requested", state=state,
    )

    assert state.outstanding_paths == ["tools/approval.py"]


def test_an_approving_verdict_settles_what_it_covers():
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])

    evaluate_review_gate(
        _plan(), [], config=OBSERVE,
        changed_paths=["tools/approval.py"], verdict="approved", state=state,
    )

    assert state.outstanding_paths == []
