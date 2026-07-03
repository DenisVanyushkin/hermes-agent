from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.recruiter_evaluation_flow import (
    RecruiterEvaluationFlowRequest,
    RecruiterEvaluationFlowStatus,
    build_recruiter_evaluation_flow,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_vacancy_url_prompt_builds_ready_flow_report() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Посмотри вот эту вакансию: https://example.com/jobs/123",
            repo_root=REPO_ROOT,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.READY
    assert report.selected_role_id == "hermes_recruiter"
    assert report.selected_bundle == "company-vacancy-decision-support"
    assert report.flow_id == "evaluate-vacancy"
    assert report.role_context_schema_version == "recruiter_role_context_v1"
    assert report.vacancy_source_status == "AVAILABLE_URL"
    assert report.private_context_status == "PRIVATE_CONTEXT_AVAILABLE"
    assert report.provider_execution_enabled is False
    assert report.document_provider_execution_enabled is False
    assert report.outbound_enabled is False
    assert report.db_write_enabled is False
    assert "build_vacancy_evaluation_input" in report.next_allowed_actions
    assert "call_provider_model" in report.forbidden_actions


def test_pasted_vacancy_text_prompt_builds_ready_flow_report() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt=(
                "Оцени вакансию:\n"
                "Head of Product\n"
                "Company: Example\n"
                "Responsibilities: Build platform strategy\n"
                "Requirements: 10+ years in product leadership"
            ),
            repo_root=REPO_ROOT,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.READY
    assert report.vacancy_source_status == "AVAILABLE_TEXT"


def test_missing_vacancy_source_blocks_flow() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Оцени вакансию",
            repo_root=REPO_ROOT,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.BLOCKED_SOURCE_REQUIRED
    assert report.required_inputs == ["vacancy_url_or_text"]


def test_missing_private_context_blocks_flow_after_source_detection() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Посмотри вакансию https://example.com/jobs/123",
            repo_root=REPO_ROOT,
            private_context_status="PRIVATE_CONTEXT_MISSING",
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.BLOCKED_PRIVATE_CONTEXT_MISSING
    assert report.vacancy_source_status == "AVAILABLE_URL"
    assert report.private_context_status == "PRIVATE_CONTEXT_MISSING"


def test_application_materials_bundle_stays_blocked_without_document_generation() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Prepare CV and cover letter for this vacancy https://example.com/jobs/123",
            repo_root=REPO_ROOT,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.BLOCKED_UNSUPPORTED_BUNDLE
    assert report.selected_bundle == "application-materials"
    assert any("POSITIONING_REQUIRED" in warning for warning in report.warnings)
    assert "recruiter-document execute" not in report.next_allowed_actions


def test_engineering_prompt_does_not_produce_active_recruiter_flow() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Debug Hermes gateway for recruiter routing",
            repo_root=REPO_ROOT,
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.BLOCKED_NOT_RECRUITER
    assert report.selected_role_id is None


def test_mixed_engineering_and_recruiter_prompt_keeps_engineering_priority() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Debug Hermes gateway for recruiter routing and evaluate this vacancy https://example.com/jobs/123",
            repo_root=REPO_ROOT,
        )
    )

    assert report.status is RecruiterEvaluationFlowStatus.BLOCKED_NOT_RECRUITER
    assert report.selected_bundle is None


def test_report_is_json_serializable() -> None:
    report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt="Посмотри вакансию https://example.com/jobs/123",
            repo_root=REPO_ROOT,
            private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        )
    )

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "recruiter_evaluation_flow_v1" in encoded
    assert "AVAILABLE_URL" in encoded
