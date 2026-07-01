from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.recruiter_context import (
    RecruiterContextPacket,
    RecruiterContextRequest,
    RecruiterContextStatus,
)
from hermes_cli.recruiter_dry_run import (
    RecruiterDryRunRequest,
    RecruiterDryRunStatus,
    RecruiterPositioningSmokeStatus,
    build_fake_positioning_packet_from_candidate_facts,
    run_recruiter_application_materials_flow_dry_run,
    run_recruiter_context_dry_run,
    run_recruiter_evaluation_flow_dry_run,
    run_recruiter_positioning_flow_dry_run,
    run_recruiter_positioning_smoke_harness,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _base_request() -> RecruiterDryRunRequest:
    return RecruiterDryRunRequest(vacancy_id=101, repo_root=REPO_ROOT)


def _packet(
    *,
    status: RecruiterContextStatus = RecruiterContextStatus.READY,
    private_status: str = "PRIVATE_CONTEXT_AVAILABLE",
    machine_score_status: str = "available",
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> RecruiterContextPacket:
    return RecruiterContextPacket(
        status=status,
        request={"vacancy_id": 101},
        vacancy={"vacancy_id": 101, "company": "Acme"},
        opportunity={"id": 501},
        company_context=[],
        application_history={"status": "not_requested", "history": [], "artifacts": [], "feedback": []},
        machine_score={"status": machine_score_status},
        role_package_context={"package_id": "hermes-recruiter"},
        private_context={"status": private_status, "dir": "/tmp/private", "files": {}},
        warnings=list(warnings or []),
        errors=list(errors or []),
        provenance={"writes_performed": False},
    )


def test_ready_context_maps_to_ready_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run.build_recruiter_context",
        lambda request: _packet(),
    )

    report = run_recruiter_context_dry_run(_base_request())

    assert report.status is RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT
    assert report.readiness["ready"] is True
    assert report.context_status == RecruiterContextStatus.READY.value
    assert "call_provider_model" in report.forbidden_actions
    assert "execute_recruiter_skill" in report.forbidden_actions
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "READY_FOR_RECRUITER_SKILL_INPUT" in encoded


def test_missing_private_context_does_not_fail_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run.build_recruiter_context",
        lambda request: _packet(private_status="PRIVATE_CONTEXT_MISSING", warnings=["private_context_missing"]),
    )

    report = run_recruiter_context_dry_run(_base_request())

    assert report.status is RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT
    assert report.readiness["ready"] is True
    assert "private_context_missing" in report.warnings
    assert "private_career_context_missing" in report.missing_requirements
    assert "provision_private_career_context" in report.next_allowed_actions


def test_machine_score_unavailable_does_not_fail_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run.build_recruiter_context",
        lambda request: _packet(machine_score_status=RecruiterContextStatus.MACHINE_SCORE_UNAVAILABLE.value),
    )

    report = run_recruiter_context_dry_run(_base_request())

    assert report.status is RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT
    assert report.readiness["ready"] is True
    assert "machine_score_unavailable" in report.missing_requirements
    assert "run_or_inspect_job_intel_evaluation" in report.next_allowed_actions
    assert report.context_packet["machine_score"]["status"] == RecruiterContextStatus.MACHINE_SCORE_UNAVAILABLE.value


@pytest.mark.parametrize(
    ("context_status", "expected"),
    [
        (RecruiterContextStatus.VACANCY_NOT_FOUND, RecruiterDryRunStatus.CONTEXT_NOT_FOUND),
        (RecruiterContextStatus.OPPORTUNITY_NOT_FOUND, RecruiterDryRunStatus.CONTEXT_NOT_FOUND),
        (RecruiterContextStatus.PACKAGE_CONTEXT_ERROR, RecruiterDryRunStatus.CONTEXT_PACKAGE_ERROR),
        (RecruiterContextStatus.FACADE_ERROR, RecruiterDryRunStatus.CONTEXT_FACADE_ERROR),
    ],
)
def test_context_status_mapping(monkeypatch: pytest.MonkeyPatch, context_status: RecruiterContextStatus, expected: RecruiterDryRunStatus) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run.build_recruiter_context",
        lambda request: _packet(status=context_status),
    )

    report = run_recruiter_context_dry_run(_base_request())

    assert report.status is expected
    assert report.readiness["ready"] is False


def test_invalid_request_maps_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run.build_recruiter_context",
        lambda request: (_ for _ in ()).throw(ValueError("exactly one of vacancy_id, vacancy_url, or opportunity_id is required")),
    )

    report = run_recruiter_context_dry_run(RecruiterDryRunRequest())

    assert report.status is RecruiterDryRunStatus.CONTEXT_INVALID_REQUEST
    assert report.readiness["ready"] is False
    assert report.context_status == RecruiterContextStatus.SOURCE_REQUIRED.value
    assert report.errors == ["exactly one of vacancy_id, vacancy_url, or opportunity_id is required"]


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_dry_run.py").read_text(encoding="utf-8")
    forbidden = [
        "import sqlite3",
        "from job_intel.store import JobIntelStore",
        "OpportunityRepository",
        "import crm_service",
        "from crm_service",
        "import crm_reconciler",
        "from crm_reconciler",
        "import gateway",
        "from gateway",
        "import orchestrator",
        "from orchestrator",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
    ]
    for needle in forbidden:
        assert needle not in source


def test_evaluation_flow_dry_run_ready_for_url_prompt() -> None:
    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Посмотри вот эту вакансию: https://example.com/jobs/123",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED
    assert report.readiness["ready"] is True
    assert report.evaluation_flow["status"] == "READY"
    assert report.evaluation_flow["vacancy_source_status"] == "AVAILABLE_URL"
    assert report.provider_called is False
    assert report.provider_execution_enabled is False
    assert report.downstream_gates["document_generation"]["enabled"] is False


def test_evaluation_flow_dry_run_blocks_missing_source() -> None:
    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Оцени вакансию",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED
    assert report.readiness["ready"] is False
    assert report.evaluation_flow["status"] == "BLOCKED_SOURCE_REQUIRED"


def test_evaluation_flow_dry_run_allows_provider_only_with_explicit_flag() -> None:
    class _FakeExecutor:
        def execute(self, *, skill_input, expected_schema):
            assert skill_input["skill_id"] == "vacancy-evaluation"
            assert skill_input["prompt_text"] == "Посмотри вакансию https://example.com/jobs/123"
            assert expected_schema["schema_version"] == "recruiter_vacancy_evaluation_packet_v1"
            return {
                "schema_version": "recruiter_vacancy_evaluation_packet_v1",
                "skill_id": "vacancy-evaluation",
                "status": "EVALUATION_READY",
                "recommendation": "APPLY",
                "fit_assessment": "Strong fit.",
                "strengths": ["Executive product leadership match."],
                "risks": ["Team size not confirmed."],
                "evidence": ["Prompt contained a vacancy URL."],
                "missing_information": [],
                "next_step": "PROCEED_TO_POSITIONING",
                "provenance": {},
            }

    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Посмотри вакансию https://example.com/jobs/123",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _FakeExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.EVALUATION_READY
    assert report.provider_called is True
    assert report.executor_called is True
    assert report.evaluation_result["schema_version"] == "recruiter_vacancy_evaluation_packet_v1"
    assert report.evaluation_result["recommendation"] == "APPLY"
    assert report.evaluation_result["fit_assessment"] == "Strong fit."
    assert report.evaluation_result["strengths"] == ["Executive product leadership match."]
    assert report.evaluation_result["risks"] == ["Team size not confirmed."]
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "provider_text" not in encoded
    assert "/Users/" not in encoded


def test_positioning_flow_dry_run_blocks_unsafe_evaluation_packet_without_echoing_raw_fields() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet={
            **_ready_evaluation_packet(),
            "fit_assessment": "Unsafe /Users/testleak/private/career leaktest@example.com",
        },
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.errors == ["evaluation_packet_unsafe"]
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.evaluation_result is None
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "/Users/testleak" not in encoded
    assert "private/career" not in encoded
    assert "leaktest@example.com" not in encoded
    assert "raw unsafe field" not in encoded
    assert "Traceback" not in encoded


def test_positioning_flow_dry_run_blocks_invalid_schema_evaluation_packet_without_echoing_raw_fields() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet={
            "schema_version": "wrong_schema",
            "status": "READY",
            "fit_assessment": "Unsafe /Users/testleak/private/career leaktest@example.com",
        },
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.errors[0].startswith("missing_required_evaluation_output_fields:")
    assert "recommendation" in report.errors[0]
    assert "missing_information" in report.errors[0]
    assert "next_step" in report.errors[0]
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.evaluation_result is None
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "/Users/testleak" not in encoded
    assert "private/career" not in encoded
    assert "leaktest@example.com" not in encoded
    assert "Traceback" not in encoded


def test_evaluation_flow_dry_run_blocks_provider_for_private_context_not_inspected() -> None:
    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Посмотри вакансию https://example.com/jobs/123",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_NOT_INSPECTED",
        allow_provider_execution=True,
    )

    assert report.status is RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.errors == ["private_context_not_ready_for_provider_execution"]


def test_evaluation_flow_dry_run_fails_closed_on_invalid_provider_json() -> None:
    class _InvalidExecutor:
        def execute(self, *, skill_input, expected_schema):
            raise ValueError("evaluation_provider_output_invalid_json")

    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Посмотри вакансию https://example.com/jobs/123",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _InvalidExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.EVALUATION_OUTPUT_INVALID
    assert report.provider_called is True
    assert report.executor_called is True
    assert report.errors == ["evaluation_provider_output_invalid_json"]


def test_evaluation_flow_dry_run_fails_closed_on_missing_required_fields() -> None:
    class _MissingFieldsExecutor:
        def execute(self, *, skill_input, expected_schema):
            return {
                "schema_version": "recruiter_vacancy_evaluation_packet_v1",
                "skill_id": "vacancy-evaluation",
                "status": "EVALUATION_READY",
                "recommendation": "APPLY",
                "fit_assessment": "Strong fit.",
                "strengths": [],
                "risks": [],
                "evidence": [],
                "provenance": {},
            }

    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Посмотри вакансию https://example.com/jobs/123",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _MissingFieldsExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.EVALUATION_OUTPUT_INVALID
    assert "missing_required_evaluation_output_fields:missing_information,next_step" in report.errors


def test_evaluation_flow_dry_run_sanitizes_provider_exception() -> None:
    class _ExplodingExecutor:
        def execute(self, *, skill_input, expected_schema):
            raise RuntimeError("raw provider body should not leak")

    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Посмотри вакансию https://example.com/jobs/123",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _ExplodingExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.PROVIDER_EXECUTION_FAILED
    assert report.errors == ["provider_execution_failed"]


def _ready_evaluation_packet(
    *,
    status: str = "EVALUATION_READY",
    recommendation: str = "APPLY",
    next_step: str = "PROCEED_TO_POSITIONING",
) -> dict[str, object]:
    return {
        "schema_version": "recruiter_vacancy_evaluation_packet_v1",
        "skill_id": "vacancy-evaluation",
        "status": status,
        "recommendation": recommendation,
        "fit_assessment": "Strong fit.",
        "strengths": ["Executive product leadership match."],
        "risks": ["Team size not confirmed."],
        "evidence": ["Prompt contained a vacancy URL."],
        "missing_information": [],
        "next_step": next_step,
        "provenance": {},
    }


def _ready_positioning_packet(
    *,
    status: str = "POSITIONING_READY",
    next_step: str = "POSITIONING_READY_FOR_DOCUMENTS",
) -> dict[str, object]:
    return {
        "schema_version": "recruiter_positioning_packet_v1",
        "skill_id": "positioning-and-evidence",
        "status": status,
        "positioning_summary": "Lead with executive product leadership.",
        "target_narrative": "Operator for scaling product organizations.",
        "evidence": ["Scaled multi-team product orgs."],
        "gaps": ["Exact industry adjacency not confirmed."],
        "risks_and_mitigations": ["Do not overstate sector depth."],
        "recommended_angle": "Scale-stage executive operator.",
        "claims_to_use": ["Led product organizations."],
        "claims_to_avoid": ["Direct ownership of unrelated sector."],
        "missing_information": [],
        "next_step": next_step,
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
        "unsupported_claims": [],
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
        "support_summary": {"explicit": 1, "derived_safe": 0, "weak": 0, "unsupported": 0},
        "privacy_notes": ["sanitized fixture packet"],
        "generation_mode": "deterministic_fake",
        "source_kind": "fake_candidate_facts",
        "provider_called": False,
        "executor_called": False,
        "provenance": {},
    }


def _ready_candidate_facts_packet() -> dict[str, object]:
    return {
        "schema_version": "recruiter_candidate_facts_packet_v1",
        "skill_id": "candidate-facts",
        "status": "READY_PROVIDER_VISIBLE",
        "candidate_ref": "candidate-test",
        "generated_at": "2026-07-01T00:00:00+00:00",
        "source_policy": {
            "raw_private_content_serialized": False,
            "raw_private_paths_serialized": False,
            "provider_visible_only_after_packet_scan": True,
        },
        "requires_user_approval": False,
        "provider_visibility_status": "READY_PROVIDER_VISIBLE",
        "facts": [
            {
                "fact_id": "fact-1",
                "category": "domain",
                "safe_summary": "Product and commercial leadership experience",
                "provider_text": "Candidate has product and commercial leadership experience in digital services.",
                "support_level": "explicit",
                "source_ref_ids": ["src-1"],
                "forbidden_expansions": ["Do not infer revenue ownership", "Do not infer team size"],
                "approval_required": False,
                "provider_visible": True,
                "log_visible": False,
            }
        ],
        "source_references": [
            {
                "source_ref_id": "src-1",
                "source_type": "test_fixture",
                "source_label": "safe-fixture",
                "source_id_hash": "fixture-hash",
                "section_label": "safe-section",
                "content_hash": "fixture-content-hash",
                "sensitivity": "private_sanitized",
                "provider_visible": True,
                "log_visible": True,
            }
        ],
        "allowed_claims": [
            {
                "claim_id": "claim-1",
                "claim_text": "Product and commercial leadership experience in digital services.",
                "source_fact_ids": ["fact-1"],
                "support_level": "explicit",
            }
        ],
        "claims_to_avoid": ["Do not claim revenue ownership."],
        "unsupported_claims": [],
        "redactions": [],
        "support_summary": {"explicit": 1, "derived_safe": 0, "weak": 0, "unsupported": 0},
        "role_target_context": {},
        "privacy_notes": ["sanitized fixture packet"],
        "next_step": "CANDIDATE_FACTS_READY_FOR_POSITIONING",
        "errors": [],
        "warnings": [],
    }


def test_positioning_flow_dry_run_blocks_provider_by_default() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.readiness["reason"] == "provider_execution_requires_explicit_opt_in"


def test_positioning_flow_dry_run_accepts_candidate_facts_and_keeps_provider_blocked_by_default() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.input["candidate_facts_status"] == "READY_PROVIDER_VISIBLE"
    assert report.input["candidate_facts_provider_visibility_status"] == "READY_PROVIDER_VISIBLE"
    assert report.input["candidate_fact_summaries"] == [
        {
            "fact_id": "fact-1",
            "category": "domain",
            "safe_summary": "Product and commercial leadership experience",
            "support_level": "explicit",
        }
    ]
    assert report.input["allowed_claims"] == [
        "Product and commercial leadership experience in digital services."
    ]
    assert "candidate_facts_packet" not in report.input


def test_positioning_flow_dry_run_report_does_not_serialize_provider_text_from_candidate_facts() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "candidate_facts_packet" not in encoded
    assert "Candidate has product and commercial leadership experience in digital services." not in encoded


def test_positioning_flow_dry_run_blocks_candidate_facts_before_provider_execution() -> None:
    blocked_packet = _ready_candidate_facts_packet()
    blocked_packet["status"] = "BLOCKED_UNSAFE_CONTENT"
    blocked_packet["provider_visibility_status"] = "BLOCKED_UNSAFE_CONTENT"
    blocked_packet["errors"] = ["unsafe_path_detected"]
    blocked_packet["facts"] = []

    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=blocked_packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.errors == ["candidate_facts_packet_not_provider_visible"]


def test_positioning_flow_dry_run_unsafe_ready_candidate_facts_do_not_leak_into_blocked_report() -> None:
    unsafe_packet = _ready_candidate_facts_packet()
    unsafe_packet["facts"] = [
        {
            "fact_id": "fact-unsafe",
            "category": "domain",
            "safe_summary": "See /Users/testleak/private/career and email leaktest@example.com",
            "provider_text": "Candidate has product leadership experience.",
            "support_level": "explicit",
            "source_ref_ids": ["src-safe"],
            "forbidden_expansions": [],
            "approval_required": False,
            "provider_visible": True,
            "log_visible": False,
        }
    ]

    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=unsafe_packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.errors == ["candidate_facts_packet_unsafe"]
    assert report.provider_called is False
    assert report.executor_called is False
    assert "/Users/testleak" not in encoded
    assert "private/career" not in encoded
    assert "leaktest@example.com" not in encoded
    assert "candidate_fact_summaries" not in encoded
    assert "See /Users/testleak/private/career and email leaktest@example.com" not in encoded


def test_positioning_flow_dry_run_wrong_schema_candidate_facts_is_controlled() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet={
            "schema_version": "wrong_schema",
            "provider_visibility_status": "READY_PROVIDER_VISIBLE",
            "facts": [{"fact_id": "x"}],
        },
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.errors == ["candidate_facts_packet_schema_invalid"]
    assert report.provider_called is False
    assert report.executor_called is False
    assert "Traceback" not in encoded
    assert "TypeError" not in encoded
    assert "candidate_fact_summaries" not in encoded
    assert "\"source_references\"" not in encoded
    assert "\"allowed_claims\"" not in encoded


def test_positioning_flow_dry_run_missing_optional_candidate_facts_keys_is_controlled() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet={
            "schema_version": "recruiter_candidate_facts_packet_v1",
            "status": "READY_PROVIDER_VISIBLE",
            "provider_visibility_status": "READY_PROVIDER_VISIBLE",
            "facts": [{"fact_id": "x"}],
        },
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.errors == ["candidate_facts_packet_invalid"]
    assert report.provider_called is False
    assert report.executor_called is False
    assert "Traceback" not in encoded
    assert "TypeError" not in encoded


def test_positioning_flow_dry_run_supports_fake_no_provider_positioning_path() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fake_positioning_result_factory=build_fake_positioning_packet_from_candidate_facts,
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_READY
    assert report.provider_called is False
    assert report.executor_called is True
    assert report.positioning_result["status"] == "POSITIONING_READY"
    assert report.positioning_result["generation_mode"] == "deterministic_fake"
    assert report.positioning_result["source_kind"] == "fake_candidate_facts"
    assert report.positioning_result["provider_called"] is False
    assert report.positioning_result["executor_called"] is False
    assert report.positioning_result["candidate_ref"] == "candidate-test"
    assert report.positioning_result["claims_to_avoid"] == ["Do not claim revenue ownership."]
    assert report.positioning_result["allowed_claims"][0]["source_fact_ids"] == ["fact-1"]
    assert report.positioning_result["evidence_items"][0]["source_ref_ids"] == ["src-1"]
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "provider_text" not in encoded
    assert "\"candidate_facts_packet\"" not in encoded
    assert "/home/" not in encoded
    assert "/Users/" not in encoded


def test_positioning_flow_dry_run_fake_path_requires_candidate_facts_packet() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fake_positioning_result_factory=lambda skill_input: {"unexpected": True},
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.errors == ["candidate_facts_packet_missing"]
    assert report.provider_called is False
    assert report.executor_called is False


def test_positioning_flow_dry_run_fake_path_fails_closed_when_claim_lacks_source_fact_ids() -> None:
    candidate_facts_packet = _ready_candidate_facts_packet()
    candidate_facts_packet["allowed_claims"] = [
        {
            "claim_id": "claim-1",
            "claim_text": "Product and commercial leadership experience in digital services.",
            "source_fact_ids": [],
            "support_level": "explicit",
        }
    ]

    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=candidate_facts_packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fake_positioning_result_factory=build_fake_positioning_packet_from_candidate_facts,
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
    assert report.errors == ["positioning_claim_without_source_fact"]


def test_positioning_flow_dry_run_fake_path_fails_closed_when_source_reference_is_missing() -> None:
    candidate_facts_packet = _ready_candidate_facts_packet()
    candidate_facts_packet["facts"][0]["source_ref_ids"] = ["src-missing"]

    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=candidate_facts_packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fake_positioning_result_factory=build_fake_positioning_packet_from_candidate_facts,
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
    assert report.errors == ["positioning_evidence_without_source"]


def test_positioning_flow_dry_run_fake_path_blocks_unsafe_output() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fake_positioning_result_factory=lambda skill_input: {
            **build_fake_positioning_packet_from_candidate_facts(skill_input),
            "privacy_notes": ["Fixture path /home/hermes/.hermes/private must not leak."],
        },
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
    assert report.errors == ["positioning_unsafe_output_detected"]


def test_positioning_flow_dry_run_blocks_need_more_info_by_default() -> None:
    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(
            status="INSUFFICIENT_INPUT",
            recommendation="NEED_MORE_INFO",
            next_step="NEED_MORE_INFO",
        ),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.errors == ["evaluation_requires_more_information"]


def test_positioning_flow_dry_run_allows_provider_only_when_all_gates_are_ready() -> None:
    class _FakeExecutor:
        def execute(self, *, skill_input, expected_schema):
            assert skill_input["skill_id"] == "positioning-and-evidence"
            assert skill_input["evaluation_packet"]["schema_version"] == "recruiter_vacancy_evaluation_packet_v1"
            assert expected_schema["schema_version"] == "recruiter_positioning_packet_v1"
            return {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "positioning-and-evidence",
                "status": "POSITIONING_READY",
                "positioning_summary": "Lead with executive B2B product leadership.",
                "target_narrative": "Operator-executive for complex platform businesses.",
                "evidence": ["Scaled product organizations.", "Worked in B2B SaaS."],
                "gaps": ["Exact fintech depth not confirmed."],
                "risks_and_mitigations": ["Avoid overstating regulated-market depth."],
                "recommended_angle": "Scale-stage product operator.",
                "claims_to_use": ["Built product orgs.", "Led platform strategy."],
                "claims_to_avoid": ["Direct fintech turnaround ownership."],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "provenance": {},
            }

    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _FakeExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_READY
    assert report.provider_called is True
    assert report.executor_called is True
    assert report.positioning_result["schema_version"] == "recruiter_positioning_packet_v1"
    assert report.positioning_result["skill_id"] == "positioning-and-evidence"
    assert report.downstream_gates["document_generation"]["enabled"] is False


def test_positioning_flow_dry_run_fails_closed_on_wrong_skill_id() -> None:
    class _WrongSkillExecutor:
        def execute(self, *, skill_input, expected_schema):
            return {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "document-writer",
                "status": "POSITIONING_READY",
                "positioning_summary": "Wrong skill.",
                "target_narrative": "Wrong skill.",
                "evidence": [],
                "gaps": [],
                "risks_and_mitigations": [],
                "recommended_angle": "Wrong skill.",
                "claims_to_use": [],
                "claims_to_avoid": [],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "provenance": {},
            }

    report = run_recruiter_positioning_flow_dry_run(
        evaluation_packet=_ready_evaluation_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _WrongSkillExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
    assert report.errors == ["positioning_output_skill_id_invalid"]


class _ApplicationMaterialsExecutor:
    provider_backed = False

    def __init__(self, *, reviewer_verdict: str = "APPROVE", invalid_writer: bool = False) -> None:
        self.calls: list[str] = []
        self.writer_inputs: list[dict[str, object]] = []
        self.reviewer_verdict = reviewer_verdict
        self.invalid_writer = invalid_writer

    def execute(self, *, skill_id, skill_input, expected_schema):
        self.calls.append(skill_id)
        if skill_id == "document-writer":
            self.writer_inputs.append(dict(skill_input))
            if self.invalid_writer:
                return {"status": "DRAFT_READY"}
            return {
                "schema_version": "recruiter_document_packet_v1",
                "document_type": skill_input["document_type"],
                "audience": skill_input.get("audience"),
                "purpose": skill_input.get("purpose"),
                "source_positioning_packet_ref": skill_input["source_positioning_packet_ref"],
                "draft": {
                    "format": "text",
                    "content": f"Draft for {skill_input['document_type']}.",
                    "notes": ["Review before any outbound action."],
                },
                "review": {"status": "PENDING"},
                "status": "DRAFT_READY",
                "warnings": [],
                "errors": [],
                "provenance": {},
            }
        return {
            "status": "SUCCESS",
            "skill_id": "document-reviewer",
            "verdict": self.reviewer_verdict,
            "hallucination_risk": "low",
            "unsupported_claims": [],
            "genericness_assessment": "specific enough",
            "tone_seniority_assessment": "appropriate",
            "missing_source_references": [],
            "required_changes": [] if self.reviewer_verdict == "APPROVE" else ["Tighten opening paragraph."],
            "warnings": [],
            "errors": [],
            "provenance": {},
        }


def test_application_materials_flow_dry_run_blocks_provider_by_default() -> None:
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_PROVIDER_EXECUTION_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.downstream_gates["document_generation"]["enabled"] is False
    assert report.downstream_gates["gmail_draft"]["enabled"] is False
    assert report.downstream_gates["linkedin_send"]["enabled"] is False


def test_application_materials_flow_dry_run_requires_private_context_available() -> None:
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_NOT_INSPECTED",
        allow_provider_execution=True,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED
    assert report.errors == ["private_context_not_ready_for_application_materials"]


def test_application_materials_flow_dry_run_rejects_claim_without_source_backing() -> None:
    packet = _ready_positioning_packet()
    packet["allowed_claims"] = [{"claim_id": "claim-1", "claim_text": "Led product organizations."}]

    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED
    assert report.errors == ["positioning_packet_claim_without_source"]


def test_application_materials_flow_dry_run_blocks_unsafe_packet_without_echoing_raw_fields() -> None:
    packet = _ready_positioning_packet()
    packet["positioning_summary"] = "Unsafe /Users/testleak/private/career leaktest@example.com"

    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED
    assert report.errors == ["positioning_packet_unsafe"]
    assert report.positioning_result in (None, {})
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "Unsafe /Users/testleak/private/career leaktest@example.com" not in encoded
    assert "/Users/testleak/private/career" not in encoded
    assert "leaktest@example.com" not in encoded


def test_application_materials_flow_dry_run_runs_writer_and_reviewer_when_ready() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    assert report.provider_called is True
    assert report.executor_called is True
    assert report.application_materials_result["schema_version"] == "recruiter_application_materials_packet_v1"
    assert report.application_materials_result["document_runs"]["cv_tailoring_notes"]["document_type"] == "cv_tailoring_notes"
    assert report.application_materials_result["document_runs"]["cover_letter_draft"]["document_type"] == "cover_letter"
    assert report.application_materials_result["document_runs"]["recruiter_message_draft"]["document_type"] == "recruiter_message"
    assert report.application_materials_result["materials"]["cover_letter_draft"]["content"] == "Draft for cover_letter."
    first_writer_input = executor.writer_inputs[0]
    positioning_result = first_writer_input["positioning_evidence_result"]
    assert positioning_result["allowed_claims"][0]["source_fact_ids"] == ["fact-1"]
    assert positioning_result["evidence_items"][0]["source_ref_ids"] == ["src-1"]
    assert positioning_result["source_references"][0]["source_ref_id"] == "src-1"
    assert positioning_result["claims_to_avoid"] == ["Direct ownership of unrelated sector."]
    assert report.application_materials_result["review"]["verdict"] == "APPROVE"
    assert executor.calls == [
        "document-writer",
        "document-reviewer",
        "document-writer",
        "document-reviewer",
        "document-writer",
        "document-reviewer",
    ]


def test_application_materials_flow_dry_run_processes_only_selected_target() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    assert set(report.application_materials_result["document_runs"]) == {"recruiter_message_draft"}
    assert set(report.application_materials_result["materials"]) == {"recruiter_message_draft", "application_summary"}
    assert report.application_materials_result["document_runs"]["recruiter_message_draft"]["document_type"] == "recruiter_message"
    assert executor.calls == ["document-writer", "document-reviewer"]


def test_application_materials_flow_dry_run_selected_target_blocks_independently() -> None:
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: _ApplicationMaterialsExecutor(reviewer_verdict="CHANGES_REQUESTED"),
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
    assert set(report.application_materials_result["document_runs"]) == {"recruiter_message_draft"}
    assert report.application_materials_result["review"]["document_type"] == "recruiter_message"
    assert report.application_materials_result["materials"] == {}


def test_application_materials_flow_dry_run_rejects_invalid_target_without_provider_call() -> None:
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="invalid_target",
        executor_factory=lambda: _ApplicationMaterialsExecutor(),
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.errors == ["invalid_document_target"]


def test_application_materials_flow_dry_run_fails_closed_on_invalid_writer_output() -> None:
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _ApplicationMaterialsExecutor(invalid_writer=True),
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_OUTPUT_INVALID


def test_application_materials_flow_dry_run_blocks_when_reviewer_requests_changes() -> None:
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: _ApplicationMaterialsExecutor(reviewer_verdict="CHANGES_REQUESTED"),
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED


def test_positioning_smoke_harness_blocks_missing_candidate_facts_without_leak() -> None:
    report = run_recruiter_positioning_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=None,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterPositioningSmokeStatus.INPUT_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.errors == ["candidate_facts_packet_missing"]
    assert "\"positioning_packet_summary\": null" in encoded


def test_positioning_smoke_harness_blocks_provider_by_default() -> None:
    report = run_recruiter_positioning_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
    )

    assert report.status is RecruiterPositioningSmokeStatus.READY_PROVIDER_BLOCKED
    assert report.provider_allowed is False
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.output_validation["status"] == "not_run"
    assert "call_provider_model" in report.forbidden_actions


def test_positioning_smoke_harness_runs_fake_executor_when_opted_in() -> None:
    class _FakeSmokeExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            assert skill_input["skill_id"] == "positioning-and-evidence"
            assert expected_schema["schema_version"] == "recruiter_positioning_packet_v1"
            return build_fake_positioning_packet_from_candidate_facts(skill_input)

    report = run_recruiter_positioning_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        allow_provider_execution=True,
        executor_factory=lambda: _FakeSmokeExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterPositioningSmokeStatus.READY
    assert report.provider_allowed is True
    assert report.provider_called is False
    assert report.executor_called is True
    assert report.output_validation["status"] == "valid"
    assert report.positioning_packet_summary["schema_version"] == "recruiter_positioning_packet_v1"
    assert "provider_text" not in encoded
    assert "\"candidate_facts_packet\"" not in encoded


def test_positioning_smoke_harness_invalid_output_fails_closed_without_leak() -> None:
    class _InvalidSmokeExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            packet = build_fake_positioning_packet_from_candidate_facts(skill_input)
            packet["allowed_claims"][0]["source_fact_ids"] = []
            return packet

    report = run_recruiter_positioning_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        allow_provider_execution=True,
        executor_factory=lambda: _InvalidSmokeExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterPositioningSmokeStatus.OUTPUT_INVALID
    assert report.provider_called is False
    assert report.executor_called is True
    assert report.errors == ["positioning_packet_claim_without_source"]
    assert "provider_text" not in encoded


def test_positioning_smoke_harness_executor_exception_is_controlled() -> None:
    class _ExplodingSmokeExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            raise RuntimeError("boom")

    report = run_recruiter_positioning_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        allow_provider_execution=True,
        executor_factory=lambda: _ExplodingSmokeExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterPositioningSmokeStatus.OUTPUT_INVALID
    assert report.errors == ["positioning_executor_failed"]
    assert "Traceback" not in encoded
