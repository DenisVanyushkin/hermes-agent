"""Disabled-by-default execution controller for gateway/orchestrator wiring."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from hermes_cli.config import cfg_get
from hermes_cli import pipeline_execution_helpers
from hermes_cli.pipeline_execution_fuse import (
    ENGINEER_SUBAGENT_ID,
    REVIEWER_SUBAGENT_ID,
    evaluate_pipeline_execution_fuse,
    evaluate_pipeline_reviewer_execution_fuse,
)
from hermes_cli.pipeline_rework_loop import evaluate_pipeline_rework_loop_fuse
from hermes_cli.pipeline_controlled_dry_run import CONTROLLED_MANUAL_MODE, CONTROLLED_VALIDATION_TRIGGER
from hermes_cli.pipeline_report_artifacts import sanitize_report_artifact_metadata
from hermes_cli.pipeline_specs import load_pipeline_specs

_AUTHORIZED_MANUAL_CHAT_PLATFORMS = {
    "discord",
    "feishu",
    "matrix",
    "signal",
    "slack",
    "sms",
    "telegram",
    "whatsapp",
}


@dataclass(frozen=True)
class PipelineExecutionControllerResult:
    status: str
    execution_allowed: bool
    blocked_reason: str | None
    selected_pipeline_id: str | None
    would_call: str | None
    actual_execution_invoked: bool
    execution_mode: str
    resolved_helper_name: str | None = None
    helper_result_status: str | None = None
    helper_result: dict[str, Any] | None = None
    helper_error: str | None = None
    final_response_text: str | None = None
    workspace_basename: str | None = None
    report_artifacts: dict[str, Any] | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "execution_allowed": self.execution_allowed,
            "blocked_reason": self.blocked_reason,
            "selected_pipeline_id": self.selected_pipeline_id,
            "would_call": self.would_call,
            "actual_execution_invoked": self.actual_execution_invoked,
            "execution_mode": self.execution_mode,
            "resolved_helper_name": self.resolved_helper_name,
            "helper_result_status": self.helper_result_status,
            "helper_result": dict(self.helper_result) if isinstance(self.helper_result, dict) else self.helper_result,
            "helper_error": self.helper_error,
            "final_response_text": _sanitize_final_response_text_for_safe_payload(
                self.final_response_text,
                workspace_basename=self.workspace_basename,
            ),
            "workspace_basename": self.workspace_basename,
            "report_artifacts": sanitize_report_artifact_metadata(self.report_artifacts),
        }


def evaluate_pipeline_execution_controller(
    *,
    config: Mapping[str, Any] | None,
    session: Any,
    state_snapshot: Any,
    execution_helper: Callable[..., Any] | None = None,
    allow_test_execution: bool = False,
    allow_registered_helper_selection: bool = False,
    helper_execution_context: Mapping[str, Any] | None = None,
) -> PipelineExecutionControllerResult:
    pipeline_id = getattr(state_snapshot, "pipeline_id", None)
    execution_mode = _execution_mode(config)
    would_call = _would_call_for_pipeline(pipeline_id)
    base = PipelineExecutionControllerResult(
        status="blocked",
        execution_allowed=False,
        blocked_reason=None,
        selected_pipeline_id=pipeline_id,
        would_call=would_call,
        actual_execution_invoked=False,
        execution_mode=execution_mode,
        resolved_helper_name=None,
    )

    if pipeline_id is None:
        return replace(base, blocked_reason="missing_pipeline_selection")

    if execution_mode == "disabled":
        return replace(base, status="disabled", blocked_reason="execution_mode_disabled")

    if not _actual_gateway_execution_enabled(config):
        return replace(base, status="would_execute", blocked_reason="gateway_execution_not_enabled")

    if execution_mode == CONTROLLED_MANUAL_MODE:
        if not _controlled_manual_execution_authorized(
            session=session,
            pipeline_id=pipeline_id,
            helper_execution_context=helper_execution_context,
        ):
            return replace(base, status="blocked", blocked_reason="controlled_manual_trigger_missing")
        allow_test_execution = True
        allow_registered_helper_selection = True

    if not allow_test_execution:
        return replace(base, status="not_wired", blocked_reason="live_execution_not_wired")

    loaded_specs = load_pipeline_specs()
    pipeline_spec = loaded_specs.pipeline_specs.get(pipeline_id)
    if not _eligible_pipeline_execution_context(
        session=session,
        state_snapshot=state_snapshot,
        pipeline_id=pipeline_id,
        pipeline_spec=pipeline_spec,
    ):
        return replace(base, blocked_reason=_context_block_reason(session=session, state_snapshot=state_snapshot, pipeline_id=pipeline_id))

    engineer_fuse = evaluate_pipeline_execution_fuse(
        config=config,
        session=session,
        state_snapshot=state_snapshot,
    )
    if not engineer_fuse.actual_invocation_allowed:
        return replace(base, blocked_reason=engineer_fuse.blocked_reason)

    reviewer_fuse = evaluate_pipeline_reviewer_execution_fuse(
        config=config,
        session=session,
        state_snapshot=_reviewer_ready_snapshot(state_snapshot),
    )
    if not reviewer_fuse.actual_invocation_allowed:
        return replace(base, blocked_reason=reviewer_fuse.blocked_reason)

    rework_fuse = evaluate_pipeline_rework_loop_fuse(
        config=dict(config or {}),
        session=session,
        state_snapshot=state_snapshot,
        pipeline_spec=pipeline_spec,
    )
    if not rework_fuse.actual_invocation_allowed:
        return replace(base, blocked_reason=rework_fuse.blocked_reason)

    if not _required_loop_subagents_allowed(config):
        return replace(base, blocked_reason="required_subagents_not_allowed")

    helper_resolution = pipeline_execution_helpers.resolve_pipeline_execution_helper(
        pipeline_id=pipeline_id,
        execution_helper=execution_helper,
        allow_registered_helper_selection=allow_registered_helper_selection,
    )
    if not helper_resolution.resolved:
        return replace(
            base,
            status=helper_resolution.status,
            blocked_reason=helper_resolution.blocked_reason,
            resolved_helper_name=helper_resolution.helper_name,
        )

    if helper_execution_context is None:
        helper_execution_context = {}

    if execution_helper is None and allow_registered_helper_selection and not _helper_execution_context_ready(helper_execution_context):
        return replace(
            base,
            status="not_wired",
            blocked_reason="helper_execution_context_missing",
            resolved_helper_name=helper_resolution.helper_name,
        )

    try:
        helper_result = helper_resolution.helper(
            config=config,
            session=session,
            state_snapshot=state_snapshot,
            loaded_specs=loaded_specs,
            pipeline_spec=pipeline_spec,
            **dict(helper_execution_context),
        )
    except Exception as exc:
        return replace(
            base,
            status="execution_failed",
            blocked_reason="controller_helper_failed",
            actual_execution_invoked=True,
            resolved_helper_name=helper_resolution.helper_name,
            helper_result_status="controller_helper_failed",
            helper_error=type(exc).__name__,
        )

    safe_helper_result = _safe_helper_result(helper_result)
    return replace(
        base,
        status=_helper_result_status(helper_result),
        execution_allowed=True,
        blocked_reason=None,
        actual_execution_invoked=True,
        resolved_helper_name=helper_resolution.helper_name,
        helper_result_status=_helper_result_status(helper_result),
        helper_result=safe_helper_result,
        final_response_text=_final_response_text(safe_helper_result, helper_execution_context),
        workspace_basename=_workspace_basename(helper_execution_context),
    )


def _execution_mode(config: Mapping[str, Any] | None) -> str:
    return str(cfg_get(config, "pipelines", "execution", "mode", default="disabled") or "disabled").strip().lower()


def _actual_gateway_execution_enabled(config: Mapping[str, Any] | None) -> bool:
    return bool(cfg_get(config, "pipelines", "execution", "enable_gateway_execution_controller", default=False))


def _would_call_for_pipeline(pipeline_id: str | None) -> str | None:
    if pipeline_id == "engineering_review_pipeline":
        return "bounded_rework_loop"
    return None


def _eligible_pipeline_execution_context(
    *,
    session: Any,
    state_snapshot: Any,
    pipeline_id: str | None,
    pipeline_spec: Mapping[str, Any] | None,
) -> bool:
    if pipeline_id is None or pipeline_spec is None or _would_call_for_pipeline(pipeline_id) is None:
        return False
    if getattr(session, "pipeline_id", None) != pipeline_id:
        return False
    if getattr(session, "pipeline_session_id", None) != getattr(state_snapshot, "pipeline_session_id", None):
        return False
    if getattr(session, "router_status", None) != "selected":
        return False
    planned_steps = list(getattr(state_snapshot, "planned_steps", []) or [])
    if len(planned_steps) < 2:
        return False
    if getattr(planned_steps[0], "step_kind", None) != "engineer" or getattr(planned_steps[0], "subagent_id", None) != ENGINEER_SUBAGENT_ID:
        return False
    if getattr(planned_steps[1], "step_kind", None) != "reviewer" or getattr(planned_steps[1], "subagent_id", None) != REVIEWER_SUBAGENT_ID:
        return False
    return True


def _context_block_reason(*, session: Any, state_snapshot: Any, pipeline_id: str | None) -> str:
    if pipeline_id is None:
        return "missing_pipeline_selection"
    if getattr(session, "pipeline_id", None) != pipeline_id:
        return "pipeline_session_mismatch"
    if getattr(session, "pipeline_session_id", None) != getattr(state_snapshot, "pipeline_session_id", None):
        return "pipeline_session_mismatch"
    return "ineligible_pipeline_execution_context"


def _required_loop_subagents_allowed(config: Mapping[str, Any] | None) -> bool:
    allowed = _string_list(cfg_get(config, "pipelines", "execution", "allowed_subagents", default=[]))
    return ENGINEER_SUBAGENT_ID in allowed and REVIEWER_SUBAGENT_ID in allowed


def _helper_execution_context_ready(helper_execution_context: Mapping[str, Any]) -> bool:
    return all(
        key in helper_execution_context and helper_execution_context[key] is not None
        for key in ("runtime_factory", "runner", "user_message")
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _reviewer_ready_snapshot(state_snapshot: Any) -> Any:
    planned_steps = list(getattr(state_snapshot, "planned_steps", []) or [])
    if not planned_steps:
        return state_snapshot
    engineer_step = planned_steps[0]
    planned_steps[0] = replace(
        engineer_step,
        runner_result={
            "status": "succeeded",
            "structured_output": {"validation_status": "valid"},
        },
        evaluation_result={"status": "candidate_complete", "completion": {"candidate_complete": True}},
    )
    return replace(state_snapshot, planned_steps=planned_steps)


def _helper_result_status(helper_result: Any) -> str:
    if isinstance(helper_result, Mapping):
        value = helper_result.get("status")
        if isinstance(value, str) and value.strip():
            return value
    return "executed"


def _safe_helper_result(helper_result: Any) -> dict[str, Any] | None:
    if hasattr(helper_result, "to_safe_dict"):
        safe = helper_result.to_safe_dict()
        return safe if isinstance(safe, dict) else {"value": safe}
    if isinstance(helper_result, Mapping):
        return dict(helper_result)
    return None


def _controlled_manual_trigger_present(helper_execution_context: Mapping[str, Any] | None) -> bool:
    if not isinstance(helper_execution_context, Mapping):
        return False
    user_message = str(helper_execution_context.get("user_message") or "")
    return CONTROLLED_VALIDATION_TRIGGER in user_message


def _controlled_manual_execution_authorized(
    *,
    session: Any,
    pipeline_id: str | None,
    helper_execution_context: Mapping[str, Any] | None,
) -> bool:
    if _controlled_manual_trigger_present(helper_execution_context):
        return True
    return _authorized_manual_chat_operator_context(
        session=session,
        pipeline_id=pipeline_id,
    )


def _authorized_manual_chat_operator_context(*, session: Any, pipeline_id: str | None) -> bool:
    if pipeline_id != "engineering_review_pipeline":
        return False
    if getattr(session, "router_status", None) != "selected":
        return False
    platform = str(getattr(session, "platform", "") or "").strip().lower()
    if not platform or platform == "cron":
        return False
    if platform not in _AUTHORIZED_MANUAL_CHAT_PLATFORMS:
        return False

    markers = (
        getattr(session, "chat_id", None),
        getattr(session, "thread_id", None),
        getattr(session, "user_id", None),
    )
    return any(str(marker or "").strip() for marker in markers)


def _workspace_basename(helper_execution_context: Mapping[str, Any] | None) -> str | None:
    if not isinstance(helper_execution_context, Mapping):
        return None
    repo_path = helper_execution_context.get("repo_path")
    if repo_path is None:
        return None
    from pathlib import Path

    return Path(str(repo_path)).name


def _final_response_text(helper_result: Any, helper_execution_context: Mapping[str, Any] | None) -> str | None:
    if not isinstance(helper_result, dict):
        return None
    from hermes_cli.pipeline_controlled_dry_run import format_controlled_manual_summary

    return format_controlled_manual_summary(helper_result, workspace_path=helper_execution_context.get("repo_path") if isinstance(helper_execution_context, Mapping) else None)


def _sanitize_final_response_text_for_safe_payload(
    final_response_text: str | None,
    *,
    workspace_basename: str | None,
) -> str | None:
    if not isinstance(final_response_text, str):
        return final_response_text

    redacted_lines: list[str] = []
    safe_workspace_suffix = workspace_basename or "workspace"
    for raw_line in final_response_text.splitlines():
        line = raw_line.strip()
        if not line:
            redacted_lines.append(raw_line)
            continue
        if line.startswith("report_path: "):
            redacted_lines.append("report_path: <redacted_absolute_path>")
            continue
        if line.startswith("workspace: "):
            redacted_lines.append(f"workspace: <redacted_absolute_path>/{safe_workspace_suffix}")
            continue
        redacted_lines.append(raw_line)
    return "\n".join(redacted_lines)
