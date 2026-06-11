"""Compact role-context helpers for Hermes Profile Architecture.

This module stays pure and import-light: it loads profile contracts from the
registry, resolves runtime aliases to canonical role IDs, and renders a short
role_context block for task execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from hermes_cli.profile_execution import RoleExecutionPlan, build_role_execution_plan
from hermes_cli.profile_routing import RouteDecision, route_task, load_profile_registry, DEFAULT_PROFILE_REGISTRY_PATH
from hermes_cli.profile_validation import PROFILE_ID_ALIASES
from utils import env_var_enabled


@dataclass(frozen=True)
class RoleContextResult:
    task: str
    selected_role: str
    canonical_role: str | None
    context_text: str
    profile_context_used: bool
    fallback_reason: str = ""
    profile_status: str = ""
    requires_reviewer: bool = False
    reviewer_profile: str | None = None
    requires_explicit_approval: bool = False
    critical_approval_required: bool = False
    approval_reason: str = ""
    operation_category: str = ""


@lru_cache(maxsize=4)
def _load_profile_registry_cached(registry_path: str) -> dict[str, Any]:
    return load_profile_registry(Path(registry_path))


def load_profile_contracts(registry_path: Path | str = DEFAULT_PROFILE_REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    """Load profile_contract records keyed by canonical role ID."""
    registry = _load_profile_registry_cached(str(Path(registry_path)))
    contracts: dict[str, dict[str, Any]] = {}

    for group_name in ("profiles", "deferred_profiles"):
        for profile in registry.get(group_name, []) or []:
            if not isinstance(profile, dict):
                continue
            contract = profile.get("profile_contract")
            canonical_id = contract.get("canonical_id") if isinstance(contract, dict) else None
            if not isinstance(canonical_id, str) or not canonical_id.strip():
                continue
            contracts[canonical_id] = {
                "runtime_id": profile.get("id"),
                "status": profile.get("status") or "active",
                "profile_contract": contract,
            }
    return contracts


def _canonical_role_id(role_id: str | None) -> str | None:
    if not isinstance(role_id, str) or not role_id.strip():
        return None
    return PROFILE_ID_ALIASES.get(role_id, role_id)


def get_profile_contract(
    role_id: str | None,
    *,
    contracts: dict[str, dict[str, Any]] | None = None,
    registry_path: Path | str = DEFAULT_PROFILE_REGISTRY_PATH,
) -> dict[str, Any] | None:
    canonical_role = _canonical_role_id(role_id)
    if canonical_role is None:
        return None
    contract_map = contracts if contracts is not None else load_profile_contracts(registry_path)
    record = contract_map.get(canonical_role)
    if not record:
        return None
    contract = record.get("profile_contract")
    return contract if isinstance(contract, dict) else None


def _compact_list(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    items = [str(item).strip() for item in values if isinstance(item, str) and item.strip()]
    return ", ".join(items)


def render_role_context(
    profile_contract: dict[str, Any] | None,
    *,
    selected_role: str,
    canonical_role: str | None = None,
    execution_plan: RoleExecutionPlan | None = None,
    route_decision: RouteDecision | None = None,
    profile_status: str = "",
) -> str:
    """Render a compact role guidance block for the current task turn."""
    if not isinstance(profile_contract, dict):
        return ""

    display_name = str(profile_contract.get("display_name") or canonical_role or selected_role).strip()
    purpose = str(profile_contract.get("purpose_summary") or "").strip()
    personality = _compact_list(profile_contract.get("personality_summary"))

    tool_contract = profile_contract.get("tool_contract") if isinstance(profile_contract.get("tool_contract"), dict) else {}
    allowed_by_default = _compact_list(tool_contract.get("allowed_by_default"))
    allowed_with_confirmation = _compact_list(tool_contract.get("allowed_with_confirmation"))
    forbidden = _compact_list(tool_contract.get("forbidden"))

    escalation_targets = _compact_list(profile_contract.get("escalation_targets"))

    lines: list[str] = [f"You are acting as Hermes role: {display_name}."]
    if profile_status:
        lines.append(f"Status: {profile_status}.")
    elif canonical_role == "trading_observer_trader_deferred" or selected_role == "trading_observer_trader":
        lines.append("Status: deferred/inactive.")

    if purpose:
        lines.extend(["", "Purpose:", purpose])
    if personality:
        lines.extend(["", "Personality:", personality])

    boundary_bits: list[str] = []
    if allowed_by_default:
        boundary_bits.append(f"allowed by default: {allowed_by_default}")
    if allowed_with_confirmation:
        boundary_bits.append(f"allowed with confirmation: {allowed_with_confirmation}")
    if forbidden:
        boundary_bits.append(f"forbidden: {forbidden}")
    if escalation_targets:
        boundary_bits.append(f"escalate to: {escalation_targets}")

    if canonical_role == "security_auditor":
        boundary_bits.append("Security Auditor is a reviewer, not a universal blocker.")
    elif canonical_role == "scribe":
        boundary_bits.append("Use Scribe only for meaningful durable outcomes; do not create noise.")
    elif canonical_role == "general_operator":
        boundary_bits.append("External commitments require confirmation before final action.")
    elif canonical_role == "engineer":
        boundary_bits.append("Repo/code mutation is allowed. Production/runtime mutation requires explicit approval.")
    elif canonical_role == "trading_observer_trader_deferred":
        boundary_bits.append("Trading remains deferred/inactive; do not initiate trading execution.")

    if boundary_bits:
        lines.extend(["", "Boundaries:", *[f"- {bit}" for bit in boundary_bits]])

    output_style = {
        "general_operator": "Short practical plan, missing info if needed, confirmation request before external commitment, final confirmation after action.",
        "engineer": "What changed, tests run, risks, next step, rollback note when applicable.",
        "security_auditor": "State the security evidence, the risk, whether a reviewer is required, and the safest next step.",
        "scribe": "Record only durable outcomes that matter; avoid noisy artifacts.",
        "researcher": "Summarize evidence, cite source quality, and call out uncertainty explicitly.",
        "career_strategist": "Give crisp job strategy, trade-offs, and next action.",
        "trading_observer_trader_deferred": "Keep trading inactive and report only observations; no execution.",
    }.get(canonical_role or selected_role, "Be concise, concrete, and guidance-only.")

    lines.extend(["", "Output style:", output_style])

    if execution_plan is not None:
        if execution_plan.requires_reviewer:
            lines.append("Reviewer policy: Security Auditor is conditional, not universal.")
        if execution_plan.requires_scribe:
            lines.append("Scribe policy: invoke only for durable outcomes when useful.")
        if execution_plan.requires_explicit_approval:
            lines.append("Approval policy: explicit approval remains required when the task triggers it.")
    if route_decision is not None and route_decision.confidence:
        lines.append(f"Routing confidence: {route_decision.confidence}.")

    return "\n".join(lines).strip()


def _role_execution_debug_header_enabled() -> bool:
    return env_var_enabled("HERMES_PROFILE_DEBUG_HEADER")


def render_role_execution_debug_header(result: RoleContextResult | None) -> str:
    """Render a compact debug header for smoke validation, gated by env flag."""
    if not _role_execution_debug_header_enabled():
        return ""
    if not isinstance(result, RoleContextResult):
        return ""

    selected_role = (result.selected_role or "").strip() or "unknown"
    canonical_role = (result.canonical_role or "").strip()
    lines = [f"Hermes role: {selected_role}"]
    if canonical_role and canonical_role != selected_role:
        lines.append(f"Canonical role: {canonical_role}")
    lines.append(f"Role context: {'used' if result.profile_context_used else 'missing'}")
    lines.append(f"Reviewer: {result.reviewer_profile or 'none'}")
    lines.append(f"Approval: {'required' if result.requires_explicit_approval else 'not_required'}")
    if result.operation_category:
        lines.append(f"Operation category: {result.operation_category}")
    if result.profile_context_used is False and result.fallback_reason:
        lines.append(f"Fallback reason: {result.fallback_reason}")
    return "\n".join(lines)


def inject_role_execution_debug_header(response_text: str, result: RoleContextResult | None) -> str:
    """Prefix a user-facing response with the opt-in role debug header."""
    if not isinstance(response_text, str) or not response_text.strip():
        return response_text
    header = render_role_execution_debug_header(result)
    if not header:
        return response_text
    return header + "\n\n" + response_text


def role_debug_header_enabled() -> bool:
    """Backward-compatible alias for the opt-in debug-header flag."""
    return _role_execution_debug_header_enabled()


def render_role_debug_header(result: RoleContextResult | None) -> str:
    """Backward-compatible alias for the opt-in debug header renderer."""
    return render_role_execution_debug_header(result)


def _planned_action_lines(task_text: str) -> list[str]:
    if not isinstance(task_text, str):
        return []
    items: list[str] = []
    for raw_line in task_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.lstrip("-* ").strip()
        if not line:
            continue
        items.append(line)
        if len(items) >= 5:
            break
    if items:
        return items
    compact = " ".join(task_text.split())
    if not compact:
        return []
    return [compact[:220] + ("..." if len(compact) > 220 else "")]


def render_explicit_approval_request(
    result: RoleContextResult | None,
    *,
    task_text: str = "",
) -> str:
    """Render the user-facing approval request for critical mutations."""
    if not isinstance(result, RoleContextResult):
        return ""

    lines = ["I need explicit approval before any mutation-capable changes."]
    planned_action = _planned_action_lines(task_text)
    if planned_action:
        lines.extend(["", "Planned action:"])
        lines.extend(f"- {item}" for item in planned_action)
    if result.approval_reason:
        lines.extend(["", "Why approval is required:", f"- {result.approval_reason}"])
    lines.extend(
        [
            "",
            "I will stop here before file writes, runtime changes, or external system mutations.",
            'Reply with explicit approve if you want me to proceed, or adjust the scope.',
        ]
    )
    return "\n".join(lines).strip()


def build_role_context_for_task(
    task: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    execution_plan: RoleExecutionPlan | dict[str, Any] | None = None,
    registry_path: Path | str = DEFAULT_PROFILE_REGISTRY_PATH,
) -> RoleContextResult:
    """Build a compact role-context block for a task, failing soft on errors."""
    if not isinstance(task, str) or not task.strip():
        return RoleContextResult(
            task="",
            selected_role="general_operator",
            canonical_role="general_operator",
            context_text="",
            profile_context_used=False,
            fallback_reason="task must be a non-empty string",
            requires_reviewer=False,
            reviewer_profile=None,
            requires_explicit_approval=False,
            critical_approval_required=False,
            approval_reason="",
            operation_category="",
        )

    try:
        resolved_route = route_decision if isinstance(route_decision, RouteDecision) else route_task(task, registry_path=registry_path)
    except Exception as exc:
        return RoleContextResult(
            task=task.strip(),
            selected_role="general_operator",
            canonical_role="general_operator",
            context_text="",
            profile_context_used=False,
            fallback_reason=f"routing failed: {exc}",
            requires_reviewer=False,
            reviewer_profile=None,
            requires_explicit_approval=False,
            critical_approval_required=False,
            approval_reason="",
            operation_category="",
        )

    try:
        resolved_plan = execution_plan if isinstance(execution_plan, RoleExecutionPlan) else build_role_execution_plan(task, route_decision=resolved_route)
    except Exception as exc:
        return RoleContextResult(
            task=task.strip(),
            selected_role=getattr(resolved_route, "primary_profile", "general_operator"),
            canonical_role=_canonical_role_id(getattr(resolved_route, "primary_profile", "general_operator")),
            context_text="",
            profile_context_used=False,
            fallback_reason=f"execution plan failed: {exc}",
            requires_reviewer=False,
            reviewer_profile=None,
            requires_explicit_approval=False,
            critical_approval_required=False,
            approval_reason="",
            operation_category="",
        )

    selected_role = resolved_plan.selected_role
    canonical_role = _canonical_role_id(selected_role)
    contracts = load_profile_contracts(registry_path)
    contract = get_profile_contract(selected_role, contracts=contracts, registry_path=registry_path)
    if contract is None and canonical_role:
        contract = get_profile_contract(canonical_role, contracts=contracts, registry_path=registry_path)

    if contract is None:
        return RoleContextResult(
            task=task.strip(),
            selected_role=selected_role,
            canonical_role=canonical_role,
            context_text="",
            profile_context_used=False,
            fallback_reason=f"profile contract missing for {selected_role}",
            profile_status="",
            requires_reviewer=resolved_plan.requires_reviewer,
            reviewer_profile=resolved_plan.reviewer_profile,
            requires_explicit_approval=resolved_plan.requires_explicit_approval,
            critical_approval_required=resolved_plan.critical_approval_required,
            approval_reason=resolved_plan.approval_reason,
            operation_category=resolved_plan.operation_category,
        )

    record = contracts.get(canonical_role or "", {})
    profile_status = str(record.get("status") or "") if isinstance(record, dict) else ""
    context_text = render_role_context(
        contract,
        selected_role=selected_role,
        canonical_role=canonical_role,
        execution_plan=resolved_plan,
        route_decision=resolved_route,
        profile_status=profile_status,
    )
    if not context_text:
        return RoleContextResult(
            task=task.strip(),
            selected_role=selected_role,
            canonical_role=canonical_role,
            context_text="",
            profile_context_used=False,
            fallback_reason=f"role context render failed for {selected_role}",
            profile_status=profile_status,
            requires_reviewer=resolved_plan.requires_reviewer,
            reviewer_profile=resolved_plan.reviewer_profile,
            requires_explicit_approval=resolved_plan.requires_explicit_approval,
            critical_approval_required=resolved_plan.critical_approval_required,
            approval_reason=resolved_plan.approval_reason,
            operation_category=resolved_plan.operation_category,
        )

    return RoleContextResult(
        task=task.strip(),
        selected_role=selected_role,
        canonical_role=canonical_role,
        context_text=context_text,
        profile_context_used=True,
        fallback_reason="",
        profile_status=profile_status,
        requires_reviewer=resolved_plan.requires_reviewer,
        reviewer_profile=resolved_plan.reviewer_profile,
        requires_explicit_approval=resolved_plan.requires_explicit_approval,
        critical_approval_required=resolved_plan.critical_approval_required,
        approval_reason=resolved_plan.approval_reason,
        operation_category=resolved_plan.operation_category,
    )
