"""Fail-closed execution fuse for controlled one-step pipeline invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_state_machine import PipelineStateSnapshot


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
ALLOWED_ONE_STEP_MODES = {"one_step", "controlled_one_step"}


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
