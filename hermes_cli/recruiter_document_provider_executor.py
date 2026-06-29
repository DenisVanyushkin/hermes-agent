from __future__ import annotations

import json
from typing import Any


_WRITER_TASK = "recruiter_document_writer"
_REVIEWER_TASK = "recruiter_document_reviewer"


class RecruiterDocumentProviderExecutor:
    provider_backed = True

    def __init__(self, *, client: Any, model: str, provider: str | None) -> None:
        self._client = client
        self._model = model
        self._provider = provider or "auto"

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        expected_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = _build_prompt(skill_id=skill_id, skill_input=skill_input, expected_schema=expected_schema)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": prompt}],
            temperature=0,
            timeout=120,
            extra_body=_json_response_format(_schema_name(skill_id), expected_schema) | _extra_body(),
        )
        raw = _response_text(response)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("document_provider_output_invalid_json") from exc
        if not isinstance(payload, dict):
            raise ValueError("document_provider_output_not_object")
        payload.setdefault("provenance", {})
        payload["provenance"] = {
            **dict(payload.get("provenance") or {}),
            "provider": self._provider,
            "model": self._model,
            "provider_backed": True,
        }
        return payload


def build_recruiter_document_provider_executor(*, provider: str | None = None, model: str | None = None) -> RecruiterDocumentProviderExecutor:
    from agent.auxiliary_client import get_text_auxiliary_client, resolve_provider_client

    if provider or model:
        client, resolved_model = resolve_provider_client(provider or "auto", model=model)
    else:
        client, resolved_model = get_text_auxiliary_client(_WRITER_TASK)
    if client is None or not resolved_model:
        raise RuntimeError("document_provider_client_unavailable")
    return RecruiterDocumentProviderExecutor(client=client, model=resolved_model, provider=provider)


def _build_prompt(*, skill_id: str, skill_input: dict[str, Any], expected_schema: dict[str, Any] | None) -> str:
    if skill_id == "document-writer":
        return (
            "You are recruiter document-writer.\n"
            "Produce draft-only, user-review-only JSON for recruiter_document_packet_v1.\n"
            "Rules: do not invent facts; unsupported claims must be omitted or flagged; "
            "no outbound sending; do not imply that an application was submitted; no CRM writes; "
            "no job-intel DB writes.\n"
            "Writer output contract:\n"
            "- status must be exactly DRAFT_READY\n"
            "- do not use draft_only as status\n"
            "- draft must be an object, not a string\n"
            "- draft.format must be text\n"
            "- draft.content must contain the generated draft text\n"
            "- draft.notes must be a list; use [] when there are no notes\n"
            "Required minimal shape example:\n"
            '{"status": "DRAFT_READY", "draft": {"format": "text", "content": "<draft text>", "notes": []}}\n'
            f"Expected schema marker: {_schema_name(skill_id)}.\n"
            f"Skill input JSON:\n{json.dumps(skill_input, ensure_ascii=True, sort_keys=True)}\n"
            f"Expected schema JSON:\n{json.dumps(expected_schema or {}, ensure_ascii=True, sort_keys=True)}"
        )
    return (
        "You are recruiter document-reviewer.\n"
        "Review the draft for hallucination risk, unsupported claims, genericness, tone/seniority, "
        "missing source references, invented facts, and any application submission implication.\n"
        "Return structured JSON with verdict APPROVE, CHANGES_REQUESTED, or BLOCKED.\n"
        f"Skill input JSON:\n{json.dumps(skill_input, ensure_ascii=True, sort_keys=True)}\n"
        f"Expected schema JSON:\n{json.dumps(expected_schema or {}, ensure_ascii=True, sort_keys=True)}"
    )


def _json_response_format(name: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "schema": _response_schema(name, schema),
                "strict": False,
            },
        }
    }


def _response_schema(name: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    if name == "recruiter_document_packet_v1":
        schema = dict(schema or {})
        draft_schema = schema.get("draft")
        if not isinstance(draft_schema, dict):
            draft_schema = {}
        return {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "enum": [str(schema.get("schema_version") or "recruiter_document_packet_v1")]},
                "document_type": {"type": "string"},
                "audience": {"type": ["string", "null"]},
                "purpose": {"type": ["string", "null"]},
                "source_positioning_packet_id": {"type": ["string", "null"]},
                "source_positioning_packet_ref": {"type": ["object", "null"]},
                "draft": {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "enum": list(draft_schema.get("format") or ["text"])},
                        "content": {"type": "string"},
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["format", "content", "notes"],
                    "additionalProperties": True,
                },
                "review": {"type": ["object", "null"]},
                "status": {"type": "string", "enum": list(schema.get("status") or ["DRAFT_READY"])},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "errors": {"type": "array", "items": {"type": "string"}},
                "provenance": {"type": "object"},
            },
            "required": ["schema_version", "document_type", "draft", "status"],
            "additionalProperties": True,
        }
    verdicts = list((schema or {}).get("verdict") or ["APPROVE", "CHANGES_REQUESTED", "BLOCKED"])
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "skill_id": {"type": "string"},
            "verdict": {"type": "string", "enum": verdicts},
            "hallucination_risk": {"type": ["string", "null"]},
            "unsupported_claims": {"type": "array", "items": {}},
            "genericness_assessment": {"type": ["string", "null"]},
            "tone_seniority_assessment": {"type": ["string", "null"]},
            "missing_source_references": {"type": "array", "items": {}},
            "required_changes": {"type": "array", "items": {}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "errors": {"type": "array", "items": {"type": "string"}},
            "provenance": {"type": "object"},
        },
        "required": ["status", "skill_id", "verdict", "unsupported_claims", "missing_source_references", "required_changes", "warnings", "errors", "provenance"],
        "additionalProperties": True,
    }


def _schema_name(skill_id: str) -> str:
    return "recruiter_document_packet_v1" if skill_id == "document-writer" else "recruiter_document_review_result_v1"


def _extra_body() -> dict[str, Any]:
    from agent.auxiliary_client import get_auxiliary_extra_body

    return get_auxiliary_extra_body() or {}


def _response_text(response: Any) -> str:
    try:
        return str(response.choices[0].message.content or "").strip()
    except Exception as exc:  # pragma: no cover
        raise ValueError("document_provider_output_missing_content") from exc
