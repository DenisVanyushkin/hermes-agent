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
    "systemd",
    "timer",
    "timers",
    "cron",
    "scheduler",
    "gateway",
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
    "investigate",
    "regression",
    "approval gate",
    "approval-gate",
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
    "оцени вакансию",
    "стоит ли откликаться",
    "зааплаиться",
    "отклик",
    "резюме",
    "cv",
    "cover letter",
    "сопроводительное письмо",
    "recruiter",
    "recruiter message",
    "head of product",
    "vp product",
    "cpo",
    "карьер",
    "career",
    "job",
    "role fit",
    "apply",
    "application",
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
    "btc",
    "bitcoin",
    "crypto",
    "binance",
    "coinbase",
    "fees",
    "commissions",
    "комиссии",
    "купить btc",
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
    "record the decision",
    "write handoff",
    "update state",
    "update docs",
    "update documentation",
    "capture durable memory",
    "capture the outcome",
    "summarize today's work",
    "today's work",
    "today's hermes role work",
    "final status",
    "status update",
    "финальный статус",
    "зафиксируй",
    "зафиксируй итог",
    "зафиксируй решение",
    "зафиксируй сегодняшнюю работу",
    "запиши итог",
    "сохрани итог",
    "сохрани в документацию",
    "напиши handoff",
    "сделай handoff",
    "обнови state",
    "обнови docs",
    "обнови документацию",
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

_READ_ONLY_DIAGNOSTIC_MARKERS = (
    "status",
    "inspect logs",
    "show logs",
    "show webui logs",
    "show status",
    "view logs",
    "view status",
    "read logs",
    "read status",
    "health",
    "health check",
    "check",
    "monitor",
    "monitoring",
)

_DURABLE_CAPTURE_TERMS = (
    "document the handoff",
    "document the result",
    "document",
    "handoff",
    "docs",
    "documentation",
    "summary",
    "summarize",
    "record the decision",
    "update docs",
    "update documentation",
    "update state",
)

_DOCS_ONLY_TARGET_HINTS = (
    "docs profile handoffs",
    "docs state",
    "current operational state md",
)

_NON_MUTATION_GUARDRAILS = (
    "do not change code",
    "do not deploy",
    "do not restart",
    "do not restart gateway",
    "do not touch cloudflare",
    "do not change cloudflare",
    "do not touch trading",
    "do not activate trading",
)

_NEGATIVE_GUARDRAIL_PREFIXES = (
    "do not",
    "don't",
    "dont",
    "without",
    "no",
    "не",
    "не трогай",
    "не менять",
    "не делай",
    "не перезапускай",
    "не деплой",
)

_INVESTIGATION_READ_ONLY_HINTS = (
    "investigate",
    "investigation",
    "read only",
    "read-only",
    "status",
    "smoke pass",
    "smoke result",
)

_SENSITIVE_TRIGGER_MAP = {
    "auth/session/cookies": ("auth", "authentication", "session", "cookie", "cookies"),
    "secrets/tokens/env": (
        "secret",
        "secrets",
        "token",
        "tokens",
        "api key",
        "api keys",
        "credential",
        "credentials",
        "provider credential",
        "provider credentials",
        "env",
        "environment variable",
        "environment variables",
    ),
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
    "persistent storage of untrusted external content": (
        "untrusted external content",
        "external content",
        "store content",
        "persistent storage",
        "persist untrusted",
    ),
}

_OPERATION_CATEGORY_GENERAL = "general_task"
_OPERATION_CATEGORY_READ_ONLY = "read_only_investigation"
_OPERATION_CATEGORY_NORMAL = "normal_operational_mutation"
_OPERATION_CATEGORY_SECURITY_CRITICAL = "security_critical_mutation"

_READ_ONLY_OPERATION_TERMS = (
    "git status",
    "systemctl status",
    "systemctl --user status",
    "journalctl",
    "list timers",
    "list services",
    "list units",
    "list-units",
    "status",
    "inspect logs",
    "show logs",
    "show status",
    "read logs",
    "read status",
    "проверь статус",
    "логи",
)

_MUTATION_INTENT_TERMS = (
    "install",
    "set up",
    "setup",
    "create",
    "add",
    "write",
    "update",
    "modify",
    "change",
    "configure",
    "enable",
    "disable",
    "restart",
    "deploy",
    "rollback",
)

_SCHEDULER_SURFACE_TERMS = (
    "cron",
    "crontab",
    "scheduler",
    "schedule",
    "systemd",
    "timer",
    "timers",
    "service",
)

_USER_LEVEL_OPERATION_TERMS = (
    "user level",
    "user-level",
    "systemctl --user",
    "user timer",
    "user service",
    "user crontab",
    "crontab -e",
    "local maintenance",
    "housekeeping",
    "fallback refresh",
    "workingdirectory",
    "state path",
)

_ROOT_SYSTEM_SCOPE_TERMS = (
    "root",
    "privileged",
    "system wide",
    "system-wide",
    "global",
    "globally",
    "/etc/systemd/system",
    "sudo",
)

_PUBLIC_EXPOSURE_TERMS = (
    "cloudflare",
    "reverse proxy",
    "firewall",
    "public exposure",
    "publicly expose",
    "open to internet",
    "open port",
    "public webui",
    "expose webui",
    "public access",
)

_SECRET_PROVIDER_MUTATION_TERMS = (
    "secret",
    "secrets",
    "token",
    "tokens",
    "auth",
    "authentication",
    "credential",
    "credentials",
    "provider config",
    "provider credentials",
    "auth json",
    ".env",
)

_GATEWAY_MUTATION_TERMS = (
    "restart gateway",
    "gateway restart",
    "deploy gateway",
    "gateway deploy",
    "rollback gateway",
    "gateway rollback",
)

_PRODUCTION_DATA_MUTATION_TERMS = (
    "database migration",
    "db migration",
    "schema migration",
    "repair database",
    "repair db",
    "production data deletion",
    "delete production data",
)

_GENERIC_HIGH_RISK_RUNTIME_TERMS = (
    "deploy",
    "restart",
    "rollback",
    "production",
)

_SECURITY_AUDITOR_TRIGGER_SET = {
    "auth/session/cookies",
    "secrets/tokens/env",
    "tool permissions",
    "Cloudflare/reverse proxy/firewall",
    "WebUI public access",
    "WebUI access model",
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
    critical_approval_required: bool
    approval_reason: str
    operation_category: str
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
    for phrase in phrases:
        normalized_phrase = _normalize(phrase)
        if not normalized_phrase:
            continue
        pattern = rf"(?:^| ){re.escape(normalized_phrase)}(?: |$)"
        if re.search(pattern, normalized_text):
            return True
    return False


def _split_task_clauses(task: str) -> list[str]:
    if not isinstance(task, str) or not task.strip():
        return []
    raw_clauses = re.split(r"(?:[\n\r]+|[.;])", task)
    clauses: list[str] = []
    for clause in raw_clauses:
        normalized_clause = _normalize(clause)
        if normalized_clause:
            clauses.append(normalized_clause)
    return clauses


def _is_negative_guardrail_clause(normalized_clause: str) -> bool:
    if not normalized_clause:
        return False
    return any(
        normalized_clause == prefix or normalized_clause.startswith(prefix + " ")
        for prefix in _NEGATIVE_GUARDRAIL_PREFIXES
    )


def _partition_task_clauses(task: str) -> tuple[list[str], list[str]]:
    action_clauses: list[str] = []
    guardrail_clauses: list[str] = []
    for clause in _split_task_clauses(task):
        if _is_negative_guardrail_clause(clause):
            guardrail_clauses.append(clause)
        else:
            action_clauses.append(clause)
    return action_clauses, guardrail_clauses


def _compose_normalized_text(clauses: list[str], changed_paths: list[str] | None = None) -> str:
    parts = list(clauses)
    if changed_paths:
        parts.extend(_normalize(path) for path in changed_paths if isinstance(path, str) and path.strip())
    return " ".join(part for part in parts if part).strip()


def _is_read_only_diagnostic_task(normalized_text: str) -> bool:
    return any(marker in normalized_text for marker in _READ_ONLY_DIAGNOSTIC_MARKERS)


def _has_mutation_intent(normalized_text: str) -> bool:
    return _contains(normalized_text, _MUTATION_INTENT_TERMS)


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
    action_clauses, _guardrail_clauses = _partition_task_clauses(task)
    normalized = _compose_normalized_text(action_clauses, changed_paths=changed_paths)
    if _ignore_sensitive_surface_classification(normalized):
        return []
    haystacks = [normalized]
    if changed_paths:
        haystacks.extend(_normalize(path) for path in changed_paths)

    hits: list[str] = []
    for trigger, phrases in _SENSITIVE_TRIGGER_MAP.items():
        if any(_contains(haystack, phrases) for haystack in haystacks):
            hits.append(trigger)
    return _dedupe_keep_order(hits)


def _task_with_paths(task: str, changed_paths: list[str] | None = None) -> str:
    return _compose_normalized_text(_split_task_clauses(task), changed_paths=changed_paths)


def _action_text(task: str, changed_paths: list[str] | None = None) -> str:
    action_clauses, _guardrail_clauses = _partition_task_clauses(task)
    return _compose_normalized_text(action_clauses, changed_paths=changed_paths)


def _guardrail_text(task: str) -> str:
    _action_clauses, guardrail_clauses = _partition_task_clauses(task)
    return " ".join(guardrail_clauses).strip()


def _is_root_or_system_scope_scheduler_mutation(normalized_text: str) -> bool:
    return _contains(normalized_text, _SCHEDULER_SURFACE_TERMS) and _contains(normalized_text, _ROOT_SYSTEM_SCOPE_TERMS)


def _is_security_critical_scheduler_mutation(normalized_text: str) -> bool:
    if not _contains(normalized_text, _SCHEDULER_SURFACE_TERMS):
        return False
    if _is_root_or_system_scope_scheduler_mutation(normalized_text):
        return True
    if _contains(normalized_text, _PUBLIC_EXPOSURE_TERMS):
        return True
    if _contains(normalized_text, _SECRET_PROVIDER_MUTATION_TERMS):
        return True
    if _contains(normalized_text, _GATEWAY_MUTATION_TERMS):
        return True
    if _contains(normalized_text, _PRODUCTION_DATA_MUTATION_TERMS):
        return True
    if _contains(normalized_text, _TRADING_TERMS) and _has_mutation_intent(normalized_text):
        return True
    return False


def _is_normal_operational_mutation(normalized_text: str) -> bool:
    if _is_docs_only_status_update(normalized_text):
        return True
    if not _has_mutation_intent(normalized_text):
        return False
    if _is_security_critical_scheduler_mutation(normalized_text):
        return False
    if not _contains(normalized_text, _SCHEDULER_SURFACE_TERMS):
        return False
    return (
        _contains(normalized_text, _USER_LEVEL_OPERATION_TERMS)
        or "local" in normalized_text
        or "localhost" in normalized_text
    )


def _is_read_only_investigation(normalized_text: str) -> bool:
    if _is_docs_only_status_update(normalized_text):
        return False
    if _has_mutation_intent(normalized_text):
        return False
    return _contains(normalized_text, _READ_ONLY_OPERATION_TERMS)


def _security_critical_approval_reason(normalized_text: str, sensitive_triggers: list[str]) -> str:
    if any(trigger in sensitive_triggers for trigger in ("Cloudflare/reverse proxy/firewall", "WebUI public access")):
        return "public exposure and ingress mutations require explicit operator approval"
    if any(trigger in sensitive_triggers for trigger in ("auth/session/cookies", "secrets/tokens/env", "tool permissions")):
        return "secrets, auth, provider config, or tool-permission mutations require explicit operator approval"
    if _contains(normalized_text, _GATEWAY_MUTATION_TERMS):
        return "gateway deploy/restart/rollback affects live service availability and requires explicit operator approval"
    if _contains(normalized_text, _PRODUCTION_DATA_MUTATION_TERMS):
        return "production database mutation requires explicit operator approval"
    if _contains(normalized_text, _GENERIC_HIGH_RISK_RUNTIME_TERMS):
        return "production/runtime mutation including deploy, restart, or rollback requires explicit operator approval"
    if _is_root_or_system_scope_scheduler_mutation(normalized_text):
        return "root/system-wide scheduler mutation requires explicit operator approval"
    if _contains(normalized_text, _TRADING_TERMS) and _has_mutation_intent(normalized_text):
        return "scheduled trading execution requires explicit operator approval"
    return "security-critical runtime mutation requires explicit operator approval"


def classify_operation_category(task: str, changed_paths: list[str] | None = None) -> str:
    normalized = _task_with_paths(task, changed_paths=changed_paths)
    risk_normalized = _action_text(task, changed_paths=changed_paths)
    sensitive_triggers = classify_sensitive_diff_triggers(task, changed_paths=changed_paths)
    if _ignore_sensitive_surface_classification(normalized):
        if _is_docs_only_status_update(normalized):
            return _OPERATION_CATEGORY_NORMAL
        return _OPERATION_CATEGORY_READ_ONLY
    if _contains(risk_normalized, _PUBLIC_EXPOSURE_TERMS):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    if _contains(risk_normalized, _SECRET_PROVIDER_MUTATION_TERMS) and _has_mutation_intent(risk_normalized):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    if _contains(risk_normalized, _GATEWAY_MUTATION_TERMS):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    if _contains(risk_normalized, _PRODUCTION_DATA_MUTATION_TERMS):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    if _contains(risk_normalized, _GENERIC_HIGH_RISK_RUNTIME_TERMS):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    if _is_security_critical_scheduler_mutation(risk_normalized):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    if _is_normal_operational_mutation(risk_normalized):
        return _OPERATION_CATEGORY_NORMAL
    if _is_read_only_investigation(normalized):
        return _OPERATION_CATEGORY_READ_ONLY
    if sensitive_triggers and _has_mutation_intent(risk_normalized):
        return _OPERATION_CATEGORY_SECURITY_CRITICAL
    return _OPERATION_CATEGORY_GENERAL


def classify_production_runtime_mutation(task: str, changed_paths: list[str] | None = None) -> bool:
    return classify_operation_category(task, changed_paths=changed_paths) == _OPERATION_CATEGORY_SECURITY_CRITICAL


def classify_external_commitment(task: str) -> bool:
    normalized = _normalize(task)
    if _contains(normalized, _EXTERNAL_COMMITMENT_TERMS):
        if "draft message" in normalized or "draft a message" in normalized:
            return False
        return True
    return False


def _contains_any(normalized_text: str, phrases: tuple[str, ...]) -> bool:
    return _contains(normalized_text, phrases)


def _is_docs_only_status_update(normalized_text: str) -> bool:
    has_docs_target = any(hint in normalized_text for hint in _DOCS_ONLY_TARGET_HINTS)
    has_docs_intent = _contains_any(normalized_text, _DOCS_TERMS) or "update" in normalized_text
    has_non_mutation_guardrail = any(phrase in normalized_text for phrase in _NON_MUTATION_GUARDRAILS)
    return has_docs_target and has_docs_intent and has_non_mutation_guardrail


def _is_read_only_sensitive_investigation(normalized_text: str) -> bool:
    has_investigation = any(hint in normalized_text for hint in _INVESTIGATION_READ_ONLY_HINTS)
    has_negative_sensitive_guardrail = (
        "do not change cloudflare" in normalized_text
        or "do not touch cloudflare" in normalized_text
    )
    return has_investigation and has_negative_sensitive_guardrail


def _ignore_sensitive_surface_classification(normalized_text: str) -> bool:
    return _is_docs_only_status_update(normalized_text) or _is_read_only_sensitive_investigation(normalized_text)


def _select_role(task: str, route_decision: RouteDecision | None) -> tuple[str, bool, str]:
    normalized = _normalize(task)
    action_normalized = _action_text(task)
    docs_first_markers = (
        "зафиксируй",
        "handoff",
        "final status",
        "финальный статус",
        "update docs",
        "update state",
        "status update",
    )
    if _contains_any(normalized, _DOCS_TERMS) and any(marker in normalized for marker in docs_first_markers):
        return "scribe", False, "documentation/status capture intent detected"

    if _contains_any(normalized, _ENGINEER_TERMS):
        if _contains_any(normalized, _SECURITY_REVIEW_TERMS):
            return "engineer", False, "engineering task with security-sensitive surface"
        return "engineer", False, "engineering / repo / code work detected"

    if _has_mutation_intent(action_normalized) and (
        _contains(action_normalized, _PUBLIC_EXPOSURE_TERMS)
        or _contains(action_normalized, _SECRET_PROVIDER_MUTATION_TERMS)
        or _contains(action_normalized, _GATEWAY_MUTATION_TERMS)
        or _contains(action_normalized, _SCHEDULER_SURFACE_TERMS)
        or _contains(action_normalized, _PRODUCTION_DATA_MUTATION_TERMS)
    ):
        return "engineer", False, "runtime mutation intent detected"

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


def _requires_reviewer(
    selected_role: str,
    sensitive_triggers: list[str],
    operation_category: str,
) -> tuple[bool, str | None]:
    if selected_role == "security_auditor":
        return True, None
    if operation_category == _OPERATION_CATEGORY_SECURITY_CRITICAL and any(
        trigger in _SECURITY_AUDITOR_TRIGGER_SET for trigger in sensitive_triggers
    ):
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
    operation_category: str,
    sensitive_triggers: list[str],
    task: str,
) -> tuple[bool, str]:
    normalized = _normalize(task)
    if operation_category == _OPERATION_CATEGORY_SECURITY_CRITICAL:
        return True, _security_critical_approval_reason(normalized, sensitive_triggers)
    if external_commitment:
        return True, "external commitment must be confirmed before final action"
    if _contains(normalized, _MONEY_TERMS):
        return True, "money/payment risk requires explicit confirmation or escalation"
    if _contains(normalized, _IDENTITY_TERMS):
        return True, "identity-document risk requires explicit confirmation or escalation"
    return False, ""


def _durable_outcome_expected(selected_role: str, external_commitment: bool, task: str) -> bool:
    normalized = _normalize(task)
    if _is_read_only_diagnostic_task(normalized):
        return False
    if selected_role == "scribe":
        return True
    if selected_role in {"engineer", "career_strategist", "researcher"} and _contains(normalized, _DURABLE_CAPTURE_TERMS):
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
    if route_obj is not None and route_obj.primary_profile == "general_operator" and selected_role == "general_operator":
        selected_role = "general_operator"
        fallback_used = True
        fallback_reason = "routing defaulted to General Operator"

    sensitive_triggers = classify_sensitive_diff_triggers(task, changed_paths=changed_paths)
    operation_category = classify_operation_category(task, changed_paths=changed_paths)
    production_runtime_mutation = operation_category == _OPERATION_CATEGORY_SECURITY_CRITICAL
    external_commitment = classify_external_commitment(task)
    durable_outcome_expected = _durable_outcome_expected(selected_role, external_commitment, task)
    requires_reviewer, reviewer_profile = _requires_reviewer(selected_role, sensitive_triggers, operation_category)
    requires_scribe, scribe_reason = _requires_scribe(selected_role, durable_outcome_expected, task)
    requires_explicit_approval, approval_reason = _requires_explicit_approval(
        selected_role=selected_role,
        external_commitment=external_commitment,
        operation_category=operation_category,
        sensitive_triggers=sensitive_triggers,
        task=task,
    )

    ordinary_personal_admin = selected_role == "general_operator" and not production_runtime_mutation
    if selected_role == "general_operator" and not external_commitment and not sensitive_triggers:
        ordinary_personal_admin = True

    trading_deferred = False

    post_change_review_policy = _post_change_review_policy(
        selected_role=selected_role,
        sensitive_triggers=sensitive_triggers,
        requires_reviewer=requires_reviewer,
        requires_scribe=requires_scribe,
        durable_outcome_expected=durable_outcome_expected,
        production_runtime_mutation=production_runtime_mutation,
    )
    critical_approval_required = operation_category == _OPERATION_CATEGORY_SECURITY_CRITICAL

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
        critical_approval_required=critical_approval_required,
        approval_reason=approval_reason,
        operation_category=operation_category,
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
