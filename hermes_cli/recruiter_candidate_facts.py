from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .recruiter_context import _DEFAULT_PRIVATE_CAREER_DIR, _PRIVATE_CAREER_FILES


SCHEMA_VERSION = "recruiter_candidate_facts_packet_v1"
SKILL_ID = "candidate-facts"
_SAFE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?:\+\d[\d .()-]{7,}\d|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\(\d{3}\)\s*\d{3}[-. ]?\d{4})"
)
_PATH_MARKERS = ("/home/", "/Users/", "~/", "~/.hermes/private", ".hermes/private", "private/career")
_REDACTED_NEXT_STEP = "CANDIDATE_FACTS_UNSAFE_CONTENT_REDACTED"


class CandidateFactsStatus(str, Enum):
    READY_PROVIDER_VISIBLE = "READY_PROVIDER_VISIBLE"
    BLOCKED_PRIVATE_CONTEXT_MISSING = "BLOCKED_PRIVATE_CONTEXT_MISSING"
    BLOCKED_APPROVAL_REQUIRED = "BLOCKED_APPROVAL_REQUIRED"
    BLOCKED_UNSUPPORTED_FACTS = "BLOCKED_UNSUPPORTED_FACTS"
    BLOCKED_UNSAFE_CONTENT = "BLOCKED_UNSAFE_CONTENT"


@dataclass(slots=True)
class CandidateFactsPacket:
    schema_version: str
    skill_id: str
    status: str
    candidate_ref: str
    generated_at: str
    source_policy: dict[str, Any]
    requires_user_approval: bool
    provider_visibility_status: str
    facts: list[dict[str, Any]]
    source_references: list[dict[str, Any]]
    allowed_claims: list[dict[str, Any]]
    claims_to_avoid: list[str]
    unsupported_claims: list[str]
    redactions: list[str]
    support_summary: dict[str, Any]
    role_target_context: dict[str, Any]
    privacy_notes: list[str]
    next_step: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_candidate_facts_packet(
    *,
    private_context_status: str,
    fixture_payload: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> CandidateFactsPacket:
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    if private_context_status != "PRIVATE_CONTEXT_AVAILABLE":
        return _blocked_packet(
            status=CandidateFactsStatus.BLOCKED_PRIVATE_CONTEXT_MISSING,
            generated_at=timestamp,
            next_step="Provide configured private career context before candidate facts can be reviewed.",
            errors=["private_context_missing"],
            privacy_notes=["No private content was read or serialized."],
        )
    if fixture_payload is None:
        return _blocked_packet(
            status=CandidateFactsStatus.BLOCKED_APPROVAL_REQUIRED,
            generated_at=timestamp,
            next_step="Safe candidate fact extraction is not implemented for real private context in this slice.",
            errors=["approval_required"],
            privacy_notes=["Private context availability was detected through metadata only."],
            requires_user_approval=True,
        )

    facts = [dict(item) for item in fixture_payload.get("facts") or []]
    source_references = [dict(item) for item in fixture_payload.get("source_references") or []]
    allowed_claims = [dict(item) for item in fixture_payload.get("allowed_claims") or []]
    claims_to_avoid = [str(item) for item in fixture_payload.get("claims_to_avoid") or []]
    unsupported_claims = [str(item) for item in fixture_payload.get("unsupported_claims") or []]
    validation_errors = _validate_fixture_payload(
        facts=facts,
        source_references=source_references,
        allowed_claims=allowed_claims,
        claims_to_avoid=claims_to_avoid,
        unsupported_claims=unsupported_claims,
    )
    packet = CandidateFactsPacket(
        schema_version=SCHEMA_VERSION,
        skill_id=SKILL_ID,
        status=CandidateFactsStatus.READY_PROVIDER_VISIBLE.value,
        candidate_ref=str(fixture_payload.get("candidate_ref") or "candidate-fixture"),
        generated_at=timestamp,
        source_policy={
            "configured_private_context_only": True,
            "fixture_mode": True,
            "no_private_file_content_read": True,
            "no_absolute_private_paths_serialized": True,
        },
        requires_user_approval=any(bool(fact.get("approval_required")) for fact in facts),
        provider_visibility_status=CandidateFactsStatus.READY_PROVIDER_VISIBLE.value,
        facts=facts,
        source_references=source_references,
        allowed_claims=allowed_claims,
        claims_to_avoid=claims_to_avoid,
        unsupported_claims=unsupported_claims,
        redactions=list(fixture_payload.get("redactions") or []),
        support_summary=_support_summary(facts),
        role_target_context=dict(fixture_payload.get("role_target_context") or {}),
        privacy_notes=list(fixture_payload.get("privacy_notes") or ["Fixture payload must remain sanitized."]),
        next_step="Candidate facts packet ready for manual review before any downstream integration.",
        warnings=[],
        errors=validation_errors,
        provenance={
            "writes_performed": False,
            "fixture_mode": True,
            "private_context_status": private_context_status,
        },
    )
    if packet.requires_user_approval:
        return _redacted_blocked_packet(
            status=CandidateFactsStatus.BLOCKED_APPROVAL_REQUIRED,
            generated_at=timestamp,
            errors=["approval_required"],
            redactions=[f"approval_required_content_withheld:fact_count={len(facts)}"],
            requires_user_approval=True,
        )
    unsupported_codes = _unsupported_claim_codes(packet)
    if unsupported_codes:
        return _redacted_blocked_packet(
            status=CandidateFactsStatus.BLOCKED_UNSUPPORTED_FACTS,
            generated_at=timestamp,
            errors=unsupported_codes,
            redactions=[f"unsupported_claims_withheld:fact_count={len(facts)}"],
            requires_user_approval=False,
        )
    if packet.errors:
        return _redacted_blocked_packet(
            status=CandidateFactsStatus.BLOCKED_UNSAFE_CONTENT,
            generated_at=timestamp,
            errors=packet.errors,
            redactions=[f"invalid_fixture_fields_redacted:fact_count={len(facts)}"],
            requires_user_approval=True,
        )
    unsafe_codes = detect_unsafe_content(packet.to_dict())
    if unsafe_codes:
        return _redacted_blocked_packet(
            status=CandidateFactsStatus.BLOCKED_UNSAFE_CONTENT,
            generated_at=timestamp,
            errors=unsafe_codes,
            redactions=[
                f"unsafe_fixture_content_redacted:fact_count={len(facts)}",
                f"unsafe_fixture_content_redacted:source_reference_count={len(source_references)}",
            ],
            requires_user_approval=True,
        )
    return packet


def run_candidate_facts_cli(*, fixture_safe_facts_json: str | None = None) -> CandidateFactsPacket:
    fixture_payload = None
    if fixture_safe_facts_json:
        fixture_payload = json.loads(Path(fixture_safe_facts_json).read_text(encoding="utf-8"))
    return build_candidate_facts_packet(
        private_context_status=discover_private_context_status(),
        fixture_payload=fixture_payload,
    )


def load_candidate_facts_packet(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("candidate_facts_packet_missing") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("candidate_facts_packet_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("candidate_facts_packet_json_invalid")
    return payload


def validate_candidate_facts_ready_for_positioning(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return "candidate_facts_packet_missing"
    if payload.get("schema_version") != SCHEMA_VERSION:
        return "candidate_facts_packet_schema_invalid"
    if payload.get("provider_visibility_status") != CandidateFactsStatus.READY_PROVIDER_VISIBLE.value:
        return "candidate_facts_packet_not_provider_visible"
    if payload.get("status") != CandidateFactsStatus.READY_PROVIDER_VISIBLE.value:
        return "candidate_facts_packet_not_provider_visible"
    facts = payload.get("facts")
    if not isinstance(facts, list) or not facts:
        return "candidate_facts_packet_empty_facts"
    if not isinstance(payload.get("allowed_claims"), list):
        return "candidate_facts_packet_invalid"
    if not isinstance(payload.get("source_references"), list):
        return "candidate_facts_packet_invalid"
    if not isinstance(payload.get("claims_to_avoid"), list):
        return "candidate_facts_packet_invalid"
    if not isinstance(payload.get("unsupported_claims"), list):
        return "candidate_facts_packet_invalid"
    unsafe_codes = detect_unsafe_content(payload)
    if unsafe_codes:
        return "candidate_facts_packet_unsafe"
    return None


def discover_private_context_status() -> str:
    private_dir = _DEFAULT_PRIVATE_CAREER_DIR.expanduser()
    present_count = sum(1 for name in _PRIVATE_CAREER_FILES if (private_dir / name).exists())
    if present_count == len(_PRIVATE_CAREER_FILES):
        return "PRIVATE_CONTEXT_AVAILABLE"
    return "PRIVATE_CONTEXT_MISSING"


def validate_no_unsafe_leakage(payload: Any) -> str | None:
    codes = detect_unsafe_content(payload)
    if not codes:
        return None
    return codes[0]


def build_application_materials_ready_fixture_payload() -> dict[str, Any]:
    source_references = [
        _fixture_source_reference(
            source_ref_id="src-candidate-role-1",
            source_label="candidate_role",
            section_label="role_scope",
        ),
        _fixture_source_reference(
            source_ref_id="src-candidate-payments-1",
            source_label="candidate_payments",
            section_label="payments_programs",
        ),
        _fixture_source_reference(
            source_ref_id="src-candidate-metric-1",
            source_label="candidate_metrics",
            section_label="growth_outcomes",
        ),
        _fixture_source_reference(
            source_ref_id="src-candidate-partnerships-1",
            source_label="candidate_partnerships",
            section_label="partner_execution",
        ),
        _fixture_source_reference(
            source_ref_id="src-vacancy-requirement-1",
            source_label="vacancy_requirements",
            section_label="role_expectations",
        ),
    ]
    facts = [
        _fixture_fact(
            fact_id="fact-role-1",
            category="role_history",
            safe_summary="Led a regional product and commercial portfolio across digital payments and adjacent platform services.",
            provider_text="Fictional candidate led a regional product and commercial portfolio spanning digital payments and platform services.",
            source_ref_ids=["src-candidate-role-1"],
            forbidden_expansions=["Do not infer employer names", "Do not infer exact headcount"],
        ),
        _fixture_fact(
            fact_id="fact-payments-1",
            category="domain",
            safe_summary="Delivered payment acceptance, checkout, and recurring billing improvements in regulated markets.",
            provider_text="Fictional candidate delivered payment acceptance, checkout, and recurring billing improvements in regulated markets.",
            source_ref_ids=["src-candidate-payments-1", "src-vacancy-requirement-1"],
            forbidden_expansions=["Do not infer banking licenses", "Do not infer card network ownership"],
        ),
        _fixture_fact(
            fact_id="fact-growth-1",
            category="achievement",
            safe_summary="Improved conversion and reduced onboarding friction with measurable marketplace and payment funnel gains.",
            provider_text="Fictional candidate improved conversion and reduced onboarding friction, producing measurable marketplace and payment funnel gains.",
            source_ref_ids=["src-candidate-metric-1"],
            forbidden_expansions=["Do not infer exact revenue", "Do not infer unsupported margin impact"],
        ),
        _fixture_fact(
            fact_id="fact-partnerships-1",
            category="scope",
            safe_summary="Worked with telecom, merchant, and ecosystem partners on integrations and go-to-market execution.",
            provider_text="Fictional candidate worked with telecom, merchant, and ecosystem partners on integrations and go-to-market execution.",
            source_ref_ids=["src-candidate-partnerships-1", "src-vacancy-requirement-1"],
            forbidden_expansions=["Do not infer exclusive partnerships", "Do not infer signed logos"],
        ),
        _fixture_fact(
            fact_id="fact-market-1",
            category="geography",
            safe_summary="Operated across multi-country launches with region-specific compliance and localization constraints.",
            provider_text="Fictional candidate operated across multi-country launches with region-specific compliance and localization constraints.",
            source_ref_ids=["src-candidate-role-1", "src-vacancy-requirement-1"],
            forbidden_expansions=["Do not infer specific regulators", "Do not infer unsupported jurisdictions"],
            support_level="derived_safe",
        ),
        _fixture_fact(
            fact_id="fact-pricing-1",
            category="achievement",
            safe_summary="Ran pricing and packaging iterations tied to adoption, retention, and partner activation.",
            provider_text="Fictional candidate ran pricing and packaging iterations tied to adoption, retention, and partner activation.",
            source_ref_ids=["src-candidate-metric-1", "src-candidate-payments-1"],
            forbidden_expansions=["Do not infer exact ARPU", "Do not infer board ownership"],
        ),
        _fixture_fact(
            fact_id="fact-risk-1",
            category="constraint",
            safe_summary="Telecom adjacency and executive seniority should be framed carefully unless directly supported in the draft.",
            provider_text="Telecom adjacency and executive seniority should be framed carefully unless directly supported in the draft.",
            source_ref_ids=["src-candidate-role-1", "src-vacancy-requirement-1"],
            forbidden_expansions=["Do not present adjacency as direct category ownership"],
            support_level="weak",
        ),
    ]
    allowed_claims = [
        _fixture_allowed_claim(
            claim_id="claim-1",
            claim_text="Regional product and commercial leadership across payments and platform services.",
            source_fact_ids=["fact-role-1", "fact-payments-1"],
        ),
        _fixture_allowed_claim(
            claim_id="claim-2",
            claim_text="Hands-on payment, checkout, and recurring billing execution in regulated environments.",
            source_fact_ids=["fact-payments-1", "fact-market-1"],
        ),
        _fixture_allowed_claim(
            claim_id="claim-3",
            claim_text="Evidence-backed growth, conversion, and onboarding optimization work.",
            source_fact_ids=["fact-growth-1", "fact-pricing-1"],
        ),
        _fixture_allowed_claim(
            claim_id="claim-4",
            claim_text="Partner integration and cross-functional go-to-market execution with telecom and merchant ecosystems.",
            source_fact_ids=["fact-partnerships-1"],
            support_level="derived_safe",
        ),
        _fixture_allowed_claim(
            claim_id="claim-5",
            claim_text="Multi-country rollout judgment with localization and compliance awareness.",
            source_fact_ids=["fact-market-1"],
            support_level="derived_safe",
        ),
        _fixture_allowed_claim(
            claim_id="claim-6",
            claim_text="Pricing and packaging iteration tied to adoption, retention, and partner activation.",
            source_fact_ids=["fact-pricing-1"],
        ),
    ]
    return {
        "candidate_ref": "candidate-application-materials-fixture",
        "facts": facts,
        "source_references": source_references,
        "allowed_claims": allowed_claims,
        "claims_to_avoid": [
            "Do not claim direct bank or card-network ownership.",
            "Do not present telecom adjacency as direct telecom category leadership.",
            "Do not state exact employer names, revenue, or team size unless explicitly supported.",
        ],
        "unsupported_claims": [
            "End-to-end P and L ownership across all markets.",
            "Named tier-one telecom operator leadership without direct evidence.",
        ],
        "role_target_context": {
            "role_family": "fictional_fintech_product_leadership",
            "target_focus": [
                "payments",
                "platform_growth",
                "partnerships",
                "regional_scaling",
            ],
        },
        "privacy_notes": [
            "synthetic fictional fixture",
            "no private career source content serialized",
            "use softening language for adjacency and seniority where needed",
        ],
    }


def detect_unsafe_content(payload: Any) -> list[str]:
    codes: set[str] = set()
    for value in _iter_strings(payload):
        if any(marker in value for marker in _PATH_MARKERS):
            codes.add("unsafe_path_detected")
        if _EMAIL_RE.search(value) or _PHONE_RE.search(value):
            codes.add("unsafe_contact_detected")
    return sorted(codes)


def _fixture_fact(
    *,
    fact_id: str,
    category: str,
    safe_summary: str,
    provider_text: str,
    source_ref_ids: list[str],
    forbidden_expansions: list[str],
    support_level: str = "explicit",
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "category": category,
        "safe_summary": safe_summary,
        "provider_text": provider_text,
        "support_level": support_level,
        "source_ref_ids": list(source_ref_ids),
        "forbidden_expansions": list(forbidden_expansions),
        "approval_required": False,
        "provider_visible": True,
        "log_visible": True,
    }


def _fixture_source_reference(
    *,
    source_ref_id: str,
    source_label: str,
    section_label: str,
) -> dict[str, Any]:
    return {
        "source_ref_id": source_ref_id,
        "source_type": "test_fixture",
        "source_label": source_label,
        "source_id_hash": build_safe_source_id_hash(source_label, section_label),
        "section_label": section_label,
        "content_hash": build_safe_source_id_hash(source_ref_id, "content"),
        "sensitivity": "private_sanitized",
        "provider_visible": True,
        "log_visible": True,
    }


def _fixture_allowed_claim(
    *,
    claim_id: str,
    claim_text: str,
    source_fact_ids: list[str],
    support_level: str = "explicit",
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "claim_text": claim_text,
        "source_fact_ids": list(source_fact_ids),
        "support_level": support_level,
    }


def _validate_fixture_payload(
    *,
    facts: list[dict[str, Any]],
    source_references: list[dict[str, Any]],
    allowed_claims: list[dict[str, Any]],
    claims_to_avoid: list[str],
    unsupported_claims: list[str],
) -> list[str]:
    errors: set[str] = set()
    fact_ids = set()
    source_ref_ids = set()
    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        fact_ids.add(fact_id)
        if str(fact.get("category")) not in {
            "role_history",
            "domain",
            "scope",
            "achievement",
            "geography",
            "preference",
            "constraint",
        }:
            errors.add("invalid_fact_category")
        if str(fact.get("support_level")) not in {"explicit", "derived_safe", "weak", "unsupported"}:
            errors.add("invalid_fact_support_level")
        if not isinstance(fact.get("source_ref_ids"), list):
            errors.add("fact_source_ref_ids_required")
        if not isinstance(fact.get("forbidden_expansions"), list):
            errors.add("fact_forbidden_expansions_required")
        if bool(fact.get("provider_visible")) and not str(fact.get("provider_text") or "").strip():
            errors.add("provider_visible_fact_requires_provider_text")
    for ref in source_references:
        source_ref_id = str(ref.get("source_ref_id") or "")
        source_ref_ids.add(source_ref_id)
        if str(ref.get("source_type")) not in {
            "structured_resume",
            "opportunity_thesis",
            "company_intel_thesis",
            "scoring_reference",
            "test_fixture",
        }:
            errors.add("invalid_source_type")
        if not _is_safe_label(str(ref.get("source_label") or "")):
            errors.add("unsafe_source_label")
        if not _is_safe_label(str(ref.get("section_label") or "")):
            errors.add("unsafe_section_label")
        if _looks_like_path(str(ref.get("source_id_hash") or "")):
            errors.add("unsafe_source_id_hash")
    for fact in facts:
        for source_ref_id in fact.get("source_ref_ids") or []:
            if str(source_ref_id) not in source_ref_ids:
                errors.add("unknown_source_ref_id")
    for claim in allowed_claims:
        if str(claim.get("support_level")) not in {"explicit", "derived_safe", "weak", "unsupported"}:
            errors.add("invalid_allowed_claim_support_level")
        for fact_id in claim.get("source_fact_ids") or []:
            if str(fact_id) not in fact_ids:
                errors.add("allowed_claim_unknown_source_fact_id")
    for text in [*claims_to_avoid, *unsupported_claims]:
        if not isinstance(text, str):
            errors.add("invalid_claim_text")
    return sorted(errors)


def _unsupported_claim_codes(packet: CandidateFactsPacket) -> list[str]:
    fact_support = {str(fact.get("fact_id")): str(fact.get("support_level")) for fact in packet.facts}
    codes: set[str] = set()
    for claim in packet.allowed_claims:
        if str(claim.get("support_level")) == "unsupported":
            codes.add("unsupported_claims_detected")
        for fact_id in claim.get("source_fact_ids") or []:
            if fact_support.get(str(fact_id)) == "unsupported":
                codes.add("unsupported_claims_detected")
    return sorted(codes)


def _blocked_packet(
    *,
    status: CandidateFactsStatus,
    generated_at: str,
    next_step: str,
    errors: list[str],
    privacy_notes: list[str],
    requires_user_approval: bool = False,
) -> CandidateFactsPacket:
    return CandidateFactsPacket(
        schema_version=SCHEMA_VERSION,
        skill_id=SKILL_ID,
        status=status.value,
        candidate_ref="candidate-private-context",
        generated_at=generated_at,
        source_policy={
            "configured_private_context_only": True,
            "fixture_mode": False,
            "no_private_file_content_read": True,
            "no_absolute_private_paths_serialized": True,
        },
        requires_user_approval=requires_user_approval,
        provider_visibility_status=status.value,
        facts=[],
        source_references=[],
        allowed_claims=[],
        claims_to_avoid=[],
        unsupported_claims=[],
        redactions=["raw_private_content_not_serialized"],
        support_summary={"total_facts": 0, "explicit": 0, "derived_safe": 0, "weak": 0, "unsupported": 0},
        role_target_context={},
        privacy_notes=privacy_notes,
        next_step=next_step,
        warnings=[],
        errors=errors,
        provenance={"writes_performed": False, "fixture_mode": False},
    )


def _redacted_blocked_packet(
    *,
    status: CandidateFactsStatus,
    generated_at: str,
    errors: list[str],
    redactions: list[str],
    requires_user_approval: bool,
) -> CandidateFactsPacket:
    return CandidateFactsPacket(
        schema_version=SCHEMA_VERSION,
        skill_id=SKILL_ID,
        status=status.value,
        candidate_ref="candidate-redacted",
        generated_at=generated_at,
        source_policy={
            "configured_private_context_only": True,
            "fixture_mode": True,
            "no_private_file_content_read": True,
            "no_absolute_private_paths_serialized": True,
        },
        requires_user_approval=requires_user_approval,
        provider_visibility_status=status.value,
        facts=[],
        source_references=[],
        allowed_claims=[],
        claims_to_avoid=[],
        unsupported_claims=[],
        redactions=redactions,
        support_summary={"total_facts": 0, "explicit": 0, "derived_safe": 0, "weak": 0, "unsupported": 0},
        role_target_context={},
        privacy_notes=["Blocked packet redacted for privacy."],
        next_step=_REDACTED_NEXT_STEP,
        warnings=[],
        errors=sorted(set(errors)),
        provenance={"writes_performed": False, "fixture_mode": True},
    )


def _support_summary(facts: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"explicit": 0, "derived_safe": 0, "weak": 0, "unsupported": 0}
    for fact in facts:
        support_level = str(fact.get("support_level"))
        if support_level in counts:
            counts[support_level] += 1
    return {"total_facts": len(facts), **counts}


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_iter_strings(item))
        return strings
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_iter_strings(item))
        return strings
    return []


def _is_safe_label(value: str) -> bool:
    return bool(_SAFE_LABEL_RE.fullmatch(value))


def _looks_like_path(value: str) -> bool:
    if not value:
        return False
    if "/" in value or "\\" in value or value.startswith("~"):
        return True
    if value.endswith(".md") or value.endswith(".json") or value.endswith(".txt"):
        return True
    return False


def build_safe_source_id_hash(label: str, section: str) -> str:
    return hashlib.sha256(f"{label}:{section}".encode("utf-8")).hexdigest()[:16]
