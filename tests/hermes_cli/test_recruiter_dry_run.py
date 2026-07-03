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
from hermes_cli.recruiter_candidate_facts import build_application_materials_ready_fixture_payload
from hermes_cli.recruiter_dry_run import (
    REQUIRED_APPLICATION_MATERIAL_TARGETS,
    RecruiterDryRunRequest,
    RecruiterDryRunStatus,
    RecruiterE2EApplicationMaterialsStatus,
    RecruiterApplicationMaterialsSmokeStatus,
    RecruiterPositioningSmokeStatus,
    build_fake_positioning_packet_from_candidate_facts,
    run_recruiter_e2e_application_materials_smoke_harness,
    run_recruiter_application_materials_smoke_harness,
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
        "target_company": "Acme Pay",
        "target_role": "VP Product, Global Payments",
        "evidence": ["Scaled multi-team product orgs."],
        "gaps": ["Exact industry adjacency not confirmed."],
        "risks_and_mitigations": ["Do not overstate sector depth."],
        "recommended_angle": "Scale-stage executive operator.",
        "claims_to_use": [
            "Improved onboarding conversion with measurable product execution gains.",
            "Drove pricing and packaging iteration tied to adoption and retention.",
        ],
        "claims_to_avoid": ["Direct ownership of unrelated sector."],
        "missing_information": [],
        "next_step": next_step,
        "allowed_claims": [
            {
                "claim_id": "claim-1",
                "claim_text": "Improved onboarding conversion with measurable product execution gains.",
                "source_fact_ids": ["fact-1"],
                "support_level": "explicit",
            },
            {
                "claim_id": "claim-2",
                "claim_text": "Drove pricing and packaging iteration tied to adoption and retention.",
                "source_fact_ids": ["fact-2"],
                "support_level": "explicit",
            }
        ],
        "evidence_items": [
            {
                "claim_text": "Improved onboarding conversion with measurable product execution gains.",
                "source_fact_ids": ["fact-1"],
                "source_ref_ids": ["src-1"],
                "support_level": "explicit",
                "category": "achievement",
                "safe_summary": "Improved onboarding conversion and reduced friction with measurable gains.",
            },
            {
                "claim_text": "Drove pricing and packaging iteration tied to adoption and retention.",
                "source_fact_ids": ["fact-2"],
                "source_ref_ids": ["src-2"],
                "support_level": "explicit",
                "category": "achievement",
                "safe_summary": "Drove pricing and packaging changes tied to adoption and retention improvements.",
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
            },
            {
                "source_ref_id": "src-2",
                "source_label": "safe-fixture",
                "source_id_hash": "fixture-hash-2",
                "section_label": "safe-section",
                "support_level": "explicit",
                "category": "test_fixture",
            }
        ],
        "support_summary": {"explicit": 2, "derived_safe": 0, "weak": 0, "unsupported": 0},
        "privacy_notes": ["sanitized fixture packet"],
        "generation_mode": "deterministic_fake",
        "source_kind": "fake_candidate_facts",
        "provider_called": False,
        "executor_called": False,
        "provenance": {},
    }


def _ready_candidate_facts_packet() -> dict[str, object]:
    fixture = build_application_materials_ready_fixture_payload()
    return {
        "schema_version": "recruiter_candidate_facts_packet_v1",
        "skill_id": "candidate-facts",
        "status": "READY_PROVIDER_VISIBLE",
        "candidate_ref": fixture["candidate_ref"],
        "generated_at": "2026-07-01T00:00:00+00:00",
        "source_policy": {
            "raw_private_content_serialized": False,
            "raw_private_paths_serialized": False,
            "provider_visible_only_after_packet_scan": True,
        },
        "requires_user_approval": False,
        "provider_visibility_status": "READY_PROVIDER_VISIBLE",
        "facts": fixture["facts"],
        "source_references": fixture["source_references"],
        "allowed_claims": fixture["allowed_claims"],
        "claims_to_avoid": fixture["claims_to_avoid"],
        "unsupported_claims": fixture["unsupported_claims"],
        "redactions": [],
        "support_summary": {"explicit": 4, "derived_safe": 2, "weak": 1, "unsupported": 0},
        "role_target_context": fixture.get("role_target_context", {}),
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
    assert len(report.input["candidate_fact_summaries"]) >= 6
    assert {"domain", "achievement", "role_history", "scope"} <= {
        item["category"] for item in report.input["candidate_fact_summaries"]
    }
    assert len(report.input["allowed_claims"]) >= 4
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
    assert report.positioning_result["candidate_ref"] == "candidate-application-materials-fixture"
    assert len(report.positioning_result["allowed_claims"]) >= 4
    assert len(report.positioning_result["evidence_items"]) >= 6
    assert len(report.positioning_result["source_references"]) >= 4
    assert len(report.positioning_result["claims_to_avoid"]) >= 2
    assert len(report.positioning_result["unsupported_claims"]) >= 2
    source_ref_ids = {item["source_ref_id"] for item in report.positioning_result["source_references"]}
    categories = {item["category"] for item in report.positioning_result["evidence_items"]}
    assert {"domain", "achievement", "role_history", "scope"} <= categories
    assert all(item["source_fact_ids"] for item in report.positioning_result["allowed_claims"])
    assert all(item["source_ref_ids"] for item in report.positioning_result["evidence_items"])
    assert all(set(item["source_ref_ids"]) <= source_ref_ids for item in report.positioning_result["evidence_items"])
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "provider_text" not in encoded
    assert "\"candidate_facts_packet\"" not in encoded
    assert "/home/" not in encoded
    assert "/Users/" not in encoded
    assert "@" not in encoded


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
                "allowed_claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_text": "Built product orgs.",
                        "source_fact_ids": ["fact-1"],
                        "support_level": "explicit",
                    }
                ],
                "evidence_items": [
                    {
                        "claim_text": "Built product orgs.",
                        "source_fact_ids": ["fact-1"],
                        "source_ref_ids": ["src-1"],
                        "support_level": "explicit",
                        "category": "leadership",
                        "safe_summary": "Scaled product organizations.",
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
                "allowed_claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_text": "Wrong skill claim.",
                        "source_fact_ids": ["fact-1"],
                        "support_level": "explicit",
                    }
                ],
                "evidence_items": [
                    {
                        "claim_text": "Wrong skill claim.",
                        "source_fact_ids": ["fact-1"],
                        "source_ref_ids": ["src-1"],
                        "support_level": "explicit",
                        "category": "leadership",
                        "safe_summary": "Wrong skill summary.",
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

    def __init__(
        self,
        *,
        reviewer_verdict: str = "APPROVE",
        invalid_writer: bool = False,
        reviewer_result_overrides: dict[str, object] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.writer_inputs: list[dict[str, object]] = []
        self.reviewer_verdict = reviewer_verdict
        self.invalid_writer = invalid_writer
        self.reviewer_result_overrides = dict(reviewer_result_overrides or {})

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
        reviewer_result = {
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
        reviewer_result.update(self.reviewer_result_overrides)
        return reviewer_result


class _PerDocumentApplicationMaterialsExecutor(_ApplicationMaterialsExecutor):
    def __init__(
        self,
        *,
        reviewer_by_document_type: dict[str, str] | None = None,
        reviewer_overrides_by_document_type: dict[str, dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self.reviewer_by_document_type = dict(reviewer_by_document_type or {})
        self.reviewer_overrides_by_document_type = {
            key: dict(value) for key, value in (reviewer_overrides_by_document_type or {}).items()
        }

    def execute(self, *, skill_id, skill_input, expected_schema):
        if skill_id != "document-reviewer":
            return super().execute(skill_id=skill_id, skill_input=skill_input, expected_schema=expected_schema)

        document_type = str(skill_input.get("document_type") or "")
        verdict = self.reviewer_by_document_type.get(document_type, "APPROVE")
        overrides = self.reviewer_overrides_by_document_type.get(document_type, {})
        self.reviewer_verdict = verdict
        self.reviewer_result_overrides = dict(overrides)
        return super().execute(skill_id=skill_id, skill_input=skill_input, expected_schema=expected_schema)


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
    assert report.application_materials_result["materials"]["cover_letter_draft"]["content"] != "Draft for cover_letter."
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
        "document-reviewer",
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
    assert executor.calls == ["document-reviewer"]


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
    executor = _PerDocumentApplicationMaterialsExecutor(
        reviewer_by_document_type={
            "cv_tailoring_notes": "APPROVE",
            "cover_letter": "CHANGES_REQUESTED",
        }
    )
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
    assert report.application_materials_result["causing_target"] == "cover_letter_draft"
    assert report.application_materials_result["block_reason"] == "REQUIRED_CHANGES_REQUESTED"
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["ready"] is False
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["blocked"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_called"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_verdict"] == "CHANGES_REQUESTED"
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["block_reason"] == "REQUIRED_CHANGES_REQUESTED"
    assert (
        report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_diagnostics_counts"][
            "required_changes_count"
        ]
        == 1
    )
    encoded = json.dumps(
        {
            "target_results": report.application_materials_result["target_results"],
            "review_summary": report.application_materials_result["review_summary"],
        },
        sort_keys=True,
    )
    assert "Draft for cover_letter." not in encoded
    assert "provider_text" not in encoded
    assert "/home/hermes" not in encoded


def test_application_materials_flow_dry_run_marks_pending_reviewer_result_invalid_and_blocking() -> None:
    executor = _PerDocumentApplicationMaterialsExecutor(
        reviewer_by_document_type={
            "cv_tailoring_notes": "APPROVE",
            "cover_letter": "APPROVE",
        },
        reviewer_overrides_by_document_type={
            "cover_letter": {"verdict": "PENDING"},
        },
    )
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_OUTPUT_INVALID
    assert report.application_materials_result["causing_target"] == "cover_letter_draft"
    assert report.application_materials_result["block_reason"] == "DOCUMENT_REVIEW_PENDING_BLOCKING"
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["ready"] is False
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["blocked"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_called"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_verdict"] == "PENDING"
    assert (
        report.application_materials_result["target_results"]["cover_letter_draft"]["block_reason"]
        == "DOCUMENT_REVIEW_PENDING_BLOCKING"
    )
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_diagnostics_counts"] == {
        "unsupported_claims_count": 0,
        "missing_source_references_count": 0,
        "required_changes_count": 0,
    }


def test_application_materials_flow_dry_run_surfaces_unsupported_claim_block_reason() -> None:
    executor = _PerDocumentApplicationMaterialsExecutor(
        reviewer_by_document_type={
            "cv_tailoring_notes": "APPROVE",
            "cover_letter": "CHANGES_REQUESTED",
        },
        reviewer_overrides_by_document_type={
            "cover_letter": {"unsupported_claims": ["Unsupported payments infrastructure ownership claim."]},
        },
    )
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
    assert report.application_materials_result["block_reason"] == "UNSUPPORTED_CLAIMS_PRESENT"
    assert report.application_materials_result["review_summary"]["unsupported_claims_present"] is True
    assert (
        report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_diagnostics_counts"][
            "unsupported_claims_count"
        ]
        == 1
    )


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


def test_positioning_smoke_harness_rejects_ready_evidence_without_source_refs() -> None:
    class _MissingEvidenceSourceExecutor:
        provider_backed = True

        def execute(self, *, skill_input, expected_schema):
            packet = build_fake_positioning_packet_from_candidate_facts(skill_input)
            packet["evidence_items"][0]["source_ref_ids"] = []
            return packet

    report = run_recruiter_positioning_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        allow_provider_execution=True,
        executor_factory=lambda: _MissingEvidenceSourceExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterPositioningSmokeStatus.OUTPUT_INVALID
    assert report.provider_called is True
    assert report.executor_called is True
    assert report.errors == ["positioning_packet_evidence_without_source"]
    assert report.output_validation["status"] == "invalid"
    assert "provider_text" not in encoded
    assert "\"evaluation_packet\"" not in encoded
    assert "\"candidate_facts_packet\"" not in encoded


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


def test_application_materials_smoke_harness_blocks_provider_by_default() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        document_target="recruiter_message_draft",
    )

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED
    assert report.provider_allowed is False
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.reviewer_called is False
    assert report.document_target == "recruiter_message_draft"
    assert report.output_validation["status"] == "not_run"
    assert report.document_summary is None
    assert report.review_summary is None


def test_application_materials_smoke_harness_runs_fake_executor_when_opted_in() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: executor,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY
    assert report.provider_allowed is True
    assert report.provider_called is False
    assert report.executor_called is True
    assert report.reviewer_called is True
    assert report.document_summary["documents"]["recruiter_message_draft"]["document_type"] == "recruiter_message"
    assert report.target_results["recruiter_message_draft"] == {
        "status": "ready",
        "blocked": False,
        "generated": True,
        "ready": True,
        "draft_only": True,
        "user_review_required": True,
        "writer_called": False,
        "reviewer_called": True,
        "document_writer_gate_status": "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER",
        "document_reviewer_gate_status": "APPROVE",
        "reviewer_verdict": "APPROVE",
        "reviewer_notes_present": True,
        "block_reason": None,
        "reviewer_diagnostics_counts": {
            "unsupported_claims_count": 0,
            "missing_source_references_count": 0,
            "required_changes_count": 0,
        },
        "sanitized_reviewer_diagnostics_summary": [],
    }
    assert report.output_validation["status"] == "valid"
    assert report.document_summary["generated_targets"] == ["recruiter_message_draft"]
    assert report.review_summary["reviewer_verdicts"]["recruiter_message_draft"] == "APPROVE"
    assert "provider_text" not in encoded
    assert "\"positioning_packet\"" not in encoded


def test_application_materials_smoke_harness_invalid_writer_flag_does_not_break_deterministic_outward_path() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: _ApplicationMaterialsExecutor(invalid_writer=True),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY
    assert report.provider_called is False
    assert report.executor_called is True
    assert report.reviewer_called is True
    assert report.errors == []
    assert "provider_text" not in encoded


def test_application_materials_smoke_harness_executor_exception_is_controlled() -> None:
    class _ExplodingApplicationMaterialsExecutor:
        provider_backed = False

        def execute(self, *, skill_id, skill_input, expected_schema):
            raise RuntimeError("boom")

    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: _ExplodingApplicationMaterialsExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterApplicationMaterialsSmokeStatus.OUTPUT_INVALID
    assert report.errors == ["application_materials_executor_failed"]
    assert "Traceback" not in encoded


def test_application_materials_smoke_harness_selected_target_changes_requested_reports_truthfully() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: _ApplicationMaterialsExecutor(reviewer_verdict="CHANGES_REQUESTED"),
    )

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.OUTPUT_INVALID
    assert report.document_summary["generated_targets"] == ["recruiter_message_draft"]
    assert report.review_summary["reviewer_verdicts"]["recruiter_message_draft"] == "CHANGES_REQUESTED"
    assert report.target_results == {
        "recruiter_message_draft": {
            "status": "review_blocked",
            "blocked": True,
            "generated": True,
            "ready": False,
            "draft_only": True,
            "user_review_required": True,
            "writer_called": False,
            "reviewer_called": True,
            "document_writer_gate_status": "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER",
            "document_reviewer_gate_status": "CHANGES_REQUESTED",
            "reviewer_verdict": "CHANGES_REQUESTED",
            "reviewer_notes_present": True,
            "block_reason": "REQUIRED_CHANGES_REQUESTED",
            "reviewer_diagnostics_counts": {
                "unsupported_claims_count": 0,
                "missing_source_references_count": 0,
                "required_changes_count": 1,
            },
            "sanitized_reviewer_diagnostics_summary": ["Tighten opening paragraph."],
        }
    }


def test_application_materials_smoke_harness_changes_requested_includes_report_safe_reviewer_diagnostics() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: _ApplicationMaterialsExecutor(
            reviewer_verdict="CHANGES_REQUESTED",
            reviewer_result_overrides={
                "hallucination_risk": "moderate",
                "unsupported_claims": [
                    "Broad ownership claim without source grounding",
                    "Draft says Jane Doe led /tmp migrations for /Users/denis accounts.",
                ],
                "missing_source_references": [
                    "Claim about pricing/monetization lacks source_ref_ids",
                    "/home/hermes/.hermes/private/source.md",
                ],
                "required_changes": [
                    "Replace broad capability statements with source-backed examples",
                    "Do not include provider_text or filesystem paths like /tmp/secret.txt",
                ],
            },
        ),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    diagnostics = report.review_summary["reviewer_diagnostics"]["recruiter_message_draft"]

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.OUTPUT_INVALID
    assert diagnostics == {
        "verdict": "CHANGES_REQUESTED",
        "hallucination_risk": "moderate",
        "unsupported_claims_count": 2,
        "missing_source_references_count": 2,
        "required_changes_count": 2,
        "unsupported_claim_summaries": ["Broad ownership claim without source grounding"],
        "missing_source_reference_summaries": ["Claim about pricing/monetization lacks source_ref_ids"],
        "required_change_summaries": ["Replace broad capability statements with source-backed examples"],
    }
    assert report.review_summary["unsupported_claims_present"] is True
    assert "Draft for recruiter_message." not in encoded
    assert "provider_text" not in encoded
    assert "/tmp/" not in encoded
    assert "/Users/" not in encoded
    assert "/home/hermes/.hermes/private" not in encoded


def test_application_materials_smoke_harness_approve_keeps_green_behavior_with_empty_diagnostics() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: _ApplicationMaterialsExecutor(),
    )

    diagnostics = report.review_summary["reviewer_diagnostics"]["recruiter_message_draft"]

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY
    assert diagnostics == {
        "verdict": "APPROVE",
        "hallucination_risk": "low",
        "unsupported_claims_count": 0,
        "missing_source_references_count": 0,
        "required_changes_count": 0,
        "unsupported_claim_summaries": [],
        "missing_source_reference_summaries": [],
        "required_change_summaries": [],
    }


def test_required_application_material_targets_are_exact_and_stable() -> None:
    assert REQUIRED_APPLICATION_MATERIAL_TARGETS == (
        "recruiter_message_draft",
        "cover_letter_draft",
        "cv_tailoring_notes",
    )
    assert len(REQUIRED_APPLICATION_MATERIAL_TARGETS) == len(set(REQUIRED_APPLICATION_MATERIAL_TARGETS))


def test_application_materials_smoke_harness_all_required_targets_blocked_by_default() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        all_required_targets=True,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED
    assert report.provider_allowed is False
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.reviewer_called is False
    assert report.target_mode == "all_required_targets"
    assert report.document_target is None
    assert report.required_targets == list(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert sorted(report.target_results) == sorted(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert {payload["status"] for payload in report.target_results.values()} == {"provider_blocked"}
    assert "provider_text" not in encoded
    assert "\"positioning_packet\"" not in encoded


def test_application_materials_smoke_harness_conflicting_target_modes_fail_closed() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        all_required_targets=True,
        document_target="recruiter_message_draft",
    )

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.INPUT_BLOCKED
    assert report.errors == ["application_materials_target_mode_conflict"]
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.reviewer_called is False


def test_application_materials_smoke_harness_all_required_targets_runs_fake_executor() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        all_required_targets=True,
        executor_factory=lambda: executor,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY
    assert report.provider_allowed is True
    assert report.provider_called is False
    assert report.executor_called is True
    assert report.reviewer_called is True
    assert report.target_mode == "all_required_targets"
    assert report.required_targets == list(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert sorted(report.target_results) == sorted(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    for target in REQUIRED_APPLICATION_MATERIAL_TARGETS:
        target_result = report.target_results[target]
        assert target_result["status"] == "ready"
        assert target_result["draft_only"] is True
        assert target_result["user_review_required"] is True
        assert target_result["reviewer_verdict"] == "APPROVE"
        assert target_result["reviewer_notes_present"] is True
    assert "provider_text" not in encoded
    assert "\"positioning_packet\"" not in encoded


def test_application_materials_smoke_harness_all_required_targets_keeps_partial_progress_truthful() -> None:
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        all_required_targets=True,
        executor_factory=lambda: _ApplicationMaterialsExecutor(reviewer_verdict="CHANGES_REQUESTED"),
    )

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.OUTPUT_INVALID
    assert report.document_summary["generated_targets"] == ["cv_tailoring_notes"]
    assert report.review_summary["reviewer_verdicts"] == {"cv_tailoring_notes": "CHANGES_REQUESTED"}
    assert report.target_results["cv_tailoring_notes"] == {
        "status": "review_blocked",
        "blocked": True,
        "generated": True,
        "ready": False,
        "draft_only": True,
        "user_review_required": True,
        "writer_called": True,
        "reviewer_called": True,
        "document_writer_gate_status": "DRAFT_READY",
        "document_reviewer_gate_status": "CHANGES_REQUESTED",
        "reviewer_verdict": "CHANGES_REQUESTED",
        "reviewer_notes_present": True,
        "block_reason": "REQUIRED_CHANGES_REQUESTED",
        "reviewer_diagnostics_counts": {
            "unsupported_claims_count": 0,
            "missing_source_references_count": 0,
            "required_changes_count": 1,
        },
        "sanitized_reviewer_diagnostics_summary": ["Tighten opening paragraph."],
    }
    assert report.target_results["cover_letter_draft"]["status"] == "not_requested"
    assert report.target_results["recruiter_message_draft"]["status"] == "not_requested"


def test_recruiter_e2e_harness_blocks_provider_by_default_without_executor_calls() -> None:
    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        all_required_targets=True,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.schema_version == "recruiter_e2e_application_materials_report_v1"
    assert report.status is RecruiterE2EApplicationMaterialsStatus.READY_PROVIDER_BLOCKED
    assert report.provider_allowed is False
    assert report.provider_called is False
    assert report.positioning_provider_called is False
    assert report.positioning_executor_called is False
    assert report.application_materials_provider_called is False
    assert report.application_materials_executor_called is False
    assert report.reviewer_called is False
    assert report.required_targets == list(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert sorted(report.target_results) == sorted(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert report.positioning_summary["status"] == RecruiterPositioningSmokeStatus.READY_PROVIDER_BLOCKED.value
    assert report.application_materials_summary["status"] == RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED.value
    assert "\"evaluation_packet\"" not in encoded
    assert "\"candidate_facts_packet\"" not in encoded
    assert "\"positioning_packet\"" not in encoded
    assert "provider_text" not in encoded


def test_recruiter_e2e_harness_blocks_unsafe_candidate_facts_without_leak() -> None:
    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet={
            **_ready_candidate_facts_packet(),
            "privacy_notes": ["Unsafe /Users/testleak/private/career leaktest@example.com"],
        },
        all_required_targets=True,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterE2EApplicationMaterialsStatus.INPUT_BLOCKED
    assert report.errors == ["candidate_facts_packet_unsafe"]
    assert report.provider_called is False
    assert report.positioning_executor_called is False
    assert report.application_materials_executor_called is False
    assert "/Users/testleak" not in encoded
    assert "private/career" not in encoded
    assert "leaktest@example.com" not in encoded
    assert "Traceback" not in encoded


def test_recruiter_e2e_harness_runs_fake_full_chain_when_opted_in() -> None:
    executor = _ApplicationMaterialsExecutor()

    class _FakePositioningExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            return build_fake_positioning_packet_from_candidate_facts(skill_input)

    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        all_required_targets=True,
        allow_provider_execution=True,
        positioning_executor_factory=lambda: _FakePositioningExecutor(),
        application_materials_executor_factory=lambda: executor,
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterE2EApplicationMaterialsStatus.READY
    assert report.provider_allowed is True
    assert report.provider_called is False
    assert report.positioning_provider_called is False
    assert report.positioning_executor_called is True
    assert report.application_materials_provider_called is False
    assert report.application_materials_executor_called is True
    assert report.reviewer_called is True
    assert report.positioning_summary["schema_version"] == "recruiter_positioning_packet_v1"
    assert report.application_materials_summary["status"] == RecruiterApplicationMaterialsSmokeStatus.READY.value
    for target in REQUIRED_APPLICATION_MATERIAL_TARGETS:
        target_result = report.target_results[target]
        assert target_result["status"] == "ready"
        assert target_result["draft_only"] is True
        assert target_result["user_review_required"] is True
        assert target_result["reviewer_verdict"] == "APPROVE"
        assert target_result["reviewer_notes_present"] is True
    assert "provider_text" not in encoded


def test_recruiter_e2e_harness_invalid_fake_positioning_output_fails_closed() -> None:
    class _InvalidPositioningExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            return {"unexpected": True}

    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        all_required_targets=True,
        allow_provider_execution=True,
        positioning_executor_factory=lambda: _InvalidPositioningExecutor(),
        application_materials_executor_factory=lambda: _ApplicationMaterialsExecutor(),
    )

    assert report.status is RecruiterE2EApplicationMaterialsStatus.OUTPUT_INVALID
    assert report.errors == [
        "missing_required_positioning_output_fields:schema_version,skill_id,status,positioning_summary,target_narrative,evidence,gaps,risks_and_mitigations,recommended_angle,claims_to_use,claims_to_avoid,missing_information,next_step,allowed_claims,evidence_items,source_references,provenance"
    ]
    assert report.application_materials_executor_called is False


def test_recruiter_e2e_harness_unsafe_fake_positioning_output_does_not_leak() -> None:
    class _UnsafePositioningExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            packet = build_fake_positioning_packet_from_candidate_facts(skill_input)
            packet["positioning_summary"] = "Unsafe /Users/testleak/private/career leaktest@example.com"
            packet["recommended_angle"] = "Unsafe /Users/testleak/private/career leaktest@example.com"
            return packet

    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        all_required_targets=True,
        allow_provider_execution=True,
        positioning_executor_factory=lambda: _UnsafePositioningExecutor(),
        application_materials_executor_factory=lambda: _ApplicationMaterialsExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterE2EApplicationMaterialsStatus.OUTPUT_INVALID
    assert report.errors == ["positioning_packet_unsafe"]
    assert report.positioning_summary is None
    assert report.application_materials_executor_called is False
    assert report.reviewer_called is False
    assert "/Users/testleak" not in encoded
    assert "private/career" not in encoded
    assert "leaktest@example.com" not in encoded
    assert "provider_text" not in encoded
    assert "\"positioning_packet\"" not in encoded
    assert "Traceback" not in encoded


def test_recruiter_e2e_harness_positioning_exception_is_controlled() -> None:
    class _ExplodingPositioningExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            raise Exception("boom")

    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        all_required_targets=True,
        allow_provider_execution=True,
        positioning_executor_factory=lambda: _ExplodingPositioningExecutor(),
        application_materials_executor_factory=lambda: _ApplicationMaterialsExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterE2EApplicationMaterialsStatus.OUTPUT_INVALID
    assert report.errors == ["positioning_executor_failed"]
    assert report.positioning_executor_called is True
    assert report.application_materials_executor_called is False
    assert report.reviewer_called is False
    assert "Traceback" not in encoded
    assert "boom" not in encoded


def test_recruiter_e2e_harness_application_materials_exception_is_controlled() -> None:
    class _FakePositioningExecutor:
        provider_backed = False

        def execute(self, *, skill_input, expected_schema):
            return build_fake_positioning_packet_from_candidate_facts(skill_input)

    class _ExplodingApplicationMaterialsExecutor:
        provider_backed = False

        def execute(self, *, skill_id, skill_input, expected_schema):
            raise RuntimeError("boom")

    report = run_recruiter_e2e_application_materials_smoke_harness(
        evaluation_packet=_ready_evaluation_packet(),
        candidate_facts_packet=_ready_candidate_facts_packet(),
        all_required_targets=True,
        allow_provider_execution=True,
        positioning_executor_factory=lambda: _FakePositioningExecutor(),
        application_materials_executor_factory=lambda: _ExplodingApplicationMaterialsExecutor(),
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RecruiterE2EApplicationMaterialsStatus.OUTPUT_INVALID
    assert report.errors == ["application_materials_executor_failed"]
    assert "Traceback" not in encoded



def test_application_materials_flow_dry_run_uses_deterministic_composer_for_recruiter_message() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    assert executor.calls == ["document-reviewer"]
    assert report.application_materials_result["writer_called"] is False
    assert report.application_materials_result["reviewer_called"] is True
    assert (
        report.application_materials_result["document_runs"]["recruiter_message_draft"]["downstream_gates"]["document_writer"]["status"]
        == "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER"
    )
    assert report.application_materials_result["document_runs"]["recruiter_message_draft"]["writer_called"] is False
    assert report.application_materials_result["document_runs"]["recruiter_message_draft"]["reviewer_called"] is True
    content = report.application_materials_result["materials"]["recruiter_message_draft"]["content"]
    assert content != "Draft for recruiter_message."
    assert "growth, pricing, or partner activation inputs" not in content
    assert "payment acceptance, checkout, or regulated-market execution" not in content


def test_application_materials_flow_dry_run_uses_deterministic_composer_for_cover_letter_truthfully() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="cover_letter_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    assert executor.calls == ["document-reviewer"]
    assert report.application_materials_result["writer_called"] is False
    assert report.application_materials_result["reviewer_called"] is True
    assert (
        report.application_materials_result["document_runs"]["cover_letter_draft"]["downstream_gates"]["document_writer"]["status"]
        == "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER"
    )
    assert report.application_materials_result["document_runs"]["cover_letter_draft"]["writer_called"] is False
    assert report.application_materials_result["document_runs"]["cover_letter_draft"]["reviewer_called"] is True


def test_application_materials_flow_dry_run_allows_natural_deterministic_recruiter_message() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    run_report = report.application_materials_result["document_runs"]["recruiter_message_draft"]
    assert run_report["status"] == "DOCUMENT_REVIEW_APPROVED"
    assert run_report["errors"] == []
    assert run_report["reviewer_called"] is True
    content = report.application_materials_result["materials"]["recruiter_message_draft"]["content"]
    assert "Acme Pay" in content
    assert "VP Product, Global Payments" in content
    assert "Improved onboarding conversion and reduced friction with measurable gains." in content


def test_application_materials_flow_dry_run_blocks_deterministic_draft_with_internal_packet_language(monkeypatch) -> None:
    from hermes_cli import recruiter_application_materials_flow as flow_module

    original_compose = flow_module.compose_deterministic_outward_draft

    def _unsafe_compose(writer_input):
        packet = original_compose(writer_input)
        packet["draft"]["content"] = "Approved evidence shows fit based on source_ref support."
        return packet

    monkeypatch.setattr(flow_module, "compose_deterministic_outward_draft", _unsafe_compose)
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="recruiter_message_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_OUTPUT_INVALID
    run_report = report.application_materials_result["document_runs"]["recruiter_message_draft"]
    assert run_report["status"] == "DOCUMENT_OUTPUT_INVALID"
    assert run_report["errors"] == ["writer_internal_language_forbidden"]
    assert run_report["reviewer_called"] is False
    assert report.application_materials_result["reviewer_called"] is False


def test_application_materials_flow_dry_run_allows_natural_deterministic_cover_letter() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="cover_letter_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    run_report = report.application_materials_result["document_runs"]["cover_letter_draft"]
    assert run_report["status"] == "DOCUMENT_REVIEW_APPROVED"
    assert run_report["errors"] == []
    assert run_report["reviewer_called"] is True
    content = report.application_materials_result["materials"]["cover_letter_draft"]["content"]
    assert "Acme Pay" in content
    assert "VP Product, Global Payments" in content
    assert "Improved onboarding conversion and reduced friction with measurable gains." in content
    assert "Drove pricing and packaging changes tied to adoption and retention improvements." in content


def test_application_materials_flow_dry_run_blocks_generic_cover_letter_placeholder() -> None:
    packet = _ready_positioning_packet()
    packet["allowed_claims"] = [
        {
            "claim_id": "claim-generic-1",
            "claim_text": "Relevant adjacent experience with payment acceptance.",
            "source_fact_ids": ["fact-generic-1"],
            "support_level": "derived_safe",
        }
    ]
    packet["evidence_items"] = [
        {
            "claim_text": "Relevant adjacent experience with payment acceptance.",
            "source_fact_ids": ["fact-generic-1"],
            "source_ref_ids": ["src-generic-1"],
            "support_level": "derived_safe",
            "category": "domain",
            "safe_summary": "Relevant adjacent experience with payment acceptance.",
        }
    ]
    packet["source_references"] = [
        {
            "source_ref_id": "src-generic-1",
            "source_label": "safe-fixture",
            "source_id_hash": "fixture-generic-1",
            "section_label": "safe-section",
            "support_level": "derived_safe",
            "category": "test_fixture",
        }
    ]
    executor = _ApplicationMaterialsExecutor()

    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=packet,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        document_target="cover_letter_draft",
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
    assert report.application_materials_result["causing_target"] == "cover_letter_draft"
    assert report.application_materials_result["block_reason"] == "DOCUMENT_DRAFT_INSUFFICIENT_GROUNDED_CLAIMS"
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["ready"] is False
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["blocked"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_called"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_verdict"] == "CHANGES_REQUESTED"
    assert (
        report.application_materials_result["target_results"]["cover_letter_draft"]["block_reason"]
        == "DOCUMENT_DRAFT_INSUFFICIENT_GROUNDED_CLAIMS"
    )
    assert (
        report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_diagnostics_counts"][
            "required_changes_count"
        ]
        >= 1
    )
    encoded = json.dumps(report.application_materials_result["target_results"], sort_keys=True)
    assert "Thank you for considering my application." not in encoded


def test_application_materials_flow_dry_run_blocks_generic_recruiter_message_placeholder() -> None:
    from hermes_cli import recruiter_application_materials_flow as flow_module

    original_compose = flow_module.compose_deterministic_outward_draft

    def _placeholder_compose(writer_input):
        packet = original_compose(writer_input)
        packet["draft"]["content"] = (
            "This role looks relevant and I'd be interested in discussing it. "
            "I can share more context if useful."
        )
        packet["claim_units"] = []
        return packet

    flow_module.compose_deterministic_outward_draft = _placeholder_compose
    executor = _ApplicationMaterialsExecutor()
    try:
        report = run_recruiter_application_materials_flow_dry_run(
            positioning_packet=_ready_positioning_packet(),
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
            allow_provider_execution=True,
            document_target="recruiter_message_draft",
            executor_factory=lambda: executor,
        )

        assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
        assert report.application_materials_result["causing_target"] == "recruiter_message_draft"
        assert report.application_materials_result["block_reason"] == "DOCUMENT_DRAFT_PLACEHOLDER_OUTPUT"
        assert report.application_materials_result["target_results"]["recruiter_message_draft"]["ready"] is False
        assert report.application_materials_result["target_results"]["recruiter_message_draft"]["blocked"] is True
        assert report.application_materials_result["target_results"]["recruiter_message_draft"]["reviewer_called"] is True
        assert report.application_materials_result["target_results"]["recruiter_message_draft"]["reviewer_verdict"] == "CHANGES_REQUESTED"
        assert (
            report.application_materials_result["target_results"]["recruiter_message_draft"]["block_reason"]
            == "DOCUMENT_DRAFT_PLACEHOLDER_OUTPUT"
        )
        assert (
            report.application_materials_result["target_results"]["recruiter_message_draft"]["reviewer_diagnostics_counts"][
                "required_changes_count"
            ]
            >= 1
        )
        encoded = json.dumps(report.application_materials_result["target_results"], sort_keys=True)
        assert "This role looks relevant and I'd be interested in discussing it." not in encoded
    finally:
        flow_module.compose_deterministic_outward_draft = original_compose


def test_application_materials_flow_dry_run_blocks_cover_letter_without_target_specificity() -> None:
    from hermes_cli import recruiter_application_materials_flow as flow_module

    original_compose = flow_module.compose_deterministic_outward_draft

    def _non_specific_compose(writer_input):
        packet = original_compose(writer_input)
        packet["draft"]["content"] = (
            "I am interested in this opportunity. "
            "Improved onboarding conversion and reduced friction with measurable gains. "
            "Drove pricing and packaging changes tied to adoption and retention improvements."
        )
        return packet

    flow_module.compose_deterministic_outward_draft = _non_specific_compose
    packet = _ready_positioning_packet()
    packet.pop("target_company", None)
    packet.pop("target_role", None)
    executor = _ApplicationMaterialsExecutor()
    try:
        report = run_recruiter_application_materials_flow_dry_run(
            positioning_packet=packet,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
            allow_provider_execution=True,
            document_target="cover_letter_draft",
            executor_factory=lambda: executor,
        )
    finally:
        flow_module.compose_deterministic_outward_draft = original_compose

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
    assert report.application_materials_result["causing_target"] == "cover_letter_draft"
    assert report.application_materials_result["block_reason"] == "DOCUMENT_DRAFT_INSUFFICIENT_ROLE_SPECIFICITY"
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["blocked"] is True
    assert report.application_materials_result["target_results"]["cover_letter_draft"]["ready"] is False
    assert (
        report.application_materials_result["target_results"]["cover_letter_draft"]["reviewer_diagnostics_counts"][
            "required_changes_count"
        ]
        >= 1
    )
    encoded = json.dumps(report.application_materials_result["target_results"], sort_keys=True)
    assert "Improved onboarding conversion and reduced friction with measurable gains." not in encoded


def test_application_materials_flow_dry_run_blocks_recruiter_message_without_target_specificity() -> None:
    from hermes_cli import recruiter_application_materials_flow as flow_module

    original_compose = flow_module.compose_deterministic_outward_draft

    def _non_specific_compose(writer_input):
        packet = original_compose(writer_input)
        packet["draft"]["content"] = (
            "Interested in this opportunity. "
            "Improved onboarding conversion and reduced friction with measurable gains."
        )
        return packet

    flow_module.compose_deterministic_outward_draft = _non_specific_compose
    packet = _ready_positioning_packet()
    packet.pop("target_company", None)
    packet.pop("target_role", None)
    executor = _ApplicationMaterialsExecutor()
    try:
        report = run_recruiter_application_materials_flow_dry_run(
            positioning_packet=packet,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
            allow_provider_execution=True,
            document_target="recruiter_message_draft",
            executor_factory=lambda: executor,
        )
    finally:
        flow_module.compose_deterministic_outward_draft = original_compose

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
    assert report.application_materials_result["causing_target"] == "recruiter_message_draft"
    assert report.application_materials_result["block_reason"] == "DOCUMENT_DRAFT_INSUFFICIENT_ROLE_SPECIFICITY"
    target_result = report.application_materials_result["target_results"]["recruiter_message_draft"]
    assert target_result["blocked"] is True
    assert target_result["ready"] is False
    assert target_result["reviewer_verdict"] == "CHANGES_REQUESTED"
    assert target_result["reviewer_diagnostics_counts"]["required_changes_count"] >= 1
    assert target_result["sanitized_reviewer_diagnostics_summary"]
    encoded = json.dumps(report.application_materials_result["target_results"], sort_keys=True)
    assert "Improved onboarding conversion and reduced friction with measurable gains." not in encoded



def test_application_materials_flow_dry_run_all_required_keeps_writer_called_truthful_when_only_cv_notes_use_writer() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_flow_dry_run(
        positioning_packet=_ready_positioning_packet(),
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        allow_provider_execution=True,
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
    assert report.application_materials_result["writer_called"] is True
    assert report.application_materials_result["reviewer_called"] is True
    assert report.application_materials_result["document_runs"]["cv_tailoring_notes"]["writer_called"] is True
    assert report.application_materials_result["document_runs"]["cover_letter_draft"]["writer_called"] is False
    assert report.application_materials_result["document_runs"]["recruiter_message_draft"]["writer_called"] is False


def test_application_materials_smoke_harness_keeps_cv_notes_on_writer_path() -> None:
    executor = _ApplicationMaterialsExecutor()
    report = run_recruiter_application_materials_smoke_harness(
        positioning_packet=_ready_positioning_packet(),
        allow_provider_execution=True,
        all_required_targets=True,
        executor_factory=lambda: executor,
    )

    assert report.status is RecruiterApplicationMaterialsSmokeStatus.READY
    assert executor.calls == [
        "document-writer",
        "document-reviewer",
        "document-reviewer",
        "document-reviewer",
    ]
    assert report.document_summary["documents"]["cv_tailoring_notes"]["document_type"] == "cv_tailoring_notes"
    assert report.document_summary["documents"]["cover_letter_draft"]["document_type"] == "cover_letter"
    assert report.document_summary["documents"]["recruiter_message_draft"]["document_type"] == "recruiter_message"
    assert report.target_results["cv_tailoring_notes"] == {
        "status": "ready",
        "blocked": False,
        "generated": True,
        "ready": True,
        "draft_only": True,
        "user_review_required": True,
        "writer_called": True,
        "reviewer_called": True,
        "document_writer_gate_status": "DRAFT_READY",
        "document_reviewer_gate_status": "APPROVE",
        "reviewer_verdict": "APPROVE",
        "reviewer_notes_present": True,
        "block_reason": None,
        "reviewer_diagnostics_counts": {
            "unsupported_claims_count": 0,
            "missing_source_references_count": 0,
            "required_changes_count": 0,
        },
        "sanitized_reviewer_diagnostics_summary": [],
    }
    assert report.target_results["cover_letter_draft"] == {
        "status": "ready",
        "blocked": False,
        "generated": True,
        "ready": True,
        "draft_only": True,
        "user_review_required": True,
        "writer_called": False,
        "reviewer_called": True,
        "document_writer_gate_status": "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER",
        "document_reviewer_gate_status": "APPROVE",
        "reviewer_verdict": "APPROVE",
        "reviewer_notes_present": True,
        "block_reason": None,
        "reviewer_diagnostics_counts": {
            "unsupported_claims_count": 0,
            "missing_source_references_count": 0,
            "required_changes_count": 0,
        },
        "sanitized_reviewer_diagnostics_summary": [],
    }
    assert report.target_results["recruiter_message_draft"] == {
        "status": "ready",
        "blocked": False,
        "generated": True,
        "ready": True,
        "draft_only": True,
        "user_review_required": True,
        "writer_called": False,
        "reviewer_called": True,
        "document_writer_gate_status": "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER",
        "document_reviewer_gate_status": "APPROVE",
        "reviewer_verdict": "APPROVE",
        "reviewer_notes_present": True,
        "block_reason": None,
        "reviewer_diagnostics_counts": {
            "unsupported_claims_count": 0,
            "missing_source_references_count": 0,
            "required_changes_count": 0,
        },
        "sanitized_reviewer_diagnostics_summary": [],
    }
