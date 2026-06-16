"""Pure pipeline structured-output evaluation contracts for metadata-only planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from hermes_cli.pipeline_control_channel import (
    ControlledMessageChannelPlan,
    ControlledSubagentMessage,
    LoopCounterSnapshot,
    build_controlled_message_decision,
    resolve_loop_limit_policy,
)
from hermes_cli.pipeline_escalation import (
    DisagreementDecision,
    DisagreementResolutionPlan,
    EscalationContext,
    ModelEscalationDecision,
    ModelEscalationPlan,
    plan_disagreement_resolution,
    plan_model_escalation,
)
from hermes_cli.subagent_runner import (
    StructuredOutputEnvelope,
    SubagentRunnerResult,
    SubagentRunnerStatus,
)


MIN_CONFIDENCE_FOR_CANDIDATE_COMPLETE = 0.75


class PipelineEvaluationStatus(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    NEEDS_ESCALATION = "needs_escalation"
    CANDIDATE_COMPLETE = "candidate_complete"


@dataclass(frozen=True)
class PipelineGateDecision:
    blockers_present: bool = False
    blocked: bool = False
    next_action: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "blockers_present": self.blockers_present,
            "blocked": self.blocked,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class PipelineReviewDecision:
    review_required: bool = False
    review_planned: bool = False
    reason: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "review_required": self.review_required,
            "review_planned": self.review_planned,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PipelineEscalationDecision:
    escalation_required: bool = False
    escalation_planned: bool = False
    reason: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "escalation_required": self.escalation_required,
            "escalation_planned": self.escalation_planned,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PipelineDisagreementDecision:
    disagreement_present: bool = False
    resolution_planned: bool = False
    reason: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "disagreement_present": self.disagreement_present,
            "resolution_planned": self.resolution_planned,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PipelineCompletionDecision:
    completion_allowed: bool = False
    candidate_complete: bool = False
    blocked_reason: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "completion_allowed": self.completion_allowed,
            "candidate_complete": self.candidate_complete,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class PipelineEvaluationRequest:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_id: str
    execution_mode: str
    runner_result: SubagentRunnerResult
    structured_output: StructuredOutputEnvelope | None = None
    pipeline_spec: Mapping[str, Any] = field(default_factory=dict)
    runtime_factory_plan: Mapping[str, Any] = field(default_factory=dict)
    subagent_spec: Mapping[str, Any] = field(default_factory=dict)
    all_subagent_specs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineEvaluationResult:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_id: str
    status: PipelineEvaluationStatus
    blockers: list[str] = field(default_factory=list)
    requires_review: bool = False
    next_action: str | None = None
    completion: PipelineCompletionDecision = field(default_factory=PipelineCompletionDecision)
    review: PipelineReviewDecision = field(default_factory=PipelineReviewDecision)
    escalation: PipelineEscalationDecision = field(default_factory=PipelineEscalationDecision)
    model_escalation: ModelEscalationPlan | None = None
    disagreement_resolution: DisagreementResolutionPlan | None = None
    disagreement: PipelineDisagreementDecision = field(default_factory=PipelineDisagreementDecision)
    control_channel: ControlledMessageChannelPlan | None = None
    failure_reason: str | None = None
    validation_errors: list[dict[str, str]] = field(default_factory=list)
    decision_path: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        blocked_statuses = {
            PipelineEvaluationStatus.BLOCKED,
            PipelineEvaluationStatus.INVALID_STRUCTURED_OUTPUT,
            PipelineEvaluationStatus.NOT_EVALUATED,
            PipelineEvaluationStatus.NEEDS_ESCALATION,
            PipelineEvaluationStatus.NEEDS_REVIEW,
        }
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "subagent_id": self.subagent_id,
            "status": self.status.value,
            "blockers": list(self.blockers),
            "requires_review": self.requires_review,
            "next_action": self.next_action,
            "completion": self.completion.to_safe_dict(),
            "gate": PipelineGateDecision(
                blockers_present=bool(self.blockers),
                blocked=self.status in blocked_statuses,
                next_action=self.next_action,
            ).to_safe_dict(),
            "review": self.review.to_safe_dict(),
            "escalation": self.escalation.to_safe_dict(),
            "model_escalation": self.model_escalation.to_safe_dict() if self.model_escalation else None,
            "disagreement_resolution": (
                self.disagreement_resolution.to_safe_dict()
                if self.disagreement_resolution
                else None
            ),
            "disagreement": self.disagreement.to_safe_dict(),
            "control_channel": self.control_channel.to_safe_dict() if self.control_channel else None,
            "failure_reason": self.failure_reason,
            "validation_errors": list(self.validation_errors),
            "decision_path": list(self.decision_path),
        }


def evaluate_pipeline_step(request: PipelineEvaluationRequest) -> PipelineEvaluationResult:
    runner_status = request.runner_result.status
    if runner_status in {SubagentRunnerStatus.NOT_INVOKED, SubagentRunnerStatus.PLAN_ONLY}:
        return _result(
            request,
            status=PipelineEvaluationStatus.NOT_EVALUATED,
            failure_reason="runner_not_invoked",
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=False,
                blocked_reason="runner_not_invoked",
            ),
            control_channel=_control_channel_plan(request, outcome="runner_not_invoked"),
            model_escalation=_blocked_runner_model_escalation(request),
            decision_path=["runner_not_invoked"],
        )

    envelope = request.structured_output or request.runner_result.structured_output
    if envelope is None:
        return _result(
            request,
            status=PipelineEvaluationStatus.BLOCKED,
            failure_reason="missing_structured_output",
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=False,
                blocked_reason="missing_structured_output",
            ),
            decision_path=["missing_structured_output"],
        )

    if envelope.validation_status != "valid":
        return _result(
            request,
            status=PipelineEvaluationStatus.INVALID_STRUCTURED_OUTPUT,
            failure_reason="invalid_structured_output",
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=False,
                blocked_reason="invalid_structured_output",
            ),
            control_channel=_control_channel_plan(request, outcome="invalid_structured_output"),
            escalation=_legacy_escalation("invalid_structured_output"),
            model_escalation=_model_escalation(
                request,
                trigger_reason="invalid_structured_output",
                source="evaluation",
            ),
            validation_errors=list(envelope.validation_errors),
            decision_path=["invalid_structured_output"],
        )

    if envelope.blockers:
        disagreement_resolution, disagreement = _explicit_disagreement_metadata(
            request,
            envelope=envelope,
            fallback_summary="Explicit disagreement signal accompanies blockers.",
        )
        return _result(
            request,
            status=PipelineEvaluationStatus.BLOCKED,
            blockers=list(envelope.blockers),
            requires_review=bool(envelope.requires_review),
            next_action=envelope.next_action,
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=False,
                blocked_reason="blockers_present",
            ),
            review=_review_decision(envelope, "blockers_present"),
            control_channel=_control_channel_plan(request, envelope=envelope, outcome="blockers_present"),
            disagreement_resolution=disagreement_resolution,
            disagreement=disagreement,
            decision_path=_decision_path(
                "valid_structured_output",
                "blockers_present",
                disagreement=disagreement,
            ),
        )

    if envelope.requires_review or envelope.status == "needs_review":
        disagreement_resolution, disagreement = _explicit_disagreement_metadata(
            request,
            envelope=envelope,
            fallback_summary="Explicit disagreement signal accompanies review metadata.",
        )
        return _result(
            request,
            status=PipelineEvaluationStatus.NEEDS_REVIEW,
            requires_review=True,
            next_action=envelope.next_action,
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=False,
                blocked_reason="review_required",
            ),
            review=_review_decision(envelope, "review_required"),
            control_channel=_control_channel_plan(request, envelope=envelope, outcome="review_required"),
            disagreement_resolution=disagreement_resolution,
            disagreement=disagreement,
            decision_path=_decision_path(
                "valid_structured_output",
                "review_required",
                disagreement=disagreement,
            ),
        )

    confidence = envelope.confidence if envelope.confidence is not None else 0.0
    if confidence < MIN_CONFIDENCE_FOR_CANDIDATE_COMPLETE:
        model_escalation = _model_escalation(
            request,
            trigger_reason="low_confidence",
            source="evaluation",
        )
        return _result(
            request,
            status=PipelineEvaluationStatus.NEEDS_ESCALATION,
            next_action=envelope.next_action,
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=False,
                blocked_reason="low_confidence",
            ),
            escalation=PipelineEscalationDecision(
                escalation_required=True,
                escalation_planned=bool(model_escalation and model_escalation.allowed),
                reason="low_confidence",
            ),
            model_escalation=model_escalation,
            control_channel=_control_channel_plan(request, envelope=envelope, outcome="low_confidence"),
            decision_path=["valid_structured_output", "low_confidence"],
        )

    if envelope.status == "succeeded":
        return _result(
            request,
            status=PipelineEvaluationStatus.CANDIDATE_COMPLETE,
            next_action=envelope.next_action,
            completion=PipelineCompletionDecision(
                completion_allowed=False,
                candidate_complete=True,
                blocked_reason="execution_disabled",
            ),
            control_channel=_control_channel_plan(request, envelope=envelope, outcome="candidate_complete"),
            decision_path=["valid_structured_output", "candidate_complete"],
        )

    return _result(
        request,
        status=PipelineEvaluationStatus.BLOCKED,
        next_action=envelope.next_action,
        completion=PipelineCompletionDecision(
            completion_allowed=False,
            candidate_complete=False,
            blocked_reason="unsupported_envelope_status",
        ),
        control_channel=_control_channel_plan(request, envelope=envelope, outcome="unsupported_envelope_status"),
        failure_reason="unsupported_envelope_status",
        decision_path=["valid_structured_output", "unsupported_envelope_status"],
    )


def _review_decision(envelope: StructuredOutputEnvelope, reason: str) -> PipelineReviewDecision:
    required = bool(envelope.requires_review or envelope.status == "needs_review")
    return PipelineReviewDecision(
        review_required=required,
        review_planned=required,
        reason=reason if required else None,
    )


def _control_channel_plan(
    request: PipelineEvaluationRequest,
    *,
    envelope: StructuredOutputEnvelope | None = None,
    outcome: str,
) -> ControlledMessageChannelPlan | None:
    if request.pipeline_id != "engineering_review_pipeline":
        return None

    policy = resolve_loop_limit_policy(request.pipeline_spec)
    counters = LoopCounterSnapshot()
    decisions = []
    runner_invoked = request.runner_result.status not in {
        SubagentRunnerStatus.NOT_INVOKED,
        SubagentRunnerStatus.PLAN_ONLY,
    }
    evaluation_id = f"{request.pipeline_session_id}:{request.step_id}:{request.subagent_id}"

    if outcome in {"review_required", "blockers_present"} and request.subagent_id == "hermes_engineer_core":
        decisions.append(
            build_controlled_message_decision(
                message=ControlledSubagentMessage(
                    pipeline_session_id=request.pipeline_session_id,
                    trace_id=request.trace_id,
                    pipeline_id=request.pipeline_id,
                    source_subagent_id="hermes_engineer_core",
                    target_subagent_id="hermes_code_reviewer",
                    message_purpose="review_feedback",
                    payload_summary=(envelope.summary if envelope and envelope.summary else "Structured output requires reviewer inspection."),
                    related_step_id=request.step_id,
                    evaluation_id=evaluation_id,
                ),
                policy=policy,
                counters=counters,
                limit_name="max_review_iterations",
                runner_invoked=runner_invoked,
            )
        )

    if outcome == "blockers_present" and request.subagent_id == "hermes_code_reviewer":
        decisions.append(
            build_controlled_message_decision(
                message=ControlledSubagentMessage(
                    pipeline_session_id=request.pipeline_session_id,
                    trace_id=request.trace_id,
                    pipeline_id=request.pipeline_id,
                    source_subagent_id="hermes_code_reviewer",
                    target_subagent_id="hermes_engineer_core",
                    message_purpose="rework_request",
                    payload_summary=", ".join(envelope.blockers) if envelope and envelope.blockers else "Reviewer reported blockers.",
                    related_step_id=request.step_id,
                    evaluation_id=evaluation_id,
                ),
                policy=policy,
                counters=counters,
                limit_name="max_review_iterations",
                runner_invoked=runner_invoked,
            )
        )

    if outcome == "invalid_structured_output":
        decisions.append(
            build_controlled_message_decision(
                message=ControlledSubagentMessage(
                    pipeline_session_id=request.pipeline_session_id,
                    trace_id=request.trace_id,
                    pipeline_id=request.pipeline_id,
                    source_subagent_id=request.subagent_id,
                    target_subagent_id=request.subagent_id,
                    message_purpose="clarification",
                    payload_summary="Retry structured output validation within policy.",
                    related_step_id=request.step_id,
                    evaluation_id=evaluation_id,
                ),
                policy=policy,
                counters=counters,
                limit_name="max_invalid_output_retries",
                runner_invoked=runner_invoked,
            )
        )

    if outcome == "low_confidence":
        decisions.append(
            build_controlled_message_decision(
                message=ControlledSubagentMessage(
                    pipeline_session_id=request.pipeline_session_id,
                    trace_id=request.trace_id,
                    pipeline_id=request.pipeline_id,
                    source_subagent_id=request.subagent_id,
                    target_subagent_id=request.subagent_id,
                    message_purpose="clarification",
                    payload_summary="Low-confidence result may require policy-bound model escalation metadata.",
                    related_step_id=request.step_id,
                    evaluation_id=evaluation_id,
                ),
                policy=policy,
                counters=counters,
                limit_name="max_model_escalations",
                runner_invoked=runner_invoked,
            )
        )

    return ControlledMessageChannelPlan(
        pipeline_session_id=request.pipeline_session_id,
        trace_id=request.trace_id,
        pipeline_id=request.pipeline_id,
        policy=policy,
        counters=counters,
        decisions=decisions,
    )


def _result(
    request: PipelineEvaluationRequest,
    *,
    status: PipelineEvaluationStatus,
    blockers: list[str] | None = None,
    requires_review: bool = False,
    next_action: str | None = None,
    completion: PipelineCompletionDecision | None = None,
    review: PipelineReviewDecision | None = None,
    escalation: PipelineEscalationDecision | None = None,
    model_escalation: ModelEscalationPlan | None = None,
    disagreement_resolution: DisagreementResolutionPlan | None = None,
    disagreement: PipelineDisagreementDecision | None = None,
    control_channel: ControlledMessageChannelPlan | None = None,
    failure_reason: str | None = None,
    validation_errors: list[dict[str, str]] | None = None,
    decision_path: list[str] | None = None,
) -> PipelineEvaluationResult:
    return PipelineEvaluationResult(
        pipeline_session_id=request.pipeline_session_id,
        trace_id=request.trace_id,
        pipeline_id=request.pipeline_id,
        step_id=request.step_id,
        subagent_id=request.subagent_id,
        status=status,
        blockers=list(blockers or []),
        requires_review=requires_review,
        next_action=next_action,
        completion=completion or PipelineCompletionDecision(),
        review=review or PipelineReviewDecision(),
        escalation=escalation or PipelineEscalationDecision(),
        model_escalation=model_escalation,
        disagreement_resolution=disagreement_resolution,
        disagreement=disagreement or PipelineDisagreementDecision(),
        control_channel=control_channel,
        failure_reason=failure_reason,
        validation_errors=list(validation_errors or []),
        decision_path=list(decision_path or []),
    )


def _escalation_context(request: PipelineEvaluationRequest) -> EscalationContext:
    return EscalationContext(
        pipeline_session_id=request.pipeline_session_id,
        trace_id=request.trace_id,
        pipeline_id=request.pipeline_id,
        step_id=request.step_id,
        subagent_id=request.subagent_id,
        pipeline_spec=request.pipeline_spec,
        current_runtime=request.runtime_factory_plan,
        subagent_spec=request.subagent_spec,
        all_subagent_specs=request.all_subagent_specs,
    )


def _legacy_escalation(reason: str) -> PipelineEscalationDecision:
    return PipelineEscalationDecision(
        escalation_required=True,
        escalation_planned=True,
        reason=reason,
    )


def _blocked_runner_model_escalation(request: PipelineEvaluationRequest) -> ModelEscalationPlan | None:
    if request.pipeline_id != "engineering_review_pipeline":
        return None
    _, plan = plan_model_escalation(
        context=_escalation_context(request),
        trigger_reason="runner_not_invoked",
        source="runner_not_invoked",
    )
    return plan


def _model_escalation(
    request: PipelineEvaluationRequest,
    *,
    trigger_reason: str,
    source: str,
) -> ModelEscalationPlan | None:
    if request.pipeline_id != "engineering_review_pipeline":
        return None
    _, plan = plan_model_escalation(
        context=_escalation_context(request),
        trigger_reason=trigger_reason,
        source=source,
    )
    return plan


def _disagreement_resolution(
    request: PipelineEvaluationRequest,
    *,
    source_subagent_id: str,
    counterparty_subagent_id: str,
    trigger_reason: str,
    summary: str,
) -> DisagreementResolutionPlan | None:
    if request.pipeline_id != "engineering_review_pipeline":
        return None
    _, plan = plan_disagreement_resolution(
        context=_escalation_context(request),
        source_subagent_id=source_subagent_id,
        counterparty_subagent_id=counterparty_subagent_id,
        trigger_reason=trigger_reason,
        summary=summary,
    )
    return plan


def _explicit_disagreement_metadata(
    request: PipelineEvaluationRequest,
    *,
    envelope: StructuredOutputEnvelope,
    fallback_summary: str,
) -> tuple[DisagreementResolutionPlan | None, PipelineDisagreementDecision]:
    if request.pipeline_id != "engineering_review_pipeline":
        return None, PipelineDisagreementDecision()

    signal_reason = _disagreement_reason(envelope)
    if signal_reason is None:
        return None, PipelineDisagreementDecision()

    source_subagent_id, counterparty_subagent_id = _disagreement_participants(request.subagent_id)
    plan = _disagreement_resolution(
        request,
        source_subagent_id=source_subagent_id,
        counterparty_subagent_id=counterparty_subagent_id,
        trigger_reason=signal_reason,
        summary=envelope.summary or fallback_summary,
    )
    resolution_planned = bool(plan and plan.status == "planned")
    return plan, PipelineDisagreementDecision(
        disagreement_present=True,
        resolution_planned=resolution_planned,
        reason=signal_reason,
    )


def _disagreement_reason(envelope: StructuredOutputEnvelope) -> str | None:
    explicit_next_actions = {
        "disagreement",
        "disagree_with_reviewer",
        "disagreement_resolution",
        "request_disagreement_resolution",
    }
    next_action = (envelope.next_action or "").strip()
    if next_action in explicit_next_actions:
        return "engineer_reviewer_disagreement"
    if (envelope.status or "").strip() in explicit_next_actions:
        return "engineer_reviewer_disagreement"
    return None


def _disagreement_participants(subagent_id: str) -> tuple[str, str]:
    if subagent_id == "hermes_code_reviewer":
        return "hermes_code_reviewer", "hermes_engineer_core"
    return subagent_id, "hermes_code_reviewer"


def _decision_path(
    *segments: str,
    disagreement: PipelineDisagreementDecision,
) -> list[str]:
    path = list(segments)
    if disagreement.disagreement_present:
        path.append("explicit_disagreement_signal")
    return path
