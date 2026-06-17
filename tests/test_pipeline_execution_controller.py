from __future__ import annotations

import importlib

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot


def _snapshot_for(
    pipeline_id: str = "engineering_review_pipeline",
    *,
    router_status: str = "selected",
):
    decision = RouterDecision(
        pipeline_session_id="pipe-controller-1",
        router_subagent_id="hermes_pipeline_router",
        status=router_status,
        selected_pipeline_id=pipeline_id if router_status == "selected" else None,
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


def _config(
    *,
    mode: str = "controlled_one_step",
    controller_enabled: bool = True,
    allow_actual_subagent_invocation: bool = True,
    allow_actual_reviewer_invocation: bool = True,
    allow_actual_rework_loop: bool = True,
    allow_pipelines: list[str] | None = None,
    allowed_subagents: list[str] | None = None,
) -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": mode,
                "enable_gateway_execution_controller": controller_enabled,
                "allow_actual_subagent_invocation": allow_actual_subagent_invocation,
                "allow_actual_reviewer_invocation": allow_actual_reviewer_invocation,
                "allow_actual_rework_loop": allow_actual_rework_loop,
                "allow_pipelines": ["engineering_review_pipeline"] if allow_pipelines is None else allow_pipelines,
                "allowed_subagents": (
                    ["hermes_engineer_core", "hermes_code_reviewer"]
                    if allowed_subagents is None
                    else allowed_subagents
                ),
            },
        }
    }


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


def test_controller_disabled_does_not_call_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(controller_enabled=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "would_execute"
    assert result.execution_allowed is False
    assert result.blocked_reason == "gateway_execution_not_enabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_execution_mode_disabled_does_not_call_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="disabled"),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "disabled"
    assert result.execution_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_enabled_like_config_without_helper_is_not_wired() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=None,
        allow_test_execution=True,
    )

    assert result.status == "not_wired"
    assert result.execution_allowed is False
    assert result.blocked_reason == "live_execution_not_wired"
    assert result.actual_execution_invoked is False


def test_engineer_fuse_failure_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_subagent_invocation=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "actual_invocation_fuse_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_reviewer_fuse_failure_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_reviewer_invocation=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "reviewer_invocation_fuse_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_rework_loop_fuse_failure_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_rework_loop=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "rework_loop_fuse_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_allowed_subagents_gate_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allowed_subagents=["hermes_engineer_core"]),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "unsupported_subagent"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_all_fuses_pass_calls_helper_exactly_once() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**kwargs):
        helper_calls.append(kwargs["session"].pipeline_session_id)
        return {"status": "executed", "execution_report": {"status": "completed"}}

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "executed"
    assert result.execution_allowed is True
    assert result.blocked_reason is None
    assert result.actual_execution_invoked is True
    assert helper_calls == ["pipe-controller-1"]
    assert result.helper_result == {"status": "executed", "execution_report": {"status": "completed"}}
    assert result.helper_result_status == "executed"


def test_helper_exception_is_fail_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")
        raise RuntimeError("helper exploded")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "execution_failed"
    assert result.execution_allowed is False
    assert result.blocked_reason == "controller_helper_failed"
    assert result.actual_execution_invoked is True
    assert result.helper_result is None
    assert result.helper_result_status == "controller_helper_failed"
    assert result.helper_error == "RuntimeError"
    assert helper_calls == ["called"]


def test_missing_pipeline_context_is_fail_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []
    snapshot = type(
        "AnonymousSnapshot",
        (),
        {"pipeline_id": None, "pipeline_session_id": snapshot.pipeline_session_id, "planned_steps": []},
    )()

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "missing_pipeline_selection"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_ineligible_pipeline_context_is_fail_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for(pipeline_id="default_conversation_pipeline", router_status="no_specialized_pipeline")
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(
            allow_pipelines=["engineering_review_pipeline", "default_conversation_pipeline"],
            allowed_subagents=["general_operator"],
        ),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "ineligible_pipeline_execution_context"
    assert result.actual_execution_invoked is False
    assert helper_calls == []
