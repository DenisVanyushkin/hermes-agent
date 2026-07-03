from __future__ import annotations

from typing import Any

from .recruiter_document_execution import DOCUMENT_PACKET_SCHEMA_VERSION


_OUTWARD_DOCUMENT_TYPES = {"cover_letter", "recruiter_message"}
_BROAD_GENERIC_PHRASES = (
    "relevant adjacent experience with payment acceptance",
    "checkout",
    "regulated-market execution",
    "contributed to commercially relevant product work tied to growth, pricing, or partner activation inputs",
    "commercially relevant product work tied to growth, pricing, or partner activation inputs",
    "broad payments/platform background",
    "worked adjacent to platform scaling or operational product execution",
)
_GENERIC_TEMPLATE_MARKERS = (
    "available source references",
    "only limited evidence is available",
)
_QUALITY_BLOCK_REASONS = {
    "placeholder_output": "DOCUMENT_DRAFT_PLACEHOLDER_OUTPUT",
    "insufficient_role_specificity": "DOCUMENT_DRAFT_INSUFFICIENT_ROLE_SPECIFICITY",
    "insufficient_grounded_claims": "DOCUMENT_DRAFT_INSUFFICIENT_GROUNDED_CLAIMS",
}


def is_deterministic_outward_document_type(document_type: str) -> bool:
    return document_type in _OUTWARD_DOCUMENT_TYPES


def compose_deterministic_outward_draft(writer_input: dict[str, Any]) -> dict[str, Any]:
    document_type = str(writer_input.get("document_type") or writer_input.get("requested_document_type") or "")
    if not is_deterministic_outward_document_type(document_type):
        raise ValueError(f"unsupported_deterministic_outward_document_type:{document_type or 'UNKNOWN'}")

    eligible_claims = _eligible_locked_claims(writer_input)
    target_hint = _target_role_or_company_hint(writer_input)
    if document_type == "recruiter_message":
        content = _compose_recruiter_message(eligible_claims, target_hint=target_hint)
    else:
        content = _compose_cover_letter(eligible_claims, target_hint=target_hint)

    return {
        "schema_version": DOCUMENT_PACKET_SCHEMA_VERSION,
        "document_type": document_type,
        "audience": writer_input.get("audience"),
        "purpose": writer_input.get("purpose"),
        "source_positioning_packet_ref": dict(writer_input.get("source_positioning_packet_ref") or {}),
        "draft": {
            "format": "text",
            "content": content,
            "notes": ["Review before any outbound action."],
        },
        "review": {"status": "PENDING"},
        "status": "DRAFT_READY",
        "draft_only": True,
        "user_review_required": True,
        "claim_units": [
            {
                "sentence": item["sentence"],
                "source_ref_ids": list(item["source_ref_ids"]),
                "evidence_item_ids": list(item["evidence_item_ids"]),
                "support_level": item["support_level"],
                "ownership_scope": item["ownership_scope"],
                "claim_id": item["claim_id"],
            }
            for item in eligible_claims
        ],
        "warnings": [],
        "errors": [],
        "provenance": {
            "builder": "recruiter_outward_drafts",
            "composition_mode": "deterministic_locked_claims",
            "eligible_claim_count": len(eligible_claims),
            "source_positioning_packet_ref": dict(writer_input.get("source_positioning_packet_ref") or {}),
        },
    }


def validate_outward_draft_usefulness(
    writer_input: dict[str, Any],
    document_packet: dict[str, Any],
) -> dict[str, Any]:
    document_type = str(document_packet.get("document_type") or "")
    if not is_deterministic_outward_document_type(document_type):
        return {
            "passed": True,
            "block_reason": None,
            "required_changes": [],
            "quality_diagnostics_summary": [],
        }

    draft = dict(document_packet.get("draft") or {})
    content = str(draft.get("content") or "").strip()
    lowered = content.casefold()
    quality_codes: list[str] = []
    required_changes: list[str] = []

    role_or_company_hint = _target_role_or_company_hint(writer_input)
    claim_units = [
        item
        for item in document_packet.get("claim_units") or []
        if isinstance(item, dict) and str(item.get("sentence") or "").strip()
    ]
    mention_count = 0
    for claim in claim_units:
        sentence = str(claim.get("sentence") or "").strip()
        if sentence and sentence in content:
            mention_count += 1

    if _is_placeholder_text(document_type, lowered):
        quality_codes.append(_QUALITY_BLOCK_REASONS["placeholder_output"])
        required_changes.append(
            "Replace the placeholder with a user-reviewable draft that connects grounded evidence to the target."
        )
    if role_or_company_hint and role_or_company_hint.casefold() not in lowered:
        quality_codes.append(_QUALITY_BLOCK_REASONS["insufficient_role_specificity"])
        required_changes.append(
            "Mention the target company or role explicitly so the draft reads as tailored application material."
        )

    minimum_claims = 2 if document_type == "cover_letter" else 1
    if mention_count < minimum_claims:
        quality_codes.append(_QUALITY_BLOCK_REASONS["insufficient_grounded_claims"])
        required_changes.append(
            "Add grounded, source-backed claims from the approved positioning evidence instead of generic interest language."
        )

    return {
        "passed": not quality_codes,
        "block_reason": quality_codes[0] if quality_codes else None,
        "required_changes": _dedupe(required_changes),
        "quality_diagnostics_summary": quality_codes,
    }


def _eligible_locked_claims(writer_input: dict[str, Any]) -> list[dict[str, Any]]:
    safe_claims = {
        str(item.get("claim_id") or ""): item
        for item in writer_input.get("safe_claims_for_document") or []
        if isinstance(item, dict)
    }
    locked_claim_items = list(writer_input.get("locked_claim_sentences") or [])
    if not locked_claim_items:
        locked_claim_items = [
            {
                "sentence": str(item.get("claim") or item.get("safe_wording") or "").strip(),
                "source_ref_ids": list(item.get("source_ref_ids") or []),
                "evidence_item_ids": list(item.get("evidence_item_ids") or []),
                "support_level": str(item.get("support_level") or "").strip(),
                "ownership_scope": "direct" if not item.get("softening_required") else "adjacent",
                "derived_from_safe_claim_id": str(item.get("claim_id") or "").strip(),
            }
            for item in safe_claims.values()
        ]
    eligible: list[dict[str, Any]] = []
    for item in locked_claim_items:
        if not isinstance(item, dict):
            continue
        sentence = str(item.get("sentence") or "").strip()
        source_ref_ids = [str(ref) for ref in item.get("source_ref_ids") or [] if isinstance(ref, str)]
        evidence_item_ids = [str(ref) for ref in item.get("evidence_item_ids") or [] if isinstance(ref, str)]
        support_level = str(item.get("support_level") or "").strip()
        ownership_scope = str(item.get("ownership_scope") or "").strip()
        claim_id = str(item.get("derived_from_safe_claim_id") or "").strip()
        if not sentence or not source_ref_ids or not evidence_item_ids or not support_level or not ownership_scope or not claim_id:
            continue
        safe_claim = safe_claims.get(claim_id) or {}
        concrete_evidence_summary = str(
            safe_claim.get("concrete_evidence_summary")
            or safe_claim.get("claim")
            or safe_claim.get("safe_wording")
            or ""
        ).strip()
        allowed_sentence_template = str(
            safe_claim.get("allowed_sentence_template")
            or safe_claim.get("claim")
            or safe_claim.get("safe_wording")
            or ""
        ).strip()
        if not concrete_evidence_summary or not allowed_sentence_template:
            continue
        lowered_sentence = sentence.lower()
        lowered_template = allowed_sentence_template.lower()
        if any(phrase in lowered_sentence for phrase in _BROAD_GENERIC_PHRASES):
            continue
        if any(phrase in lowered_template for phrase in _BROAD_GENERIC_PHRASES):
            continue
        if any(marker in lowered_template for marker in _GENERIC_TEMPLATE_MARKERS):
            continue
        if concrete_evidence_summary.lower().startswith("only limited evidence is available"):
            continue
        eligible.append(
            {
                "sentence": _compose_claim_sentence(concrete_evidence_summary, sentence),
                "source_ref_ids": source_ref_ids,
                "evidence_item_ids": evidence_item_ids,
                "support_level": support_level,
                "ownership_scope": ownership_scope,
                "claim_id": claim_id,
            }
        )
    return eligible



def _compose_claim_sentence(concrete_evidence_summary: str, fallback_sentence: str) -> str:
    summary_parts = [part.strip() for part in concrete_evidence_summary.split(";") if part.strip()]
    if summary_parts:
        return summary_parts[0]
    return fallback_sentence


def _compose_recruiter_message(eligible_claims: list[dict[str, Any]], *, target_hint: str) -> str:
    target = target_hint
    opening = "This role looks relevant and I'd be interested in discussing it."
    if target:
        opening = f"I'm interested in the {target} opportunity."
    sentences = [opening]
    if eligible_claims:
        sentences.append(eligible_claims[0]["sentence"])
    sentences.append("I can share more context if useful.")
    return " ".join(sentences)


def _compose_cover_letter(eligible_claims: list[dict[str, Any]], *, target_hint: str) -> str:
    target = target_hint
    opening = "Thank you for considering my application."
    if target:
        opening = f"I'm applying for the {target} role."
    sentences = [opening]
    sentences.extend(item["sentence"] for item in eligible_claims[:2])
    sentences.append("I would welcome the chance to discuss the role further.")
    return " ".join(sentences)


def _target_role_or_company_hint(writer_input: dict[str, Any]) -> str:
    target_role = str(writer_input.get("target_role") or "").strip()
    target_company = str(writer_input.get("target_company") or "").strip()
    if target_role and target_company:
        return f"{target_role} at {target_company}"
    return target_role or target_company


def _is_placeholder_text(document_type: str, lowered_content: str) -> bool:
    if document_type == "cover_letter":
        return (
            lowered_content.startswith("thank you for considering my application.")
            and "i would welcome the chance to discuss the role further." in lowered_content
        )
    if document_type == "recruiter_message":
        return (
            lowered_content.startswith("this role looks relevant and i'd be interested in discussing it.")
            and "i can share more context if useful." in lowered_content
        )
    return False


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered
