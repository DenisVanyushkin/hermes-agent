from __future__ import annotations

import json
from typing import Any

import pytest

from hermes_cli.recruiter_evaluation_provider_executor import (
    RecruiterEvaluationProviderExecutor,
    _json_response_format,
)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [type("_Choice", (), {"message": type("_Message", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []
        self.chat = type("_Chat", (), {"completions": type("_Completions", (), {"create": self._create})()})()

    def _create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self.content)


def test_prompt_contains_explicit_evaluation_contract() -> None:
    client = _FakeClient(
        json.dumps(
            {
                "schema_version": "recruiter_vacancy_evaluation_packet_v1",
                "skill_id": "vacancy-evaluation",
                "status": "EVALUATION_READY",
                "recommendation": "APPLY",
                "fit_assessment": "Strong fit.",
                "strengths": [],
                "risks": [],
                "evidence": [],
                "missing_information": [],
                "next_step": "PROCEED_TO_POSITIONING",
                "provenance": {},
            }
        )
    )
    executor = RecruiterEvaluationProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_input={"skill_id": "vacancy-evaluation", "prompt_text": "Посмотри вакансию https://example.com/jobs/123"},
        expected_schema={"schema_version": "recruiter_vacancy_evaluation_packet_v1"},
    )

    prompt = client.calls[0]["messages"][0]["content"]
    assert "do not invent facts" in prompt
    assert "do not imply an application was submitted" in prompt
    assert "recruiter_vacancy_evaluation_packet_v1" in prompt
    assert "skill_id must be exactly vacancy-evaluation" in prompt
    assert "recommendation must be exactly one of APPLY, MAYBE, DO_NOT_APPLY, NEED_MORE_INFO" in prompt


def test_json_response_format_uses_evaluation_schema_shape() -> None:
    payload = _json_response_format({"schema_version": "recruiter_vacancy_evaluation_packet_v1"})

    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "recruiter_vacancy_evaluation_packet_v1"
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["schema_version"]["enum"] == ["recruiter_vacancy_evaluation_packet_v1"]
    assert schema["properties"]["skill_id"]["enum"] == ["vacancy-evaluation"]
    assert schema["properties"]["status"]["enum"] == ["EVALUATION_READY", "CHANGES_REQUIRED", "INSUFFICIENT_INPUT"]
    assert schema["properties"]["recommendation"]["enum"] == ["APPLY", "MAYBE", "DO_NOT_APPLY", "NEED_MORE_INFO"]
    assert "provenance" in schema["required"]


def test_invalid_json_output_raises_controlled_error() -> None:
    executor = RecruiterEvaluationProviderExecutor(
        client=_FakeClient("not-json"),
        model="gpt-5.4-mini",
        provider="openai-codex",
    )

    with pytest.raises(ValueError, match="evaluation_provider_output_invalid_json"):
        executor.execute(
            skill_input={"skill_id": "vacancy-evaluation"},
            expected_schema={"schema_version": "recruiter_vacancy_evaluation_packet_v1"},
        )
