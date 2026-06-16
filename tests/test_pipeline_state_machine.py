from __future__ import annotations

from dataclasses import asdict

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot


def _session_for(pipeline_id: str | None, *, status: str) -> object:
    decision = RouterDecision(
        pipeline_session_id="pipe-234",
        router_subagent_id="hermes_pipeline_router",
        status=status,
        selected_pipeline_id=pipeline_id,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.95,
        reasoning_summary="route",
        fallback_safe=pipeline_id is None,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-1",
            user_message="test",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )


def test_state_machine_engineering_plan_includes_engineer_and_reviewer_steps():
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")

    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    assert snapshot.status == "planned"
    assert snapshot.state == "preflight_blocked_execution"
    assert snapshot.transition_path == [
        "task_received",
        "engineering_review_pipeline_selected",
        "engineer_step_planned",
        "reviewer_step_planned_if_code_changes",
        "preflight_blocked_execution",
    ]
    assert [step.step_kind for step in snapshot.planned_steps] == ["engineer", "reviewer"]
    assert snapshot.reviewer_condition == "code_changes_require_review"
    assert snapshot.executed is False


def test_state_machine_default_route_plans_response_and_blocks_execution():
    loaded = load_pipeline_specs()
    session = _session_for(None, status="no_specialized_pipeline")

    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["default_conversation_pipeline"],
    )

    assert snapshot.state == "preflight_blocked_execution"
    assert snapshot.transition_path == [
        "task_received",
        "default_pipeline_selected",
        "response_planned",
        "preflight_blocked_execution",
    ]
    assert snapshot.reviewer_condition is None
    assert snapshot.executed is False


def test_state_machine_output_excludes_provider_model_client_runtime_fields():
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")

    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    payload_text = str(asdict(snapshot))
    assert "actual_provider" not in payload_text
    assert "actual_model" not in payload_text
    assert "selected_provider" not in payload_text
    assert "selected_model" not in payload_text
    assert "client" not in payload_text
    assert "runtime" not in payload_text
