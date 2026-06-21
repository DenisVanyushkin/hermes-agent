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
    assert [plan["subagent_id"] for plan in snapshot.runtime_factory_plans] == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]
    assert snapshot.runtime_factory_plans[0]["status"] == "plan_only"
    assert snapshot.runtime_factory_plans[0]["provider"] == "openrouter"
    assert snapshot.runtime_factory_plans[0]["model"] == "xiaomi/mimo-v2.5-pro"
    assert snapshot.runtime_factory_plans[0]["dry_run"] is True
    assert snapshot.planned_steps[0].runtime_factory_plan["subagent_id"] == "hermes_engineer_core"
    assert snapshot.planned_steps[0].runner_request["status"] == "plan_only"
    assert snapshot.planned_steps[0].runner_request["actual_provider"] is None
    assert snapshot.planned_steps[0].runner_result["status"] == "not_invoked"
    assert snapshot.planned_steps[0].runner_result["structured_output"] is None
    assert snapshot.planned_steps[0].evaluation_result["status"] == "not_evaluated"
    assert snapshot.planned_steps[0].evaluation_result["failure_reason"] == "runner_not_invoked"
    assert snapshot.planned_steps[0].evaluation_result["control_channel"]["decisions"] == []
    assert snapshot.planned_steps[0].evaluation_result["model_escalation"]["blocked_reason"] == "runner_not_invoked"
    assert snapshot.planned_steps[0].evaluation_result["completion"]["completion_allowed"] is False
    assert snapshot.planned_steps[1].runner_request["subagent_id"] == "hermes_code_reviewer"
    assert snapshot.planned_steps[1].runner_result["failure_reason"] == "observe_mode_plan_only"
    assert snapshot.planned_steps[1].evaluation_result["status"] == "not_evaluated"
    assert snapshot.executed is False


def test_autonomous_pipeline_reuses_planned_state_contract():
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
        execution_mode="autonomous",
    )
    assert snapshot.status == "planned"
    assert snapshot.completion_reason == "plan_only"
    assert snapshot.execution_mode == "autonomous"
    assert [step.subagent_id for step in snapshot.planned_steps] == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]


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
    assert snapshot.runtime_factory_plans[0]["subagent_id"] == "general_operator"
    assert snapshot.runtime_factory_plans[0]["status"] == "plan_only"
    assert snapshot.planned_steps[0].runtime_factory_plan is None
    assert snapshot.planned_steps[0].runner_request is None
    assert snapshot.planned_steps[0].runner_result is None
    assert snapshot.planned_steps[0].evaluation_result is None
    assert snapshot.reviewer_condition is None
    assert snapshot.loop_policy["policy_source"] == "pipeline_spec"
    assert snapshot.executed is False


def test_state_machine_output_excludes_legacy_runtime_bridge_fields():
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")

    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    payload = asdict(snapshot)
    runtime_payload_text = str(payload["runtime_factory_plans"])
    assert payload["runtime_factory_plans"][0]["provider"] == "openrouter"
    assert payload["runtime_factory_plans"][0]["model"] == "xiaomi/mimo-v2.5-pro"
    assert "selected_provider" not in runtime_payload_text
    assert "selected_model" not in runtime_payload_text
    assert "constructor_provider" not in runtime_payload_text
    assert "constructor_model" not in runtime_payload_text
    assert "runtime_bridge_allowed" not in runtime_payload_text
    assert "runtime_bridge_enabled" not in runtime_payload_text

    assert payload["planned_steps"][0]["runner_request"]["actual_provider"] is None
    assert payload["planned_steps"][0]["runner_request"]["actual_model"] is None
    assert payload["planned_steps"][0]["runner_result"]["actual_provider"] is None
    assert payload["planned_steps"][0]["runner_result"]["actual_model"] is None
    assert payload["planned_steps"][0]["evaluation_result"]["status"] == "not_evaluated"
    assert payload["loop_policy"]["max_review_iterations"] == 3


def test_state_machine_observe_payload_keeps_runner_metadata_nested_under_steps_only():
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")

    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    payload = asdict(snapshot)

    assert "runner_request" not in payload
    assert "runner_result" not in payload
    assert "evaluation_result" not in payload
    assert payload["planned_steps"][0]["runner_request"]["status"] == "plan_only"
    assert payload["planned_steps"][0]["runner_result"]["status"] == "not_invoked"
    assert payload["planned_steps"][0]["evaluation_result"]["failure_reason"] == "runner_not_invoked"
    assert payload["planned_steps"][0]["evaluation_result"]["control_channel"]["policy"]["policy_source"] == "pipeline_spec"
