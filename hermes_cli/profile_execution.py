"""Role-based execution planning for Hermes Profile Architecture PR-7.

This module is intentionally pure and import-light. It does not execute runtime
workflows, call LLMs, or import agent/gateway/scheduler stacks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import json
import re

from hermes_cli.profile_approval import ApprovalPreview
from hermes_cli.profile_routing import RouteDecision, route_task


_ROLE_INTENTS = {
    "engineer": "engineering",
    "security_auditor": "security review",
    "scribe": "documentation/memory",
    "researcher": "external research",
    "career_strategist": "career/job",
    "general_operator": "personal/admin",
    "trading_observer_trader": "trading deferred",
}

_ENGINEER_TERMS = (
    "engineer",
    "engineering",
    "fix",
    "bug",
    "debug",
    "pytest",
    "test suite",
    "tests",
    "code",
    "repository",
    "repo",
    "implement",
    "build",
    "deploy",
    "service",
    "infra",
    "runtime",
    "webui",
    "cloudflare",
    "reverse proxy",
    "firewall",
    "public exposure",
    "production",
    "logs",
    "log",
)

_PERSONAL_ADMIN_TERMS = (
    "haircut",
    "calendar event",
    "calendar",
    "reminder",
    "reservation",
    "reserve",
    "book",
    "booking",
    "appointment",
    "checklist",
    "message to",
    "draft a message",
    "draft message",
    "send a message",
    "send message",
    "personal",
    "admin",
)

_CAREER_TERMS = (
    "vacancy",
    "cv",
    "cover letter",
    "recruiter",
    "job",
    "interview",
    "application",
    "apply",
)

_RESEARCH_TERMS = (
    "research",
    "source synthesis",
    "synthesize sources",
    "current facts",
    "company research",
    "news",
    "weather",
    "report",
    "digest",
)

_DOCS_TERMS = (
    "docs",
    "documentation",
    "handoff",
    "state",
    "decision",
    "open question",
    "memory",
    "note",
)

_SECURITY_REVIEW_TERMS = (
    "security review",
    "security audit",
    "audit",
    "threat model",
    "review auth",
    "review security",
    "auth cookies",
    "secret exposure",
)

_TRADING_TERMS = (
    "trading",
    "trade",
    "portfolio",
    "order",
    "buy",
    "sell",
    "execution",
    "risk",
)

_SENSITIVE_TRIGGER_MAP = {
    "auth/session/cookies": ("auth", "authentication", "session", "cookie", "cookies"),
    "secrets/tokens/env": ("secret", "secrets", "token", "tokens", "env", "environment variable", "environment variables"),
    "SSH": ("ssh",),
    "browser profiles": ("browser profile", "browser profiles", "profile path", "browser-desktop"),
    "file manager / shell / terminal / git / upload permissions": (
        "file manager",
        "shell",
        "terminal",
        "git",
        "upload",
        "permission",
        "permissions",
    ),
    "scheduler/memory writes": ("scheduler", "memory write", "memory writes", "timed write", "cron memory"),
    "tool permissions": ("tool permission", "tool permissions", "tool access", "allowlist", "denylist"),
    "Cloudflare/reverse proxy/firewall": ("cloudflare", "reverse proxy", "firewall", "public exposure", "publicly expose", "open to internet", "open port"),
    "gateway": ("gateway",),
    "cron/scheduler": ("cron", "scheduler", "schedule", "timer", "timers"),
    "WebUI public access": ("webui public", "webui exposure", "public webui", "expose webui", "public access"),
    "WebUI access model": ("webui access", "access model", "auth model", "session model", "local access"),
    "production deploy scripts": ("deploy script", "deploy scripts", "production deploy", "release script"),
    "database migrations": ("database migration", "db migration", "migration", "schema migration", "repair database", "repair db"),
    "trading/risk/execution paths": ("trading", "risk", "execution path", "execution paths", "order execution"),
    "persistent storage of untrusted external content": (
        "untrusted external content",
        "external content",
        "store content",
        "persistent storage",
        "persist untrusted",
    ),
}

_RUNTIME_MUTATION_TERMS = (
    "deploy",
    "restart",
    "rollback",
    "systemd",
    "cloudflare",
    "reverse proxy",
    "firewall",
    "secret",
    "token",
    "auth",
    "permission",
    "scheduler",
    "timer",
    "database migration",
    "database repair",
    "production data deletion",
    "delete production data",
    "trading",
)

_EXTERNAL_COMMITMENT_TERMS = (
    "book",
    "booking",
    "reserve",
    "reservation",
    "calendar event",
    "calendar",
    "reminder",
    "appointment",
    "schedule",
    "reschedule",
    "запиши",
    "записать",
    "забронировать",
    "бронь",
)

_MONEY_TERMS = ("pay", "payment", "invoice", "invoice", "money", "bank", "card", "charge", "cost")
_IDENTITY_TERMS = ("identity", "passport", "id card", "driver license", "driver's license", "documents", "document details")


@dataclass(frozen=True)
class RoleExecutionPlan:
    task: str
    selected_role: str
    role_intent: str
    fallback_used: bool
    fallback_reason: str
    requires_reviewer: bool
    reviewer_profile: str | None
    requires_scribe: bool
    scribe_reason: str
    requires_explicit_approval: bool
    approval_reason: str
    ordinary_personal_admin: bool
    external_commitment: bool
    sensitive_diff_triggers: list[str]
    production_runtime_mutation: bool
    post_change_review_policy: dict[str, Any]
    durable_outcome_expected: bool
    trading_deferred: bool


class RoleExecutionError(RuntimeError):
    """Raised when role execution planning cannot be produced safely."""


def _normalize(text: str) -> str:
    translated = text.lower().translate(str.maketrans({ch: " " for ch in "!\"#$%&'()*+,./:;<=>?@[\\]^`{|}~"}))
    return " ".join(translated.split())


def _contains(normalized_text: str, phrases: tuple[str, ...]) -> bool:
    return any(_normalize(phrase) in normalized_text for phrase in phrases)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _coerce_route_decision(route_decision: RouteDecision | dict[str, Any] | None, task: str) -> RouteDecision | None:
    if route_decision is None:
        return None
    if isinstance(route_decision, RouteDecision):
        return route_decision
    if isinstance(route_decision, dict):
        return RouteDecision(
            request_text=str(route_decision.get("request_text", task)),
            coordinator_profile=str(route_decision.get("coordinator_profile", "chief_hermes")),
            primary_profile=str(route_decision.get("primary_profile", "general_operator")),
            selected_profiles=[str(item) for item in route_decision.get("selected_profiles", []) or []],
            route_chain=[],
            route_reason=str(route_decision.get("route_reason", "")),
            validation_status=str(route_decision.get("validation_status", "unknown")),
            confidence=str(route_decision.get("confidence", "unknown")),
            ambiguity_reasons=[str(item) for item in route_decision.get("ambiguity_reasons", []) or []],
            max_chain_limit_applied=bool(route_decision.get("max_chain_limit_applied", False)),
        )
    raise RoleExecutionError("route_decision must be a RouteDecision or mapping")


def classify_sensitive_diff_triggers(task: str, changed_paths: list[str] | None = None) -> list[str]:
    normalized = _normalize(task)
    haystacks = [normalized]
    if changed_paths:
        haystacks.extend(_normalize(path) for path in changed_paths)

    hits: list[str] = []
    for trigger, phrases in _SENSITIVE_TRIGGER_MAP.items():
        if any(_contains(haystack, phrases) for haystack in haystacks):
            hits.append(trigger)
    return _dedupe_keep_order(hits)


def classify_production_runtime_mutation(task: str, changed_paths: list[str] | None = None) -> bool:
    normalized = _normalize(task)
    if changed_paths:
        normalized = " ".join([normalized, *(_normalize(path) for path in changed_paths)])
    if _contains(normalized, _RUNTIME_MUTATION_TERMS):
        return True
    return bool(classify_sensitive_diff_triggers(task, changed_paths=changed_paths))


def classify_external_commitment(task: str) -> bool:
    normalized = _normalize(task)
    if _contains(normalized, _EXTERNAL_COMMITMENT_TERMS):
        if "draft message" in normalized or "draft a message" in normalized:
            return False
        return True
    return False


def _contains_any(normalized_text: str, phrases: tuple[str, ...]) -> bool:
    return _contains(normalized_text, phrases)


def _select_role(task: str, route_decision: RouteDecision | None) -> tuple[str, bool, str]:
    normalized = _normalize(task)
    if _contains_any(normalized, _TRADING_TERMS):
        return "trading_observer_trader", True, "trading remains deferred"

    if _contains_any(normalized, _ENGINEER_TERMS):
        if _contains_any(normalized, _SECURITY_REVIEW_TERMS):
            return "engineer", False, "engineering task with security-sensitive surface"
        return "engineer", False, "engineering / repo / code work detected"

    if _contains_any(normalized, _SECURITY_REVIEW_TERMS):
        return "security_auditor", False, "task explicitly requests security review"
    if _contains_any(normalized, _CAREER_TERMS):
        return "career_strategist", False, "job/career/vacancy intent detected"
    if _contains_any(normalized, _DOCS_TERMS):
        return "scribe", False, "documentation/memory intent detected"
    if _contains_any(normalized, _RESEARCH_TERMS):
        return "researcher", False, "external research intent detected"
    if _contains_any(normalized, _PERSONAL_ADMIN_TERMS):
        return "general_operator", True, "ordinary personal/admin fallback"

    if route_decision is not None and route_decision.primary_profile and route_decision.primary_profile != "chief_hermes":
        if route_decision.primary_profile == "general_operator":
            return "general_operator", True, "ordinary safe personal/admin fallback from routing"
        return route_decision.primary_profile, False, "routing selected specialized role"

    return "general_operator", True, "no specialized role matched; safe fallback to General Operator"


def _role_intent(role: str) -> str:
    return _ROLE_INTENTS.get(role, "general")


def _requires_reviewer(selected_role: str, sensitive_triggers: list[str]) -> tuple[bool, str | None]:
    if selected_role == "security_auditor":
        return True, None
    if sensitive_triggers:
        return True, "security_auditor"
    return False, None


def _requires_scribe(selected_role: str, durable_outcome_expected: bool, task: str) -> tuple[bool, str]:
    if selected_role == "scribe":
        normalized = _normalize(task)
        if "handoff" in normalized or "document" in normalized or "docs" in normalized:
            return True, "durable handoff/documentation outcome should be recorded"
        return True, "selected role is documentation/memory capture"
    if durable_outcome_expected:
        return True, "durable outcome should be recorded by Scribe"
    return False, ""


def _requires_explicit_approval(
    *,
    selected_role: str,
    external_commitment: bool,
    production_runtime_mutation: bool,
    sensitive_triggers: list[str],
    task: str,
) -> tuple[bool, str]:
    normalized = _normalize(task)
    if production_runtime_mutation:
        return True, "production/runtime mutation requires explicit operator approval"
    if external_commitment:
        return True, "external commitment must be confirmed before final action"
    if _contains(normalized, _MONEY_TERMS):
        return True, "money/payment risk requires explicit confirmation or escalation"
    if _contains(normalized, _IDENTITY_TERMS):
        return True, "identity-document risk requires explicit confirmation or escalation"
    if sensitive_triggers and selected_role != "security_auditor":
        return True, "sensitive diff triggers require reviewer or explicit approval"
    return False, ""


def _durable_outcome_expected(selected_role: str, external_commitment: bool, task: str) -> bool:
    normalized = _normalize(task)
    readonly_markers = (
        "status",
        "inspect logs",
        "inspect",
        "read logs",
        "health",
        "check",
        "monitor",
        "monitoring",
    )
    if any(marker in normalized for marker in readonly_markers):
        return False
    if selected_role in {"scribe", "engineer", "security_auditor", "career_strategist", "researcher"}:
        return True
    if external_commitment and any(term in normalized for term in ("book", "reserve", "calendar event", "reminder", "appointment")):
        return False
    return False


def _post_change_review_policy(
    *,
    selected_role: str,
    sensitive_triggers: list[str],
    requires_reviewer: bool,
    requires_scribe: bool,
    durable_outcome_expected: bool,
    production_runtime_mutation: bool,
) -> dict[str, Any]:
    should_summarize_diff = selected_role == "engineer" or bool(sensitive_triggers) or production_runtime_mutation
    should_run_tests = selected_role == "engineer" or bool(sensitive_triggers) or production_runtime_mutation
    return {
        "summarize_diff": should_summarize_diff,
        "run_relevant_tests": should_run_tests,
        "invoke_security_auditor": requires_reviewer,
        "invoke_scribe": requires_scribe and durable_outcome_expected,
        "sensitive_diff_triggers": list(sensitive_triggers),
        "note": (
            "review sensitive surfaces with Security Auditor and record durable outcomes with Scribe"
            if sensitive_triggers or durable_outcome_expected
            else "no post-change reviewer needed"
        ),
    }


def build_role_execution_plan(
    task: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
) -> RoleExecutionPlan:
    if not isinstance(task, str) or not task.strip():
        raise RoleExecutionError("task must be a non-empty string")

    route_obj = _coerce_route_decision(route_decision, task)
    selected_role, fallback_used, fallback_reason = _select_role(task, route_obj)
    if route_obj is not None and route_obj.primary_profile == "general_operator":
        selected_role = "general_operator"
        fallback_used = True
        fallback_reason = "routing defaulted to General Operator"

    sensitive_triggers = classify_sensitive_diff_triggers(task, changed_paths=changed_paths)
    production_runtime_mutation = classify_production_runtime_mutation(task, changed_paths=changed_paths)
    external_commitment = classify_external_commitment(task)
    durable_outcome_expected = _durable_outcome_expected(selected_role, external_commitment, task)
    requires_reviewer, reviewer_profile = _requires_reviewer(selected_role, sensitive_triggers)
    requires_scribe, scribe_reason = _requires_scribe(selected_role, durable_outcome_expected, task)
    requires_explicit_approval, approval_reason = _requires_explicit_approval(
        selected_role=selected_role,
        external_commitment=external_commitment,
        production_runtime_mutation=production_runtime_mutation,
        sensitive_triggers=sensitive_triggers,
        task=task,
    )

    ordinary_personal_admin = selected_role == "general_operator" and not production_runtime_mutation
    if selected_role == "general_operator" and not external_commitment and not sensitive_triggers:
        ordinary_personal_admin = True

    trading_deferred = selected_role == "trading_observer_trader"

    post_change_review_policy = _post_change_review_policy(
        selected_role=selected_role,
        sensitive_triggers=sensitive_triggers,
        requires_reviewer=requires_reviewer,
        requires_scribe=requires_scribe,
        durable_outcome_expected=durable_outcome_expected,
        production_runtime_mutation=production_runtime_mutation,
    )

    return RoleExecutionPlan(
        task=task.strip(),
        selected_role=selected_role,
        role_intent=_role_intent(selected_role),
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        requires_reviewer=requires_reviewer,
        reviewer_profile=reviewer_profile,
        requires_scribe=requires_scribe,
        scribe_reason=scribe_reason,
        requires_explicit_approval=requires_explicit_approval,
        approval_reason=approval_reason,
        ordinary_personal_admin=ordinary_personal_admin,
        external_commitment=external_commitment,
        sensitive_diff_triggers=sensitive_triggers,
        production_runtime_mutation=production_runtime_mutation,
        post_change_review_policy=post_change_review_policy,
        durable_outcome_expected=durable_outcome_expected,
        trading_deferred=trading_deferred,
    )


def execution_plan_to_dict(plan: RoleExecutionPlan) -> dict[str, Any]:
    if not isinstance(plan, RoleExecutionPlan):
        raise RoleExecutionError("execution_plan_to_dict expects a RoleExecutionPlan")
    return asdict(plan)


def execution_plan_to_json(plan: RoleExecutionPlan) -> str:
    return json.dumps(execution_plan_to_dict(plan), ensure_ascii=False, indent=2)
