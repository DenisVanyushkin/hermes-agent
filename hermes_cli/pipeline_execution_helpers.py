"""Helper selection seam for executable pipeline controller paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from hermes_cli.pipeline_rework_loop import execute_bounded_rework_loop


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
BOUNDED_REWORK_LOOP_HELPER = "bounded_rework_loop"


@dataclass(frozen=True)
class PipelineExecutionHelperResolution:
    status: str
    helper_name: str | None
    blocked_reason: str | None
    helper: Callable[..., Any] | None

    @property
    def resolved(self) -> bool:
        return self.helper is not None


def resolve_pipeline_execution_helper(
    *,
    pipeline_id: str | None,
    execution_helper: Callable[..., Any] | None = None,
    allow_registered_helper_selection: bool = False,
) -> PipelineExecutionHelperResolution:
    if execution_helper is not None:
        return PipelineExecutionHelperResolution(
            status="resolved",
            helper_name="injected_helper",
            blocked_reason=None,
            helper=execution_helper,
        )

    helper_name = _helper_name_for_pipeline(pipeline_id)
    if not allow_registered_helper_selection:
        return PipelineExecutionHelperResolution(
            status="not_wired",
            helper_name=helper_name,
            blocked_reason="live_execution_not_wired",
            helper=None,
        )

    if pipeline_id == ENGINEERING_PIPELINE_ID:
        return PipelineExecutionHelperResolution(
            status="resolved",
            helper_name=BOUNDED_REWORK_LOOP_HELPER,
            blocked_reason=None,
            helper=execute_engineering_review_helper,
        )

    return PipelineExecutionHelperResolution(
        status="not_wired",
        helper_name=helper_name,
        blocked_reason="unsupported_pipeline_helper",
        helper=None,
    )


def execute_engineering_review_helper(
    *,
    config: dict[str, Any] | None,
    session: Any,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: Any,
    user_message: str,
    **_kwargs: Any,
) -> Any:
    return execute_bounded_rework_loop(
        config=config,
        session=session,
        loaded_specs=loaded_specs,
        runtime_factory=runtime_factory,
        runner=runner,
        user_message=user_message,
    )


def _helper_name_for_pipeline(pipeline_id: str | None) -> str | None:
    if pipeline_id == ENGINEERING_PIPELINE_ID:
        return BOUNDED_REWORK_LOOP_HELPER
    return None
