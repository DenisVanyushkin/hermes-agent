"""Review debt: an unresolved changes_requested outlives the turn that earned it.

Today `review_required` is `material_change_detected`, which is derived from the
file-mutation tool calls of the *current* turn. Nothing remembers anything. That
is why 25 July went: 07:06 changes_requested with changed_paths=3, then 07:16 and
07:18 reporting `changed_paths_count=0, status=not_required` and the commit going
out — the rework turns edited nothing new, so the gate saw a clean turn and
concluded there was nothing to review. "Ревью подтверждено" was the model
attesting for itself.

Debt is per path: an approval only settles the paths it actually covers.
"""
import pytest

from hermes_cli.review_gate import ReviewGateState, evaluate_review_requirement


def test_pending_changes_requested_forces_re_review():
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    decision = evaluate_review_requirement(state, changed_paths_this_turn=[])
    assert decision.review_required
    assert decision.reason == "unresolved_changes_requested"


def test_approving_verdict_clears_the_debt():
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.record_verdict("approved", changed_paths=["tools/approval.py"])
    decision = evaluate_review_requirement(state, changed_paths_this_turn=[])
    assert not decision.review_required


def test_clean_session_requires_nothing():
    state = ReviewGateState(task_key="s1")
    decision = evaluate_review_requirement(state, changed_paths_this_turn=[])
    assert not decision.review_required


def test_approving_a_different_path_does_not_settle_the_debt():
    """Otherwise a reviewer approving anything at all discharges everything."""
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.record_verdict("approved", changed_paths=["docs/readme.md"])
    decision = evaluate_review_requirement(state, changed_paths_this_turn=[])
    assert decision.review_required
    assert decision.outstanding_paths == ["tools/approval.py"]


def test_partial_approval_leaves_the_rest_outstanding():
    state = ReviewGateState(task_key="s1")
    state.record_verdict(
        "changes_requested", changed_paths=["a.py", "b.py", "c.py"]
    )
    state.record_verdict("approved", changed_paths=["a.py", "c.py"])
    decision = evaluate_review_requirement(state, changed_paths_this_turn=[])
    assert decision.review_required
    assert decision.outstanding_paths == ["b.py"]


def test_new_edits_still_require_review_on_their_own():
    """Debt is an addition to the existing rule, not a replacement for it."""
    state = ReviewGateState(task_key="s1")
    decision = evaluate_review_requirement(
        state, changed_paths_this_turn=["hermes_cli/thing.py"]
    )
    assert decision.review_required
    assert decision.reason == "changed_paths_this_turn"


def test_an_operator_waiver_settles_the_debt():
    """`waived` is already a first-class verdict that unblocks the gate."""
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.record_verdict("waived", changed_paths=["tools/approval.py"])
    assert not evaluate_review_requirement(state, changed_paths_this_turn=[]).review_required


def test_blocked_verdict_is_debt_too():
    state = ReviewGateState(task_key="s1")
    state.record_verdict("blocked", changed_paths=["tools/approval.py"])
    assert evaluate_review_requirement(state, changed_paths_this_turn=[]).review_required


def test_debt_survives_a_round_trip_through_disk(tmp_path):
    """A turn is not a process: the gateway restarts, the debt must not evaporate."""
    state = ReviewGateState(task_key="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.save(tmp_path)

    restored = ReviewGateState.load("s1", tmp_path)
    decision = evaluate_review_requirement(restored, changed_paths_this_turn=[])
    assert decision.review_required
    assert decision.outstanding_paths == ["tools/approval.py"]


def test_loading_an_unknown_session_is_a_clean_slate(tmp_path):
    state = ReviewGateState.load("never-seen", tmp_path)
    assert not evaluate_review_requirement(state, changed_paths_this_turn=[]).review_required
