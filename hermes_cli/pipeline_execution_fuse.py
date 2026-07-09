"""Fail-closed execution fuse for controlled one-step pipeline invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_state_machine import PipelineStateSnapshot


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
REVIEWER_SUBAGENT_ID = "hermes_code_reviewer"
ALLOWED_ONE_STEP_MODES = {"autonomous"}


@dataclass(frozen=True)
class PipelineExecutionFuseResult:
    execution_mode: str
    actual_invocation_allowed: bool
    blocked_reason: str | None
    selected_pipeline_id: str | None
    selected_step_kind: str | None
    selected_subagent_id: str | None
    execution_scope: str = "one_step_only"
    tools_allowed: bool = False
    file_mutation_allowed: bool = False
    reviewer_allowed: bool = False
    loop_allowed: bool = False
    model_escalation_allowed: bool = False
    live_gateway_allowed: bool = False
    max_review_iterations: int | None = None
    loop_policy_source: str | None = None
    requirements_met: list[str] = field(default_factory=list)
    requirements_failed: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "actual_invocation_allowed": self.actual_invocation_allowed,
            "blocked_reason": self.blocked_reason,
            "selected_pipeline_id": self.selected_pipeline_id,
            "selected_step_kind": self.selected_step_kind,
            "selected_subagent_id": self.selected_subagent_id,
            "execution_scope": self.execution_scope,
            "tools_allowed": self.tools_allowed,
            "file_mutation_allowed": self.file_mutation_allowed,
            "reviewer_allowed": self.reviewer_allowed,
            "loop_allowed": self.loop_allowed,
            "model_escalation_allowed": self.model_escalation_allowed,
            "live_gateway_allowed": self.live_gateway_allowed,
            "max_review_iterations": self.max_review_iterations,
            "loop_policy_source": self.loop_policy_source,
            "requirements_met": list(self.requirements_met),
            "requirements_failed": list(self.requirements_failed),
        }


def evaluate_pipeline_execution_fuse(
    *,
    config: Mapping[str, Any] | None,
    session: Any,
    state_snapshot: PipelineStateSnapshot,
) -> PipelineExecutionFuseResult:
    del session

    selected_step = state_snapshot.planned_steps[0] if state_snapshot.planned_steps else None
    pipeline_id = state_snapshot.pipeline_id
    step_kind = getattr(selected_step, "step_kind", None)
    subagent_id = getattr(selected_step, "subagent_id", None)
    requirements_met: list[str] = []
    requirements_failed: list[str] = []

    execution_mode = _execution_mode(config)
    if execution_mode not in ALLOWED_ONE_STEP_MODES:
        requirements_failed.append("allowed_execution_mode")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="execution_mode_disabled",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("allowed_execution_mode")

    if not bool(cfg_get(config, "pipelines", "execution", "allow_actual_subagent_invocation", default=False)):
        requirements_failed.append("allow_actual_subagent_invocation")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="actual_invocation_fuse_disabled",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("allow_actual_subagent_invocation")

    allowed_pipelines = _string_list(cfg_get(config, "pipelines", "execution", "allow_pipelines", default=[]))
    if pipeline_id != ENGINEERING_PIPELINE_ID or pipeline_id not in allowed_pipelines:
        requirements_failed.append("supported_pipeline_selected")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="unsupported_pipeline",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("supported_pipeline_selected")

    allowed_subagents = _string_list(
        cfg_get(config, "pipelines", "execution", "allowed_subagents", default=[ENGINEER_SUBAGENT_ID])
    )
    if subagent_id != ENGINEER_SUBAGENT_ID or subagent_id not in allowed_subagents or step_kind != "engineer":
        requirements_failed.append("supported_subagent_selected")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="unsupported_subagent",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("supported_subagent_selected")

    return PipelineExecutionFuseResult(
        execution_mode=execution_mode,
        actual_invocation_allowed=True,
        blocked_reason=None,
        selected_pipeline_id=pipeline_id,
        selected_step_kind=step_kind,
        selected_subagent_id=subagent_id,
        requirements_met=requirements_met,
        requirements_failed=requirements_failed,
    )


def evaluate_pipeline_reviewer_execution_fuse(
    *,
    config: Mapping[str, Any] | None,
    session: Any,
    state_snapshot: PipelineStateSnapshot,
    material_changes_present: bool = False,
) -> PipelineExecutionFuseResult:
    del session

    reviewer_step = _reviewer_step(state_snapshot)
    pipeline_id = state_snapshot.pipeline_id
    step_kind = getattr(reviewer_step, "step_kind", "reviewer")
    subagent_id = getattr(reviewer_step, "subagent_id", REVIEWER_SUBAGENT_ID)
    requirements_met: list[str] = []
    requirements_failed: list[str] = []

    execution_mode = _execution_mode(config)
    if execution_mode not in ALLOWED_ONE_STEP_MODES:
        requirements_failed.append("allowed_execution_mode")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="execution_mode_disabled",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("allowed_execution_mode")

    if not bool(cfg_get(config, "pipelines", "execution", "allow_actual_reviewer_invocation", default=False)):
        requirements_failed.append("allow_actual_reviewer_invocation")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="reviewer_invocation_fuse_disabled",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("allow_actual_reviewer_invocation")

    allowed_pipelines = _string_list(cfg_get(config, "pipelines", "execution", "allow_pipelines", default=[]))
    if pipeline_id != ENGINEERING_PIPELINE_ID or pipeline_id not in allowed_pipelines:
        requirements_failed.append("supported_pipeline_selected")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="unsupported_pipeline",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("supported_pipeline_selected")

    allowed_subagents = _string_list(cfg_get(config, "pipelines", "execution", "allowed_subagents", default=[]))
    if step_kind != "reviewer" or subagent_id != REVIEWER_SUBAGENT_ID or subagent_id not in allowed_subagents:
        requirements_failed.append("supported_subagent_selected")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason="unsupported_subagent",
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("supported_subagent_selected")

    engineer_gate_reason = _reviewer_prereq_failure(
        state_snapshot,
        material_changes_present=material_changes_present,
    )
    if engineer_gate_reason is not None:
        requirements_failed.append("valid_engineer_result_present")
        return _blocked(
            execution_mode=execution_mode,
            blocked_reason=engineer_gate_reason,
            pipeline_id=pipeline_id,
            step_kind=step_kind,
            subagent_id=subagent_id,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("valid_engineer_result_present")

    return PipelineExecutionFuseResult(
        execution_mode=execution_mode,
        actual_invocation_allowed=True,
        blocked_reason=None,
        selected_pipeline_id=pipeline_id,
        selected_step_kind=step_kind,
        selected_subagent_id=subagent_id,
        reviewer_allowed=True,
        requirements_met=requirements_met,
        requirements_failed=requirements_failed,
    )


def _blocked(
    *,
    execution_mode: str,
    blocked_reason: str,
    pipeline_id: str | None,
    step_kind: str | None,
    subagent_id: str | None,
    requirements_met: list[str],
    requirements_failed: list[str],
) -> PipelineExecutionFuseResult:
    return PipelineExecutionFuseResult(
        execution_mode=execution_mode,
        actual_invocation_allowed=False,
        blocked_reason=blocked_reason,
        selected_pipeline_id=pipeline_id,
        selected_step_kind=step_kind,
        selected_subagent_id=subagent_id,
        requirements_met=requirements_met,
        requirements_failed=requirements_failed,
    )


def _execution_mode(config: Mapping[str, Any] | None) -> str:
    raw = cfg_get(config, "pipelines", "execution", "mode", default="disabled")
    if not isinstance(raw, str):
        return "disabled"
    return raw.strip().lower() or "disabled"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _reviewer_step(state_snapshot: PipelineStateSnapshot) -> Any:
    if len(state_snapshot.planned_steps) > 1:
        return state_snapshot.planned_steps[1]
    return None


def _reviewer_prereq_failure(
    state_snapshot: PipelineStateSnapshot,
    *,
    material_changes_present: bool = False,
) -> str | None:
    if not state_snapshot.planned_steps:
        return "engineer_result_missing"

    engineer_step = state_snapshot.planned_steps[0]
    runner_result = getattr(engineer_step, "runner_result", None) or {}
    evaluation_result = getattr(engineer_step, "evaluation_result", None) or {}
    structured_output = runner_result.get("structured_output") or {}
    runner_status = str(runner_result.get("status") or "not_invoked")
    evaluation_status = str(evaluation_result.get("status") or "not_evaluated")
    validation_status = str(structured_output.get("validation_status") or "not_applicable")

    if runner_status == "not_invoked":
        return "engineer_result_missing"
    if runner_status != "succeeded":
        return "engineer_result_failed"
    if validation_status != "valid" or evaluation_status == "invalid_structured_output":
        if material_changes_present:
            return None
        return "engineer_result_invalid"
    if evaluation_status != "candidate_complete":
        if material_changes_present:
            return None
        return "engineer_result_invalid"
    return None
