from __future__ import annotations

import importlib

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot


def _snapshot_for(pipeline_id: str = "engineering_review_pipeline"):
    decision = RouterDecision(
        pipeline_session_id="pipe-controller-1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id=pipeline_id,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.96,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-controller-1",
            user_message="Implement controller slice",
            created_at="2026-06-17T00:00:00+00:00",
        )
    )
    loaded = load_pipeline_specs()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )
    return session, snapshot


def test_default_config_returns_disabled() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_controller(
        config=None,
        session=session,
        state_snapshot=snapshot,
    )

    assert result.status == "disabled"
    assert result.execution_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"
    assert result.selected_pipeline_id == "engineering_review_pipeline"
    assert result.would_call == "bounded_rework_loop"
    assert result.actual_execution_invoked is False


def test_explicit_disabled_mode_returns_disabled() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_controller(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=session,
        state_snapshot=snapshot,
    )

    assert result.status == "disabled"
    assert result.execution_allowed is False
    assert result.actual_execution_invoked is False


def test_enabled_like_config_returns_would_execute_without_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config={"pipelines": {"execution": {"mode": "controlled_one_step"}}},
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
    )

    assert result.status == "would_execute"
    assert result.execution_allowed is False
    assert result.blocked_reason == "gateway_execution_not_enabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_test_only_injection_can_invoke_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config={
            "pipelines": {
                "execution": {
                    "mode": "controlled_one_step",
                    "enable_gateway_execution_controller": True,
                }
            }
        },
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "would_execute"
    assert result.execution_allowed is True
    assert result.blocked_reason is None
    assert result.actual_execution_invoked is True
    assert helper_calls == ["called"]
