from __future__ import annotations

import json
from typing import Any


POSITIONING_PACKET_SCHEMA_VERSION = "recruiter_positioning_packet_v1"
POSITIONING_SKILL_ID = "positioning-and-evidence"
REQUIRED_POSITIONING_PACKET_FIELDS = [
    "schema_version",
    "skill_id",
    "status",
    "positioning_summary",
    "target_narrative",
    "evidence",
    "gaps",
    "risks_and_mitigations",
    "recommended_angle",
    "claims_to_use",
    "claims_to_avoid",
    "missing_information",
    "next_step",
    "allowed_claims",
    "evidence_items",
    "source_references",
    "provenance",
]


class RecruiterPositioningProviderExecutor:
    provider_backed = True

    def __init__(self, *, client: Any, model: str, provider: str | None) -> None:
        self._client = client
        self._model = model
        self._provider = provider or "auto"

    def execute(
        self,
        *,
        skill_input: dict[str, Any],
        expected_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = _build_prompt(skill_input=skill_input, expected_schema=expected_schema)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": prompt}],
            temperature=0,
            timeout=120,
            extra_body=_extra_body() | _json_response_format(expected_schema),
        )
        raw = _response_text(response)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("positioning_provider_output_invalid_json") from exc
        if not isinstance(payload, dict):
            raise ValueError("positioning_provider_output_not_object")
        payload.setdefault("provenance", {})
        payload["provenance"] = {
            **dict(payload.get("provenance") or {}),
            "provider": self._provider,
            "model": self._model,
            "provider_backed": True,
        }
        return payload


def build_recruiter_positioning_provider_executor(
    *,
    provider: str | None = None,
    model: str | None = None,
) -> RecruiterPositioningProviderExecutor:
    from agent.auxiliary_client import get_text_auxiliary_client, resolve_provider_client

    if provider or model:
        client, resolved_model = resolve_provider_client(provider or "auto", model=model)
    else:
        client, resolved_model = get_text_auxiliary_client("recruiter_positioning")
    if client is None or not resolved_model:
        raise RuntimeError("positioning_provider_client_unavailable")
    return RecruiterPositioningProviderExecutor(client=client, model=resolved_model, provider=provider)


def positioning_expected_schema() -> dict[str, Any]:
    return {
        "schema_version": POSITIONING_PACKET_SCHEMA_VERSION,
        "skill_id": POSITIONING_SKILL_ID,
        "status": ["POSITIONING_READY", "POSITIONING_INPUT_BLOCKED"],
        "next_step": ["POSITIONING_READY_FOR_DOCUMENTS", "NEED_MORE_INFO", "DO_NOT_PROCEED"],
    }


def _build_prompt(*, skill_input: dict[str, Any], expected_schema: dict[str, Any] | None) -> str:
    return (
        "You are recruiter positioning-and-evidence.\n"
        "Return only one JSON object for recruiter_positioning_packet_v1.\n"
        "Rules: do not invent facts; use only the supplied vacancy-evaluation packet and safe context metadata; "
        "mark uncertainty as gaps; do not send outbound messages; do not write CRM, job-intel DB, or private files.\n"
        "Every allowed claim must be backed by at least one evidence item and at least one source reference from the provided evaluation and candidate-facts inputs.\n"
        "Do not invent candidate facts.\n"
        "Do not invent vacancy facts.\n"
        "Do not include claims without evidence.\n"
        "If a claim is not supported, put it in claims_to_avoid or missing_information, not allowed_claims.\n"
        "Do not return READY/success with empty allowed_claims, evidence_items, or source_references.\n"
        "If the supplied evidence is insufficient to produce a valid source-backed positioning packet, return POSITIONING_INPUT_BLOCKED rather than empty READY arrays.\n"
        "If the input packets provide enough source-backed information to create at least one allowed_claim, one evidence_item, and one source_reference, return POSITIONING_READY with next_step POSITIONING_READY_FOR_DOCUMENTS.\n"
        "A minimal one-claim packet is valid and must be POSITIONING_READY if it is fully source-backed.\n"
        "Do not return POSITIONING_INPUT_BLOCKED merely because the evidence is sparse, synthetic, smoke-oriented, limited to one usable claim, or missing non-essential context.\n"
        "Prefer a small, conservative POSITIONING_READY packet over POSITIONING_INPUT_BLOCKED when at least one fully supported claim exists.\n"
        "Return POSITIONING_INPUT_BLOCKED only when no valid source-backed positioning packet can be produced without inventing facts, using unsupported claims, leaving allowed_claims/evidence_items/source_references empty, or emitting evidence without valid source_ref_ids.\n"
        "Every evidence_items entry must include at least one source_ref_ids value.\n"
        "Every source_ref_ids value must exactly match a source_references source_ref_id from the same output packet.\n"
        "POSITIONING_READY is forbidden if any evidence_items entry has an empty source_ref_ids list.\n"
        "If any evidence item cannot be source-backed, return POSITIONING_INPUT_BLOCKED.\n"
        "Do not use placeholder, invented, or empty source reference ids.\n"
        "Required contract:\n"
        "- schema_version must be exactly recruiter_positioning_packet_v1\n"
        "- skill_id must be exactly positioning-and-evidence\n"
        "- status must be exactly one of POSITIONING_READY or POSITIONING_INPUT_BLOCKED\n"
        "- positioning_summary must be a string\n"
        "- target_narrative must be a string\n"
        "- evidence must be a list; use [] when none\n"
        "- gaps must be a list; use [] when none\n"
        "- risks_and_mitigations must be a list; use [] when none\n"
        "- recommended_angle must be a string\n"
        "- claims_to_use must be a list; use [] when none\n"
        "- claims_to_avoid must be a list; use [] when none\n"
        "- missing_information must be a list; use [] when none\n"
        "- next_step must be exactly one of POSITIONING_READY_FOR_DOCUMENTS, NEED_MORE_INFO, DO_NOT_PROCEED\n"
        "- allowed_claims must be a non-empty list when status is POSITIONING_READY; use [] only when status is POSITIONING_INPUT_BLOCKED\n"
        "- each allowed_claims item must include claim_id, claim_text, source_fact_ids, and support_level\n"
        "- evidence_items must be a non-empty list when status is POSITIONING_READY; use [] only when status is POSITIONING_INPUT_BLOCKED\n"
        "- each evidence_items item must include claim_text, source_fact_ids, source_ref_ids, support_level, category, and safe_summary\n"
        "- source_references must be a non-empty list when status is POSITIONING_READY; use [] only when status is POSITIONING_INPUT_BLOCKED\n"
        "- each source_references item must include source_ref_id, source_label, source_id_hash, section_label, support_level, and category\n"
        "- provenance must be an object\n"
        "Concise valid JSON example:\n"
        "{"
        "\"status\":\"POSITIONING_READY\","
        "\"allowed_claims\":[{\"claim_id\":\"claim-1\",\"claim_text\":\"...\",\"source_fact_ids\":[\"fact-1\"],\"support_level\":\"explicit\"}],"
        "\"evidence_items\":[{\"claim_text\":\"...\",\"source_fact_ids\":[\"fact-1\"],\"source_ref_ids\":[\"src-1\"],\"support_level\":\"explicit\",\"category\":\"candidate_fact\",\"safe_summary\":\"...\"}],"
        "\"source_references\":[{\"source_ref_id\":\"src-1\",\"source_label\":\"safe-fixture\",\"source_id_hash\":\"fixture-hash\",\"section_label\":\"safe-section\",\"support_level\":\"explicit\",\"category\":\"candidate_fact\"}]"
        "}\n"
        f"Skill input JSON:\n{json.dumps(skill_input, ensure_ascii=True, sort_keys=True)}\n"
        f"Expected schema JSON:\n{json.dumps(expected_schema or {}, ensure_ascii=True, sort_keys=True)}"
    )


def _json_response_format(schema: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": POSITIONING_PACKET_SCHEMA_VERSION,
                "schema": _response_schema(schema),
                "strict": False,
            },
        }
    }


def _response_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    schema = dict(schema or {})
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "enum": [str(schema.get("schema_version") or POSITIONING_PACKET_SCHEMA_VERSION)]},
            "skill_id": {"type": "string", "enum": [str(schema.get("skill_id") or POSITIONING_SKILL_ID)]},
            "status": {"type": "string", "enum": list(schema.get("status") or ["POSITIONING_READY", "POSITIONING_INPUT_BLOCKED"])},
            "positioning_summary": {"type": "string"},
            "target_narrative": {"type": "string"},
            "evidence": {"type": "array", "items": {}},
            "gaps": {"type": "array", "items": {}},
            "risks_and_mitigations": {"type": "array", "items": {}},
            "recommended_angle": {"type": "string"},
            "claims_to_use": {"type": "array", "items": {}},
            "claims_to_avoid": {"type": "array", "items": {}},
            "missing_information": {"type": "array", "items": {}},
            "next_step": {"type": "string", "enum": list(schema.get("next_step") or ["POSITIONING_READY_FOR_DOCUMENTS", "NEED_MORE_INFO", "DO_NOT_PROCEED"])},
            "allowed_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "claim_text": {"type": "string"},
                        "source_fact_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "support_level": {"type": "string"},
                    },
                    "required": ["claim_id", "claim_text", "source_fact_ids", "support_level"],
                    "additionalProperties": True,
                },
            },
            "evidence_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_text": {"type": "string"},
                        "source_fact_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "source_ref_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "support_level": {"type": "string"},
                        "category": {"type": "string"},
                        "safe_summary": {"type": "string"},
                    },
                    "required": ["claim_text", "source_fact_ids", "source_ref_ids", "support_level", "category", "safe_summary"],
                    "additionalProperties": True,
                },
            },
            "source_references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_ref_id": {"type": "string"},
                        "source_label": {"type": "string"},
                        "source_id_hash": {"type": "string"},
                        "section_label": {"type": "string"},
                        "support_level": {"type": "string"},
                        "category": {"type": "string"},
                    },
                    "required": ["source_ref_id", "source_label", "source_id_hash", "section_label", "support_level", "category"],
                    "additionalProperties": True,
                },
            },
            "provenance": {"type": "object"},
        },
        "required": list(REQUIRED_POSITIONING_PACKET_FIELDS),
        "allOf": [
            {
                "if": {
                    "properties": {
                        "status": {"const": "POSITIONING_READY"},
                    },
                    "required": ["status"],
                },
                "then": {
                    "properties": {
                        "allowed_claims": {"minItems": 1},
                        "evidence_items": {"minItems": 1},
                        "source_references": {"minItems": 1},
                    }
                },
            }
        ],
        "additionalProperties": True,
    }


def _extra_body() -> dict[str, Any]:
    from agent.auxiliary_client import get_auxiliary_extra_body

    return get_auxiliary_extra_body() or {}


def _response_text(response: Any) -> str:
    try:
        return str(response.choices[0].message.content or "").strip()
    except Exception as exc:  # pragma: no cover
        raise ValueError("positioning_provider_output_missing_content") from exc
