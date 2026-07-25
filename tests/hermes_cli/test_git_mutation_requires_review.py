"""git_remote_mutation is gated on an approving reviewer verdict.

25 July: the 07:18 turn carried operation_category=git_remote_mutation and went
through as an ordinary chat turn. Commit 09ebaa2dd reached
origin/local/customizations while a changes_requested from 07:06 was still
outstanding. Nothing consulted the review gate at all.

The block message names the outstanding findings, not just "review failed" —
an operator who cannot see what is unresolved cannot resolve it.
"""
import pytest

from hermes_cli.review_gate import ReviewGateState, authorize_operation


def test_commit_blocked_while_review_debt_open():
    state = ReviewGateState(session="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    decision = authorize_operation(state, operation_category="git_remote_mutation")
    assert not decision.allowed
    assert "review" in decision.reason


def test_commit_allowed_after_approval():
    state = ReviewGateState(session="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.record_verdict("approved", changed_paths=["tools/approval.py"])
    decision = authorize_operation(state, operation_category="git_remote_mutation")
    assert decision.allowed


def test_read_only_operations_unaffected():
    state = ReviewGateState(session="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    decision = authorize_operation(state, operation_category="read_only")
    assert decision.allowed


def test_repo_mutation_is_gated_too():
    """A local commit strands the same unreviewed change; only the blast radius differs."""
    state = ReviewGateState(session="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    assert not authorize_operation(state, operation_category="repo_mutation").allowed


def test_the_block_names_the_outstanding_findings():
    state = ReviewGateState(session="s1")
    state.record_verdict(
        "changes_requested",
        changed_paths=["tools/approval.py", "cron/scheduler.py"],
        findings=["cron_mode=smart is undocumented", "no test for the deferred path"],
    )
    decision = authorize_operation(state, operation_category="git_remote_mutation")
    assert not decision.allowed
    assert "tools/approval.py" in decision.detail
    assert "cron_mode=smart is undocumented" in decision.detail


def test_a_clean_session_may_commit():
    state = ReviewGateState(session="s1")
    assert authorize_operation(state, operation_category="git_remote_mutation").allowed


def test_unknown_operation_categories_are_not_silently_gated():
    """Only the categories that actually move code are this gate's business."""
    state = ReviewGateState(session="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    assert authorize_operation(state, operation_category="image_generation").allowed
