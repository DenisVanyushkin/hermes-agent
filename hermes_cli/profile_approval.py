"""Preview-only Engineer approval gate skeleton for Hermes Profile Architecture PR-3A.

This module is intentionally pure and import-light. It classifies requested
changes and produces approval metadata without executing any runtime actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional
import json
import string

from hermes_cli.profile_routing import RouteDecision, route_task


class ApprovalError(RuntimeError):
    """Raised when approval classification cannot be produced safely."""


@dataclass(frozen=True)
class ApprovalRequest:
    """Structured request inputs for preview-only Engineer approval review."""

    task_text: str
    target_host: Optional[str] = None
    target_service: Optional[str] = None
    intended_change: Optional[str] = None
    commands_or_control_script: Optional[str] = None
    expected_effect: Optional[str] = None
    risk: Optional[str] = None
    rollback_plan: Optional[str] = None
    evidence_before: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ApprovalPreview:
    """Flattened preview result returned by the Engineer approval classifier."""

    coordinator_profile: str
    primary_profile: str
    selected_profiles: list[str]
    route_reason: str
    route_validation_status: str
    route_confidence: str
    profile: str
    action_type: str
    target_host: Optional[str]
    target_service: Optional[str]
    intended_change: Optional[str]
    commands_or_control_script: Optional[str]
    expected_effect: Optional[str]
    risk: Optional[str]
    rollback_plan: Optional[str]
    evidence_before: list[str]
    requires_approval: bool
    blocked_until_approved: bool
    classification_reason: str
    confidence: str
    ambiguity_reasons: list[str]
    approval_applicability: str


_APPROVAL_APPLICABILITY_VALUES = {"applicable", "not_applicable"}

_MUTATION_KEYWORDS = {
    "deploy": ("deploy", "rollout", "ship"),
    "rollback": ("rollback", "roll back", "revert", "undo deploy", "undo rollout"),
    "service_control": (
        "service start",
        "service stop",
        "service restart",
        "service reload",
        "start service",
        "stop service",
        "restart service",
        "reload service",
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl reload",
        "start the service",
        "stop the service",
        "restart the service",
        "reload the service",
        "restart webui",
        "reload webui",
        "start webui",
        "stop webui",
        "restart hermes webui",
        "reload hermes webui",
        "start hermes webui",
        "stop hermes webui",
    ),
    "production_config_change": (
        "production config",
        "prod config",
        "config change",
        "update config",
        "edit config",
        "modify config",
        "change config",
        "production settings",
        "prod settings",
        "reconfigure production",
    ),
    "production_db_migration_or_repair": (
        "database migration",
        "db migration",
        "production db migration",
        "database repair",
        "db repair",
        "repair database",
        "repair db",
        "schema migration",
        "migrate production database",
        "fix production database",
        "repair the production database",
        "repair the production db",
        "production database repair",
        "production db repair",
    ),
    "public_exposure_change": (
        "cloudflare",
        "firewall",
        "reverse proxy",
        "public exposure",
        "expose to public",
        "publicly expose",
        "open to internet",
        "open port",
        "publish publicly",
    ),
    "scheduler_timer_change": (
        "scheduler",
        "timer",
        "timers",
        "cron",
        "scheduled task",
        "schedule change",
        "timer change",
        "cron change",
    ),
    "tool_permission_change": (
        "tool permission",
        "tool permissions",
        "tool access",
        "allowlist",
        "denylist",
        "permission change",
        "permissions change",
        "grant tool",
        "revoke tool",
    ),
    "auth_secret_handling_change": (
        "auth change",
        "auth handling",
        "secret handling",
        "secrets handling",
        "secret change",
        "secrets change",
        "secret rotation",
        "rotate secret",
        "rotate secrets",
        "token rotation",
        "rotate token",
        "credential rotation",
        "rotate credential",
        "update auth",
        "update secrets",
        "update secret",
        "update token",
        "credential change",
        "rotate auth secret",
        "auth secret",
        "auth secrets",
        "update credentials",
        "credential update",
    ),
}

_READ_ONLY_KEYWORDS = {
    "status": ("status", "check status", "show status", "read status", "проверь статус", "статус webui"),
    "health_check": ("health check", "health", "check health", "show health", "проверка здоровья"),
    "log_inspection": ("inspect logs", "log inspection", "read logs", "view logs", "show logs", "show webui logs", "проверь логи", "посмотри логи", "логи", "logs"),
    "config_read": ("config read", "read config", "view config", "show config"),
    "git_status_diff_read": ("git status", "git diff", "diff read", "read diff"),
    "systemctl_status": ("systemctl status",),
    "docker_inspect": ("docker ps", "docker logs", "docker inspect", "container inspect"),
    "smoke_check": ("smoke", "smoke check", "smoke-test", "smoketest"),
}

_MUTATION_VERBS = (
    "deploy",
    "rollback",
    "restart",
    "reload",
    "start",
    "stop",
    "change",
    "update",
    "modify",
    "edit",
    "repair",
    "migrate",
    "expose",
    "open",
    "publish",
    "grant",
    "revoke",
    "rotate",
    "reconfigure",
    "enable",
    "disable",
)

_MUTATION_OBJECT_HINTS = (
    "webui",
    "service",
    "config",
    "database",
    "db",
    "cloudflare",
    "firewall",
    "reverse proxy",
    "public",
    "exposure",
    "scheduler",
    "timer",
    "cron",
    "tool",
    "permission",
    "auth",
    "secret",
    "secrets",
    "token",
    "credential",
    "port",
    "service",
)


def _normalize(text: str) -> str:
    translated = text.lower().translate(str.maketrans({ch: " " for ch in string.punctuation}))
    return " ".join(translated.split())


def _contains_any(normalized_text: str, phrases: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for phrase in phrases:
        if _normalize(phrase) in normalized_text:
            hits.append(phrase)
    return hits


def _coerce_text(value: Optional[str]) -> str:
    return value if isinstance(value, str) else ""


def _join_evidence(evidence_before: list[str]) -> str:
    return " \n ".join(item for item in evidence_before if isinstance(item, str))


def _build_context_map(request: ApprovalRequest) -> dict[str, str]:
    return {
        "task_text": request.task_text,
        "target_host": _coerce_text(request.target_host),
        "target_service": _coerce_text(request.target_service),
        "intended_change": _coerce_text(request.intended_change),
        "commands_or_control_script": _coerce_text(request.commands_or_control_script),
        "expected_effect": _coerce_text(request.expected_effect),
        "risk": _coerce_text(request.risk),
        "rollback_plan": _coerce_text(request.rollback_plan),
        "evidence_before": _join_evidence(request.evidence_before),
    }


def _combine_context(context_map: dict[str, str]) -> str:
    return _normalize(" \n ".join(value for value in context_map.values() if value))


def _field_matches(context_map: dict[str, str], phrases: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for field_name, text in context_map.items():
        if not text:
            continue
        normalized = _normalize(text)
        for phrase in phrases:
            normalized_phrase = _normalize(phrase)
            if normalized_phrase and normalized_phrase in normalized:
                hits.append(f"{field_name}:{phrase}")
    return hits


def _has_mutation_signature(normalized_text: str) -> bool:
    has_verb = any(verb in normalized_text for verb in _MUTATION_VERBS)
    has_object = any(hint in normalized_text for hint in _MUTATION_OBJECT_HINTS)
    if has_verb and has_object:
        return True
    for category, phrases in _MUTATION_KEYWORDS.items():
        if _contains_any(normalized_text, phrases):
            return True
    return False


def _detect_mutation_hits(context_map: dict[str, str], normalized_text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for category, phrases in _MUTATION_KEYWORDS.items():
        field_hits = _field_matches(context_map, phrases)
        if field_hits:
            hits[category] = field_hits
    if not hits and _has_mutation_signature(normalized_text):
        hits["mutation_unknown"] = ["combined-context mutation signature"]
    return hits


def _detect_readonly_hits(context_map: dict[str, str], normalized_text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for category, phrases in _READ_ONLY_KEYWORDS.items():
        field_hits = _field_matches(context_map, phrases)
        if field_hits:
            hits[category] = field_hits
    if "smoke" in normalized_text and not hits.get("smoke_check"):
        hits.setdefault("smoke_check", ["combined-context smoke reference"])
    return hits


def _select_action_type(mutation_hits: dict[str, list[str]], readonly_hits: dict[str, list[str]], normalized_text: str) -> str:
    priority = (
        "deploy",
        "rollback",
        "service_control",
        "production_config_change",
        "production_db_migration_or_repair",
        "public_exposure_change",
        "scheduler_timer_change",
        "tool_permission_change",
        "auth_secret_handling_change",
    )
    for category in priority:
        if mutation_hits.get(category):
            return category

    if mutation_hits:
        return "mutation_unknown"

    readonly_priority = (
        "smoke_check",
        "git_status_diff_read",
        "systemctl_status",
        "docker_inspect",
        "config_read",
        "health_check",
        "log_inspection",
        "status",
    )
    for category in readonly_priority:
        if readonly_hits.get(category):
            return category

    if mutation_hits:
        return "mutation_unknown"
    if "smoke" in normalized_text:
        return "smoke_check"
    if readonly_hits:
        return "read_only_unknown"
    return "unclear"


def _route_is_engineer_related(route_decision: RouteDecision) -> bool:
    return route_decision.primary_profile == "engineer" or "engineer" in route_decision.selected_profiles


def _approval_applicability(route_decision: RouteDecision) -> str:
    return "applicable" if _route_is_engineer_related(route_decision) else "not_applicable"


def _derive_risk(request: ApprovalRequest, requires_approval: bool, applicability: str) -> Optional[str]:
    if request.risk:
        return request.risk
    if applicability == "not_applicable":
        return "low"
    return "high" if requires_approval else "low"


def _build_classification_reason(
    *,
    route_decision: RouteDecision,
    action_type: str,
    requires_approval: bool,
    applicability: str,
    mutation_hits: dict[str, list[str]],
    readonly_hits: dict[str, list[str]],
    ambiguity_reasons: list[str],
) -> str:
    if applicability == "not_applicable":
        return (
            "Engineer approval gate does not apply to the selected route "
            f"(primary_profile={route_decision.primary_profile}, selected_profiles={route_decision.selected_profiles})."
        )

    if requires_approval:
        if mutation_hits:
            mutation_summary = ", ".join(mutation_hits.keys())
            return f"Engineer route selected and mutation-class action detected ({mutation_summary}); approval required."
        if ambiguity_reasons:
            return f"Engineer route selected but action is ambiguous; approval required. {'; '.join(ambiguity_reasons)}"
        return "Engineer route selected and approval is required by fail-closed policy."

    if readonly_hits:
        readonly_summary = ", ".join(readonly_hits.keys())
        return f"Engineer route selected, but action is read-only ({readonly_summary}); approval not required."

    return f"Engineer route selected, but action type {action_type!r} is non-mutating; approval not required."


def _build_ambiguity_reasons(
    mutation_hits: dict[str, list[str]],
    readonly_hits: dict[str, list[str]],
    context_map: dict[str, str],
) -> list[str]:
    ambiguity_reasons: list[str] = []
    if mutation_hits and readonly_hits:
        ambiguity_reasons.append("mixed mutation and read-only signals present")
    if mutation_hits and "smoke_check" in readonly_hits:
        ambiguity_reasons.append("smoke appears alongside mutation-class signals")
    if not mutation_hits and not readonly_hits:
        ambiguity_reasons.append("no clear mutation or read-only signal matched")
    if "commands_or_control_script" in context_map and context_map["commands_or_control_script"]:
        normalized_commands = _normalize(context_map["commands_or_control_script"])
        if any(verb in normalized_commands for verb in ("restart", "reload", "deploy", "rollback", "update", "change", "repair", "migrate", "expose", "publish", "open")):
            ambiguity_reasons.append("structured field commands_or_control_script contains mutation cues")
    if "intended_change" in context_map and context_map["intended_change"]:
        normalized_change = _normalize(context_map["intended_change"])
        if any(
            phrase in normalized_change
            for phrase in (
                "production config",
                "config change",
                "production db",
                "database migration",
                "db migration",
                "cloudflare",
                "firewall",
                "reverse proxy",
                "public exposure",
                "scheduler",
                "timer",
                "tool permission",
                "auth",
                "secret",
                "token",
                "credential",
            )
        ):
            ambiguity_reasons.append("structured field intended_change contains mutation cues")
    return ambiguity_reasons


def build_approval_request(route_decision: RouteDecision, request: ApprovalRequest) -> ApprovalPreview:
    if not isinstance(route_decision, RouteDecision):
        raise ApprovalError("route_decision must be a RouteDecision")
    if not isinstance(request, ApprovalRequest):
        raise ApprovalError("request must be an ApprovalRequest")
    if route_decision.selected_profiles is None:
        raise ApprovalError("route_decision is malformed: selected_profiles missing")

    applicability = _approval_applicability(route_decision)
    context_map = _build_context_map(request)
    normalized_text = _combine_context(context_map)
    mutation_hits = _detect_mutation_hits(context_map, normalized_text)
    readonly_hits = _detect_readonly_hits(context_map, normalized_text)
    action_type = _select_action_type(mutation_hits, readonly_hits, normalized_text)
    ambiguity_reasons = _build_ambiguity_reasons(mutation_hits, readonly_hits, context_map)

    if applicability == "not_applicable":
        requires_approval = False
        blocked_until_approved = False
        confidence = "high" if readonly_hits or mutation_hits else "medium"
    else:
        if mutation_hits:
            requires_approval = True
            blocked_until_approved = True
            confidence = "high" if len(mutation_hits) == 1 and not ambiguity_reasons else "medium"
        elif readonly_hits and not ambiguity_reasons:
            requires_approval = False
            blocked_until_approved = False
            confidence = "high"
        else:
            requires_approval = True
            blocked_until_approved = True
            confidence = "low" if not readonly_hits else "medium"
            if not ambiguity_reasons:
                ambiguity_reasons.append("vague or mixed action defaults to fail-closed approval")

    classification_reason = _build_classification_reason(
        route_decision=route_decision,
        action_type=action_type,
        requires_approval=requires_approval,
        applicability=applicability,
        mutation_hits=mutation_hits,
        readonly_hits=readonly_hits,
        ambiguity_reasons=ambiguity_reasons,
    )

    risk = _derive_risk(request, requires_approval, applicability)

    return ApprovalPreview(
        coordinator_profile=route_decision.coordinator_profile,
        primary_profile=route_decision.primary_profile,
        selected_profiles=list(route_decision.selected_profiles),
        route_reason=route_decision.route_reason,
        route_validation_status=route_decision.validation_status,
        route_confidence=route_decision.confidence,
        profile="engineer",
        action_type=action_type,
        target_host=request.target_host,
        target_service=request.target_service,
        intended_change=request.intended_change or request.task_text,
        commands_or_control_script=request.commands_or_control_script,
        expected_effect=request.expected_effect,
        risk=risk,
        rollback_plan=request.rollback_plan,
        evidence_before=list(request.evidence_before),
        requires_approval=requires_approval,
        blocked_until_approved=blocked_until_approved,
        classification_reason=classification_reason,
        confidence=confidence,
        ambiguity_reasons=ambiguity_reasons,
        approval_applicability=applicability,
    )


def classify_engineer_approval(
    task_text: str,
    *,
    route_decision: RouteDecision | None = None,
    target_host: Optional[str] = None,
    target_service: Optional[str] = None,
    intended_change: Optional[str] = None,
    commands_or_control_script: Optional[str] = None,
    expected_effect: Optional[str] = None,
    risk: Optional[str] = None,
    rollback_plan: Optional[str] = None,
    evidence_before: Optional[list[str]] = None,
) -> ApprovalPreview:
    if not isinstance(task_text, str) or not task_text.strip():
        raise ApprovalError("task_text must be a non-empty string")

    decision = route_decision or route_task(task_text)
    request = ApprovalRequest(
        task_text=task_text,
        target_host=target_host,
        target_service=target_service,
        intended_change=intended_change,
        commands_or_control_script=commands_or_control_script,
        expected_effect=expected_effect,
        risk=risk,
        rollback_plan=rollback_plan,
        evidence_before=list(evidence_before or []),
    )
    return build_approval_request(decision, request)


def decision_to_dict(decision: ApprovalPreview) -> dict[str, Any]:
    return asdict(decision)


def decision_to_json(decision: ApprovalPreview) -> str:
    return json.dumps(decision_to_dict(decision), ensure_ascii=False, indent=2)
