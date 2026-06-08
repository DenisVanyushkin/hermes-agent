"""Deterministic profile routing preview for Hermes Profile Architecture PR-2.

This module is intentionally pure and import-light. It does not execute runtime
workflows, call LLMs, or import the agent/gateway/scheduler stacks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
import json
import string

import yaml

from hermes_cli.profile_validation import (
    ACTIVE_PROFILE_IDS,
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    _issue,
    format_issues,
    validate_model_policy,
    validate_profile_registry,
)


class RoutingError(RuntimeError):
    """Raised when routing cannot be produced safely."""


@dataclass(frozen=True)
class ResolvedModel:
    profile_id: str
    model_tier: str
    provider: str
    model: str
    model_resolution_status: str
    fallback_status: str


@dataclass(frozen=True)
class RouteHop:
    profile_id: str
    routing_reason: str
    model_tier: str
    provider: str
    model: str
    escalation_reason: str
    model_resolution_status: str
    fallback_status: str


@dataclass(frozen=True)
class RouteDecision:
    request_text: str
    coordinator_profile: str
    primary_profile: str
    selected_profiles: list[str]
    route_chain: list[RouteHop]
    route_reason: str
    validation_status: str
    confidence: str
    ambiguity_reasons: list[str]
    max_chain_limit_applied: bool


_SECURITY_TERMS = (
    "auth",
    "authentication",
    "secrets",
    "secret",
    "tokens",
    "token",
    "exposure",
    "public access",
    "cloudflare",
    "firewall",
    "permissions",
    "permission",
    "scheduler",
    "tool boundary",
    "tool-boundary",
    "browser profile",
    "privileged",
)

_INFRA_TERMS = (
    "webui",
    "deploy",
    "docker",
    "systemd",
    "smoke",
    "rollback",
    "logs",
    "log",
    "db",
    "database",
    "monitoring",
    "runtime",
    "production",
    "host",
    "service",
    "operational",
    "change",
    "restart",
    "reload",
    "patch",
    "build",
    "migration",
    "repair",
)

_CAREER_TERMS = (
    "vacancy",
    "cv",
    "cover letter",
    "recruiter",
    "job intel",
    "job-intel",
    "apply strategy",
    "job opportunity",
    "interview",
    "application strategy",
)

_DOCS_TERMS = (
    "docs",
    "documentation",
    "document",
    "runbook",
    "state",
    "decision",
    "open question",
    "profile handoff",
    "handoff",
    "record",
    "note",
)

_RESEARCH_TERMS = (
    "weather",
    "news",
    "company research",
    "current facts",
    "digest",
    "report",
    "due diligence",
    "market overview",
    "current context",
)


def _normalize(text: str) -> str:
    translated = text.lower().translate(str.maketrans({ch: " " for ch in string.punctuation}))
    return " ".join(translated.split())


def _contains_any(normalized_text: str, phrases: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for phrase in phrases:
        normalized_phrase = _normalize(phrase)
        if normalized_phrase and normalized_phrase in normalized_text:
            hits.append(phrase)
    return hits


def _load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingError(f"failed to read YAML file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RoutingError(f"failed to parse YAML file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RoutingError(f"YAML file {path} must contain a mapping")
    return data


def load_profile_registry(path: Path | str = DEFAULT_PROFILE_REGISTRY_PATH) -> dict[str, Any]:
    return _load_yaml_file(Path(path))


def load_model_policy(path: Path | str = DEFAULT_MODEL_POLICY_PATH) -> dict[str, Any]:
    return _load_yaml_file(Path(path))


def _validate_loaded_architecture(registry: dict[str, Any], policy: dict[str, Any]) -> None:
    issues = []
    issues.extend(validate_profile_registry(registry))
    issues.extend(validate_model_policy(policy, active_profile_ids=set(ACTIVE_PROFILE_IDS)))

    if isinstance(registry, dict) and isinstance(policy, dict):
        profile_tiers = policy.get("profile_tiers", {})
        if isinstance(profile_tiers, dict):
            for profile in registry.get("profiles", []):
                if not isinstance(profile, dict):
                    continue
                profile_id = profile.get("id")
                if not isinstance(profile_id, str) or profile_id not in ACTIVE_PROFILE_IDS:
                    continue
                if profile_tiers.get(profile_id) != profile.get("default_model"):
                    issues.append(
                        _issue(
                            "error",
                            f"profile {profile_id} default_model {profile.get('default_model')!r} does not match policy tier {profile_tiers.get(profile_id)!r}",
                        )
                    )

    if issues:
        raise RoutingError(format_issues(issues))


def resolve_profile_model(profile_id: str, policy: dict[str, Any]) -> ResolvedModel:
    tiers = policy.get("tiers")
    profile_tiers = policy.get("profile_tiers")
    if not isinstance(tiers, dict):
        raise RoutingError("model policy tiers must be a mapping")
    if not isinstance(profile_tiers, dict):
        raise RoutingError("model policy profile_tiers must be a mapping")

    tier_name = profile_tiers.get(profile_id)
    if not isinstance(tier_name, str) or not tier_name.strip():
        raise RoutingError(f"model policy does not define a tier for profile {profile_id}")

    tier = tiers.get(tier_name)
    if not isinstance(tier, dict):
        raise RoutingError(f"model policy tier {tier_name!r} for profile {profile_id} is missing or invalid")

    provider = tier.get("provider")
    model = tier.get("model")
    if not isinstance(provider, str) or not provider.strip():
        raise RoutingError(f"model policy tier {tier_name!r} for profile {profile_id} is missing provider")
    if not isinstance(model, str) or not model.strip():
        raise RoutingError(f"model policy tier {tier_name!r} for profile {profile_id} is missing model")

    if tier_name == "critical":
        model_resolution_status = "no_fallback_stop_and_escalate"
        fallback_status = "stop_and_escalate"
    else:
        fallback_behavior = tier.get("fallback_behavior")
        if fallback_behavior == "fallback_allowed":
            model_resolution_status = "fallback_available_by_policy"
            fallback_status = "fallback_allowed"
        else:
            model_resolution_status = "direct"
            fallback_status = str(fallback_behavior or "not_applicable")

    return ResolvedModel(
        profile_id=profile_id,
        model_tier=tier_name,
        provider=provider,
        model=model,
        model_resolution_status=model_resolution_status,
        fallback_status=fallback_status,
    )


def _determine_primary_profile(normalized_text: str, matched: dict[str, list[str]]) -> str:
    has_security = bool(matched["security"])
    has_infra = bool(matched["infra"])
    has_career = bool(matched["career"])
    has_docs = bool(matched["docs"])
    has_research = bool(matched["research"])

    if has_infra:
        return "engineer"
    if has_security:
        return "security_auditor"
    if has_career:
        return "career_strategist"
    if has_docs:
        return "scribe"
    if has_research:
        return "researcher"
    return "chief_hermes"


def _build_overlays(primary_profile: str, matched: dict[str, list[str]], normalized_text: str) -> list[tuple[str, str]]:
    overlays: list[tuple[str, str]] = []

    security_overlay_needed = bool(matched["security"]) and primary_profile != "security_auditor"
    researcher_overlay_needed = bool(matched["research"]) and primary_profile == "career_strategist"

    docs_follow_up_needed = False
    if primary_profile == "engineer":
        docs_follow_up_needed = bool(matched["docs"]) or bool(matched["security"])
    elif primary_profile == "security_auditor":
        docs_follow_up_needed = bool(matched["docs"]) or "document" in normalized_text or "documenting" in normalized_text
    elif primary_profile == "career_strategist":
        docs_follow_up_needed = bool(matched["docs"]) and ("document" in normalized_text or "handoff" in normalized_text)

    if primary_profile == "engineer" and security_overlay_needed:
        overlays.append(("security_auditor", "security-sensitive risk overlay detected"))
    if primary_profile == "career_strategist" and researcher_overlay_needed:
        overlays.append(("researcher", "company research/current facts requested alongside job-intel analysis"))
    if primary_profile in {"engineer", "security_auditor", "career_strategist"} and docs_follow_up_needed:
        overlays.append(("scribe", "documentation/state handoff follow-up required"))

    return overlays


def _build_route_reason(primary_profile: str, matched: dict[str, list[str]], overlays: list[tuple[str, str]]) -> str:
    reasons: list[str] = []
    if matched["security"]:
        reasons.append("security-sensitive triggers: " + ", ".join(matched["security"]))
    if matched["infra"]:
        reasons.append("infra/runtime triggers: " + ", ".join(matched["infra"]))
    if matched["career"]:
        reasons.append("career/job-intel triggers: " + ", ".join(matched["career"]))
    if matched["docs"]:
        reasons.append("documentation/state triggers: " + ", ".join(matched["docs"]))
    if matched["research"]:
        reasons.append("research/current-info triggers: " + ", ".join(matched["research"]))
    if overlays:
        reasons.append("overlays: " + "; ".join(f"{profile} ({reason})" for profile, reason in overlays))
    reasons.append(f"primary profile selected: {primary_profile}")
    return "; ".join(reasons)


def _confidence_from_route(primary_profile: str, matched: dict[str, list[str]], overlays: list[tuple[str, str]]) -> tuple[str, list[str]]:
    ambiguity_reasons: list[str] = []
    domain_hits = [name for name, hits in matched.items() if hits]
    if not domain_hits:
        return "low", ["no specific domain trigger matched; defaulting to chief_hermes metadata only"]

    if len(domain_hits) > 1:
        ambiguity_reasons.append("multiple domain signals detected: " + ", ".join(domain_hits))
    if overlays:
        ambiguity_reasons.append("route chain includes follow-up hops: " + ", ".join(profile for profile, _ in overlays))

    if len(domain_hits) == 1 and not overlays:
        return "high", ambiguity_reasons
    return "medium", ambiguity_reasons or ["follow-up hops were added deterministically"]


def route_task(
    request_text: str,
    *,
    registry_path: Path | str = DEFAULT_PROFILE_REGISTRY_PATH,
    policy_path: Path | str = DEFAULT_MODEL_POLICY_PATH,
    max_chain_limit: int = 3,
) -> RouteDecision:
    registry = load_profile_registry(registry_path)
    policy = load_model_policy(policy_path)
    _validate_loaded_architecture(registry, policy)

    normalized_text = _normalize(request_text)
    matched = {
        "security": _contains_any(normalized_text, _SECURITY_TERMS),
        "infra": _contains_any(normalized_text, _INFRA_TERMS),
        "career": _contains_any(normalized_text, _CAREER_TERMS),
        "docs": _contains_any(normalized_text, _DOCS_TERMS),
        "research": _contains_any(normalized_text, _RESEARCH_TERMS),
    }

    primary_profile = _determine_primary_profile(normalized_text, matched)
    overlays = _build_overlays(primary_profile, matched, normalized_text)

    route_profiles: list[tuple[str, str]] = []
    if primary_profile != "chief_hermes":
        route_profiles.append((primary_profile, "primary route selected from matched task signals"))
    route_profiles.extend(overlays)

    max_chain_limit_applied = False
    if len(route_profiles) > max_chain_limit:
        route_profiles = route_profiles[:max_chain_limit]
        max_chain_limit_applied = True

    route_chain: list[RouteHop] = []
    for index, (profile_id, routing_reason) in enumerate(route_profiles):
        resolved = resolve_profile_model(profile_id, policy)
        escalation_reason = "primary route" if index == 0 else routing_reason
        route_chain.append(
            RouteHop(
                profile_id=profile_id,
                routing_reason=routing_reason,
                model_tier=resolved.model_tier,
                provider=resolved.provider,
                model=resolved.model,
                escalation_reason=escalation_reason,
                model_resolution_status=resolved.model_resolution_status,
                fallback_status=resolved.fallback_status,
            )
        )

    confidence, ambiguity_reasons = _confidence_from_route(primary_profile, matched, overlays)
    route_reason = _build_route_reason(primary_profile, matched, overlays)

    return RouteDecision(
        request_text=request_text,
        coordinator_profile="chief_hermes",
        primary_profile=primary_profile,
        selected_profiles=[hop.profile_id for hop in route_chain],
        route_chain=route_chain,
        route_reason=route_reason,
        validation_status="passed",
        confidence=confidence,
        ambiguity_reasons=ambiguity_reasons,
        max_chain_limit_applied=max_chain_limit_applied,
    )


def decision_to_dict(decision: RouteDecision) -> dict[str, Any]:
    payload = asdict(decision)
    return payload


def decision_to_json(decision: RouteDecision) -> str:
    return json.dumps(decision_to_dict(decision), ensure_ascii=False, indent=2)
