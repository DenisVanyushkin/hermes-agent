"""Metadata-only final response and execution report contracts for pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from hermes_cli.pipeline_session import PipelineSession, PipelineStepPlan
from hermes_cli.pipeline_state_machine import PipelineStateSnapshot


class PipelineReportStatus(str, Enum):
    NOT_EXECUTED = "not_executed"
    BLOCKED = "blocked"
    COMPLETION_ALLOWED = "completion_allowed"
    COMPLETED = "completed"


@dataclass(frozen=True)
class PipelineReportSummary:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    router_status: str
    router_confidence: float
    execution_mode: str
    route_status: str
    selected_subagents: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    user_action_required: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "router_status": self.router_status,
            "router_confidence": self.router_confidence,
            "execution_mode": self.execution_mode,
            "route_status": self.route_status,
            "selected_subagents": list(self.selected_subagents),
            "blockers": list(self.blockers),
            "user_action_required": self.user_action_required,
        }


@dataclass(frozen=True)
class PipelineSubagentReport:
    step_kind: str
    subagent_id: str
    condition: str | None
    planning_mode: str
    execution_status: str
    runner_status: str
    structured_output_validation_status: str
    evaluation_status: str
    blockers: list[str] = field(default_factory=list)
    review_required: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "step_kind": self.step_kind,
            "subagent_id": self.subagent_id,
            "condition": self.condition,
            "planning_mode": self.planning_mode,
            "execution_status": self.execution_status,
            "runner_status": self.runner_status,
            "structured_output_validation_status": self.structured_output_validation_status,
            "evaluation_status": self.evaluation_status,
            "blockers": list(self.blockers),
            "review_required": self.review_required,
        }


@dataclass(frozen=True)
class PipelineModelReport:
    subagent_id: str
    role_id: str
    provider: str | None
    model: str | None
    model_class: str | None
    runtime_status: str
    execution_mode: str
    dry_run: bool
    candidate_model: dict[str, Any] | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "role_id": self.role_id,
            "provider": self.provider,
            "model": self.model,
            "model_class": self.model_class,
            "runtime_status": self.runtime_status,
            "execution_mode": self.execution_mode,
            "dry_run": self.dry_run,
            "candidate_model": dict(self.candidate_model) if self.candidate_model is not None else None,
        }


@dataclass(frozen=True)
class PipelineGateReport:
    preflight_allowed: bool | None
    preflight_reason_code: str | None
    evaluation_statuses: list[str] = field(default_factory=list)
    review_required: bool = False
    escalation_required: bool = False
    disagreement_present: bool = False
    control_statuses: list[str] = field(default_factory=list)
    loop_limit_statuses: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "preflight_allowed": self.preflight_allowed,
            "preflight_reason_code": self.preflight_reason_code,
            "evaluation_statuses": list(self.evaluation_statuses),
            "review_required": self.review_required,
            "escalation_required": self.escalation_required,
            "disagreement_present": self.disagreement_present,
            "control_statuses": list(self.control_statuses),
            "loop_limit_statuses": list(self.loop_limit_statuses),
        }


@dataclass(frozen=True)
class PipelineSafetyReport:
    executed: bool
    execution_enabled: bool
    policy_notes: list[str] = field(default_factory=list)
    secrets_redacted: bool = True
    prompts_redacted: bool = True
    environment_redacted: bool = True

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "execution_enabled": self.execution_enabled,
            "policy_notes": list(self.policy_notes),
            "secrets_redacted": self.secrets_redacted,
            "prompts_redacted": self.prompts_redacted,
            "environment_redacted": self.environment_redacted,
        }


@dataclass(frozen=True)
class PipelineUsageReport:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    cache_hit: bool | None = None
    cache_write: bool | None = None
    tool_calls: int = 0
    usage_known: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit": self.cache_hit,
            "cache_write": self.cache_write,
            "tool_calls": self.tool_calls,
            "usage_known": self.usage_known,
        }


@dataclass(frozen=True)
class PipelineCompletionReport:
    completion_allowed: bool
    candidate_complete: bool
    blocked_reason: str | None
    final_verdict: str
    review_required: bool
    escalation_required: bool
    disagreement_present: bool
    user_action_required: bool

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "completion_allowed": self.completion_allowed,
            "candidate_complete": self.candidate_complete,
            "blocked_reason": self.blocked_reason,
            "final_verdict": self.final_verdict,
            "review_required": self.review_required,
            "escalation_required": self.escalation_required,
            "disagreement_present": self.disagreement_present,
            "user_action_required": self.user_action_required,
        }


@dataclass(frozen=True)
class PipelineFinalResponse:
    status: PipelineReportStatus
    text: str | None
    placeholder_reason: str | None
    user_action_required: bool
    executed: bool

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "text": self.text,
            "placeholder_reason": self.placeholder_reason,
            "user_action_required": self.user_action_required,
            "executed": self.executed,
        }


@dataclass(frozen=True)
class PipelineExecutionReport:
    status: PipelineReportStatus
    executed: bool
    execution_mode: str
    summary: PipelineReportSummary
    subagents: list[PipelineSubagentReport]
    models: list[PipelineModelReport]
    gate: PipelineGateReport
    safety: PipelineSafetyReport
    usage: PipelineUsageReport
    completion: PipelineCompletionReport
    final_response: PipelineFinalResponse

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "executed": self.executed,
            "execution_mode": self.execution_mode,
            "summary": self.summary.to_safe_dict(),
            "subagents": [item.to_safe_dict() for item in self.subagents],
            "models": [item.to_safe_dict() for item in self.models],
            "gate": self.gate.to_safe_dict(),
            "safety": self.safety.to_safe_dict(),
            "usage": self.usage.to_safe_dict(),
            "completion": self.completion.to_safe_dict(),
            "final_response": self.final_response.to_safe_dict(),
        }


def build_pipeline_execution_report(
    *,
    session: PipelineSession,
    state_snapshot: PipelineStateSnapshot,
    preflight_result: Mapping[str, Any] | None = None,
    final_response_text: str | None = None,
) -> PipelineExecutionReport:
    _validate_required_metadata(session=session, state_snapshot=state_snapshot)

    subagents = [_build_subagent_report(step) for step in state_snapshot.planned_steps]
    models = [_build_model_report(step, plan) for step, plan in zip(state_snapshot.planned_steps, state_snapshot.runtime_factory_plans)]
    blockers = _collect_blockers(state_snapshot.planned_steps)
    review_required = any(item.review_required for item in subagents)
    escalation_required = any(_evaluation(step).get("escalation", {}).get("escalation_required", False) for step in state_snapshot.planned_steps)
    disagreement_present = any(_evaluation(step).get("disagreement", {}).get("disagreement_present", False) for step in state_snapshot.planned_steps)
    user_action_required = _user_action_required(state_snapshot.planned_steps)
    completion_allowed = bool(state_snapshot.executed and state_snapshot.completion_allowed)
    candidate_complete = any(_evaluation(step).get("completion", {}).get("candidate_complete", False) for step in state_snapshot.planned_steps)
    blocked_reason = (
        state_snapshot.completion_blocked_reason
        or _first_present(
            _evaluation(step).get("completion", {}).get("blocked_reason")
            for step in state_snapshot.planned_steps
        )
        or ("execution_disabled" if not state_snapshot.executed else None)
    )
    status = _report_status(
        executed=state_snapshot.executed,
        completion_allowed=completion_allowed,
        blocked_reason=blocked_reason,
    )
    policy_notes = _policy_notes(
        state_snapshot=state_snapshot,
        preflight_result=preflight_result,
        review_required=review_required,
        escalation_required=escalation_required,
        disagreement_present=disagreement_present,
    )

    usage = _build_usage_report(state_snapshot.planned_steps)
    final_response = PipelineFinalResponse(
        status=status,
        text=final_response_text if state_snapshot.executed else None,
        placeholder_reason=blocked_reason,
        user_action_required=user_action_required,
        executed=state_snapshot.executed,
    )
    completion = PipelineCompletionReport(
        completion_allowed=completion_allowed,
        candidate_complete=candidate_complete,
        blocked_reason=blocked_reason,
        final_verdict=state_snapshot.final_verdict,
        review_required=review_required,
        escalation_required=escalation_required,
        disagreement_present=disagreement_present,
        user_action_required=user_action_required,
    )
    summary = PipelineReportSummary(
        pipeline_session_id=session.pipeline_session_id,
        trace_id=session.trace_id,
        pipeline_id=session.pipeline_id,
        router_status=session.router_status,
        router_confidence=session.router_confidence,
        execution_mode=state_snapshot.execution_mode,
        route_status=state_snapshot.status,
        selected_subagents=list(state_snapshot.selected_subagent_ids),
        blockers=blockers,
        user_action_required=user_action_required,
    )
    gate = PipelineGateReport(
        preflight_allowed=_mapping_value(preflight_result, "allowed"),
        preflight_reason_code=_mapping_value(preflight_result, "reason_code"),
        evaluation_statuses=[item.evaluation_status for item in subagents],
        review_required=review_required,
        escalation_required=escalation_required,
        disagreement_present=disagreement_present,
        control_statuses=_control_statuses(state_snapshot.planned_steps),
        loop_limit_statuses=_loop_limit_statuses(state_snapshot.planned_steps),
    )
    safety = PipelineSafetyReport(
        executed=state_snapshot.executed,
        execution_enabled=bool(state_snapshot.executed),
        policy_notes=policy_notes,
    )
    return PipelineExecutionReport(
        status=status,
        executed=state_snapshot.executed,
        execution_mode=state_snapshot.execution_mode,
        summary=summary,
        subagents=subagents,
        models=models,
        gate=gate,
        safety=safety,
        usage=usage,
        completion=completion,
        final_response=final_response,
    )


def _validate_required_metadata(*, session: PipelineSession, state_snapshot: PipelineStateSnapshot) -> None:
    if not session.pipeline_session_id or not session.trace_id or not session.pipeline_id:
        raise ValueError("missing required pipeline session metadata")
    if not state_snapshot.pipeline_session_id or not state_snapshot.pipeline_id:
        raise ValueError("missing required pipeline state metadata")
    if session.pipeline_session_id != state_snapshot.pipeline_session_id:
        raise ValueError("pipeline session id mismatch")
    if session.pipeline_id != state_snapshot.pipeline_id:
        raise ValueError("pipeline id mismatch")


def _build_subagent_report(step: PipelineStepPlan) -> PipelineSubagentReport:
    runner_result = dict(step.runner_result or {})
    evaluation_result = dict(step.evaluation_result or {})
    structured_output = dict(runner_result.get("structured_output") or {})
    return PipelineSubagentReport(
        step_kind=step.step_kind,
        subagent_id=step.subagent_id,
        condition=step.condition,
        planning_mode=step.planning_mode,
        execution_status=step.execution_status,
        runner_status=str(runner_result.get("status") or "not_invoked"),
        structured_output_validation_status=str(
            structured_output.get("validation_status")
            or runner_result.get("schema_validation_status")
            or "not_applicable"
        ),
        evaluation_status=str(evaluation_result.get("status") or "not_evaluated"),
        blockers=list(evaluation_result.get("blockers") or []),
        review_required=bool(
            evaluation_result.get("requires_review")
            or (evaluation_result.get("review") or {}).get("review_required", False)
        ),
    )


def _build_model_report(step: PipelineStepPlan, runtime_plan: Mapping[str, Any]) -> PipelineModelReport:
    evaluation_result = dict(step.evaluation_result or {})
    escalation = dict(evaluation_result.get("model_escalation") or {})
    candidate_model = escalation.get("candidate_model")
    return PipelineModelReport(
        subagent_id=step.subagent_id,
        role_id=step.step_kind,
        provider=_mapping_value(runtime_plan, "provider"),
        model=_mapping_value(runtime_plan, "model"),
        model_class=_mapping_value(runtime_plan, "model_class"),
        runtime_status=str(_mapping_value(runtime_plan, "status") or "unknown"),
        execution_mode=str(_mapping_value(runtime_plan, "execution_mode") or "unknown"),
        dry_run=bool(_mapping_value(runtime_plan, "dry_run")),
        candidate_model=dict(candidate_model) if isinstance(candidate_model, Mapping) else None,
    )


def _build_usage_report(steps: list[PipelineStepPlan]) -> PipelineUsageReport:
    usage_known = False
    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    total_tokens = 0
    tool_calls = 0
    cache_hit: bool | None = None
    cache_write: bool | None = None
    for step in steps:
        runner_result = dict(step.runner_result or {})
        usage = dict(runner_result.get("usage_summary") or {})
        cache = dict(runner_result.get("cache_summary") or {})
        tool_summaries = list(runner_result.get("tool_call_summaries") or [])
        if any(usage.get(key) is not None for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")):
            usage_known = True
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            reasoning_tokens += int(usage.get("reasoning_tokens") or 0)
            total_tokens += int(usage.get("total_tokens") or 0)
        tool_calls += sum(int(item.get("call_count") or 0) for item in tool_summaries if isinstance(item, Mapping))
        if cache_hit is None and cache.get("cache_hit") is not None:
            cache_hit = bool(cache.get("cache_hit"))
        if cache_write is None and cache.get("cache_write") is not None:
            cache_write = bool(cache.get("cache_write"))
    return PipelineUsageReport(
        input_tokens=input_tokens if usage_known else None,
        output_tokens=output_tokens if usage_known else None,
        reasoning_tokens=reasoning_tokens if usage_known else None,
        total_tokens=total_tokens if usage_known else None,
        cache_hit=cache_hit,
        cache_write=cache_write,
        tool_calls=tool_calls,
        usage_known=usage_known,
    )


def _collect_blockers(steps: list[PipelineStepPlan]) -> list[str]:
    blockers: list[str] = []
    for step in steps:
        for blocker in list(_evaluation(step).get("blockers") or []):
            if blocker not in blockers:
                blockers.append(str(blocker))
    return blockers


def _policy_notes(
    *,
    state_snapshot: PipelineStateSnapshot,
    preflight_result: Mapping[str, Any] | None,
    review_required: bool,
    escalation_required: bool,
    disagreement_present: bool,
) -> list[str]:
    notes: list[str] = []
    if not state_snapshot.executed:
        notes.append("execution_disabled")
    if not _mapping_value(preflight_result, "allowed"):
        reason_code = _mapping_value(preflight_result, "reason_code")
        if reason_code:
            notes.append(str(reason_code))
    if review_required:
        notes.append("review_required")
    if escalation_required:
        notes.append("escalation_pending")
    if disagreement_present:
        notes.append("disagreement_pending")
    return notes


def _report_status(*, executed: bool, completion_allowed: bool, blocked_reason: str | None) -> PipelineReportStatus:
    if not executed:
        return PipelineReportStatus.NOT_EXECUTED
    if completion_allowed:
        return PipelineReportStatus.COMPLETION_ALLOWED
    if blocked_reason:
        return PipelineReportStatus.BLOCKED
    return PipelineReportStatus.COMPLETED


def _evaluation(step: PipelineStepPlan) -> dict[str, Any]:
    return dict(step.evaluation_result or {})


def _user_action_required(steps: list[PipelineStepPlan]) -> bool:
    for step in steps:
        evaluation = _evaluation(step)
        if (evaluation.get("model_escalation") or {}).get("user_approval_required", False):
            return True
        if (evaluation.get("disagreement_resolution") or {}).get("user_approval_required", False):
            return True
        control = dict(evaluation.get("control_channel") or {})
        for decision in list(control.get("decisions") or []):
            if decision.get("user_action_required", False):
                return True
            loop_limit = decision.get("loop_limit_decision") or {}
            if loop_limit.get("user_action_required", False):
                return True
    return False


def _control_statuses(steps: list[PipelineStepPlan]) -> list[str]:
    statuses: list[str] = []
    for step in steps:
        control = dict(_evaluation(step).get("control_channel") or {})
        for decision in list(control.get("decisions") or []):
            status = decision.get("status")
            if status and status not in statuses:
                statuses.append(str(status))
    return statuses


def _loop_limit_statuses(steps: list[PipelineStepPlan]) -> list[str]:
    statuses: list[str] = []
    for step in steps:
        control = dict(_evaluation(step).get("control_channel") or {})
        for decision in list(control.get("decisions") or []):
            loop_limit = decision.get("loop_limit_decision") or {}
            status = loop_limit.get("status")
            if status and status not in statuses:
                statuses.append(str(status))
    return statuses


def _first_present(values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _mapping_value(payload: Mapping[str, Any] | None, key: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    return payload.get(key)
