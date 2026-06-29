from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.recruiter_document_inputs import (
    ALLOWED_DOCUMENT_TYPES,
    REQUIRED_POSITIONING_FIELDS,
    RecruiterDocumentInputStatus,
    build_recruiter_document_writer_input_packet,
)
from hermes_cli.recruiter_skill_execution import RecruiterSkillExecutionReport, RecruiterSkillExecutionStatus


REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT = object()


def _execution_report(
    *,
    status: RecruiterSkillExecutionStatus = RecruiterSkillExecutionStatus.EXECUTION_READY,
    vacancy_result: dict[str, object] | None | object = _DEFAULT,
    positioning_result: dict[str, object] | None | object = _DEFAULT,
    document_writer_gate: str = "POSITIONING_AVAILABLE",
) -> RecruiterSkillExecutionReport:
    return RecruiterSkillExecutionReport(
        status=status,
        flow_id="evaluate-and-position",
        context_status="READY",
        skill_input_status="READY",
        execution_status="completed",
        provider_called=False,
        executor_called=True,
        vacancy_evaluation_result=vacancy_result
        if vacancy_result is not _DEFAULT
        else {
            "status": "SUCCESS",
            "skill_id": "vacancy-evaluation",
            "vacancy_evaluation_summary": "Strong fit for executive product role.",
            "fit_interpretation": "Match is strong on product leadership and scale.",
            "evidence_gaps": ["Exact team size not confirmed."],
            "recommendation_for_next_step": "Proceed to draft preparation.",
            "provenance": {"source": "fake-test"},
        },
        positioning_evidence_result=positioning_result
        if positioning_result is not _DEFAULT
        else {
            "status": "SUCCESS",
            "skill_id": "positioning-and-evidence",
            "positioning_summary": "Lead with platform scaling and executive product leadership.",
            "evidence_map": {"leadership": ["Scaled multi-team product org."]},
            "proven_facts": ["Led product orgs.", "Built B2B platforms."],
            "derived_positioning": ["Position as operator with platform depth."],
            "gaps": ["Need stronger direct domain match proof."],
            "risks_and_mitigations": ["Avoid overstating company-stage similarity."],
            "provenance": {"source": "fake-test"},
        },
        downstream_gates={
            "document_writer": {
                "skill_id": "document-writer",
                "status": document_writer_gate,
                "reason": (
                    "positioning packet available for downstream draft-only writer"
                    if document_writer_gate == "POSITIONING_AVAILABLE"
                    else "document-writer requires positioning-and-evidence output packet"
                ),
                "requires": ["positioning-and-evidence"],
                "references": ["role-packages/recruiter/skills/document-writer/SKILL.md"],
            }
        },
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "session_id": "fake-session"},
        forbidden_actions=[
            "send_outbound_message",
            "apply_to_job",
            "write_crm",
            "write_job_intel_db",
            "read_private_file_contents",
            "mutate_live_config",
            "restart_gateway",
        ],
        planned_flow=["vacancy-evaluation", "positioning-and-evidence"],
    )


def test_ready_happy_path_builds_json_serializable_writer_input() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cover_letter",
        audience="HR recruiter",
        purpose="First-pass tailored draft",
    )

    assert packet.status is RecruiterDocumentInputStatus.READY
    assert packet.document_writer_input is not None
    assert packet.document_writer_input["status"] == "READY"
    assert packet.document_writer_input["skill_id"] == "document-writer"
    assert packet.document_writer_input["document_type"] == "cover_letter"
    assert packet.document_writer_input["audience"] == "HR recruiter"
    assert packet.document_writer_input["purpose"] == "First-pass tailored draft"
    assert "draft" not in packet.document_writer_input
    assert "generate_final_draft" in packet.forbidden_actions
    assert "execute_document_writer" in packet.forbidden_actions
    assert packet.downstream_gates["document_writer"]["status"] == "READY_FOR_INPUT"
    json.dumps(packet.to_dict(), sort_keys=True)


def test_positioning_required_when_positioning_result_missing() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=None, document_writer_gate="POSITIONING_REQUIRED"),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.POSITIONING_REQUIRED
    assert packet.document_writer_input is None
    assert "document-writer requires positioning-and-evidence output packet" in packet.errors


def test_positioning_required_when_document_writer_gate_not_available() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(document_writer_gate="POSITIONING_REQUIRED"),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.POSITIONING_REQUIRED
    assert packet.document_writer_input is None
    assert any("positioning-and-evidence output packet" in item for item in packet.errors)


def test_invalid_positioning_result_missing_required_field_is_blocked() -> None:
    positioning_result = {
        "status": "SUCCESS",
        "skill_id": "positioning-and-evidence",
        "positioning_summary": "Lead with platform scaling and executive product leadership.",
        "evidence_map": {"leadership": ["Scaled multi-team product org."]},
        "proven_facts": ["Led product orgs.", "Built B2B platforms."],
        "derived_positioning": ["Position as operator with platform depth."],
        "gaps": ["Need stronger direct domain match proof."],
    }

    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=positioning_result),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_POSITIONING_RESULT_INVALID
    assert packet.document_writer_input is None
    assert "missing_positioning_fields:risks_and_mitigations" in packet.errors


def test_unsupported_document_type_is_controlled_block() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="press_release",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_UNSUPPORTED_DOCUMENT_TYPE
    assert packet.document_writer_input is None


def test_ready_packet_mentions_future_draft_schema_without_generating_draft() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="linkedin_dm",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    assert writer_input["expected_future_output_schema"] == "recruiter_document_packet_v1"
    assert "draft" in writer_input["expected_future_output_fields"]
    assert "draft" not in writer_input


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_document_inputs.py").read_text(encoding="utf-8")
    forbidden = [
        "import sqlite3",
        "from sqlite3",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "import slack",
        "from slack",
        "import telegram",
        "from telegram",
        "import gmail",
        "from gmail",
        "import linkedin",
        "from linkedin",
        "import browser",
        "from browser",
        ".gateway",
        ".router",
        "read_text(",
        "read_bytes(",
        "open(",
    ]
    for needle in forbidden:
        assert needle not in source


def test_supported_document_type_constants_match_sot() -> None:
    assert ALLOWED_DOCUMENT_TYPES == [
        "cover_letter",
        "recruiter_message",
        "linkedin_dm",
        "follow_up",
        "cv_tailoring_notes",
        "application_answer",
        "executive_bio",
    ]
    assert REQUIRED_POSITIONING_FIELDS == [
        "positioning_summary",
        "evidence_map",
        "proven_facts",
        "derived_positioning",
        "gaps",
        "risks_and_mitigations",
    ]
