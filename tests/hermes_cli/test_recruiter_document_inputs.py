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
    assert packet.document_writer_input["document_constraints"]["genre"] == "submission_ready_cover_letter"
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


def test_missing_vacancy_context_is_blocked() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(vacancy_result=None),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_MISSING_VACANCY_CONTEXT
    assert "vacancy_evaluation_result_missing" in packet.errors


def test_non_ready_execution_report_is_blocked() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(status=RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_INVALID_EXECUTION_REPORT
    assert "execution_report_not_ready:PROVIDER_EXECUTION_BLOCKED" in packet.errors


def test_dict_input_path_is_supported() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report().to_dict(),
        document_type="cover_letter",
        audience="HR recruiter",
        purpose="Tailored draft",
    )

    assert packet.status is RecruiterDocumentInputStatus.READY
    assert packet.document_writer_input is not None


def test_unsupported_document_type_takes_precedence_over_invalid_execution_report() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(status=RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED),
        document_type="press_release",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_UNSUPPORTED_DOCUMENT_TYPE
    assert "unsupported_document_type:press_release" in packet.errors
    assert not any(item.startswith("execution_report_not_ready:") for item in packet.errors)


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


def test_missing_audience_and_purpose_warn_but_do_not_block() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="linkedin_dm",
    )

    assert packet.status is RecruiterDocumentInputStatus.READY
    assert "audience_missing" in packet.warnings
    assert "purpose_missing" in packet.warnings


def test_cover_letter_constraints_require_submission_ready_grounded_letter() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = packet.document_writer_input["document_constraints"]
    assert writer_input["requested_document_type"] == "cover_letter"
    assert constraints["document_type"] == "cover_letter"
    assert constraints["genre"] == "submission_ready_cover_letter"
    assert "a submission-ready letter structure" in constraints["must_include"]
    assert "disclaimer paragraph" in constraints["must_avoid"]
    assert "synthetic packet" in constraints["must_avoid"]
    assert "evidence packet" in constraints["must_avoid"]
    assert "source-backed claims" in constraints["must_avoid"]
    assert "conditional claims" in constraints["must_avoid"]
    assert "reviewer" in constraints["must_avoid"]
    assert "Hermes" in constraints["must_avoid"]
    assert "Use only facts supported by the provided evidence." in constraints["grounding_rules"]
    assert "Do not invent employers, dates, metrics, team sizes, revenue numbers, product names, or outcomes." in constraints["grounding_rules"]


def test_recruiter_message_constraints_require_short_non_meta_message() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = packet.document_writer_input["document_constraints"]
    assert writer_input["requested_document_type"] == "recruiter_message"
    assert constraints["document_type"] == "recruiter_message"
    assert constraints["genre"] == "concise_recruiter_message"
    assert "a concise recruiter-facing outreach or application message" in constraints["must_include"]
    assert "2-4 short sentences maximum" in constraints["must_include"]
    assert "one role-interest sentence" in constraints["must_include"]
    assert "one evidence-backed fit sentence" in constraints["must_include"]
    assert "optional soft call-to-action" in constraints["must_include"]
    assert "synthetic packet" in constraints["must_avoid"]
    assert "evidence packet" in constraints["must_avoid"]
    assert "reviewer" in constraints["must_avoid"]
    assert "Hermes" in constraints["must_avoid"]
    assert "product executive" in constraints["must_avoid"]
    assert "payments leader" in constraints["must_avoid"]
    assert "commercially sensitive environments" in constraints["must_avoid"]
    assert "strategic bets" in constraints["must_avoid"]
    assert "monetization launches" in constraints["must_avoid"]
    assert "cross-functional launches" in constraints["must_avoid"]
    assert "unsupported employer names" in constraints["must_avoid"]
    assert "Use only 1-2 of the strongest supported claims from the provided evidence." in constraints["grounding_rules"]
    assert "Keep the message to 2-4 short sentences with no cover-letter structure." in constraints["grounding_rules"]
    assert "If a claim is only adjacent rather than exact, soften it with phrases like relevant adjacent experience, could be relevant, experience that may map well to, or background across." in constraints["grounding_rules"]
    assert "Do not use broad executive-branding language or unsupported title/seniority claims." in constraints["grounding_rules"]
    assert "Do not invent payments leadership, strategic bets, monetization launches, cross-functional launches, executive-level ownership, team size, metrics, revenue, product scale, employer names, dates, or outcomes." in constraints["grounding_rules"]
    assert "Keep the message short, concrete, human, and naturally conservative." in constraints["grounding_rules"]
    assert "is 2-4 short sentences, recruiter-facing, and concrete" in constraints["review_success_criteria"]
    assert "uses only 1-2 supported claims without stacking unsupported claims" in constraints["review_success_criteria"]
    assert "softens adjacent experience rather than overstating it" in constraints["review_success_criteria"]


def test_recruiter_message_writer_input_preserves_tight_constraints() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = writer_input["document_constraints"]
    assert writer_input["requested_document_type"] == "recruiter_message"
    assert writer_input["document_type"] == "recruiter_message"
    assert constraints["document_type"] == "recruiter_message"
    assert constraints["target_audience"] == "Recruiter"
    assert constraints["tone"] == "short, concrete, human, and professional"
    assert "no generic \"I am uniquely positioned\" style" in constraints["must_avoid"]
    assert "no broad executive branding paragraph" in constraints["must_avoid"]
    assert "\u201c" not in "".join(constraints["must_avoid"])
    assert "\u201d" not in "".join(constraints["must_avoid"])


def test_cv_tailoring_notes_constraints_preserve_analytical_language() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cv_tailoring_notes",
    )

    constraints = packet.document_writer_input["document_constraints"]
    assert constraints["document_type"] == "cv_tailoring_notes"
    assert constraints["genre"] == "analytical_cv_tailoring_notes"
    assert "supported claims to use" in constraints["must_include"]
    assert "unsupported claims to avoid" in constraints["must_include"]
    assert "It may explicitly list supported claims, unsupported claims to avoid, gaps, and evidence notes." in constraints["grounding_rules"]
    assert "Separate use claims from avoid claims when practical." in constraints["grounding_rules"]


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
