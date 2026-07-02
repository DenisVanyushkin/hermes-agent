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


def is_deterministic_outward_document_type(document_type: str) -> bool:
    return document_type in _OUTWARD_DOCUMENT_TYPES


def compose_deterministic_outward_draft(writer_input: dict[str, Any]) -> dict[str, Any]:
    document_type = str(writer_input.get("document_type") or writer_input.get("requested_document_type") or "")
    if not is_deterministic_outward_document_type(document_type):
        raise ValueError(f"unsupported_deterministic_outward_document_type:{document_type or 'UNKNOWN'}")

    eligible_claims = _eligible_locked_claims(writer_input)
    if document_type == "recruiter_message":
        content = _compose_recruiter_message(eligible_claims)
    else:
        content = _compose_cover_letter(eligible_claims)

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


def _eligible_locked_claims(writer_input: dict[str, Any]) -> list[dict[str, Any]]:
    safe_claims = {
        str(item.get("claim_id") or ""): item
        for item in writer_input.get("safe_claims_for_document") or []
        if isinstance(item, dict)
    }
    eligible: list[dict[str, Any]] = []
    for item in writer_input.get("locked_claim_sentences") or []:
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
        concrete_evidence_summary = str(safe_claim.get("concrete_evidence_summary") or "").strip()
        allowed_sentence_template = str(safe_claim.get("allowed_sentence_template") or "").strip()
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


def _compose_recruiter_message(eligible_claims: list[dict[str, Any]]) -> str:
    sentences = ["This role looks relevant and I'd be interested in discussing it."]
    if eligible_claims:
        sentences.append(eligible_claims[0]["sentence"])
    sentences.append("I can share more context if useful.")
    return " ".join(sentences)


def _compose_cover_letter(eligible_claims: list[dict[str, Any]]) -> str:
    sentences = ["Thank you for considering my application."]
    sentences.extend(item["sentence"] for item in eligible_claims[:2])
    sentences.append("I would welcome the chance to discuss the role further.")
    return " ".join(sentences)
