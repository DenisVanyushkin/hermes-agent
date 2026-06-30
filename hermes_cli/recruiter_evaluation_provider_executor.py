from __future__ import annotations

import json
from typing import Any


VACANCY_EVALUATION_PACKET_SCHEMA_VERSION = "recruiter_vacancy_evaluation_packet_v1"
VACANCY_EVALUATION_SKILL_ID = "vacancy-evaluation"
REQUIRED_VACANCY_EVALUATION_PACKET_FIELDS = [
    "schema_version",
    "skill_id",
    "status",
    "recommendation",
    "fit_assessment",
    "strengths",
    "risks",
    "evidence",
    "missing_information",
    "next_step",
    "provenance",
]


class RecruiterEvaluationProviderExecutor:
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
            extra_body=_json_response_format(expected_schema) | _extra_body(),
        )
        raw = _response_text(response)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("evaluation_provider_output_invalid_json") from exc
        if not isinstance(payload, dict):
            raise ValueError("evaluation_provider_output_not_object")
        payload.setdefault("provenance", {})
        payload["provenance"] = {
            **dict(payload.get("provenance") or {}),
            "provider": self._provider,
            "model": self._model,
            "provider_backed": True,
        }
        return payload


def build_recruiter_evaluation_provider_executor(
    *,
    provider: str | None = None,
    model: str | None = None,
) -> RecruiterEvaluationProviderExecutor:
    from agent.auxiliary_client import get_text_auxiliary_client, resolve_provider_client

    if provider or model:
        client, resolved_model = resolve_provider_client(provider or "auto", model=model)
    else:
        client, resolved_model = get_text_auxiliary_client("recruiter_vacancy_evaluation")
    if client is None or not resolved_model:
        raise RuntimeError("evaluation_provider_client_unavailable")
    return RecruiterEvaluationProviderExecutor(client=client, model=resolved_model, provider=provider)


def vacancy_evaluation_expected_schema() -> dict[str, Any]:
    return {
        "schema_version": VACANCY_EVALUATION_PACKET_SCHEMA_VERSION,
        "skill_id": VACANCY_EVALUATION_SKILL_ID,
        "status": ["EVALUATION_READY", "CHANGES_REQUIRED", "INSUFFICIENT_INPUT"],
        "recommendation": ["APPLY", "MAYBE", "DO_NOT_APPLY", "NEED_MORE_INFO"],
        "next_step": ["PROCEED_TO_POSITIONING", "NEED_MORE_INFO", "DO_NOT_APPLY"],
    }


def _build_prompt(*, skill_input: dict[str, Any], expected_schema: dict[str, Any] | None) -> str:
    return (
        "You are recruiter vacancy-evaluation.\n"
        "Return only one JSON object for recruiter_vacancy_evaluation_packet_v1.\n"
        "Rules: do not invent facts; use the supplied vacancy and machine-score evidence only; "
        "do not send outbound messages; do not imply an application was submitted; "
        "do not write CRM, job-intel DB, or private files.\n"
        "Required contract:\n"
        "- schema_version must be exactly recruiter_vacancy_evaluation_packet_v1\n"
        "- skill_id must be exactly vacancy-evaluation\n"
        "- status must be exactly one of EVALUATION_READY, CHANGES_REQUIRED, INSUFFICIENT_INPUT\n"
        "- recommendation must be exactly one of APPLY, MAYBE, DO_NOT_APPLY, NEED_MORE_INFO\n"
        "- fit_assessment must be a string\n"
        "- strengths must be a list; use [] when none\n"
        "- risks must be a list; use [] when none\n"
        "- evidence must be a list; use [] when none\n"
        "- missing_information must be a list; use [] when none\n"
        "- next_step must be exactly one of PROCEED_TO_POSITIONING, NEED_MORE_INFO, DO_NOT_APPLY\n"
        "- provenance must be an object\n"
        "Required minimal shape example:\n"
        '{"schema_version":"recruiter_vacancy_evaluation_packet_v1","skill_id":"vacancy-evaluation","status":"EVALUATION_READY","recommendation":"APPLY","fit_assessment":"Strong executive product fit.","strengths":[],"risks":[],"evidence":[],"missing_information":[],"next_step":"PROCEED_TO_POSITIONING","provenance":{}}\n'
        f"Skill input JSON:\n{json.dumps(skill_input, ensure_ascii=True, sort_keys=True)}\n"
        f"Expected schema JSON:\n{json.dumps(expected_schema or {}, ensure_ascii=True, sort_keys=True)}"
    )


def _json_response_format(schema: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": VACANCY_EVALUATION_PACKET_SCHEMA_VERSION,
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
            "schema_version": {"type": "string", "enum": [str(schema.get("schema_version") or VACANCY_EVALUATION_PACKET_SCHEMA_VERSION)]},
            "skill_id": {"type": "string", "enum": [str(schema.get("skill_id") or VACANCY_EVALUATION_SKILL_ID)]},
            "status": {"type": "string", "enum": list(schema.get("status") or ["EVALUATION_READY", "CHANGES_REQUIRED", "INSUFFICIENT_INPUT"])},
            "recommendation": {"type": "string", "enum": list(schema.get("recommendation") or ["APPLY", "MAYBE", "DO_NOT_APPLY", "NEED_MORE_INFO"])},
            "fit_assessment": {"type": "string"},
            "strengths": {"type": "array", "items": {}},
            "risks": {"type": "array", "items": {}},
            "evidence": {"type": "array", "items": {}},
            "missing_information": {"type": "array", "items": {}},
            "next_step": {"type": "string", "enum": list(schema.get("next_step") or ["PROCEED_TO_POSITIONING", "NEED_MORE_INFO", "DO_NOT_APPLY"])},
            "provenance": {"type": "object"},
        },
        "required": list(REQUIRED_VACANCY_EVALUATION_PACKET_FIELDS),
        "additionalProperties": True,
    }


def _extra_body() -> dict[str, Any]:
    from agent.auxiliary_client import get_auxiliary_extra_body

    return get_auxiliary_extra_body() or {}


def _response_text(response: Any) -> str:
    try:
        return str(response.choices[0].message.content or "").strip()
    except Exception as exc:  # pragma: no cover
        raise ValueError("evaluation_provider_output_missing_content") from exc
