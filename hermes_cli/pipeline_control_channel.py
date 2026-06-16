"""Pure metadata contracts for bounded subagent message planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


SAFE_LOOP_POLICY_DEFAULTS = {
    "max_review_iterations": 1,
    "max_peer_discussion_rounds": 0,
    "max_disagreement_rounds": 0,
    "max_invalid_output_retries": 0,
    "max_tool_retries": 0,
    "max_model_escalations": 0,
    "max_clarification_rounds": 0,
    "on_limit_exceeded": "block_and_escalate_to_user",
}

LOOP_POLICY_ALIASES = {
    "max_peer_discussion_rounds": (
        "max_peer_discussion_rounds",
        "max_peer_discussion_rounds_per_iteration",
    ),
    "max_disagreement_rounds": (
        "max_disagreement_rounds",
        "max_disagreement_rounds_per_iteration",
    ),
}


class PipelineControlStatus(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    LOOP_LIMIT_EXCEEDED = "loop_limit_exceeded"
    RUNNER_NOT_INVOKED = "runner_not_invoked"


@dataclass(frozen=True)
class LoopLimitPolicy:
    max_review_iterations: int
    max_peer_discussion_rounds: int
    max_disagreement_rounds: int
    max_invalid_output_retries: int
    max_tool_retries: int
    max_model_escalations: int
    max_clarification_rounds: int
    on_limit_exceeded: str
    resets_on_model_escalation: bool = False
    policy_source: str = "default"

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "max_review_iterations": self.max_review_iterations,
            "max_peer_discussion_rounds": self.max_peer_discussion_rounds,
            "max_disagreement_rounds": self.max_disagreement_rounds,
            "max_invalid_output_retries": self.max_invalid_output_retries,
            "max_tool_retries": self.max_tool_retries,
            "max_model_escalations": self.max_model_escalations,
            "max_clarification_rounds": self.max_clarification_rounds,
            "on_limit_exceeded": self.on_limit_exceeded,
            "resets_on_model_escalation": self.resets_on_model_escalation,
            "policy_source": self.policy_source,
        }


@dataclass(frozen=True)
class LoopCounterSnapshot:
    review_iterations: int = 0
    peer_discussion_rounds: int = 0
    disagreement_rounds: int = 0
    invalid_output_retries: int = 0
    tool_retries: int = 0
    model_escalations: int = 0
    clarification_rounds: int = 0

    def with_increment(self, counter_name: str) -> "LoopCounterSnapshot":
        values = self.to_safe_dict()
        values[counter_name] = int(values.get(counter_name, 0)) + 1
        return LoopCounterSnapshot(**values)

    def to_safe_dict(self) -> dict[str, int]:
        return {
            "review_iterations": self.review_iterations,
            "peer_discussion_rounds": self.peer_discussion_rounds,
            "disagreement_rounds": self.disagreement_rounds,
            "invalid_output_retries": self.invalid_output_retries,
            "tool_retries": self.tool_retries,
            "model_escalations": self.model_escalations,
            "clarification_rounds": self.clarification_rounds,
        }


@dataclass(frozen=True)
class LoopLimitDecision:
    allowed: bool
    status: PipelineControlStatus
    limit_name: str
    current_count: int
    limit_value: int
    next_counter: LoopCounterSnapshot
    reason: str | None = None
    user_action_required: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status.value,
            "limit_name": self.limit_name,
            "current_count": self.current_count,
            "limit_value": self.limit_value,
            "next_counter": self.next_counter.to_safe_dict(),
            "reason": self.reason,
            "user_action_required": self.user_action_required,
        }


@dataclass(frozen=True)
class ControlledSubagentMessage:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    source_subagent_id: str
    target_subagent_id: str
    message_purpose: str
    payload_summary: str
    related_step_id: str | None = None
    evaluation_id: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "source_subagent_id": self.source_subagent_id,
            "target_subagent_id": self.target_subagent_id,
            "message_purpose": self.message_purpose,
            "payload_summary": self.payload_summary,
            "related_step_id": self.related_step_id,
            "evaluation_id": self.evaluation_id,
        }


@dataclass(frozen=True)
class ControlledMessageDecision:
    message: ControlledSubagentMessage
    allowed: bool
    status: PipelineControlStatus
    blocked_reason: str | None = None
    loop_limit_decision: LoopLimitDecision | None = None
    user_action_required: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "message": self.message.to_safe_dict(),
            "allowed": self.allowed,
            "status": self.status.value,
            "blocked_reason": self.blocked_reason,
            "loop_limit_decision": (
                self.loop_limit_decision.to_safe_dict()
                if self.loop_limit_decision is not None
                else None
            ),
            "user_action_required": self.user_action_required,
        }


@dataclass(frozen=True)
class ControlledMessageChannelPlan:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    policy: LoopLimitPolicy
    counters: LoopCounterSnapshot
    decisions: list[ControlledMessageDecision] = field(default_factory=list)
    bounded: bool = True
    execution_enabled: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "policy": self.policy.to_safe_dict(),
            "counters": self.counters.to_safe_dict(),
            "decisions": [item.to_safe_dict() for item in self.decisions],
            "bounded": self.bounded,
            "execution_enabled": self.execution_enabled,
        }


def resolve_loop_limit_policy(pipeline_spec: Mapping[str, Any] | None) -> LoopLimitPolicy:
    spec = dict(pipeline_spec or {})
    policy_payload = dict(spec.get("loop_policy") or {})
    escalation_payload = dict(spec.get("model_escalation_policy") or {})
    values: dict[str, Any] = {}
    policy_source = "default"

    for key, default_value in SAFE_LOOP_POLICY_DEFAULTS.items():
        aliases = LOOP_POLICY_ALIASES.get(key, (key,))
        selected = None
        for alias in aliases:
            if alias in policy_payload:
                selected = policy_payload[alias]
                policy_source = "pipeline_spec"
                break
        if selected is None:
            selected = default_value
        values[key] = selected

    values["resets_on_model_escalation"] = bool(escalation_payload.get("resets_loop_counters", False))
    if escalation_payload:
        policy_source = "pipeline_spec" if policy_source != "pipeline_spec" else policy_source
    values["policy_source"] = policy_source
    return LoopLimitPolicy(
        max_review_iterations=max(0, int(values["max_review_iterations"])),
        max_peer_discussion_rounds=max(0, int(values["max_peer_discussion_rounds"])),
        max_disagreement_rounds=max(0, int(values["max_disagreement_rounds"])),
        max_invalid_output_retries=max(0, int(values["max_invalid_output_retries"])),
        max_tool_retries=max(0, int(values["max_tool_retries"])),
        max_model_escalations=max(0, int(values["max_model_escalations"])),
        max_clarification_rounds=max(0, int(values["max_clarification_rounds"])),
        on_limit_exceeded=str(values["on_limit_exceeded"]),
        resets_on_model_escalation=bool(values["resets_on_model_escalation"]),
        policy_source=policy_source,
    )


def evaluate_loop_limit(
    *,
    policy: LoopLimitPolicy,
    counters: LoopCounterSnapshot,
    limit_name: str,
) -> LoopLimitDecision:
    counter_name = _counter_name_for_limit(limit_name)
    current_count = getattr(counters, counter_name)
    limit_value = getattr(policy, limit_name)
    if current_count >= limit_value:
        reason = f"{limit_name}_exceeded"
        return LoopLimitDecision(
            allowed=False,
            status=PipelineControlStatus.LOOP_LIMIT_EXCEEDED,
            limit_name=limit_name,
            current_count=current_count,
            limit_value=limit_value,
            next_counter=counters,
            reason=reason,
            user_action_required=True,
        )

    return LoopLimitDecision(
        allowed=True,
        status=PipelineControlStatus.ALLOWED,
        limit_name=limit_name,
        current_count=current_count,
        limit_value=limit_value,
        next_counter=counters.with_increment(counter_name),
        reason=None,
        user_action_required=False,
    )


def build_controlled_message_decision(
    *,
    message: ControlledSubagentMessage,
    policy: LoopLimitPolicy,
    counters: LoopCounterSnapshot,
    limit_name: str,
    runner_invoked: bool,
) -> ControlledMessageDecision:
    if not runner_invoked:
        return ControlledMessageDecision(
            message=message,
            allowed=False,
            status=PipelineControlStatus.RUNNER_NOT_INVOKED,
            blocked_reason="runner_not_invoked",
            user_action_required=False,
        )

    limit_decision = evaluate_loop_limit(policy=policy, counters=counters, limit_name=limit_name)
    if not limit_decision.allowed:
        return ControlledMessageDecision(
            message=message,
            allowed=False,
            status=PipelineControlStatus.LOOP_LIMIT_EXCEEDED,
            blocked_reason=limit_decision.reason,
            loop_limit_decision=limit_decision,
            user_action_required=True,
        )

    return ControlledMessageDecision(
        message=message,
        allowed=True,
        status=PipelineControlStatus.ALLOWED,
        loop_limit_decision=limit_decision,
        user_action_required=False,
    )


def _counter_name_for_limit(limit_name: str) -> str:
    mapping = {
        "max_review_iterations": "review_iterations",
        "max_peer_discussion_rounds": "peer_discussion_rounds",
        "max_disagreement_rounds": "disagreement_rounds",
        "max_invalid_output_retries": "invalid_output_retries",
        "max_tool_retries": "tool_retries",
        "max_model_escalations": "model_escalations",
        "max_clarification_rounds": "clarification_rounds",
    }
    if limit_name not in mapping:
        raise KeyError(f"unsupported loop limit: {limit_name}")
    return mapping[limit_name]
