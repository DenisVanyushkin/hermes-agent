from __future__ import annotations

import json
from typing import Any

import pytest

from hermes_cli.recruiter_positioning_provider_executor import (
    RecruiterPositioningProviderExecutor,
    positioning_expected_schema,
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


def test_prompt_contains_explicit_positioning_contract() -> None:
    client = _FakeClient(
        json.dumps(
            {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "positioning-and-evidence",
                "status": "POSITIONING_READY",
                "positioning_summary": "Lead with executive B2B product leadership.",
                "target_narrative": "Operator-executive for complex platform businesses.",
                "evidence": [],
                "gaps": [],
                "risks_and_mitigations": [],
                "recommended_angle": "Scale-stage operator.",
                "claims_to_use": [],
                "claims_to_avoid": [],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "provenance": {},
            }
        )
    )
    executor = RecruiterPositioningProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_input={"skill_id": "positioning-and-evidence", "evaluation_packet": {"schema_version": "recruiter_vacancy_evaluation_packet_v1"}},
        expected_schema=positioning_expected_schema(),
    )

    prompt = client.calls[0]["messages"][0]["content"]
    assert "Return only one JSON object for recruiter_positioning_packet_v1" in prompt
    assert "do not invent facts" in prompt
    assert "claims_to_avoid" in prompt
    assert "skill_id must be exactly positioning-and-evidence" in prompt
    assert "allowed_claims must be a non-empty list when status is POSITIONING_READY" in prompt
    assert "evidence_items must be a non-empty list when status is POSITIONING_READY" in prompt
    assert "source_references must be a non-empty list when status is POSITIONING_READY" in prompt
    assert "Every allowed claim must be backed by at least one evidence item" in prompt
    assert "Do not return READY/success with empty allowed_claims, evidence_items, or source_references" in prompt
    assert "Every evidence_items entry must include at least one source_ref_ids value" in prompt
    assert "Every source_ref_ids value must exactly match a source_references source_ref_id" in prompt
    assert "POSITIONING_READY is forbidden if any evidence_items entry has an empty source_ref_ids list" in prompt
    assert "If any evidence item cannot be source-backed, return POSITIONING_INPUT_BLOCKED" in prompt
    assert "Do not use placeholder, invented, or empty source reference ids" in prompt
    assert '"source_ref_id":"src-1"' in prompt
    assert '"source_ref_ids":["src-1"]' in prompt


def test_response_schema_requires_provenance_bearing_arrays() -> None:
    client = _FakeClient(
        json.dumps(
            {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "positioning-and-evidence",
                "status": "POSITIONING_READY",
                "positioning_summary": "Lead with executive B2B product leadership.",
                "target_narrative": "Operator-executive for complex platform businesses.",
                "evidence": ["Scaled multi-team product orgs."],
                "gaps": [],
                "risks_and_mitigations": [],
                "recommended_angle": "Scale-stage operator.",
                "claims_to_use": ["Led product organizations."],
                "claims_to_avoid": [],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "allowed_claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_text": "Led product organizations.",
                        "source_fact_ids": ["fact-1"],
                        "support_level": "explicit",
                    }
                ],
                "evidence_items": [
                    {
                        "claim_text": "Led product organizations.",
                        "source_fact_ids": ["fact-1"],
                        "source_ref_ids": ["src-1"],
                        "support_level": "explicit",
                        "category": "leadership",
                        "safe_summary": "Scaled multi-team product orgs.",
                    }
                ],
                "source_references": [
                    {
                        "source_ref_id": "src-1",
                        "source_label": "safe-fixture",
                        "source_id_hash": "fixture-hash",
                        "section_label": "safe-section",
                        "support_level": "explicit",
                        "category": "test_fixture",
                    }
                ],
                "provenance": {},
            }
        )
    )
    executor = RecruiterPositioningProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_input={"skill_id": "positioning-and-evidence", "evaluation_packet": {"schema_version": "recruiter_vacancy_evaluation_packet_v1"}},
        expected_schema=positioning_expected_schema(),
    )

    schema = client.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"]
    required = set(schema["required"])
    assert "allowed_claims" in required
    assert "evidence_items" in required
    assert "source_references" in required
    assert client.calls[0]["extra_body"]["response_format"]["json_schema"]["strict"] is True

    evidence_items_schema = schema["properties"]["evidence_items"]["items"]["properties"]["source_ref_ids"]
    assert evidence_items_schema["minItems"] == 1


def test_invalid_json_output_raises_controlled_error() -> None:
    executor = RecruiterPositioningProviderExecutor(
        client=_FakeClient("not-json"),
        model="gpt-5.4-mini",
        provider="openai-codex",
    )

    with pytest.raises(ValueError, match="positioning_provider_output_invalid_json"):
        executor.execute(
            skill_input={"skill_id": "positioning-and-evidence"},
            expected_schema=positioning_expected_schema(),
        )


def test_response_format_cannot_be_clobbered_by_auxiliary_extra_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_positioning_provider_executor._extra_body",
        lambda: {"response_format": {"type": "text"}, "metadata": {"source": "aux"}},
    )
    client = _FakeClient(
        json.dumps(
            {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "positioning-and-evidence",
                "status": "POSITIONING_READY",
                "positioning_summary": "Lead with executive B2B product leadership.",
                "target_narrative": "Operator-executive for complex platform businesses.",
                "evidence": [],
                "gaps": [],
                "risks_and_mitigations": [],
                "recommended_angle": "Scale-stage operator.",
                "claims_to_use": [],
                "claims_to_avoid": [],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "provenance": {},
            }
        )
    )
    executor = RecruiterPositioningProviderExecutor(client=client, model="gpt-5.4-mini", provider="openai-codex")

    executor.execute(
        skill_input={"skill_id": "positioning-and-evidence", "evaluation_packet": {"schema_version": "recruiter_vacancy_evaluation_packet_v1"}},
        expected_schema=positioning_expected_schema(),
    )

    extra_body = client.calls[0]["extra_body"]
    assert extra_body["metadata"] == {"source": "aux"}
    assert extra_body["response_format"]["type"] == "json_schema"
    assert extra_body["response_format"]["json_schema"]["name"] == "recruiter_positioning_packet_v1"
