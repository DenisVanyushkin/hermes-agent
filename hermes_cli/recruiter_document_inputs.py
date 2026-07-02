from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .recruiter_skill_execution import RecruiterSkillExecutionReport


ALLOWED_DOCUMENT_TYPES = [
    "cover_letter",
    "recruiter_message",
    "linkedin_dm",
    "follow_up",
    "cv_tailoring_notes",
    "application_answer",
    "executive_bio",
]
REQUIRED_POSITIONING_FIELDS = [
    "positioning_summary",
    "evidence_map",
    "proven_facts",
    "derived_positioning",
    "gaps",
    "risks_and_mitigations",
]
_OUTWARD_FACING_DOCUMENT_TYPES = {"cover_letter", "recruiter_message"}
_SAFE_CLAIM_LIMITS = {
    "cover_letter": 3,
    "recruiter_message": 2,
}
_SUPPORT_LEVEL_MAP = {
    "explicit": "direct",
    "derived_safe": "adjacent",
    "weak": "limited",
}
_READY_EXECUTION_STATUSES = {"EXECUTION_READY", "SUCCESS", "READY"}
_READY_POSITIONING_STATUSES = {"SUCCESS", "READY", "POSITIONING_AVAILABLE"}
_POSITIONING_REQUIRED_REASON = "document-writer requires positioning-and-evidence output packet"
_SOT_REFERENCES = [
    "docs/hermes-recruiter-action-plan.md",
    "docs/hermes-recruiter-skill-package-architecture-sot.md",
    "docs/career-search-source-of-truth.md",
]
_SKILL_REFERENCES = [
    "role-packages/recruiter/skills/document-writer/SKILL.md",
    "role-packages/recruiter/skills/document-reviewer/SKILL.md",
]
_FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "execute_document_writer",
    "generate_final_draft",
    "send_outbound_message",
    "apply_to_job",
    "write_crm",
    "write_job_intel_db",
    "read_private_file_contents",
    "mutate_live_config",
    "restart_gateway",
]
_BROAD_OVERCLAIM_PHRASES = [
    "product executive",
    "senior executive",
    "payments leader",
    "fintech leader",
    "product leadership across complex organizations",
    "owning product strategy",
    "strategy ownership",
    "roadmap ownership",
    "commercial ownership",
    "pricing ownership",
    "monetization ownership",
    "multi-team leadership",
    "executive-level ownership",
    "p and l ownership",
    "p&l ownership",
]
_ADJACENT_KEYWORDS = (
    "payments",
    "payment",
    "platform",
    "telecom",
    "commercial",
    "pricing",
    "monetization",
    "partner",
    "growth",
    "regulated",
)
_DOCUMENT_TYPE_CONSTRAINTS = {
    "cover_letter": {
        "target_audience": "Hiring manager",
        "genre": "submission_ready_cover_letter",
        "tone": "professional, confident, concrete, and naturally evidence-grounded",
        "must_include": [
            "a submission-ready letter structure",
            "role-relevant supported achievements from the provided evidence",
            "natural language that connects evidence to likely role needs without overclaiming",
        ],
        "must_avoid": [
            "synthetic packet",
            "evidence packet",
            "source-backed claims",
            "conditional claims",
            "reviewer",
            "Hermes",
            "disclaimer paragraph",
            "unsupported product leadership",
            "unsupported strategy ownership",
            "unsupported roadmap ownership",
            "unsupported commercial ownership",
            "unsupported pricing ownership",
            "unsupported monetization ownership",
            "unsupported multi-team leadership",
            "unsupported payments leadership",
            "unsupported telecom category leadership",
            "unsupported commercially sensitive product environments",
            "unsupported employers",
            "unsupported dates",
            "unsupported metrics",
            "unsupported team sizes",
            "unsupported revenue numbers",
            "unsupported product names",
            "unsupported outcomes",
        ],
        "grounding_rules": [
            "Use only facts supported by the provided evidence.",
            "If a claim is not directly supported, omit it or phrase it as adjacent or transferable experience without unsupported specifics.",
            "Do not convert broad positioning_summary or recommended_angle language into stronger ownership, seniority, or leadership claims.",
            "Do not claim product leadership, strategy ownership, roadmap ownership, commercial ownership, pricing ownership, monetization ownership, or multi-team leadership unless the evidence explicitly supports it.",
            "If a claim appears in claims_to_avoid, unsupported_claims, or risk notes, do not use it.",
            "If the evidence is adjacent rather than direct, soften payments, platform, telecom, or commercially sensitive experience instead of presenting it as direct ownership.",
            "Prefer specific supported examples over broad leadership or seniority branding.",
            "If concrete metrics, outcomes, or scope are unavailable, write a modest fit statement instead of inventing scale or impact.",
            "When in doubt, omit or soften the claim rather than overstate it.",
            "Do not invent employers, dates, metrics, team sizes, revenue numbers, product names, or outcomes.",
            "Ground each paragraph in either role needs or provided evidence.",
            "Do not mention internal artifacts, internal review, or evidence disclaimer language.",
        ],
        "review_success_criteria": [
            "reads like a submission-ready cover letter rather than internal analysis",
            "stays concrete without unsupported specifics",
            "contains no internal or meta evidence language",
        ],
    },
    "recruiter_message": {
        "target_audience": "Recruiter",
        "genre": "concise_recruiter_message",
        "tone": "short, concrete, human, and professional",
        "must_include": [
            "a concise recruiter-facing outreach or application message",
            "2-4 short sentences maximum",
            "one role-interest sentence",
            "one evidence-backed fit sentence",
            "optional soft call-to-action",
        ],
        "must_avoid": [
            "synthetic packet",
            "evidence packet",
            "source-backed claims",
            "conditional claims",
            "reviewer",
            "Hermes",
            "product executive",
            "senior executive",
            "payments leader",
            "fintech leader",
            "commercial product executive",
            "commercially sensitive environments",
            "strategic bets",
            "monetization launches",
            "cross-functional launches",
            "executive-level ownership",
            "unsupported metrics",
            "unsupported team sizes",
            "unsupported dates",
            "unsupported employer names",
            "unsupported revenue numbers",
            "unsupported product scale",
            "unsupported outcomes",
            "no generic \"I am uniquely positioned\" style",
            "no broad executive branding paragraph",
        ],
        "grounding_rules": [
            "Use only supported claims from the provided evidence.",
            "Use only 1-2 of the strongest supported claims from the provided evidence.",
            "Keep the message to 2-4 short sentences with no cover-letter structure.",
            "Include one role-interest sentence, one evidence-backed fit sentence, and at most one soft call-to-action.",
            "Do not import broad positioning_summary or recommended_angle branding blindly.",
            "If a claim is only adjacent rather than exact, soften it with phrases like relevant adjacent experience, could be relevant, experience that may map well to, or background across.",
            "If a claim appears in claims_to_avoid, unsupported_claims, or risk notes, do not use it.",
            "Do not use broad executive-branding language or unsupported title/seniority claims.",
            "Do not invent payments leadership, strategic bets, monetization launches, cross-functional launches, executive-level ownership, team size, metrics, revenue, product scale, employer names, dates, or outcomes.",
            "Avoid unsupported specifics and internal evidence disclaimers.",
            "When in doubt, omit the claim rather than overstate it.",
            "Keep the message short, concrete, human, and naturally conservative.",
        ],
        "review_success_criteria": [
            "is 2-4 short sentences, recruiter-facing, and concrete",
            "contains no internal or meta evidence language",
            "avoids unsupported specifics",
            "uses only 1-2 supported claims without stacking unsupported claims",
            "softens adjacent experience rather than overstating it",
        ],
    },
    "cv_tailoring_notes": {
        "target_audience": "Recruiter",
        "genre": "analytical_cv_tailoring_notes",
        "tone": "analytical, explicit, and evidence-aware",
        "must_include": [
            "supported claims to use",
            "unsupported claims to avoid",
            "gaps or missing information warnings when relevant",
            "evidence-aware CV tailoring guidance",
        ],
        "must_avoid": [
            "invented facts",
            "outbound or submission language that implies the draft was sent",
        ],
        "grounding_rules": [
            "It may explicitly list supported claims, unsupported claims to avoid, gaps, and evidence notes.",
            "Separate use claims from avoid claims when practical.",
            "Keep analytical language explicit rather than polishing it into a submission-ready letter.",
        ],
        "review_success_criteria": [
            "clearly distinguishes use versus avoid claims",
            "preserves analytical evidence and gap language",
            "does not invent unsupported specifics",
        ],
    },
}


class RecruiterDocumentInputStatus(str, Enum):
    READY = "READY"
    POSITIONING_REQUIRED = "POSITIONING_REQUIRED"
    BLOCKED_INVALID_EXECUTION_REPORT = "BLOCKED_INVALID_EXECUTION_REPORT"
    BLOCKED_POSITIONING_RESULT_INVALID = "BLOCKED_POSITIONING_RESULT_INVALID"
    BLOCKED_UNSUPPORTED_DOCUMENT_TYPE = "BLOCKED_UNSUPPORTED_DOCUMENT_TYPE"
    BLOCKED_MISSING_VACANCY_CONTEXT = "BLOCKED_MISSING_VACANCY_CONTEXT"


@dataclass(slots=True)
class RecruiterDocumentInputPacket:
    status: RecruiterDocumentInputStatus
    document_writer_input: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))
    downstream_gates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def build_recruiter_document_writer_input_packet(
    execution_report: dict[str, Any] | RecruiterSkillExecutionReport,
    document_type: str,
    audience: str | None = None,
    purpose: str | None = None,
) -> RecruiterDocumentInputPacket:
    report = _report_dict(execution_report)
    if report is None:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_INVALID_EXECUTION_REPORT,
            errors=["invalid_execution_report_type"],
        )

    warnings = _dedupe(_string_list(report.get("warnings")))
    errors = _dedupe(_string_list(report.get("errors")))
    provenance = {
        **_dict(report.get("provenance")),
        "writes_performed": False,
        "builder": "recruiter_document_inputs",
    }

    if document_type not in ALLOWED_DOCUMENT_TYPES:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_UNSUPPORTED_DOCUMENT_TYPE,
            warnings=warnings,
            errors=[*errors, f"unsupported_document_type:{document_type}"],
            provenance=provenance,
        )

    execution_status = str(report.get("status") or "")
    vacancy_result = _dict(report.get("vacancy_evaluation_result"))
    positioning_result = _dict(report.get("positioning_evidence_result"))
    gate = _dict(_dict(report.get("downstream_gates")).get("document_writer"))
    gate_status = str(gate.get("status") or "")

    if execution_status not in _READY_EXECUTION_STATUSES:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_INVALID_EXECUTION_REPORT,
            warnings=warnings,
            errors=[*errors, f"execution_report_not_ready:{execution_status or 'UNKNOWN'}"],
            provenance=provenance,
        )

    if audience is None:
        warnings.append("audience_missing")
    if purpose is None:
        warnings.append("purpose_missing")

    if not vacancy_result:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_MISSING_VACANCY_CONTEXT,
            warnings=warnings,
            errors=[*errors, "vacancy_evaluation_result_missing"],
            provenance=provenance,
        )

    if not positioning_result or gate_status != "POSITIONING_AVAILABLE":
        return _blocked_packet(
            RecruiterDocumentInputStatus.POSITIONING_REQUIRED,
            warnings=warnings,
            errors=[*errors, _POSITIONING_REQUIRED_REASON],
            provenance=provenance,
        )

    positioning_status = str(positioning_result.get("status") or "")
    missing_fields = [field for field in REQUIRED_POSITIONING_FIELDS if field not in positioning_result]
    if positioning_status not in _READY_POSITIONING_STATUSES or missing_fields:
        invalid_errors = list(errors)
        if positioning_status not in _READY_POSITIONING_STATUSES:
            invalid_errors.append(f"positioning_result_not_ready:{positioning_status or 'UNKNOWN'}")
        if missing_fields:
            invalid_errors.append(f"missing_positioning_fields:{','.join(missing_fields)}")
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_POSITIONING_RESULT_INVALID,
            warnings=warnings,
            errors=invalid_errors,
            provenance=provenance,
        )

    return RecruiterDocumentInputPacket(
        status=RecruiterDocumentInputStatus.READY,
        document_writer_input={
            "skill_id": "document-writer",
            "status": "READY",
            "requested_document_type": document_type,
            "document_type": document_type,
            "audience": audience,
            "purpose": purpose,
            "source_execution_report_ref": {
                "flow_id": report.get("flow_id"),
                "execution_status": execution_status,
            },
            "source_positioning_packet_ref": {
                "skill_id": positioning_result.get("skill_id"),
                "status": positioning_status,
            },
            "vacancy_evaluation_result": vacancy_result,
            "positioning_evidence_result": positioning_result,
            "required_source_fields": list(REQUIRED_POSITIONING_FIELDS),
            "allowed_document_types": list(ALLOWED_DOCUMENT_TYPES),
            "skill_references": list(_SKILL_REFERENCES),
            "source_of_truth_references": list(_SOT_REFERENCES),
            "expected_future_output_schema": "recruiter_document_packet_v1",
            "expected_future_output_fields": [
                "schema_version",
                "document_type",
                "audience",
                "purpose",
                "source_positioning_packet_id",
                "draft",
                "review",
                "status",
            ],
            "document_constraints": _document_constraints(document_type, audience),
            **_outward_facing_claim_guidance(document_type, positioning_result),
            "boundaries": {
                "no_invented_facts": True,
                "use_only_positioning_evidence": True,
                "unsupported_claims_must_be_omitted_or_flagged": True,
                "draft_only": True,
                "user_review_required": True,
                "do_not_imply_application_submission": True,
                "no_outbound": True,
                "no_crm_write": True,
                "no_job_intel_db_write": True,
                "no_private_file_content_read": True,
                "no_provider_call_in_this_slice": True,
            },
            "provenance": {
                "execution_report": provenance,
                "document_writer_gate": gate,
            },
        },
        warnings=warnings,
        errors=errors,
        provenance=provenance,
        downstream_gates={
            "document_writer": {
                "skill_id": "document-writer",
                "status": "READY_FOR_INPUT",
                "reason": "document-writer input packet ready; writer has not executed",
                "requires": ["positioning-and-evidence"],
                "references": list(_SKILL_REFERENCES),
            }
        },
    )


def _blocked_packet(
    status: RecruiterDocumentInputStatus,
    *,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> RecruiterDocumentInputPacket:
    return RecruiterDocumentInputPacket(
        status=status,
        document_writer_input=None,
        warnings=_dedupe(warnings or []),
        errors=_dedupe(errors or []),
        provenance={**(provenance or {}), "writes_performed": False, "builder": "recruiter_document_inputs"},
        downstream_gates={
            "document_writer": {
                "skill_id": "document-writer",
                "status": "POSITIONING_REQUIRED",
                "reason": _POSITIONING_REQUIRED_REASON,
                "requires": ["positioning-and-evidence"],
                "references": list(_SKILL_REFERENCES),
            }
        },
    )


def _report_dict(value: dict[str, Any] | RecruiterSkillExecutionReport) -> dict[str, Any] | None:
    if isinstance(value, RecruiterSkillExecutionReport):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return None


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _document_constraints(document_type: str, audience: str | None) -> dict[str, Any]:
    constraints = dict(_DOCUMENT_TYPE_CONSTRAINTS.get(document_type) or {})
    if not constraints:
        return {
            "document_type": document_type,
            "target_audience": audience,
        }
    return {
        "document_type": document_type,
        "target_audience": audience or constraints.get("target_audience"),
        "genre": constraints["genre"],
        "tone": constraints["tone"],
        "must_include": list(constraints["must_include"]),
        "must_avoid": list(constraints["must_avoid"]),
        "grounding_rules": list(constraints["grounding_rules"]),
        "review_success_criteria": list(constraints["review_success_criteria"]),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _outward_facing_claim_guidance(document_type: str, positioning_result: dict[str, Any]) -> dict[str, Any]:
    if document_type not in _OUTWARD_FACING_DOCUMENT_TYPES:
        return {}
    return {
        "safe_claims_for_document": _select_safe_claims_for_document(document_type, positioning_result),
        "claim_source_priority": {
            "primary": "safe_claims_for_document",
            "context_only": ["positioning_summary", "recommended_angle"],
        },
        "writer_guidance": {
            "safe_claims_for_document_primary": True,
            "positioning_summary_context_only": True,
            "recommended_angle_context_only": True,
        },
    }


def _select_safe_claims_for_document(document_type: str, positioning_result: dict[str, Any]) -> list[dict[str, Any]]:
    limit = _SAFE_CLAIM_LIMITS[document_type]
    evidence_items = [item for item in positioning_result.get("evidence_items") or [] if isinstance(item, dict)]
    source_references = [item for item in positioning_result.get("source_references") or [] if isinstance(item, dict)]
    source_ref_ids = {
        str(item.get("source_ref_id") or "")
        for item in source_references
        if str(item.get("source_ref_id") or "")
    }
    evidence_by_fact_id = {
        str(fact_id): item
        for item in evidence_items
        for fact_id in item.get("source_fact_ids") or []
        if isinstance(fact_id, str)
    }
    blocked_phrases = {
        phrase.lower()
        for phrase in [
            *_BROAD_OVERCLAIM_PHRASES,
            *_string_list(positioning_result.get("claims_to_avoid")),
            *_string_list(positioning_result.get("unsupported_claims")),
            *_string_list(positioning_result.get("risks_and_mitigations")),
        ]
        if phrase
    }

    candidates: list[dict[str, Any]] = []
    for claim in positioning_result.get("allowed_claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_text = str(claim.get("claim_text") or "").strip()
        if not claim_text:
            continue
        normalized_claim = claim_text.lower()
        if any(phrase in normalized_claim or normalized_claim in phrase for phrase in blocked_phrases):
            continue
        source_fact_ids = [str(item) for item in claim.get("source_fact_ids") or [] if isinstance(item, str)]
        if not source_fact_ids:
            continue
        linked_evidence_items = [evidence_by_fact_id[fact_id] for fact_id in source_fact_ids if fact_id in evidence_by_fact_id]
        linked_source_ref_ids = _dedupe(
            [
                str(source_ref_id)
                for item in linked_evidence_items
                for source_ref_id in item.get("source_ref_ids") or []
                if isinstance(source_ref_id, str) and str(source_ref_id) in source_ref_ids
            ]
        )
        if not linked_source_ref_ids:
            continue
        support_level = _normalized_support_level(
            str(claim.get("support_level") or ""),
            linked_evidence_items,
        )
        softening_required = support_level != "direct" or any(keyword in normalized_claim for keyword in _ADJACENT_KEYWORDS)
        safe_wording = _safe_wording_for_claim(claim_text, support_level, softening_required)
        do_not_say = _claim_do_not_say(claim_text)
        candidates.append(
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "claim": claim_text,
                "safe_wording": safe_wording,
                "source_ref_ids": linked_source_ref_ids,
                "evidence_item_ids": source_fact_ids,
                "support_level": support_level,
                "softening_required": softening_required,
                "do_not_say": do_not_say,
                "_priority": _claim_priority(claim_text, support_level),
            }
        )

    candidates.sort(key=lambda item: item["_priority"], reverse=True)
    selected = candidates[:limit]
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in selected]


def _normalized_support_level(raw_support_level: str, evidence_items: list[dict[str, Any]]) -> str:
    if raw_support_level in _SUPPORT_LEVEL_MAP:
        return _SUPPORT_LEVEL_MAP[raw_support_level]
    for item in evidence_items:
        level = str(item.get("support_level") or "")
        if level in _SUPPORT_LEVEL_MAP:
            return _SUPPORT_LEVEL_MAP[level]
    return "limited"


def _safe_wording_for_claim(claim_text: str, support_level: str, softening_required: bool) -> str:
    text = claim_text.strip()
    lowered = text.lower()
    if not softening_required:
        return text
    if "payment" in lowered or "payments" in lowered:
        return "Experience adjacent to payment acceptance, checkout, and regulated-market execution."
    if "telecom" in lowered or "partner" in lowered:
        return "Experience involving partner coordination and adjacent telecom or ecosystem execution."
    if "pricing" in lowered or "commercial" in lowered or "growth" in lowered:
        return "Commercially relevant product execution across growth, pricing, or partner activation inputs."
    if "platform" in lowered:
        return "Experience adjacent to platform scaling and operational product execution."
    if support_level == "limited":
        return f"Relevant adjacent experience related to {text.lower()}."
    return text


def _claim_do_not_say(claim_text: str) -> list[str]:
    text = claim_text.lower()
    blocked = [phrase for phrase in _BROAD_OVERCLAIM_PHRASES if phrase in text]
    if "payment" in text or "payments" in text:
        blocked.append("payments leader")
    if "telecom" in text:
        blocked.append("telecom category leadership")
    if "pricing" in text or "commercial" in text or "growth" in text:
        blocked.append("direct ownership")
    return _dedupe(blocked)


def _claim_priority(claim_text: str, support_level: str) -> int:
    score = {
        "direct": 30,
        "adjacent": 20,
        "limited": 10,
    }.get(support_level, 0)
    lowered = claim_text.lower()
    if "growth" in lowered or "conversion" in lowered or "onboarding" in lowered:
        score += 5
    if "payment" in lowered or "payments" in lowered:
        score += 4
    if "partner" in lowered or "telecom" in lowered:
        score += 3
    if any(phrase in lowered for phrase in _BROAD_OVERCLAIM_PHRASES):
        score -= 50
    return score
