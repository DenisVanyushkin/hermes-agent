from __future__ import annotations

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session


def _decision(
    *,
    status: str,
    selected_pipeline_id: str | None,
    fallback_pipeline_id: str = "default_conversation_pipeline",
    confidence: float = 0.91,
) -> RouterDecision:
    return RouterDecision(
        pipeline_session_id="pipe-123",
        router_subagent_id="hermes_pipeline_router",
        status=status,
        selected_pipeline_id=selected_pipeline_id,
        fallback_pipeline_id=fallback_pipeline_id,
        confidence=confidence,
        reasoning_summary="route",
        fallback_safe=selected_pipeline_id is None,
    )


def test_create_pipeline_session_for_default_route():
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=_decision(status="no_specialized_pipeline", selected_pipeline_id=None),
            execution_mode="observe",
            platform="telegram",
            session_id="sess-default",
            session_key="agent:main:telegram:dm",
            chat_id="chat-1",
            user_id="user-1",
            user_message="hello",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )

    assert session.pipeline_id == "default_conversation_pipeline"
    assert session.router_status == "no_specialized_pipeline"
    assert session.router_confidence == 0.91
    assert session.current_state == "task_received"
    assert session.selected_subagent_ids == ["general_operator"]
    assert session.reviewer_condition is None


def test_create_pipeline_session_for_engineering_route():
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=_decision(status="selected", selected_pipeline_id="engineering_review_pipeline", confidence=0.97),
            execution_mode="observe",
            platform="slack",
            session_id="sess-eng",
            thread_id="thread-1",
            user_message="implement code",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )

    assert session.pipeline_id == "engineering_review_pipeline"
    assert session.router_status == "selected"
    assert session.current_state == "task_received"
    assert session.selected_subagent_ids == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert session.created_at == "2026-06-16T00:00:00+00:00"
    assert session.trace_id == "pipe-123"
