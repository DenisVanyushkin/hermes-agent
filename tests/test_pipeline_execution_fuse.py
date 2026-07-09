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
    allow_actual_reviewer_invocation: bool = False,
    allow_actual_rework_loop: bool = False,
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
                "allow_actual_reviewer_invocation": allow_actual_reviewer_invocation,
                "allow_actual_rework_loop": allow_actual_rework_loop,
                "min_router_confidence": 0.90,
            }
        }
    }


def test_default_config_blocks_rework_loop_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    loaded = load_pipeline_specs()
    session = _session_for()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )

    result = module.evaluate_pipeline_rework_loop_fuse(
        config=None,
        session=session,
        state_snapshot=snapshot,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"


def test_missing_explicit_loop_fuse_blocks_rework_loop() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    loaded = load_pipeline_specs()
    session = _session_for()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )

    result = module.evaluate_pipeline_rework_loop_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "rework_loop_fuse_disabled"


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
        config=_config(mode="autonomous", allow_actual_subagent_invocation=False),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "actual_invocation_fuse_disabled"


def test_wrong_pipeline_blocks_actual_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for("default_conversation_pipeline")

    result = module.evaluate_pipeline_execution_fuse(
        config=_config(mode="autonomous", allow_actual_subagent_invocation=True),
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
            mode="autonomous",
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
        config=_config(mode="autonomous", allow_actual_subagent_invocation=True),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is True
    assert result.blocked_reason is None
    assert result.execution_mode == "autonomous"
    assert result.selected_pipeline_id == "engineering_review_pipeline"
    assert result.selected_subagent_id == "hermes_engineer_core"
    assert result.selected_step_kind == "engineer"
    assert result.tools_allowed is False
    assert result.file_mutation_allowed is False
    assert result.reviewer_allowed is False
    assert result.loop_allowed is False
    assert result.model_escalation_allowed is False
    assert result.live_gateway_allowed is False


def test_default_config_blocks_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=None,
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"
    assert result.selected_step_kind == "reviewer"
    assert result.selected_subagent_id == "hermes_code_reviewer"


def test_missing_reviewer_boolean_fuse_blocks_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=False,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "reviewer_invocation_fuse_disabled"


def test_wrong_pipeline_blocks_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for("default_conversation_pipeline")

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "unsupported_pipeline"


def test_wrong_reviewer_subagent_blocks_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    reviewer_step = snapshot.planned_steps[1]
    snapshot = snapshot.__class__(
        **{
            **snapshot.__dict__,
            "planned_steps": [
                snapshot.planned_steps[0],
                reviewer_step.__class__(
                    step_kind="reviewer",
                    subagent_id="wrong_reviewer",
                    condition=reviewer_step.condition,
                    execution_status=reviewer_step.execution_status,
                    planning_mode=reviewer_step.planning_mode,
                    runtime_factory_plan=reviewer_step.runtime_factory_plan,
                    runner_request=reviewer_step.runner_request,
                    runner_result=reviewer_step.runner_result,
                    evaluation_result=reviewer_step.evaluation_result,
                ),
            ],
        }
    )

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "unsupported_subagent"


def test_reviewer_requires_existing_engineer_result() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "engineer_result_missing"


def test_invalid_engineer_result_blocks_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()
    engineer_step = snapshot.planned_steps[0]
    snapshot = snapshot.__class__(
        **{
            **snapshot.__dict__,
            "planned_steps": [
                engineer_step.__class__(
                    step_kind=engineer_step.step_kind,
                    subagent_id=engineer_step.subagent_id,
                    condition=engineer_step.condition,
                    execution_status="executed_one_step",
                    planning_mode="controlled_one_step",
                    runtime_factory_plan=engineer_step.runtime_factory_plan,
                    runner_request=engineer_step.runner_request,
                    runner_result={
                        "status": "succeeded",
                        "structured_output": {"validation_status": "invalid_structured_output"},
                    },
                    evaluation_result={"status": "invalid_structured_output"},
                ),
                snapshot.planned_steps[1],
            ],
        }
    )

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "engineer_result_invalid"


def test_invalid_engineer_result_with_material_changes_allows_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()
    engineer_step = snapshot.planned_steps[0]
    snapshot = snapshot.__class__(
        **{
            **snapshot.__dict__,
            "planned_steps": [
                engineer_step.__class__(
                    step_kind=engineer_step.step_kind,
                    subagent_id=engineer_step.subagent_id,
                    condition=engineer_step.condition,
                    execution_status="executed_one_step",
                    planning_mode="controlled_one_step",
                    runtime_factory_plan=engineer_step.runtime_factory_plan,
                    runner_request=engineer_step.runner_request,
                    runner_result={
                        "status": "succeeded",
                        "structured_output": {"validation_status": "invalid_structured_output"},
                    },
                    evaluation_result={"status": "invalid_structured_output"},
                ),
                snapshot.planned_steps[1],
            ],
        }
    )

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
        material_changes_present=True,
    )

    assert result.actual_invocation_allowed is True
    assert result.blocked_reason is None
    assert result.selected_subagent_id == "hermes_code_reviewer"


def test_failed_engineer_result_blocks_reviewer_invocation() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()
    engineer_step = snapshot.planned_steps[0]
    snapshot = snapshot.__class__(
        **{
            **snapshot.__dict__,
            "planned_steps": [
                engineer_step.__class__(
                    step_kind=engineer_step.step_kind,
                    subagent_id=engineer_step.subagent_id,
                    condition=engineer_step.condition,
                    execution_status="executed_one_step",
                    planning_mode="controlled_one_step",
                    runtime_factory_plan=engineer_step.runtime_factory_plan,
                    runner_request=engineer_step.runner_request,
                    runner_result={"status": "failed"},
                    evaluation_result={"status": "blocked"},
                ),
                snapshot.planned_steps[1],
            ],
        }
    )

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is False
    assert result.blocked_reason == "engineer_result_failed"


def test_valid_engineer_result_and_explicit_fuse_allow_reviewer_one_step() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_fuse")
    session, snapshot = _snapshot_for()
    engineer_step = snapshot.planned_steps[0]
    snapshot = snapshot.__class__(
        **{
            **snapshot.__dict__,
            "planned_steps": [
                engineer_step.__class__(
                    step_kind=engineer_step.step_kind,
                    subagent_id=engineer_step.subagent_id,
                    condition=engineer_step.condition,
                    execution_status="executed_one_step",
                    planning_mode="controlled_one_step",
                    runtime_factory_plan=engineer_step.runtime_factory_plan,
                    runner_request=engineer_step.runner_request,
                    runner_result={
                        "status": "succeeded",
                        "structured_output": {"validation_status": "valid"},
                    },
                    evaluation_result={"status": "candidate_complete"},
                ),
                snapshot.planned_steps[1],
            ],
        }
    )

    result = module.evaluate_pipeline_reviewer_execution_fuse(
        config=_config(
            mode="autonomous",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=session,
        state_snapshot=snapshot,
    )

    assert result.actual_invocation_allowed is True
    assert result.blocked_reason is None
    assert result.selected_step_kind == "reviewer"
    assert result.selected_subagent_id == "hermes_code_reviewer"


def _reviewer_prereq_snapshot(*, runner_status="succeeded", evaluation_status, validation_status="valid"):
    from types import SimpleNamespace

    step = SimpleNamespace(
        runner_result={"status": runner_status, "structured_output": {"validation_status": validation_status}},
        evaluation_result={"status": evaluation_status},
    )
    return SimpleNamespace(planned_steps=[step])


def test_reviewer_prereq_allows_blocked_engineer_when_material_changes_present():
    # A synthesized "blocked" envelope (valid schema) with real file changes must
    # reach the reviewer, mirroring _engineer_fail_closed_reason escape hatch.
    from hermes_cli.pipeline_execution_fuse import _reviewer_prereq_failure

    snap = _reviewer_prereq_snapshot(evaluation_status="blocked", validation_status="valid")
    assert _reviewer_prereq_failure(snap, material_changes_present=True) is None


def test_reviewer_prereq_blocks_blocked_engineer_without_material_changes():
    from hermes_cli.pipeline_execution_fuse import _reviewer_prereq_failure

    snap = _reviewer_prereq_snapshot(evaluation_status="blocked", validation_status="valid")
    assert _reviewer_prereq_failure(snap, material_changes_present=False) == "engineer_result_invalid"


def test_reviewer_prereq_still_blocks_failed_runner_even_with_material_changes():
    # runner failure is infra, not a format issue -- material changes must NOT rescue it.
    from hermes_cli.pipeline_execution_fuse import _reviewer_prereq_failure

    snap = _reviewer_prereq_snapshot(runner_status="failed", evaluation_status="not_evaluated")
    assert _reviewer_prereq_failure(snap, material_changes_present=True) == "engineer_result_failed"
