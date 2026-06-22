"""Canonical preflight policy for future pipeline execution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_router import RouterDecision


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
EXPECTED_SUBAGENT_IDS = ("hermes_engineer_core", "hermes_code_reviewer")
REVIEWER_CONDITION = "code_changes_require_review"


class PipelineGateMode(str, Enum):
    DISABLED = "disabled"
    OBSERVE = "observe"
    PLAN_ONLY = "plan_only"
    EXECUTE = "execute"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class PipelineGatePolicy:
    enabled: bool
    mode: PipelineGateMode
    allow_pipelines: tuple[str, ...]
    min_router_confidence: float
    config_valid: bool = True


@dataclass(frozen=True)
class PipelineGateRequest:
    config: Mapping[str, Any] | None
    router_decision: RouterDecision | None
    pipeline_plan_payload: Mapping[str, Any] | None
    platform: str | None = None
    platform_allowed: bool | None = None
    destructive_task: bool | None = None
    explicit_approval: bool | None = None
    user_message: str | None = None


@dataclass(frozen=True)
class PipelineGateDecision:
    allowed: bool
    mode: PipelineGateMode
    pipeline_id: str | None
    pipeline_session_id: str | None
    selected_pipeline_id: str | None
    planned_steps_count: int
    reason_code: str
    reason: str
    would_execute: bool
    executed: bool
    requirements_met: list[str] = field(default_factory=list)
    requirements_failed: list[str] = field(default_factory=list)
    risk_level: str = "high"
    safe_to_log_payload: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blocked": not self.allowed,
            "mode": self.mode.value,
            "pipeline_id": self.pipeline_id,
            "pipeline_session_id": self.pipeline_session_id,
            "selected_pipeline_id": self.selected_pipeline_id,
            "planned_steps_count": self.planned_steps_count,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "would_execute": self.would_execute,
            "executed": self.executed,
            "requirements_met": list(self.requirements_met),
            "requirements_failed": list(self.requirements_failed),
            "required_checks_summary": {
                "passed": list(self.requirements_met),
                "failed": list(self.requirements_failed),
                "passed_count": len(self.requirements_met),
                "failed_count": len(self.requirements_failed),
            },
            "risk_level": self.risk_level,
            "safe_to_log_payload": dict(self.safe_to_log_payload),
        }


def evaluate_pipeline_gate(request: PipelineGateRequest) -> PipelineGateDecision:
    policy = _load_policy(request.config)
    raw_execution_mode = _raw_execution_mode(request.config)
    router = request.router_decision
    payload = dict(request.pipeline_plan_payload or {})
    selected_pipeline_id = getattr(router, "selected_pipeline_id", None)
    pipeline_id = selected_pipeline_id or getattr(router, "fallback_pipeline_id", None)
    pipeline_session_id = getattr(router, "pipeline_session_id", None)
    planned_steps_count = int(payload.get("planned_steps_count") or 0)
    requirements_met: list[str] = []
    requirements_failed: list[str] = []

    def deny(code: str, message: str, *, risk_level: str = "high") -> PipelineGateDecision:
        return PipelineGateDecision(
            allowed=False,
            mode=policy.mode,
            pipeline_id=pipeline_id,
            pipeline_session_id=pipeline_session_id,
            selected_pipeline_id=selected_pipeline_id,
            planned_steps_count=planned_steps_count,
            reason_code=code,
            reason=message,
            would_execute=False,
            executed=False,
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
            risk_level=risk_level,
            safe_to_log_payload=_safe_payload(request, policy, pipeline_id, pipeline_session_id),
        )

    if raw_execution_mode != "invalid" and raw_execution_mode not in {mode.value for mode in PipelineGateMode}:
        requirements_failed.append("supported_execution_mode")
        return deny(
            f"unsupported_execution_mode:{raw_execution_mode}",
            f"Unsupported pipeline execution mode: {raw_execution_mode}",
        )

    if not policy.config_valid:
        requirements_failed.append("valid_execution_config")
        return deny("missing_required_config", "Pipeline execution config is missing, malformed, or ambiguous.")

    if not policy.enabled or policy.mode == PipelineGateMode.DISABLED:
        requirements_failed.append("execution_mode_enabled")
        return deny("gate_disabled", "Pipeline execution mode is disabled by configuration.")

    if not policy.allow_pipelines:
        requirements_failed.append("allow_pipelines_configured")
        return deny("missing_required_config", "Execution allowlist is missing or empty.")
    requirements_met.append("execution_mode_enabled")

    if router is None or router.status != "selected":
        requirements_failed.append("router_selected")
        return deny("router_not_selected", "Router decision must be selected before execution can be allowed.")
    requirements_met.append("router_selected")

    if not selected_pipeline_id:
        requirements_failed.append("known_pipeline_selected")
        return deny("router_not_selected", "Router did not select a pipeline id.")
    requirements_met.append("known_pipeline_selected")

    if selected_pipeline_id != ENGINEERING_PIPELINE_ID or selected_pipeline_id not in policy.allow_pipelines:
        requirements_failed.append("supported_pipeline_selected")
        return deny("unsupported_pipeline", "Only engineering_review_pipeline is eligible for future execution.")
    requirements_met.append("supported_pipeline_selected")

    if pipeline_session_id is None:
        requirements_failed.append("pipeline_session_present")
        return deny("missing_pipeline_session", "Selected pipeline execution requires a pipeline session id.")
    requirements_met.append("pipeline_session_present")

    if _router_confidence_below_threshold(router, policy):
        requirements_failed.append("router_confidence_threshold")
        return deny("low_router_confidence", "Router confidence is below the configured execution threshold.")
    requirements_met.append("router_confidence_threshold")

    if payload.get("runtime_plan_failed"):
        requirements_failed.append("runtime_plan_succeeded")
        return deny("runtime_plan_failed", "Runtime planning reported a failure.")
    requirements_met.append("runtime_plan_succeeded")

    if payload.get("pipeline_plan_error") is not None:
        requirements_failed.append("plan_error_absent")
        return deny("plan_error", "Pipeline planning returned an explicit error payload.")
    requirements_met.append("plan_error_absent")

    if payload.get("pipeline_plan_status") != "planned" or payload.get("pipeline_plan_completion_reason") != "plan_only":
        requirements_failed.append("plan_ready")
        return deny("plan_not_ready", "Pipeline plan must be in planned/plan_only state before execution can be allowed.")
    requirements_met.append("plan_ready")

    planned_subagent_ids = list(payload.get("planned_subagent_ids") or [])
    step_records = list(((payload.get("pipeline_plan") or {}).get("step_records")) or [])
    planned_step_subagents = [str(step.get("subagent_id")) for step in step_records if isinstance(step, Mapping)]
    if not _contains_expected_steps(planned_subagent_ids, planned_step_subagents):
        requirements_failed.append("expected_steps_present")
        return deny("missing_expected_steps", "Planned engineer/reviewer steps are missing from the pipeline plan.")
    requirements_met.append("expected_steps_present")

    reviewer_step = next(
        (step for step in step_records if isinstance(step, Mapping) and step.get("step_kind") == "reviewer"),
        None,
    )
    if not isinstance(reviewer_step, Mapping) or reviewer_step.get("condition") != REVIEWER_CONDITION:
        requirements_failed.append("reviewer_conditional")
        return deny("reviewer_not_conditional", "Reviewer must remain conditional on code changes.")
    requirements_met.append("reviewer_conditional")

    engineer_step = next(
        (step for step in step_records if isinstance(step, Mapping) and step.get("step_kind") == "engineer"),
        None,
    )
    if not isinstance(engineer_step, Mapping):
        requirements_failed.append("engineer_step_present")
        return deny("missing_engineer_step", "Engineering pipeline must include an engineer step.")
    requirements_met.append("engineer_step_present")

    if policy.mode == PipelineGateMode.OBSERVE:
        requirements_failed.append("execute_mode_required")
        return deny("observe_only", "Observe mode may report planning data but must deny execution.", risk_level="medium")

    if policy.mode == PipelineGateMode.PLAN_ONLY:
        requirements_failed.append("execute_mode_required")
        return deny("plan_only", "Plan-only mode may build a dry-run plan but must deny execution.", risk_level="medium")

    if not _constructors_verified(step_records):
        requirements_failed.append("runtime_constructor_verified")
        return deny("runtime_constructor_unverified", "Runtime constructors must be verified before future execution.")
    requirements_met.append("runtime_constructor_verified")

    if request.platform_allowed is False:
        requirements_failed.append("platform_allowed")
        return deny("unsafe_platform", "Platform/session policy does not allow pipeline execution here.")
    if request.platform_allowed is True:
        requirements_met.append("platform_allowed")

    if request.destructive_task and not request.explicit_approval:
        requirements_failed.append("destructive_task_approved")
        return deny("destructive_task_requires_approval", "Destructive tasks require explicit approval before execution.")
    if request.destructive_task is False or request.explicit_approval:
        requirements_met.append("destructive_task_approved")

    return PipelineGateDecision(
        allowed=True,
        mode=policy.mode,
        pipeline_id=selected_pipeline_id,
        pipeline_session_id=pipeline_session_id,
        selected_pipeline_id=selected_pipeline_id,
        planned_steps_count=planned_steps_count,
        reason_code="allowed",
        reason="All configured preflight checks passed for future execute mode.",
        would_execute=True,
        executed=False,
        requirements_met=requirements_met,
        requirements_failed=requirements_failed,
        risk_level="medium",
        safe_to_log_payload=_safe_payload(request, policy, selected_pipeline_id, pipeline_session_id),
    )


def _load_policy(config: Mapping[str, Any] | None) -> PipelineGatePolicy:
    enabled = bool(cfg_get(config, "pipelines", "enabled", default=False))
    execution_cfg = cfg_get(config, "pipelines", "execution", default=None)
    config_valid = True
    if execution_cfg is None:
        execution_cfg = {}
    elif not isinstance(execution_cfg, Mapping):
        execution_cfg = {}
        config_valid = False

    raw_mode_value = execution_cfg.get("mode", "disabled")
    if raw_mode_value is None:
        raw_mode_value = "disabled"
    if not isinstance(raw_mode_value, str):
        raw_mode = "disabled"
        config_valid = False
    else:
        raw_mode = raw_mode_value.strip().lower() or "disabled"
    try:
        mode = PipelineGateMode(raw_mode)
    except ValueError:
        mode = PipelineGateMode.DISABLED
        config_valid = False

    raw_allow_pipelines = execution_cfg.get("allow_pipelines", [])
    if raw_allow_pipelines is None:
        raw_allow_pipelines = []
    if isinstance(raw_allow_pipelines, (str, bytes)) or not isinstance(raw_allow_pipelines, (list, tuple)):
        allow_pipelines = ()
        config_valid = False
    else:
        allow_pipelines = tuple(str(item) for item in raw_allow_pipelines if str(item).strip())

    raw_confidence = execution_cfg.get("min_router_confidence", 0.90)
    try:
        min_router_confidence = float(raw_confidence if raw_confidence is not None else 0.90)
    except (TypeError, ValueError):
        min_router_confidence = 0.90
        config_valid = False

    return PipelineGatePolicy(
        enabled=enabled,
        mode=mode,
        allow_pipelines=allow_pipelines,
        min_router_confidence=min_router_confidence,
        config_valid=config_valid,
    )


def _router_confidence_below_threshold(router: RouterDecision, policy: PipelineGatePolicy) -> bool:
    confidence = float(router.confidence)
    if confidence >= float(policy.min_router_confidence):
        return False
    return not _strict_engineering_heuristic_route(router)


def _strict_engineering_heuristic_route(router: RouterDecision) -> bool:
    return (
        getattr(router, "selected_pipeline_id", None) == ENGINEERING_PIPELINE_ID
        and bool(getattr(router, "routing_fallback_used", False))
        and str(getattr(router, "router_strategy", "") or "").strip().lower() == "heuristic_timeout_fallback"
        and str(getattr(router, "routing_confidence_source", "") or "").strip().lower() == "heuristic_strict"
    )


def _raw_execution_mode(config: Mapping[str, Any] | None) -> str:
    raw = cfg_get(config, "pipelines", "execution", "mode", default="disabled")
    if not isinstance(raw, str):
        return "invalid"
    return raw.strip().lower() or "disabled"


def _contains_expected_steps(planned_subagent_ids: list[str], planned_step_subagents: list[str]) -> bool:
    expected = set(EXPECTED_SUBAGENT_IDS)
    return expected.issubset(set(planned_subagent_ids)) and expected.issubset(set(planned_step_subagents))


def _constructors_verified(step_records: list[Mapping[str, Any]]) -> bool:
    for step in step_records:
        runtime_plan = step.get("runtime_factory_plan")
        provider = step.get("constructor_provider") or (runtime_plan.get("provider") if isinstance(runtime_plan, Mapping) else None)
        model = step.get("constructor_model") or (runtime_plan.get("model") if isinstance(runtime_plan, Mapping) else None)
        if not provider or not model:
            return False
    return True


def _safe_payload(
    request: PipelineGateRequest,
    policy: PipelineGatePolicy,
    pipeline_id: str | None,
    pipeline_session_id: str | None,
) -> dict[str, Any]:
    return {
        "mode": policy.mode.value,
        "pipelines_enabled": policy.enabled,
        "config_valid": policy.config_valid,
        "pipeline_id": pipeline_id,
        "pipeline_session_id": pipeline_session_id,
        "platform": request.platform,
        "platform_allowed": request.platform_allowed,
        "destructive_task": bool(request.destructive_task),
        "explicit_approval": bool(request.explicit_approval),
        "user_message_length": len(request.user_message or ""),
        "user_message_hash": hashlib.sha256((request.user_message or "").encode("utf-8")).hexdigest()[:16],
    }
