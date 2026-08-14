"""Helper selection seam for executable pipeline controller paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from hermes_cli.pipeline_controlled_dry_run import ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID
from hermes_cli.pipeline_rework_loop import execute_bounded_rework_loop


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
BOUNDED_REWORK_LOOP_HELPER = "bounded_rework_loop"
RECRUITER_PIPELINE_ID = "recruiter_decision_support_pipeline"
RECRUITER_DECISION_HELPER = "recruiter_decision_support_flow"
RECRUITER_APPLICATION_HELPER = "recruiter_application_package_flow"


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
    user_message: str = "",
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

    if pipeline_id == RECRUITER_PIPELINE_ID:
        recruiter_helper_name, recruiter_helper = _resolve_recruiter_helper(user_message)
        return PipelineExecutionHelperResolution(
            status="resolved",
            helper_name=recruiter_helper_name,
            blocked_reason=None,
            helper=recruiter_helper,
        )

    return PipelineExecutionHelperResolution(
        status="not_wired",
        helper_name=helper_name,
        blocked_reason="unsupported_pipeline_helper",
        helper=None,
    )


def _resolve_recruiter_helper(user_message: str) -> tuple[str, Callable[..., Any]]:
    """Pick the recruiter helper by routing bundle (Task 4: bundle-aware dispatch).

    Application-materials prompts get the document-package flow; everything
    else (including no/ambiguous message) keeps the existing decision-support
    flow so recruiter pipeline evaluation prompts are unaffected.
    """
    from hermes_cli.recruiter_decision_execution import execute_recruiter_decision_support_helper
    from hermes_cli.recruiter_routing import APPLICATION_MATERIALS_BUNDLE_ID, route_recruiter_prompt

    decision = route_recruiter_prompt(user_message or "")
    if decision.selected_bundle == APPLICATION_MATERIALS_BUNDLE_ID:
        from hermes_cli.recruiter_application_package_execution import (
            execute_recruiter_application_package_helper,
        )

        return RECRUITER_APPLICATION_HELPER, execute_recruiter_application_package_helper

    return RECRUITER_DECISION_HELPER, execute_recruiter_decision_support_helper


def execute_engineering_review_helper(
    *,
    config: dict[str, Any] | None,
    session: Any,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: Any,
    user_message: str,
    repo_path: str | None = None,
    test_summary: Any = None,
    allow_completion_after_review: bool = False,
    controlled_runtime_context: Any = None,
    engineering_task_context: Any = None,
    **_kwargs: Any,
) -> Any:
    from hermes_cli.engineering_task_context import (
        is_engineering_execution_continuation,
        validate_engineering_task_context,
    )

    resolved_user_message = user_message
    resolved_operator_instruction: str | None = None
    if engineering_task_context is not None:
        envelope, task_context_error = validate_engineering_task_context(
            engineering_task_context
        )
        if task_context_error is not None or envelope is None:
            return _blocked_helper_payload(
                task_context_error or "engineering_task_context_invalid"
            )
        if envelope.source_session_id != str(getattr(session, "session_id", "") or ""):
            return _blocked_helper_payload("engineering_task_session_mismatch")
        if envelope.source_kind in {"approved_plan", "external_reference"}:
            resolved_user_message = envelope.task_text or ""
            resolved_operator_instruction = envelope.operator_instruction
    elif is_engineering_execution_continuation(user_message):
        return _blocked_helper_payload("engineering_task_context_missing")

    execution_mode = str((((config or {}).get('pipelines') or {}).get('execution') or {}).get('mode') or '').strip().lower()
    if execution_mode == "autonomous":
        blocked_reason = _controlled_manual_blocked_reason(controlled_runtime_context)
        if not isinstance(controlled_runtime_context, dict) or controlled_runtime_context.get("real_executor_ready") is not True:
            return _blocked_helper_payload(blocked_reason)
        if not _has_real_executor_path(controlled_runtime_context):
            return _blocked_helper_payload(blocked_reason)
    try:
        return execute_bounded_rework_loop(
            config=config,
            session=session,
            loaded_specs=loaded_specs,
            runtime_factory=runtime_factory,
            runner=runner,
            user_message=resolved_user_message,
            operator_instruction=resolved_operator_instruction,
            repo_path=repo_path,
            test_summary=test_summary,
            allow_completion_after_review=allow_completion_after_review,
            controlled_runtime_context=controlled_runtime_context,
        )
    except ValueError:
        if controlled_runtime_context is None:
            raise
        return {
            "status": "blocked",
            "blocked_reason": "invalid_controlled_runtime_context",
            "completion_allowed": False,
            "candidate_complete": False,
            "user_action_required": True,
            "subagent_runs": [],
            "usage_summary": {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "token_sources": [],
                "cache_sources": [],
                "planned_subagent_count": 0,
                "executed_subagent_count": 0,
                "subagent_run_instance_count": 0,
                "execution_round_count": 0,
                "subagent_count": 0,
                "models_used": [],
                "providers_used": [],
            },
        }


def _blocked_helper_payload(blocked_reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "blocked_reason": blocked_reason,
        "completion_allowed": False,
        "candidate_complete": False,
        "user_action_required": True,
        "subagent_runs": [],
        "usage_summary": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "token_sources": [],
            "cache_sources": [],
            "planned_subagent_count": 0,
            "executed_subagent_count": 0,
            "subagent_run_instance_count": 0,
            "execution_round_count": 0,
            "subagent_count": 0,
            "models_used": [],
            "providers_used": [],
        },
        "report": {
            "status": "not_executed",
            "routing": {
                "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
                "router_status": "selected",
            },
            "controller": {
                "executed": False,
                "execution_mode": "autonomous",
            },
            "completion": {
                "final_verdict": "blocked",
                "blocked_reason": blocked_reason,
            },
            "tests": {"status": "unavailable", "summary": None},
            "usage_summary": {"providers_used": [], "models_used": []},
            "review": {"reviewer_invoked": False},
            "changed_files": [],
        },
    }


def _controlled_manual_blocked_reason(controlled_runtime_context: Any) -> str:
    if isinstance(controlled_runtime_context, dict):
        value = str(controlled_runtime_context.get("blocked_reason") or "").strip()
        if value:
            return value
    return "real_subagent_executor_missing"


def _has_real_executor_path(controlled_runtime_context: dict[str, Any]) -> bool:
    executor_bridge = controlled_runtime_context.get("executor_bridge")
    if callable(executor_bridge):
        return True
    if isinstance(executor_bridge, dict):
        return all(callable(executor_bridge.get(subagent_id)) for subagent_id in (ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID))
    return (
        callable(controlled_runtime_context.get("invocation_client"))
        or callable(controlled_runtime_context.get("real_provider_client_factory"))
    )


def _helper_name_for_pipeline(pipeline_id: str | None) -> str | None:
    if pipeline_id == ENGINEERING_PIPELINE_ID:
        return BOUNDED_REWORK_LOOP_HELPER
    if pipeline_id == RECRUITER_PIPELINE_ID:
        return RECRUITER_DECISION_HELPER
    return None
