from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermes_cli.recruiter_document_execution import (
    RecruiterDocumentExecutionStatus,
    RecruiterDocumentExecutor,
    run_recruiter_document_execution,
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


class _FakeDocumentExecutor(RecruiterDocumentExecutor):
    def __init__(self, *, reviewer_verdict: str = "APPROVE") -> None:
        self.calls: list[str] = []
        self.reviewer_verdict = reviewer_verdict

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        expected_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(skill_id)
        if skill_id == "document-writer":
            return {
                "schema_version": "recruiter_document_packet_v1",
                "document_type": skill_input["document_type"],
                "audience": skill_input.get("audience"),
                "purpose": skill_input.get("purpose"),
                "source_positioning_packet_ref": skill_input["source_positioning_packet_ref"],
                "draft": {
                    "format": "text",
                    "content": "Draft content for user review only.",
                    "notes": ["Do not send without user approval."],
                },
                "review": {"status": "PENDING"},
                "status": "DRAFT_READY",
                "warnings": [],
                "errors": [],
                "provenance": {"expected_schema": expected_schema},
            }
        return {
            "status": "SUCCESS",
            "skill_id": "document-reviewer",
            "verdict": self.reviewer_verdict,
            "hallucination_risk": "low",
            "unsupported_claims": [],
            "genericness_assessment": "specific enough",
            "tone_seniority_assessment": "appropriate for executive audience",
            "missing_source_references": [],
            "required_changes": [] if self.reviewer_verdict == "APPROVE" else ["Tighten opening paragraph."],
            "warnings": [],
            "errors": [],
            "provenance": {"expected_schema": expected_schema},
        }


def test_default_mode_blocks_document_execution_without_executor_calls() -> None:
    executor = _FakeDocumentExecutor()

    report = run_recruiter_document_execution(
        _execution_report(),
        document_type="cover_letter",
        audience="HR recruiter",
        purpose="First-pass tailored draft",
        executor=executor,
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_EXECUTION_BLOCKED
    assert report.writer_called is False
    assert report.reviewer_called is False
    assert report.provider_called is False
    assert report.document_packet is None
    assert report.review_result is None
    assert report.downstream_gates["document_writer"]["status"] == "READY_FOR_INPUT"
    assert executor.calls == []


def test_allow_execution_without_executor_fails_closed() -> None:
    report = run_recruiter_document_execution(
        _execution_report(),
        document_type="cover_letter",
        allow_document_execution=True,
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_EXECUTOR_NOT_WIRED
    assert report.writer_called is False
    assert report.reviewer_called is False
    assert report.provider_called is False
    assert report.document_packet is None


def test_happy_fake_execution_runs_writer_then_reviewer_and_stays_blocked_on_outbound() -> None:
    executor = _FakeDocumentExecutor()

    report = run_recruiter_document_execution(
        _execution_report(),
        document_type="cover_letter",
        audience="HR recruiter",
        purpose="First-pass tailored draft",
        allow_document_execution=True,
        executor=executor,
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_APPROVED
    assert report.writer_called is True
    assert report.reviewer_called is True
    assert executor.calls == ["document-writer", "document-reviewer"]
    assert report.document_packet is not None
    assert report.review_result is not None
    assert report.downstream_gates["document_review"]["status"] == "APPROVED"
    assert report.downstream_gates["outbound_delivery"]["status"] == "BLOCKED_USER_REVIEW_REQUIRED"
    assert report.downstream_gates["crm_writeback"]["status"] == "BLOCKED_OUT_OF_SCOPE"
    assert "call_provider_model" in report.forbidden_actions
    assert "read_private_file_contents" in report.forbidden_actions
    assert "create_gmail_draft" in report.forbidden_actions
    json.dumps(report.to_dict(), sort_keys=True)


def test_invalid_writer_output_blocks_before_reviewer() -> None:
    class _InvalidWriterExecutor(_FakeDocumentExecutor):
        def execute(self, **kwargs: Any) -> dict[str, Any]:
            skill_id = kwargs["skill_id"]
            self.calls.append(skill_id)
            if skill_id == "document-writer":
                return {"status": "DRAFT_READY", "document_type": "cover_letter"}
            return super().execute(**kwargs)

    executor = _InvalidWriterExecutor()
    report = run_recruiter_document_execution(
        _execution_report(),
        document_type="cover_letter",
        allow_document_execution=True,
        executor=executor,
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_OUTPUT_INVALID
    assert report.reviewer_called is False
    assert executor.calls == ["document-writer"]
    assert report.document_packet is not None


def test_invalid_reviewer_output_preserves_draft_packet() -> None:
    class _InvalidReviewerExecutor(_FakeDocumentExecutor):
        def execute(self, **kwargs: Any) -> dict[str, Any]:
            payload = super().execute(**kwargs)
            if kwargs["skill_id"] == "document-reviewer":
                payload.pop("verdict")
            return payload

    report = run_recruiter_document_execution(
        _execution_report(),
        document_type="cover_letter",
        allow_document_execution=True,
        executor=_InvalidReviewerExecutor(),
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_INVALID
    assert report.document_packet is not None
    assert report.review_result is not None


def test_reviewer_changes_requested_keeps_draft_not_approved() -> None:
    report = run_recruiter_document_execution(
        _execution_report(),
        document_type="cover_letter",
        allow_document_execution=True,
        executor=_FakeDocumentExecutor(reviewer_verdict="CHANGES_REQUESTED"),
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_CHANGES_REQUESTED
    assert report.document_packet is not None
    assert report.downstream_gates["document_review"]["status"] == "REVIEW_CHANGES_REQUESTED"


def test_input_not_ready_blocks_before_executor() -> None:
    executor = _FakeDocumentExecutor()
    report = run_recruiter_document_execution(
        _execution_report(positioning_result=None, document_writer_gate="POSITIONING_REQUIRED"),
        document_type="cover_letter",
        allow_document_execution=True,
        executor=executor,
    )

    assert report.status is RecruiterDocumentExecutionStatus.DOCUMENT_INPUT_NOT_READY
    assert report.writer_called is False
    assert executor.calls == []


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_document_execution.py").read_text(encoding="utf-8")
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
    ]
    for needle in forbidden:
        assert needle not in source
