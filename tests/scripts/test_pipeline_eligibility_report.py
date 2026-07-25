"""Aggregation of controlled-run reports."""
from scripts.pipeline_eligibility_report import summarize_runs


def _report(reason, pipeline, dirty=False, reviewer=False, head_changed=False,
            router_status=None):
    return {
        "pipeline_execution_report": {
            "gate": {"preflight_reason_code": reason},
            "completion": {"blocked_reason": "workspace_dirty_baseline" if dirty else None},
            "git_gate": {"head_changed": head_changed},
        },
        "execution": {"effective_pipeline_id": pipeline, "reviewer_invoked": reviewer},
        "routing": {"router_status": router_status},
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


def test_router_not_selected_is_split_by_what_the_router_actually_did():
    """Counting reason codes alone reads a working router as a broken one.

    ``router_not_selected`` is the gate's code for "no pipeline was selected",
    which covers both the router deliberately declining an ordinary chat turn and
    a routing failure. Lumping them together made a correct decline look like a
    loss -- it is what produced a "56 % of runs lost to routing" reading when 52
    of those 54 runs were the router correctly refusing to send avatar requests
    and read-only questions into the engineering pipeline.
    """
    out = summarize_runs([
        _report("router_not_selected", "default_conversation_pipeline",
                router_status="no_specialized_pipeline"),
        _report("router_not_selected", "default_conversation_pipeline",
                router_status="no_specialized_pipeline"),
        _report("router_not_selected", "default_conversation_pipeline",
                router_status="needs_clarification"),
        _report(None, "engineering_review_pipeline", router_status="selected"),
    ])
    assert out["by_router_status"] == {
        "no_specialized_pipeline": 2, "needs_clarification": 1, "selected": 1,
    }
    # Declining is the router working, not the pipeline being lost.
    assert out["router_declined"] == 2
    assert out["router_needs_clarification"] == 1


def test_missing_routing_section_is_unknown_not_declined():
    out = summarize_runs([{}])
    assert out["by_router_status"] == {"unknown": 1}
    assert out["router_declined"] == 0
