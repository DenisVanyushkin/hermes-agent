"""Preview/write-only Security Auditor review layer for Hermes profile architecture.

This module is intentionally pure and import-light. It records durable security
review artifacts without invoking runtime execution, subprocesses, external
network calls, or any secret inspection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import json
import re

import yaml

from hermes_cli.profile_approval import (
    ApprovalPreview,
    classify_engineer_approval,
    decision_to_dict as approval_decision_to_dict,
)
from hermes_cli.profile_routing import (
    RouteDecision,
    RouteHop,
    decision_to_dict as route_decision_to_dict,
    route_task,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCS_ROOT = REPO_ROOT / "docs"
ALLOWED_SECURITY_REVIEW_STATUSES = {"not_applicable", "pass", "conditional_pass", "fail"}


class SecurityReviewError(RuntimeError):
    """Raised when a security review cannot be produced safely."""


@dataclass(frozen=True)
class EvidenceEntry:
    """Typed evidence entry used by security review artifacts."""

    type: str
    source: Optional[str]
    summary: str


@dataclass
class SecurityReview:
    review_id: str
    timestamp_utc: str
    task_summary: str
    route_decision: dict[str, Any] | None = None
    approval_preview: dict[str, Any] | None = None
    reviewed_profile_chain: list[str] = field(default_factory=list)
    security_triggers: list[str] = field(default_factory=list)
    reviewed_risks: list[str] = field(default_factory=list)
    required_changes: list[str] = field(default_factory=list)
    residual_risks: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    security_review_status: str = "fail"
    status_reason: str = ""
    reviewed_by_profile: str = "security_auditor"
    model_tier: str = "unknown"
    selected_model: str | None = None
    model_fallback_used: bool = False
    artifact_path: str = ""
    write_performed: bool = False
    write_verified: bool = False
    write_error: str | None = None


@dataclass
class SecurityReviewResult:
    review: SecurityReview
    artifact_path: str
    markdown: str
    write_performed: bool
    write_verified: bool
    write_error: str | None = None


_SECURITY_REVIEW_STATUS_ORDER = ("not_applicable", "pass", "conditional_pass", "fail")

_TRIGGER_DEFINITIONS: list[tuple[str, tuple[str, ...]]] = [
    (
        "public exposure",
        (
            "public exposure",
            "publicly expose",
            "expose publicly",
            "expose to public",
            "open to internet",
            "public access",
            "open port",
            "cloudflare",
            "reverse proxy",
            "firewall",
        ),
    ),
    (
        "WebUI access model",
        (
            "webui access",
            "webui login",
            "webui auth",
            "webui session",
            "webui cookie",
            "webui cookies",
            "webui password",
            "webui permission",
            "webui permissions",
            "admin ui",
            "admin interface",
        ),
    ),
    (
        "auth/session/cookies",
        (
            "auth",
            "authentication",
            "session",
            "cookie",
            "cookies",
            "login",
            "signin",
            "sign in",
            "password",
        ),
    ),
    (
        "secrets/tokens/API keys",
        (
            "secret",
            "secrets",
            "token",
            "tokens",
            "api key",
            "api keys",
            "credential",
            "credentials",
        ),
    ),
    (
        "SSH",
        (
            "ssh",
            "ssh tunnel",
            "ssh access",
            "remote shell",
        ),
    ),
    (
        "browser profiles",
        (
            "browser profile",
            "browser profiles",
            "browser-desktop",
            "chrome profile",
            "firefox profile",
        ),
    ),
    (
        "file manager / shell / terminal / git / upload permissions",
        (
            "file manager",
            "shell",
            "terminal",
            "git",
            "upload",
            "workspace permission",
            "workspace permissions",
            "file permission",
            "file permissions",
        ),
    ),
    (
        "scheduler/memory writes",
        (
            "scheduler",
            "timer",
            "cron",
            "memory write",
            "memory writes",
            "state write",
            "persistent memory",
            "memory update",
        ),
    ),
    (
        "tool permissions",
        (
            "tool permission",
            "tool permissions",
            "tool access",
            "allowlist",
            "denylist",
            "grant tool",
            "revoke tool",
        ),
    ),
    (
        "Cloudflare/reverse proxy/firewall",
        (
            "cloudflare",
            "reverse proxy",
            "firewall",
            "nginx",
            "traefik",
        ),
    ),
    (
        "persistent storage of untrusted external content",
        (
            "untrusted content",
            "external content",
            "untrusted external content",
            "prompt injection",
            "uploaded content",
            "persistent storage",
            "store external",
        ),
    ),
]

_TRIGGER_RISK_MAP: dict[str, str] = {
    "public exposure": "Public exposure can widen the blast radius of privileged WebUI/admin access.",
    "WebUI access model": "WebUI access model changes can grant powerful admin capabilities to the wrong boundary.",
    "auth/session/cookies": "Auth/session/cookie handling mistakes can expose or replay privileged sessions.",
    "secrets/tokens/API keys": "Secret, token, or API key leakage can create irreversible compromise risk.",
    "SSH": "SSH access can expose shell-level control over the host and its runtime state.",
    "browser profiles": "Browser profile access can leak user sessions and identity-linked state.",
    "file manager / shell / terminal / git / upload permissions": "File, shell, git, or upload permissions can mutate the workspace or import untrusted content.",
    "scheduler/memory writes": "Scheduler or memory writes can persist decisions beyond the current review context.",
    "tool permissions": "Tool permission changes can expand the agent's capability boundary unsafely.",
    "Cloudflare/reverse proxy/firewall": "Network boundary changes can publish previously private surfaces.",
    "persistent storage of untrusted external content": "Persisting untrusted content can retain prompt-injection or malware-like payloads.",
}

_TRIGGER_REQUIRED_CHANGE_MAP: dict[str, str] = {
    "public exposure": "Keep the WebUI loopback-only or behind an authenticated SSH tunnel; do not publish it publicly.",
    "WebUI access model": "Verify the WebUI access boundary, authentication, and admin authorization model before broadening access.",
    "auth/session/cookies": "Validate auth/session/cookie handling, secure flags, and session invalidation behavior.",
    "secrets/tokens/API keys": "Keep secrets and tokens out of the artifact and verify redaction and storage boundaries.",
    "SSH": "Confirm the SSH access path is intentional and does not expose broader shell access than required.",
    "browser profiles": "Limit browser profile access to the minimum required account/profile set.",
    "file manager / shell / terminal / git / upload permissions": "Restrict file, shell, git, and upload permissions to the minimum required workspace boundary.",
    "scheduler/memory writes": "Avoid unreviewed persistent scheduler or memory writes; record any required durable state changes.",
    "tool permissions": "Keep tool permissions tightly scoped and review any expansion of the tool boundary.",
    "Cloudflare/reverse proxy/firewall": "Do not publish the service until exposure boundaries and firewall/reverse-proxy rules are explicitly verified.",
    "persistent storage of untrusted external content": "Do not persist untrusted external content without explicit sanitization and review.",
}

_PUBLIC_EXPOSURE_MITIGATION_TERMS = (
    "127.0.0.1",
    "localhost",
    "loopback",
    "ssh tunnel",
    "ssh-tunnel",
    "tunnel only",
    "local only",
    "not public",
    "no public exposure",
    "password auth",
    "password authentication",
    "auth enabled",
    "user hermes",
    "not root",
    "firewall closed",
    "port not exposed",
)


def _now_timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filename_timestamp(timestamp_utc: str) -> str:
    cleaned = timestamp_utc.strip().replace("-", "").replace(":", "")
    cleaned = cleaned.replace("T", "T").replace("Z", "Z")
    return cleaned or _now_timestamp_utc().replace("-", "").replace(":", "")


def _slugify(text: str, *, max_length: int = 72) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = "task"
    return slug[:max_length].strip("-") or "task"


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]


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


def _coerce_route_hop(raw_hop: RouteHop | dict[str, Any]) -> RouteHop:
    if isinstance(raw_hop, RouteHop):
        return raw_hop
    if not isinstance(raw_hop, dict):
        raise SecurityReviewError("route_decision route_chain entries must be mappings")
    return RouteHop(
        profile_id=str(raw_hop.get("profile_id", "")),
        routing_reason=str(raw_hop.get("routing_reason", "")),
        model_tier=str(raw_hop.get("model_tier", "unknown")),
        provider=str(raw_hop.get("provider", "")),
        model=str(raw_hop.get("model", "")),
        escalation_reason=str(raw_hop.get("escalation_reason", "")),
        model_resolution_status=str(raw_hop.get("model_resolution_status", "unknown")),
        fallback_status=str(raw_hop.get("fallback_status", "unknown")),
    )


def _route_dict_to_dataclass(data: dict[str, Any]) -> RouteDecision:
    route_chain = [_coerce_route_hop(item) for item in data.get("route_chain", []) or []]
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


def _coerce_route_decision(route_decision: RouteDecision | dict[str, Any] | None, task_summary: str) -> RouteDecision:
    if route_decision is None:
        return route_task(task_summary)
    if isinstance(route_decision, RouteDecision):
        return route_decision
    if isinstance(route_decision, dict):
        return _route_dict_to_dataclass(route_decision)
    raise SecurityReviewError("route_decision must be a RouteDecision or mapping")


def _coerce_approval_preview(approval_preview: ApprovalPreview | dict[str, Any] | None) -> dict[str, Any] | None:
    if approval_preview is None:
        return None
    if isinstance(approval_preview, ApprovalPreview):
        return approval_decision_to_dict(approval_preview)
    if isinstance(approval_preview, dict):
        return {str(key): _to_plain_object(value) for key, value in approval_preview.items()}
    raise SecurityReviewError("approval_preview must be an ApprovalPreview or mapping")


def _normalize_evidence_item(item: Any) -> dict[str, Any]:
    if isinstance(item, EvidenceEntry):
        return {"type": item.type, "source": item.source, "summary": item.summary}
    if isinstance(item, str):
        summary = item.strip()
        if not summary:
            raise SecurityReviewError("evidence items must not be empty")
        return {"type": "operator_note", "source": "cli", "summary": summary}
    if isinstance(item, dict):
        normalized = {
            "type": str(item.get("type", "unknown")).strip() or "unknown",
            "source": item.get("source"),
            "summary": str(item.get("summary", "")).strip(),
        }
        if not normalized["summary"]:
            raise SecurityReviewError("evidence item summary must not be empty")
        if normalized["source"] is not None:
            normalized["source"] = str(normalized["source"])
        return normalized
    if is_dataclass(item):
        mapping = asdict(item)
        return _normalize_evidence_item(mapping)
    summary = str(item).strip()
    if not summary:
        raise SecurityReviewError("evidence items must not be empty")
    return {"type": "operator_note", "source": "cli", "summary": summary}


def _normalize_evidence(entries: Optional[list[Any]]) -> list[dict[str, Any]]:
    if not entries:
        return []
    return [_normalize_evidence_item(item) for item in entries]


def _route_chain_profiles(route_decision: RouteDecision) -> list[str]:
    seen: set[str] = set()
    profiles: list[str] = []
    for hop in route_decision.route_chain:
        if hop.profile_id and hop.profile_id not in seen:
            seen.add(hop.profile_id)
            profiles.append(hop.profile_id)
    for profile in route_decision.selected_profiles:
        if profile and profile not in seen:
            seen.add(profile)
            profiles.append(profile)
    if not profiles and route_decision.primary_profile:
        profiles.append(route_decision.primary_profile)
    return profiles


def _select_review_hop(route_decision: RouteDecision) -> RouteHop | None:
    if not route_decision.route_chain:
        return None
    for hop in route_decision.route_chain:
        if hop.profile_id == "security_auditor":
            return hop
    return route_decision.route_chain[0]


def _extract_model_context(route_decision: RouteDecision) -> tuple[str, str | None, bool]:
    hop = _select_review_hop(route_decision)
    if hop is None:
        return "unknown", None, False
    selected_model = f"{hop.provider}/{hop.model}" if hop.provider and hop.model else None
    fallback_used = str(hop.fallback_status).lower() in {"fallback_used", "used_fallback", "fallback"}
    return hop.model_tier or "unknown", selected_model, fallback_used


def _combined_review_text(
    task_summary: str,
    route_decision: RouteDecision,
    approval_preview: dict[str, Any] | None,
    evidence_entries: list[dict[str, Any]],
) -> str:
    parts = [task_summary, route_decision.route_reason, route_decision.request_text]
    if approval_preview:
        parts.append(str(approval_preview.get("classification_reason", "")))
        parts.append(str(approval_preview.get("intended_change", "")))
        parts.append(str(approval_preview.get("commands_or_control_script", "")))
        parts.append(str(approval_preview.get("expected_effect", "")))
    for entry in evidence_entries:
        parts.append(str(entry.get("summary", "")))
        parts.append(str(entry.get("type", "")))
        parts.append(str(entry.get("source", "")))
    return _normalize_text(" ".join(part for part in parts if part))


def _trigger_matches(normalized_text: str) -> list[str]:
    matched: list[str] = []
    for trigger_name, phrases in _TRIGGER_DEFINITIONS:
        if any(phrase in normalized_text for phrase in phrases):
            matched.append(trigger_name)
            continue
        if trigger_name == "WebUI access model" and "webui" in normalized_text:
            if any(term in normalized_text for term in ("access", "login", "session", "cookie", "cookies", "auth", "password", "admin")):
                matched.append(trigger_name)
        elif trigger_name == "auth/session/cookies":
            if any(term in normalized_text for term in ("auth", "authentication", "session", "cookie", "cookies", "login", "signin", "sign in", "password")):
                matched.append(trigger_name)
    return matched


def _evidence_contains_any(normalized_text: str, terms: tuple[str, ...]) -> bool:
    return any(term in normalized_text for term in terms)


def _has_public_exposure_support(normalized_text: str) -> bool:
    return _evidence_contains_any(normalized_text, _PUBLIC_EXPOSURE_MITIGATION_TERMS)


def _derive_reviewed_risks(triggers: list[str]) -> list[str]:
    risks: list[str] = []
    for trigger in triggers:
        risk = _TRIGGER_RISK_MAP.get(trigger)
        if risk and risk not in risks:
            risks.append(risk)
    return risks


def _derive_required_changes(triggers: list[str], explicit_required_changes: list[str]) -> list[str]:
    if explicit_required_changes:
        return explicit_required_changes
    derived: list[str] = []
    for trigger in triggers:
        change = _TRIGGER_REQUIRED_CHANGE_MAP.get(trigger)
        if change and change not in derived:
            derived.append(change)
    return derived


def _derive_residual_risks(triggers: list[str], reviewed_risks: list[str], explicit_residual_risks: list[str]) -> list[str]:
    if explicit_residual_risks:
        return explicit_residual_risks
    if not triggers:
        return []
    return [risk for risk in reviewed_risks]


def _security_review_status(
    *,
    triggers: list[str],
    reviewed_profile_chain: list[str],
    evidence_entries: list[dict[str, Any]],
    required_changes: list[str],
    residual_risks: list[str],
    normalized_text: str,
) -> tuple[str, str]:
    evidence_present = bool(evidence_entries)
    security_auditor_in_chain = "security_auditor" in reviewed_profile_chain

    if not triggers and not security_auditor_in_chain:
        return "not_applicable", "No security trigger matched and security_auditor is not in the route chain."

    if not evidence_present:
        if triggers:
            return "fail", "Security-sensitive review is insufficiently evidenced."
        return "fail", "Security auditor review is in-scope but evidence is insufficient."

    has_public_exposure = "public exposure" in triggers or "Cloudflare/reverse proxy/firewall" in triggers
    if has_public_exposure:
        if not _has_public_exposure_support(normalized_text):
            return "fail", "Public exposure, Cloudflare, reverse proxy, or firewall changes require explicit loopback/SSH-tunnel/auth mitigations."
        if required_changes or residual_risks:
            return "conditional_pass", "Public exposure-related review completed with explicit required changes and residual risks."
        return "conditional_pass", "Public exposure-related review completed with explicit mitigations; conservative status remains conditional_pass."

    if required_changes or residual_risks:
        return "conditional_pass", "Security review completed with required changes and residual risks that must be addressed before execution."

    return "pass", "Security risks reviewed; evidence is sufficient and no required changes remain."


def _review_id_for(task_summary: str, timestamp_utc: str, review_id: str | None) -> str:
    if review_id and review_id.strip():
        return review_id.strip()
    return f"{_filename_timestamp(timestamp_utc)}-{_slugify(task_summary)}"


def _artifact_path_for(
    review: SecurityReview,
    *,
    output_root: Path | str | None = None,
) -> Path:
    base_root = Path(output_root) if output_root is not None else DEFAULT_DOCS_ROOT
    base_root = base_root.expanduser()
    timestamp = _filename_timestamp(review.timestamp_utc)
    slug = _slugify(review.task_summary)
    return base_root / "security-reviews" / review.timestamp_utc[:10] / f"{timestamp}-{slug}.md"


def _validate_target_path(target_path: Path, output_root: Path) -> None:
    resolved_root = output_root.resolve(strict=False)
    resolved_target = target_path.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise SecurityReviewError("target path escapes the allowed output root") from exc


def _metadata_for_front_matter(review: SecurityReview) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "review_id": review.review_id,
        "timestamp_utc": review.timestamp_utc,
        "reviewed_by_profile": review.reviewed_by_profile,
        "security_review_status": review.security_review_status,
        "security_triggers": review.security_triggers,
        "write_verified": review.write_verified,
    }


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def _evidence_block(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "- None"
    lines: list[str] = []
    for entry in entries:
        lines.append(f"- type: {entry.get('type', 'unknown')}")
        lines.append(f"  source: {entry.get('source', 'null') if entry.get('source', None) is not None else 'null'}")
        lines.append(f"  summary: {entry.get('summary', '')}")
    return "\n".join(lines)


def _json_block(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, indent=2)


def render_security_review_markdown(review: SecurityReview) -> str:
    if not isinstance(review, SecurityReview):
        raise SecurityReviewError("render_security_review_markdown expects a SecurityReview")

    metadata = yaml.safe_dump(_metadata_for_front_matter(review), sort_keys=False, default_flow_style=False).strip()
    route_block = _json_block(review.route_decision)
    approval_block = _json_block(review.approval_preview)

    parts = [
        "---",
        metadata,
        "---",
        "# Security Review",
        "",
        "## Summary",
        f"- review_id: {review.review_id}",
        f"- timestamp_utc: {review.timestamp_utc}",
        f"- reviewed_by_profile: {review.reviewed_by_profile}",
        f"- task_summary: {review.task_summary}",
        f"- model_tier: {review.model_tier}",
        f"- selected_model: {review.selected_model if review.selected_model is not None else 'null'}",
        f"- model_fallback_used: {str(review.model_fallback_used).lower()}",
        f"- artifact_path: {review.artifact_path}",
        f"- write_performed: {str(review.write_performed).lower()}",
        f"- write_verified: {str(review.write_verified).lower()}",
        "",
        "## Security Triggers",
        _bullet_list(review.security_triggers),
        "",
        "## Reviewed Risks",
        _bullet_list(review.reviewed_risks),
        "",
        "## Required Changes",
        _bullet_list(review.required_changes),
        "",
        "## Residual Risks",
        _bullet_list(review.residual_risks),
        "",
        "## Evidence",
        _evidence_block(review.evidence),
        "",
        "## Assumptions",
        _bullet_list(review.assumptions),
        "",
        "## Route Decision",
        "```json",
        route_block,
        "```",
        "",
        "## Approval Preview",
        "```json",
        approval_block,
        "```",
        "",
        "## Security Review Status",
        f"- security_review_status: {review.security_review_status}",
        f"- status_reason: {review.status_reason}",
        f"- reviewed_profile_chain: {json.dumps(review.reviewed_profile_chain, ensure_ascii=False)}",
        f"- reviewed_by_profile: {review.reviewed_by_profile}",
        f"- model_tier: {review.model_tier}",
        f"- selected_model: {review.selected_model if review.selected_model is not None else 'null'}",
        f"- model_fallback_used: {str(review.model_fallback_used).lower()}",
        f"- artifact_path: {review.artifact_path}",
        f"- write_performed: {str(review.write_performed).lower()}",
        f"- write_verified: {str(review.write_verified).lower()}",
        f"- write_error: {review.write_error if review.write_error is not None else 'null'}",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _build_review_core(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[Any]] = None,
    assumptions: Optional[list[str]] = None,
    required_changes: Optional[list[str]] = None,
    residual_risks: Optional[list[str]] = None,
    review_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    output_root: Path | str | None = None,
) -> SecurityReview:
    if not isinstance(task_summary, str) or not task_summary.strip():
        raise SecurityReviewError("task_summary must be a non-empty string")

    timestamp_value = (timestamp_utc or _now_timestamp_utc()).strip() or _now_timestamp_utc()
    review_id_value = _review_id_for(task_summary, timestamp_value, review_id)
    route_obj = _coerce_route_decision(route_decision, task_summary)
    approval_obj = _coerce_approval_preview(approval_preview)
    evidence_entries = _normalize_evidence(evidence)
    if approval_obj is None:
        approval_obj = _to_plain_object(
            classify_engineer_approval(
                task_summary,
                route_decision=route_obj,
                evidence_before=[entry["summary"] for entry in evidence_entries],
            )
        )

    reviewed_profile_chain = _route_chain_profiles(route_obj)
    triggers_text = _combined_review_text(task_summary, route_obj, approval_obj, evidence_entries)
    security_triggers = _trigger_matches(triggers_text)
    reviewed_risks = _derive_reviewed_risks(security_triggers)
    explicit_required_changes = _ensure_list(required_changes)
    explicit_residual_risks = _ensure_list(residual_risks)
    required_changes_value = explicit_required_changes
    residual_risks_value = explicit_residual_risks
    security_review_status, status_reason = _security_review_status(
        triggers=security_triggers,
        reviewed_profile_chain=reviewed_profile_chain,
        evidence_entries=evidence_entries,
        required_changes=required_changes_value,
        residual_risks=residual_risks_value,
        normalized_text=triggers_text,
    )
    model_tier, selected_model, model_fallback_used = _extract_model_context(route_obj)
    artifact_path = str(_artifact_path_for(
        SecurityReview(
            review_id=review_id_value,
            timestamp_utc=timestamp_value,
            task_summary=task_summary.strip(),
        ),
        output_root=output_root,
    ))

    return SecurityReview(
        review_id=review_id_value,
        timestamp_utc=timestamp_value,
        task_summary=task_summary.strip(),
        route_decision=_to_plain_object(route_obj),
        approval_preview=approval_obj,
        reviewed_profile_chain=reviewed_profile_chain,
        security_triggers=security_triggers,
        reviewed_risks=reviewed_risks,
        required_changes=required_changes_value,
        residual_risks=residual_risks_value,
        evidence=evidence_entries,
        assumptions=_ensure_list(assumptions),
        security_review_status=security_review_status,
        status_reason=status_reason,
        reviewed_by_profile="security_auditor",
        model_tier=model_tier,
        selected_model=selected_model,
        model_fallback_used=model_fallback_used,
        artifact_path=artifact_path,
        write_performed=False,
        write_verified=False,
        write_error=None,
    )


def build_security_review(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[Any]] = None,
    assumptions: Optional[list[str]] = None,
    required_changes: Optional[list[str]] = None,
    residual_risks: Optional[list[str]] = None,
    review_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    output_root: Path | str | None = None,
) -> SecurityReview:
    return _build_review_core(
        task_summary,
        route_decision=route_decision,
        approval_preview=approval_preview,
        evidence=evidence,
        assumptions=assumptions,
        required_changes=required_changes,
        residual_risks=residual_risks,
        review_id=review_id,
        timestamp_utc=timestamp_utc,
        output_root=output_root,
    )


def decision_to_dict(review: SecurityReview) -> dict[str, Any]:
    if not isinstance(review, SecurityReview):
        raise SecurityReviewError("decision_to_dict expects a SecurityReview")
    return _to_plain_object(asdict(review))


def decision_to_json(review: SecurityReview) -> str:
    return json.dumps(decision_to_dict(review), ensure_ascii=False, indent=2)


def result_to_dict(result: SecurityReviewResult) -> dict[str, Any]:
    if not isinstance(result, SecurityReviewResult):
        raise SecurityReviewError("result_to_dict expects a SecurityReviewResult")
    return {
        "artifact_path": result.artifact_path,
        "write_performed": result.write_performed,
        "write_verified": result.write_verified,
        "write_error": result.write_error,
        "review": decision_to_dict(result.review),
    }


def result_to_json(result: SecurityReviewResult) -> str:
    return json.dumps(result_to_dict(result), ensure_ascii=False, indent=2)


def _write_result(
    review: SecurityReview,
    *,
    output_root: Path | str | None = None,
) -> SecurityReviewResult:
    artifact_path = _artifact_path_for(review, output_root=output_root)
    docs_root = Path(output_root) if output_root is not None else DEFAULT_DOCS_ROOT
    docs_root = docs_root.expanduser()
    review.artifact_path = str(artifact_path)
    markdown = render_security_review_markdown(review)
    result = SecurityReviewResult(
        review=review,
        artifact_path=str(artifact_path),
        markdown=markdown,
        write_performed=True,
        write_verified=False,
        write_error=None,
    )

    try:
        if artifact_path.exists():
            raise SecurityReviewError("artifact already exists; refusing to overwrite silently")
        _validate_target_path(artifact_path, docs_root)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(markdown, encoding="utf-8")
        written_back = artifact_path.read_text(encoding="utf-8")
        if written_back != markdown:
            raise SecurityReviewError("written artifact did not round-trip exactly")
        review.write_performed = True
        review.write_verified = True
        review.write_error = None
        result.write_verified = True
    except FileNotFoundError as exc:
        review.write_performed = True
        review.write_verified = False
        review.write_error = str(exc)
        result.write_error = str(exc)
    except OSError as exc:
        review.write_performed = True
        review.write_verified = False
        review.write_error = str(exc)
        result.write_error = str(exc)
    except SecurityReviewError as exc:
        review.write_performed = True
        review.write_verified = False
        review.write_error = str(exc)
        result.write_error = str(exc)
    return result


def preview_security_review(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[Any]] = None,
    assumptions: Optional[list[str]] = None,
    required_changes: Optional[list[str]] = None,
    residual_risks: Optional[list[str]] = None,
    review_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    output_root: Path | str | None = None,
    write: bool = False,
) -> SecurityReviewResult:
    review = _build_review_core(
        task_summary,
        route_decision=route_decision,
        approval_preview=approval_preview,
        evidence=evidence,
        assumptions=assumptions,
        required_changes=required_changes,
        residual_risks=residual_risks,
        review_id=review_id,
        timestamp_utc=timestamp_utc,
        output_root=output_root,
    )

    if not write:
        review.write_performed = False
        review.write_verified = False
        review.write_error = None
        return SecurityReviewResult(
            review=review,
            artifact_path=review.artifact_path,
            markdown=render_security_review_markdown(review),
            write_performed=False,
            write_verified=False,
            write_error=None,
        )

    return _write_result(review, output_root=output_root)


def write_security_review(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[Any]] = None,
    assumptions: Optional[list[str]] = None,
    required_changes: Optional[list[str]] = None,
    residual_risks: Optional[list[str]] = None,
    review_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    output_root: Path | str | None = None,
) -> SecurityReviewResult:
    review = _build_review_core(
        task_summary,
        route_decision=route_decision,
        approval_preview=approval_preview,
        evidence=evidence,
        assumptions=assumptions,
        required_changes=required_changes,
        residual_risks=residual_risks,
        review_id=review_id,
        timestamp_utc=timestamp_utc,
        output_root=output_root,
    )
    return _write_result(review, output_root=output_root)
