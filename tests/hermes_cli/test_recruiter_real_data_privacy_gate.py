from __future__ import annotations

import json
from dataclasses import replace

from hermes_cli.recruiter_real_data_privacy_gate import (
    CareerSourceApproval,
    RealDataPrivacyGateRequest,
    RealDataPrivacyGateStatus,
    evaluate_real_data_application_materials_privacy_gate,
)


def _base_request() -> RealDataPrivacyGateRequest:
    return RealDataPrivacyGateRequest(
        vacancy_source_type="vacancy_payload",
        vacancy_source_approved=True,
        career_sources=[
            CareerSourceApproval(
                source_id="career-facts-1",
                source_kind="career_fact",
                source_type="career_fact_packet",
                approved=True,
            )
        ],
        permitted_source_types=["vacancy_payload", "career_fact_packet"],
        output_mode="draft_only",
        outbound_enabled=False,
        crm_writes_enabled=False,
        job_intel_writes_enabled=False,
        browser_automation_enabled=False,
        private_file_access_requested=False,
        private_file_access_approved=False,
    )


def test_gate_blocks_without_approved_vacancy_source() -> None:
    report = evaluate_real_data_application_materials_privacy_gate(replace(_base_request(), vacancy_source_approved=False))

    assert report.status is RealDataPrivacyGateStatus.BLOCKED
    assert report.blocked_reason == "vacancy_source_not_approved"
    assert "vacancy_input_source_approval" in report.required_approvals


def test_gate_blocks_without_approved_career_sources() -> None:
    report = evaluate_real_data_application_materials_privacy_gate(replace(_base_request(), career_sources=[]))

    assert report.status is RealDataPrivacyGateStatus.BLOCKED
    assert report.blocked_reason == "career_sources_not_approved"
    assert "career_fact_source_approval" in report.required_approvals


def test_gate_rejects_generated_drafts_as_career_facts() -> None:
    request = _base_request()
    request.career_sources = [
        CareerSourceApproval(
            source_id="generated-draft-1",
            source_kind="generated_draft",
            source_type="career_fact_packet",
            approved=True,
        )
    ]

    report = evaluate_real_data_application_materials_privacy_gate(request)

    assert report.status is RealDataPrivacyGateStatus.BLOCKED
    assert report.blocked_reason == "generated_materials_not_allowed_as_career_facts"


def test_gate_blocks_private_file_sources_without_explicit_approval() -> None:
    request = _base_request()
    request.career_sources.append(
        CareerSourceApproval(
            source_id="private-file-1",
            source_kind="private_file",
            source_type="private_file_metadata",
            approved=True,
        )
    )

    report = evaluate_real_data_application_materials_privacy_gate(request)

    assert report.status is RealDataPrivacyGateStatus.BLOCKED
    assert report.blocked_reason == "private_file_access_not_approved"
    assert "private_file_access_approval" in report.required_approvals


def test_gate_blocks_outbound_and_write_and_browser_capabilities() -> None:
    report = evaluate_real_data_application_materials_privacy_gate(
        replace(
            _base_request(),
            outbound_enabled=True,
            crm_writes_enabled=True,
            job_intel_writes_enabled=True,
            browser_automation_enabled=True,
        )
    )

    assert report.status is RealDataPrivacyGateStatus.BLOCKED
    assert report.blocked_reason == "outbound_actions_must_be_disabled"
    assert report.capability_flags["crm_writes_disabled"] is False
    assert report.capability_flags["job_intel_writes_disabled"] is False
    assert report.capability_flags["browser_automation_disabled"] is False


def test_gate_allows_metadata_only_draft_safe_request() -> None:
    report = evaluate_real_data_application_materials_privacy_gate(_base_request())

    assert report.status is RealDataPrivacyGateStatus.READY
    assert report.ready is True
    assert report.blocked_reason is None
    assert report.approved_source_count == 2
    assert report.approved_source_types == ["career_fact_packet", "vacancy_payload"]
    assert report.capability_flags == {
        "draft_only": True,
        "outbound_disabled": True,
        "crm_writes_disabled": True,
        "job_intel_writes_disabled": True,
        "browser_automation_disabled": True,
        "private_file_access_disabled": True,
    }


def test_blocked_report_is_report_safe() -> None:
    request = _base_request()
    request.career_sources = [
        CareerSourceApproval(
            source_id="/home/hermes/.hermes/private/career/resume.md",
            source_kind="private_file",
            source_type="private_file_metadata",
            approved=True,
        )
    ]
    report = evaluate_real_data_application_materials_privacy_gate(request)

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert report.status is RealDataPrivacyGateStatus.BLOCKED
    assert "/home/hermes" not in encoded
    assert "resume.md" not in encoded


def test_allowed_report_summarizes_metadata_only() -> None:
    encoded = json.dumps(evaluate_real_data_application_materials_privacy_gate(_base_request()).to_dict(), sort_keys=True)

    assert "career-facts-1" not in encoded
    assert "generated_draft" not in encoded
    assert "/home/hermes" not in encoded
