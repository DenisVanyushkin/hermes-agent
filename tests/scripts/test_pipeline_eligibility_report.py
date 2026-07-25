"""Aggregation of controlled-run reports."""
from scripts.pipeline_eligibility_report import summarize_runs


def _report(reason, pipeline, dirty=False, reviewer=False, head_changed=False):
    return {
        "pipeline_execution_report": {
            "gate": {"preflight_reason_code": reason},
            "completion": {"blocked_reason": "workspace_dirty_baseline" if dirty else None},
            "git_gate": {"head_changed": head_changed},
        },
        "execution": {"effective_pipeline_id": pipeline, "reviewer_invoked": reviewer},
    }


def test_counts_reason_codes():
    out = summarize_runs([
        _report("router_not_selected", "default_conversation_pipeline"),
        _report("router_not_selected", "default_conversation_pipeline"),
        _report(None, "engineering_review_pipeline", dirty=True),
    ])
    assert out["total"] == 3
    assert out["by_reason_code"]["router_not_selected"] == 2
    assert out["blocked_dirty"] == 1


def test_counts_reviewer_invocations():
    out = summarize_runs([
        _report(None, "engineering_review_pipeline", reviewer=True),
        _report(None, "engineering_review_pipeline", reviewer=False),
    ])
    assert out["reviewer_invoked"] == 1


def test_tolerates_missing_sections():
    out = summarize_runs([{}])
    assert out["total"] == 1
    assert out["by_reason_code"] == {"unknown": 1}


def test_counts_commits_that_skipped_review():
    """HEAD moving without a reviewer is the defect the whole plan is about.

    'commit landed' is git_gate.head_changed, not execution.commit_status --
    the latter only reports whether the gate was armed ('enabled'/'unavailable').
    """
    out = summarize_runs([
        _report(None, "engineering_review_pipeline", reviewer=False, head_changed=True),
        _report(None, "engineering_review_pipeline", reviewer=True, head_changed=True),
        _report(None, "engineering_review_pipeline", reviewer=False, head_changed=False),
    ])
    assert out["commits_without_review"] == 1
