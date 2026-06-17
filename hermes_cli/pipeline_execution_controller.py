"""Disabled-by-default execution controller for gateway/orchestrator wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from hermes_cli.config import cfg_get


@dataclass(frozen=True)
class PipelineExecutionControllerResult:
    status: str
    execution_allowed: bool
    blocked_reason: str | None
    selected_pipeline_id: str | None
    would_call: str | None
    actual_execution_invoked: bool
    execution_mode: str

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "execution_allowed": self.execution_allowed,
            "blocked_reason": self.blocked_reason,
            "selected_pipeline_id": self.selected_pipeline_id,
            "would_call": self.would_call,
            "actual_execution_invoked": self.actual_execution_invoked,
            "execution_mode": self.execution_mode,
        }


def evaluate_pipeline_execution_controller(
    *,
    config: Mapping[str, Any] | None,
    session: Any,
    state_snapshot: Any,
    execution_helper: Callable[..., Any] | None = None,
    allow_test_execution: bool = False,
) -> PipelineExecutionControllerResult:
    del session

    pipeline_id = getattr(state_snapshot, "pipeline_id", None)
    execution_mode = _execution_mode(config)
    would_call = _would_call_for_pipeline(pipeline_id)

    if pipeline_id is None:
        return PipelineExecutionControllerResult(
            status="not_wired",
            execution_allowed=False,
            blocked_reason="missing_pipeline_selection",
            selected_pipeline_id=None,
            would_call=None,
            actual_execution_invoked=False,
            execution_mode=execution_mode,
        )

    if execution_mode == "disabled":
        return PipelineExecutionControllerResult(
            status="disabled",
            execution_allowed=False,
            blocked_reason="execution_mode_disabled",
            selected_pipeline_id=pipeline_id,
            would_call=would_call,
            actual_execution_invoked=False,
            execution_mode=execution_mode,
        )

    if not _actual_gateway_execution_enabled(config):
        return PipelineExecutionControllerResult(
            status="would_execute",
            execution_allowed=False,
            blocked_reason="gateway_execution_not_enabled",
            selected_pipeline_id=pipeline_id,
            would_call=would_call,
            actual_execution_invoked=False,
            execution_mode=execution_mode,
        )

    if execution_helper is None or not allow_test_execution:
        return PipelineExecutionControllerResult(
            status="not_wired",
            execution_allowed=False,
            blocked_reason="live_execution_not_wired",
            selected_pipeline_id=pipeline_id,
            would_call=would_call,
            actual_execution_invoked=False,
            execution_mode=execution_mode,
        )

    execution_helper(
        config=config,
        state_snapshot=state_snapshot,
    )
    return PipelineExecutionControllerResult(
        status="would_execute",
        execution_allowed=True,
        blocked_reason=None,
        selected_pipeline_id=pipeline_id,
        would_call=would_call,
        actual_execution_invoked=True,
        execution_mode=execution_mode,
    )


def _execution_mode(config: Mapping[str, Any] | None) -> str:
    return str(cfg_get(config, "pipelines", "execution", "mode", default="disabled") or "disabled").strip().lower()


def _actual_gateway_execution_enabled(config: Mapping[str, Any] | None) -> bool:
    return bool(cfg_get(config, "pipelines", "execution", "enable_gateway_execution_controller", default=False))


def _would_call_for_pipeline(pipeline_id: str | None) -> str | None:
    if pipeline_id == "engineering_review_pipeline":
        return "bounded_rework_loop"
    return None
