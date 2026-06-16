"""Pure pipeline structured-output evaluation contracts for metadata-only planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

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
            validation_errors=list(envelope.validation_errors),
            decision_path=["invalid_structured_output"],
        )

    if envelope.blockers:
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
            decision_path=["valid_structured_output", "blockers_present"],
        )

    if envelope.requires_review or envelope.status == "needs_review":
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
            decision_path=["valid_structured_output", "review_required"],
        )

    confidence = envelope.confidence if envelope.confidence is not None else 0.0
    if confidence < MIN_CONFIDENCE_FOR_CANDIDATE_COMPLETE:
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
                escalation_planned=True,
                reason="low_confidence",
            ),
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
        failure_reason=failure_reason,
        validation_errors=list(validation_errors or []),
        decision_path=list(decision_path or []),
    )
