from __future__ import annotations

import importlib

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot


def _session_for(pipeline_id: str = "engineering_review_pipeline"):
    decision = RouterDecision(
        pipeline_session_id="pipe-fuse-1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id=pipeline_id,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.96,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-fuse-1",
            user_message="Implement one-step execution slice",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )


def _snapshot_for(pipeline_id: str = "engineering_review_pipeline"):
    loaded = load_pipeline_specs()
    session = _session_for(pipeline_id)
    return session, build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )


def _config(
    *,
    mode: str = "disabled",
    allow_actual_subagent_invocation: bool = False,
    allow_pipelines: list[str] | None = None,
    allowed_subagents: list[str] | None = None,
) -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": mode,
                "allow_pipelines": ["engineering_review_pipeline"] if allow_pipelines is None else allow_pipelines,
                "allowed_subagents": ["hermes_engineer_core"] if allowed_subagents is None else allowed_subagents,
                "allow_actual_subagent_invocation": allow_actual_subagent_invocation,
                "min_router_confidence": 0.90,
            }
        }
    }


def test_default_config_blocks_actual_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_fuse(
        config=None,
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.execution_mode == "disabled"
    assert result.blocked_reason == "execution_mode_disabled"
    assert result.execution_scope == "one_step_only"
    assert result.selected_pipeline_id == "engineering_review_pipeline"
    assert result.selected_subagent_id == "hermes_engineer_core"
    assert result.tools_allowed is False
    assert result.file_mutation_allowed is False
    assert result.reviewer_allowed is False
    assert result.loop_allowed is False
    assert result.model_escalation_allowed is False
    assert result.live_gateway_allowed is False


def test_disabled_mode_blocks_actual_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_fuse(
        config=_config(mode="disabled", allow_actual_subagent_invocation=True),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"


def test_missing_boolean_fuse_blocks_actual_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_fuse(
        config=_config(mode="one_step", allow_actual_subagent_invocation=False),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "actual_invocation_fuse_disabled"


def test_wrong_pipeline_blocks_actual_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for("default_conversation_pipeline")

    result = module.evaluate_pipeline_execution_fuse(
        config=_config(mode="one_step", allow_actual_subagent_invocation=True),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "unsupported_pipeline"
    assert result.selected_pipeline_id == "default_conversation_pipeline"


def test_wrong_subagent_blocks_actual_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_fuse(
        config=_config(
            mode="controlled_one_step",
            allow_actual_subagent_invocation=True,
            allowed_subagents=["hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "unsupported_subagent"
    assert result.selected_subagent_id == "hermes_engineer_core"


def test_one_step_mode_and_explicit_fuse_allow_only_first_engineer_step() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_fuse(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is True
    assert result.blocked_reason is None
    assert result.execution_mode == "controlled_one_step"
    assert result.selected_pipeline_id == "engineering_review_pipeline"
    assert result.selected_subagent_id == "hermes_engineer_core"
    assert result.selected_step_kind == "engineer"
    assert result.tools_allowed is False
    assert result.file_mutation_allowed is False
    assert result.reviewer_allowed is False
    assert result.loop_allowed is False
    assert result.model_escalation_allowed is False
    assert result.live_gateway_allowed is False
