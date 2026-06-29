from __future__ import annotations

import json
from typing import Any

import pytest

from hermes_cli.recruiter_document_provider_executor import (
    RecruiterDocumentProviderExecutor,
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


def test_writer_prompt_contains_manual_safety_boundaries() -> None:
    client = _FakeClient(
        json.dumps(
            {
                "schema_version": "recruiter_document_packet_v1",
                "document_type": "cover_letter",
                "audience": "Hiring manager",
                "purpose": "Draft cover letter for user review",
                "source_positioning_packet_ref": {"skill_id": "positioning-and-evidence", "status": "SUCCESS"},
                "draft": {"format": "text", "content": "Draft"},
                "review": {"status": "PENDING"},
                "status": "DRAFT_READY",
            }
        )
    )
    executor = RecruiterDocumentProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_id="document-writer",
        skill_input={
            "document_type": "cover_letter",
            "audience": "Hiring manager",
            "purpose": "Draft cover letter for user review",
            "positioning_evidence_result": {"positioning_summary": "Lead with proof."},
            "vacancy_evaluation_result": {"vacancy_evaluation_summary": "Strong fit."},
            "boundaries": {
                "draft_only": True,
                "user_review_required": True,
                "do_not_imply_application_submission": True,
                "no_outbound": True,
                "no_invented_facts": True,
            },
        },
        expected_schema={"schema_version": "recruiter_document_packet_v1", "draft": "required"},
    )

    prompt = client.calls[0]["messages"][0]["content"]
    assert "draft-only" in prompt
    assert "user-review-only" in prompt
    assert "do not invent facts" in prompt
    assert "no outbound sending" in prompt
    assert "do not imply that an application was submitted" in prompt
    assert "recruiter_document_packet_v1" in prompt


def test_writer_prompt_requires_exact_draft_ready_schema() -> None:
    client = _FakeClient(
        json.dumps(
            {
                "schema_version": "recruiter_document_packet_v1",
                "document_type": "cover_letter",
                "audience": "Hiring manager",
                "purpose": "Draft cover letter for user review",
                "source_positioning_packet_ref": {"skill_id": "positioning-and-evidence", "status": "SUCCESS"},
                "draft": {"format": "text", "content": "Draft", "notes": []},
                "review": {"status": "PENDING"},
                "status": "DRAFT_READY",
            }
        )
    )
    executor = RecruiterDocumentProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_id="document-writer",
        skill_input={
            "document_type": "cover_letter",
            "audience": "Hiring manager",
            "purpose": "Draft cover letter for user review",
            "positioning_evidence_result": {"positioning_summary": "Lead with proof."},
            "vacancy_evaluation_result": {"vacancy_evaluation_summary": "Strong fit."},
            "boundaries": {
                "draft_only": True,
                "user_review_required": True,
                "do_not_imply_application_submission": True,
                "no_outbound": True,
                "no_invented_facts": True,
            },
        },
        expected_schema={"schema_version": "recruiter_document_packet_v1", "draft": "required"},
    )

    prompt = client.calls[0]["messages"][0]["content"]
    assert "status must be exactly DRAFT_READY" in prompt
    assert "do not use draft_only as status" in prompt
    assert '"format": "text"' in prompt
    assert '"content": "<draft text>"' in prompt
    assert '"notes": []' in prompt


def test_reviewer_prompt_contains_review_checks() -> None:
    client = _FakeClient(
        json.dumps(
            {
                "status": "SUCCESS",
                "skill_id": "document-reviewer",
                "verdict": "APPROVE",
                "hallucination_risk": "low",
                "unsupported_claims": [],
                "genericness_assessment": "specific enough",
                "tone_seniority_assessment": "appropriate",
                "missing_source_references": [],
                "required_changes": [],
            }
        )
    )
    executor = RecruiterDocumentProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_id="document-reviewer",
        skill_input={
            "document_packet": {"draft": {"content": "Draft"}},
            "source_positioning_packet": {"positioning_summary": "Lead with proof."},
        },
        expected_schema={"verdict": ["APPROVE", "CHANGES_REQUESTED", "BLOCKED"]},
    )

    prompt = client.calls[0]["messages"][0]["content"]
    assert "hallucination risk" in prompt
    assert "unsupported claims" in prompt
    assert "genericness" in prompt
    assert "tone/seniority" in prompt
    assert "missing source references" in prompt
    assert "application submission implication" in prompt


def test_invalid_json_output_raises_controlled_error() -> None:
    executor = RecruiterDocumentProviderExecutor(
        client=_FakeClient("not-json"),
        model="gpt-5.4-mini",
        provider="openai-codex",
    )

    with pytest.raises(ValueError, match="document_provider_output_invalid_json"):
        executor.execute(
            skill_id="document-writer",
            skill_input={"document_type": "cover_letter"},
            expected_schema={"schema_version": "recruiter_document_packet_v1"},
        )


def test_json_response_format_uses_writer_schema_shape() -> None:
    payload = _json_response_format(
        "recruiter_document_packet_v1",
        {"schema_version": "recruiter_document_packet_v1", "draft": "required"},
    )
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "recruiter_document_packet_v1"
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["status"]["enum"] == ["DRAFT_READY"]
    assert schema["properties"]["draft"]["type"] == "object"
    assert schema["properties"]["draft"]["properties"]["format"]["enum"] == ["text"]
    assert schema["properties"]["draft"]["properties"]["content"]["type"] == "string"
    assert schema["properties"]["draft"]["properties"]["notes"]["type"] == "array"
    assert "status" in schema["required"]
    assert "draft" in schema["required"]
