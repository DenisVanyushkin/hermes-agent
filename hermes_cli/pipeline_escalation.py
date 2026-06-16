"""Pure metadata contracts for pipeline model escalation and disagreement resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from hermes_cli.pipeline_control_channel import (
    LoopCounterSnapshot,
    LoopLimitDecision,
    evaluate_loop_limit,
    resolve_loop_limit_policy,
)


SAFE_MODEL_ESCALATION_DEFAULTS = {
    "enabled": False,
    "on_escalation_unavailable": "block_and_escalate_to_user",
    "requires_future_execution_gate": True,
    "user_approval_after_loop_limit_exceeded": True,
}

SAFE_DISAGREEMENT_DEFAULTS = {
    "enabled": False,
    "escalation_allowed": False,
    "on_unresolved_disagreement": "block_and_escalate_to_user",
    "max_rounds": 0,
}


@dataclass(frozen=True)
class ModelEscalationRule:
    condition: str
    target_subagent: str
    escalate_to_model_class: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "target_subagent": self.target_subagent,
            "escalate_to_model_class": self.escalate_to_model_class,
        }


@dataclass(frozen=True)
class ModelEscalationPolicy:
    enabled: bool
    resets_loop_counters: bool
    on_escalation_unavailable: str
    requires_future_execution_gate: bool
    user_approval_after_loop_limit_exceeded: bool
    policy_source: str = "default"
    rules: list[ModelEscalationRule] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "resets_loop_counters": self.resets_loop_counters,
            "on_escalation_unavailable": self.on_escalation_unavailable,
            "requires_future_execution_gate": self.requires_future_execution_gate,
            "user_approval_after_loop_limit_exceeded": self.user_approval_after_loop_limit_exceeded,
            "policy_source": self.policy_source,
            "rules": [rule.to_safe_dict() for rule in self.rules],
        }


@dataclass(frozen=True)
class ModelEscalationTrigger:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_ids: list[str]
    reason: str
    source: str
    loop_limit_decision: LoopLimitDecision | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "subagent_ids": list(self.subagent_ids),
            "reason": self.reason,
            "source": self.source,
            "loop_limit_decision": (
                self.loop_limit_decision.to_safe_dict()
                if self.loop_limit_decision is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ModelEscalationDecision:
    allowed: bool = False
    planned: bool = False
    execution_enabled: bool = False
    user_approval_required: bool = False
    counters_reset: bool = False
    counters_persist: bool = True
    blocked_reason: str | None = None
    decision_path: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "planned": self.planned,
            "execution_enabled": self.execution_enabled,
            "user_approval_required": self.user_approval_required,
            "counters_reset": self.counters_reset,
            "counters_persist": self.counters_persist,
            "blocked_reason": self.blocked_reason,
            "decision_path": list(self.decision_path),
        }


@dataclass(frozen=True)
class ModelEscalationPlan:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_id: str
    trigger: ModelEscalationTrigger
    policy: ModelEscalationPolicy
    current_model: dict[str, Any]
    candidate_model: dict[str, Any] | None
    allowed: bool
    status: str
    blocked_reason: str | None
    user_approval_required: bool
    counters_reset: bool
    counters_persist: bool
    metadata_only: bool = True
    execution_enabled: bool = False
    decision_path: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "subagent_id": self.subagent_id,
            "trigger": self.trigger.to_safe_dict(),
            "policy": self.policy.to_safe_dict(),
            "current_model": dict(self.current_model),
            "candidate_model": dict(self.candidate_model) if self.candidate_model is not None else None,
            "allowed": self.allowed,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "user_approval_required": self.user_approval_required,
            "counters_reset": self.counters_reset,
            "counters_persist": self.counters_persist,
            "metadata_only": self.metadata_only,
            "execution_enabled": self.execution_enabled,
            "decision_path": list(self.decision_path),
        }


@dataclass(frozen=True)
class DisagreementPolicy:
    enabled: bool
    decisive_subagent: str | None
    arbitrator_subagent: str | None
    escalation_allowed: bool
    escalation_trigger: str | None
    on_unresolved_disagreement: str
    max_rounds: int
    policy_source: str = "default"
    rules: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "decisive_subagent": self.decisive_subagent,
            "arbitrator_subagent": self.arbitrator_subagent,
            "escalation_allowed": self.escalation_allowed,
            "escalation_trigger": self.escalation_trigger,
            "on_unresolved_disagreement": self.on_unresolved_disagreement,
            "max_rounds": self.max_rounds,
            "policy_source": self.policy_source,
            "rules": list(self.rules),
        }


@dataclass(frozen=True)
class DisagreementSignal:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    source_subagent_id: str
    counterparty_subagent_id: str
    reason: str
    source: str
    summary: str

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "source_subagent_id": self.source_subagent_id,
            "counterparty_subagent_id": self.counterparty_subagent_id,
            "reason": self.reason,
            "source": self.source,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class DisagreementDecision:
    allowed: bool = False
    planned: bool = False
    requires_user_escalation: bool = False
    blocked_reason: str | None = None
    authoritative_subagent_id: str | None = None
    decision_path: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "planned": self.planned,
            "requires_user_escalation": self.requires_user_escalation,
            "blocked_reason": self.blocked_reason,
            "authoritative_subagent_id": self.authoritative_subagent_id,
            "decision_path": list(self.decision_path),
        }


@dataclass(frozen=True)
class DisagreementResolutionPlan:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    signal: DisagreementSignal
    policy: DisagreementPolicy
    resolution_mode: str
    status: str
    authoritative_subagent_id: str | None
    escalation_target_subagent_id: str | None
    user_approval_required: bool
    blocked_reason: str | None
    metadata_only: bool = True
    decision_path: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "signal": self.signal.to_safe_dict(),
            "policy": self.policy.to_safe_dict(),
            "resolution_mode": self.resolution_mode,
            "status": self.status,
            "authoritative_subagent_id": self.authoritative_subagent_id,
            "escalation_target_subagent_id": self.escalation_target_subagent_id,
            "user_approval_required": self.user_approval_required,
            "blocked_reason": self.blocked_reason,
            "metadata_only": self.metadata_only,
            "decision_path": list(self.decision_path),
        }


@dataclass(frozen=True)
class EscalationContext:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_id: str
    pipeline_spec: Mapping[str, Any]
    current_runtime: Mapping[str, Any] | None = None
    subagent_spec: Mapping[str, Any] | None = None
    all_subagent_specs: Mapping[str, Mapping[str, Any]] | None = None


def resolve_model_escalation_policy(pipeline_spec: Mapping[str, Any] | None) -> ModelEscalationPolicy:
    payload = dict((pipeline_spec or {}).get("model_escalation_policy") or {})
    policy_source = "pipeline_spec" if payload else "default"
    rules: list[ModelEscalationRule] = []
    for item in payload.get("rules") or []:
        if not isinstance(item, Mapping):
            continue
        condition = str(item.get("condition") or "").strip()
        target_subagent = str(item.get("target_subagent") or "").strip()
        if not condition or not target_subagent:
            continue
        rules.append(
            ModelEscalationRule(
                condition=condition,
                target_subagent=target_subagent,
                escalate_to_model_class=_nullable_str(item.get("escalate_to_model_class")),
            )
        )
    return ModelEscalationPolicy(
        enabled=bool(payload.get("enabled", SAFE_MODEL_ESCALATION_DEFAULTS["enabled"])),
        resets_loop_counters=bool(payload.get("resets_loop_counters", False)),
        on_escalation_unavailable=str(
            payload.get(
                "on_escalation_unavailable",
                SAFE_MODEL_ESCALATION_DEFAULTS["on_escalation_unavailable"],
            )
        ),
        requires_future_execution_gate=bool(
            payload.get(
                "requires_future_execution_gate",
                SAFE_MODEL_ESCALATION_DEFAULTS["requires_future_execution_gate"],
            )
        ),
        user_approval_after_loop_limit_exceeded=bool(
            payload.get(
                "user_approval_after_loop_limit_exceeded",
                SAFE_MODEL_ESCALATION_DEFAULTS["user_approval_after_loop_limit_exceeded"],
            )
        ),
        policy_source=policy_source,
        rules=rules,
    )


def resolve_disagreement_policy(pipeline_spec: Mapping[str, Any] | None) -> DisagreementPolicy:
    payload = dict((pipeline_spec or {}).get("disagreement_policy") or {})
    policy_source = "pipeline_spec" if payload else "default"
    return DisagreementPolicy(
        enabled=bool(payload.get("enabled", SAFE_DISAGREEMENT_DEFAULTS["enabled"])),
        decisive_subagent=_nullable_str(payload.get("decisive_subagent")),
        arbitrator_subagent=_nullable_str(payload.get("arbitrator_subagent")),
        escalation_allowed=bool(payload.get("escalation_allowed", SAFE_DISAGREEMENT_DEFAULTS["escalation_allowed"])),
        escalation_trigger=_nullable_str(payload.get("escalation_trigger")),
        on_unresolved_disagreement=str(
            payload.get(
                "on_unresolved_disagreement",
                SAFE_DISAGREEMENT_DEFAULTS["on_unresolved_disagreement"],
            )
        ),
        max_rounds=max(0, int(payload.get("max_rounds", SAFE_DISAGREEMENT_DEFAULTS["max_rounds"]))),
        policy_source=policy_source,
        rules=[str(item) for item in payload.get("rules") or [] if isinstance(item, str)],
    )


def plan_model_escalation(
    *,
    context: EscalationContext,
    trigger_reason: str,
    source: str,
    counters: LoopCounterSnapshot | None = None,
    loop_limit_name: str = "max_model_escalations",
) -> tuple[ModelEscalationDecision, ModelEscalationPlan]:
    policy = resolve_model_escalation_policy(context.pipeline_spec)
    trigger = ModelEscalationTrigger(
        pipeline_session_id=context.pipeline_session_id,
        trace_id=context.trace_id,
        pipeline_id=context.pipeline_id,
        step_id=context.step_id,
        subagent_ids=[context.subagent_id],
        reason=trigger_reason,
        source=source,
    )
    current_model = _current_model_metadata(context)
    rule = _select_escalation_rule(policy, trigger_reason)
    candidate_model = _candidate_model_metadata(context, rule)

    if not policy.enabled:
        decision = ModelEscalationDecision(
            blocked_reason="policy_disabled",
            decision_path=["policy_disabled"],
        )
        return decision, _build_model_plan(context, trigger, policy, current_model, candidate_model, decision)

    if source == "runner_not_invoked":
        decision = ModelEscalationDecision(
            blocked_reason="runner_not_invoked",
            decision_path=["runner_not_invoked"],
        )
        return decision, _build_model_plan(context, trigger, policy, current_model, candidate_model, decision)

    loop_decision = evaluate_loop_limit(
        policy=resolve_loop_limit_policy(context.pipeline_spec),
        counters=counters or LoopCounterSnapshot(),
        limit_name=loop_limit_name,
    )
    trigger = ModelEscalationTrigger(
        pipeline_session_id=trigger.pipeline_session_id,
        trace_id=trigger.trace_id,
        pipeline_id=trigger.pipeline_id,
        step_id=trigger.step_id,
        subagent_ids=trigger.subagent_ids,
        reason=trigger.reason,
        source=trigger.source,
        loop_limit_decision=loop_decision,
    )
    if not loop_decision.allowed:
        decision = ModelEscalationDecision(
            allowed=False,
            planned=False,
            user_approval_required=policy.user_approval_after_loop_limit_exceeded,
            counters_reset=False,
            counters_persist=True,
            blocked_reason="loop_limit_exceeded",
            decision_path=["loop_limit_exceeded", "user_escalation_required"],
        )
        return decision, _build_model_plan(context, trigger, policy, current_model, candidate_model, decision)

    if candidate_model is None:
        decision = ModelEscalationDecision(
            blocked_reason="escalation_target_unavailable",
            user_approval_required=True,
            decision_path=["escalation_target_unavailable"],
        )
        return decision, _build_model_plan(context, trigger, policy, current_model, candidate_model, decision)

    decision = ModelEscalationDecision(
        allowed=True,
        planned=True,
        execution_enabled=False,
        user_approval_required=False,
        counters_reset=policy.resets_loop_counters,
        counters_persist=not policy.resets_loop_counters,
        blocked_reason=None,
        decision_path=["policy_enabled", "metadata_only_plan_created"],
    )
    return decision, _build_model_plan(context, trigger, policy, current_model, candidate_model, decision)


def plan_disagreement_resolution(
    *,
    context: EscalationContext,
    source_subagent_id: str,
    counterparty_subagent_id: str,
    trigger_reason: str,
    summary: str,
    counters: LoopCounterSnapshot | None = None,
) -> tuple[DisagreementDecision, DisagreementResolutionPlan]:
    policy = resolve_disagreement_policy(context.pipeline_spec)
    signal = DisagreementSignal(
        pipeline_session_id=context.pipeline_session_id,
        trace_id=context.trace_id,
        pipeline_id=context.pipeline_id,
        step_id=context.step_id,
        source_subagent_id=source_subagent_id,
        counterparty_subagent_id=counterparty_subagent_id,
        reason=trigger_reason,
        source="evaluation",
        summary=summary,
    )
    authoritative_subagent_id = policy.arbitrator_subagent or policy.decisive_subagent
    escalation_target = None
    if policy.escalation_trigger == trigger_reason and policy.escalation_allowed:
        escalation_target = authoritative_subagent_id

    if not policy.enabled:
        decision = DisagreementDecision(
            blocked_reason="policy_disabled",
            authoritative_subagent_id=authoritative_subagent_id,
            decision_path=["policy_disabled"],
        )
        return decision, _build_disagreement_plan(policy, signal, authoritative_subagent_id, escalation_target, decision)

    loop_policy = resolve_loop_limit_policy(context.pipeline_spec)
    loop_decision = evaluate_loop_limit(
        policy=loop_policy,
        counters=counters or LoopCounterSnapshot(),
        limit_name="max_disagreement_rounds",
    )
    if not loop_decision.allowed:
        decision = DisagreementDecision(
            allowed=False,
            planned=False,
            requires_user_escalation=True,
            blocked_reason="disagreement_round_limit_exceeded",
            authoritative_subagent_id=authoritative_subagent_id,
            decision_path=["disagreement_round_limit_exceeded", "user_escalation_required"],
        )
        return decision, _build_disagreement_plan(policy, signal, authoritative_subagent_id, escalation_target, decision)

    if (
        trigger_reason == "reviewer_blocks_completion"
        and policy.decisive_subagent
        and source_subagent_id != policy.decisive_subagent
    ):
        decision = DisagreementDecision(
            allowed=False,
            planned=False,
            requires_user_escalation=False,
            blocked_reason="reviewer_block_authoritative",
            authoritative_subagent_id=policy.decisive_subagent,
            decision_path=["reviewer_block_authoritative"],
        )
        return decision, _build_disagreement_plan(policy, signal, policy.decisive_subagent, escalation_target, decision)

    if loop_decision.next_counter.disagreement_rounds > policy.max_rounds:
        decision = DisagreementDecision(
            allowed=False,
            planned=False,
            requires_user_escalation=True,
            blocked_reason="max_disagreement_rounds_reached",
            authoritative_subagent_id=authoritative_subagent_id,
            decision_path=["max_disagreement_rounds_reached", "user_escalation_required"],
        )
        return decision, _build_disagreement_plan(policy, signal, authoritative_subagent_id, escalation_target, decision)

    decision = DisagreementDecision(
        allowed=True,
        planned=True,
        requires_user_escalation=False,
        blocked_reason=None,
        authoritative_subagent_id=authoritative_subagent_id,
        decision_path=["policy_enabled", "controlled_resolution_metadata_only"],
    )
    return decision, _build_disagreement_plan(policy, signal, authoritative_subagent_id, escalation_target, decision)


def _build_model_plan(
    context: EscalationContext,
    trigger: ModelEscalationTrigger,
    policy: ModelEscalationPolicy,
    current_model: dict[str, Any],
    candidate_model: dict[str, Any] | None,
    decision: ModelEscalationDecision,
) -> ModelEscalationPlan:
    status = "planned" if decision.allowed and decision.planned else "blocked"
    return ModelEscalationPlan(
        pipeline_session_id=context.pipeline_session_id,
        trace_id=context.trace_id,
        pipeline_id=context.pipeline_id,
        step_id=context.step_id,
        subagent_id=context.subagent_id,
        trigger=trigger,
        policy=policy,
        current_model=current_model,
        candidate_model=candidate_model,
        allowed=decision.allowed,
        status=status,
        blocked_reason=decision.blocked_reason,
        user_approval_required=decision.user_approval_required,
        counters_reset=decision.counters_reset,
        counters_persist=decision.counters_persist,
        decision_path=list(decision.decision_path),
    )


def _build_disagreement_plan(
    policy: DisagreementPolicy,
    signal: DisagreementSignal,
    authoritative_subagent_id: str | None,
    escalation_target_subagent_id: str | None,
    decision: DisagreementDecision,
) -> DisagreementResolutionPlan:
    return DisagreementResolutionPlan(
        pipeline_session_id=signal.pipeline_session_id,
        trace_id=signal.trace_id,
        pipeline_id=signal.pipeline_id,
        step_id=signal.step_id,
        signal=signal,
        policy=policy,
        resolution_mode="metadata_only",
        status="planned" if decision.allowed and decision.planned else "blocked",
        authoritative_subagent_id=authoritative_subagent_id,
        escalation_target_subagent_id=escalation_target_subagent_id,
        user_approval_required=decision.requires_user_escalation,
        blocked_reason=decision.blocked_reason,
        decision_path=list(decision.decision_path),
    )


def _current_model_metadata(context: EscalationContext) -> dict[str, Any]:
    runtime = dict(context.current_runtime or {})
    return {
        "provider": runtime.get("provider"),
        "model": runtime.get("model"),
        "model_class": runtime.get("model_class"),
        "runtime_factory_status": runtime.get("status"),
    }


def _candidate_model_metadata(
    context: EscalationContext,
    rule: ModelEscalationRule | None,
) -> dict[str, Any] | None:
    if rule is None:
        return None
    target_subagent = _resolve_target_subagent_id(context.pipeline_spec, rule.target_subagent)
    specs = dict(context.all_subagent_specs or {})
    target_spec = dict(specs.get(target_subagent) or {})
    allowed = target_spec.get("models", {}).get("allowed") if isinstance(target_spec.get("models"), Mapping) else []
    if isinstance(allowed, list):
        for entry in allowed:
            if not isinstance(entry, Mapping):
                continue
            if rule.escalate_to_model_class and entry.get("class") != rule.escalate_to_model_class:
                continue
            return {
                "provider": entry.get("provider"),
                "model": entry.get("model"),
                "model_class": entry.get("class"),
                "target_subagent_id": target_subagent,
            }
    return None


def _select_escalation_rule(
    policy: ModelEscalationPolicy,
    trigger_reason: str,
) -> ModelEscalationRule | None:
    for rule in policy.rules:
        if rule.condition == trigger_reason:
            return rule
    for rule in policy.rules:
        if rule.condition.startswith(trigger_reason):
            return rule
    return None


def _resolve_target_subagent_id(pipeline_spec: Mapping[str, Any], target_subagent: str) -> str:
    subagents = pipeline_spec.get("subagents")
    if isinstance(subagents, Mapping):
        if target_subagent == "decisive_subagent_or_arbitrator":
            disagreement = pipeline_spec.get("disagreement_policy")
            if isinstance(disagreement, Mapping):
                return str(
                    disagreement.get("arbitrator_subagent")
                    or disagreement.get("decisive_subagent")
                    or target_subagent
                )
        resolved = subagents.get(target_subagent)
        if isinstance(resolved, str):
            return resolved
    return target_subagent


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
