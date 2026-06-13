"""Composed profile preview layer for Hermes profile architecture PR-6.

This module is intentionally pure and import-light. It composes the already-
existing validation, routing, approval, scribe handoff, and security review
preview layers into one operator-facing summary without invoking runtime
execution or performing filesystem writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Optional
import json

from hermes_cli.profile_approval import ApprovalPreview, classify_engineer_approval, decision_to_dict as approval_decision_to_dict
from hermes_cli.profile_handoff import (
    HandoffError,
    ScribeHandoffResult,
    preview_scribe_handoff,
    result_to_dict as scribe_result_to_dict,
)
from hermes_cli.profile_routing import (
    RouteDecision,
    RouteHop,
    RoutingError,
    ResolvedModel,
    decision_to_dict as route_decision_to_dict,
    load_model_policy,
    resolve_profile_model,
    route_task,
)
from hermes_cli.profile_security_review import (
    SecurityReviewResult,
    preview_security_review,
    result_to_dict as security_result_to_dict,
)
from hermes_cli.profile_execution import RoleExecutionPlan, build_role_execution_plan, execution_plan_to_dict
from hermes_cli.profile_validation import (
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    ValidationIssue,
    format_issues,
    validate_profile_architecture,
)


class ProfilePreviewError(RuntimeError):
    """Raised when a composed profile preview cannot be produced safely."""


@dataclass
class ProfilePreview:
    task: str
    validation_status: str
    validation_issues: list[dict[str, Any]] = field(default_factory=list)
    execution_plan: dict[str, Any] | None = None
    selected_role: str | None = None
    fallback_used: bool = False
    requires_reviewer: bool = False
    reviewer_profile: str | None = None
    requires_scribe: bool = False
    requires_explicit_approval: bool = False
    ordinary_personal_admin: bool = False
    external_commitment: bool = False
    production_runtime_mutation: bool = False
    review_gate_candidate: bool = False
    sensitive_diff_triggers: list[str] = field(default_factory=list)
    durable_outcome_expected: bool = False
    route_decision: dict[str, Any] | None = None
    model_selection: dict[str, Any] | None = None
    approval_preview: dict[str, Any] | None = None
    security_review_preview: dict[str, Any] | None = None
    scribe_handoff_preview: dict[str, Any] | None = None
    overall_profile_chain: list[str] = field(default_factory=list)
    overall_status: str = "preview_ready"
    blocked_reasons: list[str] = field(default_factory=list)
    required_operator_actions: list[str] = field(default_factory=list)
    write_performed: bool = False
    write_verified: bool = False
    route_error: str | None = None


_STATUS_BLOCKERS = {
    "validation_failed": "blocked_validation_failed",
    "routing_failed": "blocked_routing_failed",
    "security_review_failed": "blocked_security_review_failed",
    "security_review_conditional_pass": "conditional_pending_mitigations",
    "engineer_approval_required": "blocked_pending_approval",
}


def _to_plain_object(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return {key: _to_plain_object(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_plain_object(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_object(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_object(item) for item in value]
    return value


def _validation_issues_to_dicts(issues: list[ValidationIssue]) -> list[dict[str, Any]]:
    return [
        {"severity": issue.severity, "message": issue.message, "path": issue.path}
        for issue in issues
    ]


def _normalize_model_selection(route_decision: RouteDecision | None) -> dict[str, Any] | None:
    if route_decision is None or not route_decision.route_chain:
        return None

    try:
        policy = load_model_policy()
        resolved: ResolvedModel = resolve_profile_model(route_decision.primary_profile, policy)
    except Exception:
        primary_hop = route_decision.route_chain[0]
        selected_model = f"{primary_hop.provider}/{primary_hop.model}" if primary_hop.provider and primary_hop.model else None
        return {
            "profile_id": primary_hop.profile_id,
            "model_tier": primary_hop.model_tier,
            "provider": primary_hop.provider,
            "model": primary_hop.model,
            "selected_model": selected_model,
            "model_resolution_status": primary_hop.model_resolution_status,
            "fallback_status": primary_hop.fallback_status,
            "model_fallback_used": primary_hop.fallback_status in {"fallback_used", "used_fallback", "fallback"},
            "route_hop": route_decision_to_dict(primary_hop),
        }

    primary_hop = route_decision.route_chain[0]
    selected_model = f"{resolved.provider}/{resolved.model}" if resolved.provider and resolved.model else None
    return {
        "profile_id": resolved.profile_id,
        "model_tier": resolved.model_tier,
        "provider": resolved.provider,
        "model": resolved.model,
        "selected_model": selected_model,
        "model_resolution_status": resolved.model_resolution_status,
        "fallback_status": resolved.fallback_status,
        "model_fallback_used": resolved.fallback_status in {"fallback_used", "used_fallback", "fallback"},
        "route_hop": route_decision_to_dict(primary_hop),
    }


def _overall_profile_chain(route_decision: RouteDecision | None) -> list[str]:
    if route_decision is None:
        return []
    seen: set[str] = set()
    chain: list[str] = []
    for hop in route_decision.route_chain:
        if hop.profile_id and hop.profile_id not in seen:
            seen.add(hop.profile_id)
            chain.append(hop.profile_id)
    for profile_id in route_decision.selected_profiles:
        if profile_id and profile_id not in seen:
            seen.add(profile_id)
            chain.append(profile_id)
    if not chain and route_decision.primary_profile:
        chain.append(route_decision.primary_profile)
    return chain


def _derive_blocked_reasons(
    *,
    validation_failed: bool,
    route_error: str | None,
    security_status: str,
    approval_requires_approval: bool,
) -> list[str]:
    reasons: list[str] = []
    if validation_failed:
        reasons.append("validation_failed")
    if route_error:
        reasons.append("routing_failed")
    if security_status == "fail":
        reasons.append("security_review_failed")
    elif security_status == "conditional_pass":
        reasons.append("security_review_conditional_pass")
    if approval_requires_approval:
        reasons.append("engineer_approval_required")
    return reasons


def _derive_required_operator_actions(
    *,
    validation_failed: bool,
    route_error: str | None,
    security_status: str,
    approval_requires_approval: bool,
    requires_scribe: bool,
    external_commitment: bool,
    scribe_write_performed: bool,
) -> list[str]:
    actions: list[str] = []
    if validation_failed:
        actions.append("fix_profile_architecture_validation_issues")
    if route_error:
        actions.append("resolve_routing_failure_or_invalid_input")
    if approval_requires_approval:
        actions.append("approve_or_reject_engineer_mutation")
    if security_status == "fail":
        actions.append("provide_security_evidence_or_mitigations")
    elif security_status == "conditional_pass":
        actions.append("review_required_changes_and_residual_risks")
    if requires_scribe and not scribe_write_performed:
        actions.append("run_scribe_write_if_durable_record_needed")
    if external_commitment:
        actions.append("confirm_external_commitment_before_final_action")
    return actions


def _overall_status(
    *,
    validation_failed: bool,
    route_error: str | None,
    security_status: str,
    approval_requires_approval: bool,
) -> str:
    if validation_failed:
        return "blocked_validation_failed"
    if route_error:
        return "blocked_routing_failed"
    if security_status == "fail":
        return "blocked_security_review_failed"
    if security_status == "conditional_pass":
        return "conditional_pending_mitigations"
    if approval_requires_approval:
        return "blocked_pending_approval"
    return "preview_ready"


def build_profile_preview(
    task: str,
    *,
    validation_issues: Optional[list[ValidationIssue | dict[str, Any]]] = None,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    security_review_preview: SecurityReviewResult | dict[str, Any] | None = None,
    scribe_handoff_preview: ScribeHandoffResult | dict[str, Any] | None = None,
    execution_plan: RoleExecutionPlan | dict[str, Any] | None = None,
    model_selection: dict[str, Any] | None = None,
    route_error: str | None = None,
) -> ProfilePreview:
    if not isinstance(task, str) or not task.strip():
        raise ProfilePreviewError("task must be a non-empty string")

    normalized_validation_issues: list[dict[str, Any]] = []
    if validation_issues:
        for issue in validation_issues:
            if isinstance(issue, ValidationIssue):
                normalized_validation_issues.append({"severity": issue.severity, "message": issue.message, "path": issue.path})
            elif isinstance(issue, dict):
                normalized_validation_issues.append(
                    {
                        "severity": str(issue.get("severity", "error")),
                        "message": str(issue.get("message", "")),
                        "path": str(issue.get("path", "")),
                    }
                )
            else:
                normalized_validation_issues.append({"severity": "error", "message": str(issue), "path": ""})

    validation_failed = bool(normalized_validation_issues)
    validation_status = "passed" if not validation_failed else "failed"

    route_obj: RouteDecision | None = None
    route_decision_dict: dict[str, Any] | None = None
    approval_dict: dict[str, Any] | None = None
    security_dict: dict[str, Any] | None = None
    scribe_dict: dict[str, Any] | None = None
    execution_plan_dict: dict[str, Any] | None = None
    overall_chain: list[str] = []
    approval_requires_approval = False
    security_status = "not_applicable"
    scribe_write_performed = False

    if not validation_failed and route_decision is not None:
        if isinstance(route_decision, RouteDecision):
            route_obj = route_decision
        elif isinstance(route_decision, dict):
            route_obj = _dict_to_route_decision(route_decision)
        else:
            raise ProfilePreviewError("route_decision must be a RouteDecision or mapping")

        route_decision_dict = route_decision_to_dict(route_obj)
        overall_chain = _overall_profile_chain(route_obj)
        if model_selection is None:
            model_selection = _normalize_model_selection(route_obj)
    elif not validation_failed and route_decision is None and route_error is None:
        pass

    if execution_plan is not None:
        execution_plan_dict = execution_plan_to_dict(execution_plan) if isinstance(execution_plan, RoleExecutionPlan) else _to_plain_object(execution_plan)
    elif route_obj is not None:
        execution_plan_dict = execution_plan_to_dict(build_role_execution_plan(task, route_decision=route_obj))

    if execution_plan_dict is None and route_obj is not None:
        execution_plan_dict = execution_plan_to_dict(build_role_execution_plan(task, route_decision=route_obj))

    selected_role = str(execution_plan_dict.get("selected_role")) if execution_plan_dict else None
    fallback_used = bool(execution_plan_dict.get("fallback_used", False)) if execution_plan_dict else False
    requires_reviewer = bool(execution_plan_dict.get("requires_reviewer", False)) if execution_plan_dict else False
    reviewer_profile = execution_plan_dict.get("reviewer_profile") if execution_plan_dict else None
    requires_scribe = bool(execution_plan_dict.get("requires_scribe", False)) if execution_plan_dict else False
    requires_explicit_approval = bool(execution_plan_dict.get("requires_explicit_approval", False)) if execution_plan_dict else False
    ordinary_personal_admin = bool(execution_plan_dict.get("ordinary_personal_admin", False)) if execution_plan_dict else False
    external_commitment = bool(execution_plan_dict.get("external_commitment", False)) if execution_plan_dict else False
    production_runtime_mutation = bool(execution_plan_dict.get("production_runtime_mutation", False)) if execution_plan_dict else False
    review_gate_candidate = bool(execution_plan_dict.get("review_gate_candidate", False)) if execution_plan_dict else False
    sensitive_diff_triggers = list(execution_plan_dict.get("sensitive_diff_triggers", []) or []) if execution_plan_dict else []
    durable_outcome_expected = bool(execution_plan_dict.get("durable_outcome_expected", False)) if execution_plan_dict else False

    if approval_preview is not None:
        approval_dict = approval_decision_to_dict(approval_preview) if isinstance(approval_preview, ApprovalPreview) else _to_plain_object(approval_preview)
        approval_requires_approval = bool(approval_dict.get("requires_approval"))

    if security_review_preview is not None:
        security_dict = security_result_to_dict(security_review_preview) if isinstance(security_review_preview, SecurityReviewResult) else _to_plain_object(security_review_preview)
        security_status = str(security_dict.get("review", {}).get("security_review_status", security_dict.get("security_review_status", "not_applicable")))

    if scribe_handoff_preview is not None:
        scribe_dict = scribe_result_to_dict(scribe_handoff_preview) if isinstance(scribe_handoff_preview, ScribeHandoffResult) else _to_plain_object(scribe_handoff_preview)
        scribe_write_performed = bool(scribe_dict.get("write_performed", False))

    overall_status = _overall_status(
        validation_failed=validation_failed,
        route_error=route_error,
        security_status=security_status,
        approval_requires_approval=approval_requires_approval,
    )
    blocked_reasons = _derive_blocked_reasons(
        validation_failed=validation_failed,
        route_error=route_error,
        security_status=security_status,
        approval_requires_approval=approval_requires_approval,
    )
    required_operator_actions = _derive_required_operator_actions(
        validation_failed=validation_failed,
        route_error=route_error,
        security_status=security_status,
        approval_requires_approval=approval_requires_approval,
        requires_scribe=requires_scribe,
        external_commitment=external_commitment,
        scribe_write_performed=scribe_write_performed,
    )

    return ProfilePreview(
        task=task.strip(),
        validation_status=validation_status,
        validation_issues=normalized_validation_issues,
        execution_plan=execution_plan_dict,
        selected_role=selected_role,
        fallback_used=fallback_used,
        requires_reviewer=requires_reviewer,
        reviewer_profile=reviewer_profile,
        requires_scribe=requires_scribe,
        requires_explicit_approval=requires_explicit_approval,
        ordinary_personal_admin=ordinary_personal_admin,
        external_commitment=external_commitment,
        production_runtime_mutation=production_runtime_mutation,
        review_gate_candidate=review_gate_candidate,
        sensitive_diff_triggers=sensitive_diff_triggers,
        durable_outcome_expected=durable_outcome_expected,
        route_decision=route_decision_dict,
        model_selection=model_selection,
        approval_preview=approval_dict,
        security_review_preview=security_dict,
        scribe_handoff_preview=scribe_dict,
        overall_profile_chain=overall_chain,
        overall_status=overall_status,
        blocked_reasons=blocked_reasons,
        required_operator_actions=required_operator_actions,
        write_performed=False,
        write_verified=False,
        route_error=route_error,
    )


def _dict_to_route_decision(data: dict[str, Any]) -> RouteDecision:
    route_chain = []
    for raw_hop in data.get("route_chain", []) or []:
        if not isinstance(raw_hop, dict):
            raise ProfilePreviewError("route_decision route_chain entries must be mappings")
        route_chain.append(
            RouteHop(
                profile_id=str(raw_hop.get("profile_id", "")),
                routing_reason=str(raw_hop.get("routing_reason", "")),
                model_tier=str(raw_hop.get("model_tier", "unknown")),
                provider=str(raw_hop.get("provider", "")),
                model=str(raw_hop.get("model", "")),
                escalation_reason=str(raw_hop.get("escalation_reason", "")),
                model_resolution_status=str(raw_hop.get("model_resolution_status", "unknown")),
                fallback_status=str(raw_hop.get("fallback_status", "unknown")),
            )
        )
    return RouteDecision(
        request_text=str(data.get("request_text", "")),
        coordinator_profile=str(data.get("coordinator_profile", "chief_hermes")),
        primary_profile=str(data.get("primary_profile", "unknown")),
        selected_profiles=[str(item) for item in data.get("selected_profiles", []) or []],
        route_chain=route_chain,
        route_reason=str(data.get("route_reason", "")),
        validation_status=str(data.get("validation_status", "unknown")),
        confidence=str(data.get("confidence", "unknown")),
        ambiguity_reasons=[str(item) for item in data.get("ambiguity_reasons", []) or []],
        max_chain_limit_applied=bool(data.get("max_chain_limit_applied", False)),
    )


def preview_profile(
    task: str,
    *,
    security_evidence: Optional[list[Any]] = None,
    security_required_changes: Optional[list[str]] = None,
    security_residual_risks: Optional[list[str]] = None,
) -> ProfilePreview:
    if not isinstance(task, str) or not task.strip():
        raise ProfilePreviewError("task must be a non-empty string")

    validation_issues = validate_profile_architecture(DEFAULT_PROFILE_REGISTRY_PATH, DEFAULT_MODEL_POLICY_PATH)
    if validation_issues:
        return build_profile_preview(task, validation_issues=validation_issues)

    try:
        route_decision = route_task(task)
    except RoutingError as exc:
        return build_profile_preview(task, route_error=str(exc))

    execution_plan = build_role_execution_plan(task, route_decision=route_decision)

    approval_preview = None
    if execution_plan.selected_role == "engineer" or execution_plan.requires_explicit_approval or execution_plan.production_runtime_mutation:
        approval_preview = classify_engineer_approval(task, route_decision=route_decision)

    security_review_preview = None
    if execution_plan.requires_reviewer or execution_plan.selected_role == "security_auditor":
        security_review_preview = preview_security_review(
            task,
            route_decision=route_decision,
            approval_preview=approval_preview,
            evidence=security_evidence,
            required_changes=security_required_changes,
            residual_risks=security_residual_risks,
            write=False,
        )

    scribe_handoff_preview = None
    if execution_plan.requires_scribe or execution_plan.selected_role == "scribe":
        scribe_handoff_preview = preview_scribe_handoff(
            task,
            route_decision=route_decision,
            approval_preview=approval_preview,
            write=False,
        )

    model_selection = _normalize_model_selection(route_decision)

    return build_profile_preview(
        task,
        validation_issues=[],
        route_decision=route_decision,
        approval_preview=approval_preview,
        security_review_preview=security_review_preview,
        scribe_handoff_preview=scribe_handoff_preview,
        execution_plan=execution_plan,
        model_selection=model_selection,
    )


def preview_to_dict(preview: ProfilePreview) -> dict[str, Any]:
    if not isinstance(preview, ProfilePreview):
        raise ProfilePreviewError("preview_to_dict expects a ProfilePreview")
    return _to_plain_object(asdict(preview))


def preview_to_json(preview: ProfilePreview) -> str:
    return json.dumps(preview_to_dict(preview), ensure_ascii=False, indent=2)
