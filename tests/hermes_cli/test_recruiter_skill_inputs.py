from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.recruiter_context import RecruiterContextPacket, RecruiterContextStatus
from hermes_cli.recruiter_skill_inputs import (
    RecruiterSkillInputStatus,
    build_recruiter_skill_input_packets,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


_DEFAULT_VACANCY = object()


def _context_packet(
    *,
    status: RecruiterContextStatus = RecruiterContextStatus.READY,
    private_status: str = "PRIVATE_CONTEXT_AVAILABLE",
    private_files: dict[str, dict[str, bool]] | None = None,
    role_id: str = "hermes_recruiter",
    vacancy: dict[str, object] | None | object = _DEFAULT_VACANCY,
) -> RecruiterContextPacket:
    return RecruiterContextPacket(
        status=status,
        request={"vacancy_id": 101},
        vacancy={

            "vacancy_id": 101,
            "vacancy_key": "vac-101",
            "source_url": "https://example.com/jobs/101",
            "title": "Head of Product",
            "company": "Acme",
            "location": "Remote",
            "source_kind": "linkedin",
            "evaluation": {"score": 92, "tier": "strong_fit", "recommendation": "apply"},
            "provenance": {"source_table": "vacancies", "source_url": "https://example.com/jobs/101"},
        }
        if vacancy is _DEFAULT_VACANCY
        else vacancy,
        opportunity={"id": 501, "vacancy_id": 101, "stage": "new"},
        company_context=[
            {
                "company": "Acme",
                "summary": "Category leader",
                "provenance": {"source_table": "company_intelligence"},
            }
        ],
        application_history={"status": "found", "history": [], "artifacts": [], "feedback": []},
        machine_score={
            "status": "available",
            "score": 92,
            "tier": "strong_fit",
            "recommendation": "apply",
            "matched_signals": ["b2b_saas", "leadership"],
            "concerns": [],
            "reasons": ["strong product leadership match"],
        },
        role_package_context={
            "package_id": "hermes-recruiter",
            "package_path": "role-packages/recruiter",
            "role_id": role_id,
            "skills_by_id": {
                "vacancy-evaluation": {"id": "vacancy-evaluation", "path": "role-packages/recruiter/skills/vacancy-evaluation/SKILL.md"},
                "positioning-and-evidence": {"id": "positioning-and-evidence", "path": "role-packages/recruiter/skills/positioning-and-evidence/SKILL.md"},
                "document-writer": {"id": "document-writer", "path": "role-packages/recruiter/skills/document-writer/SKILL.md"},
            },
            "bundles_by_id": {
                "evaluate-vacancy": {"id": "evaluate-vacancy", "skills": ["vacancy-evaluation", "positioning-and-evidence"]},
                "application-materials": {
                    "id": "application-materials",
                    "skills": ["vacancy-evaluation", "positioning-and-evidence", "document-writer", "document-reviewer"],
                },
            },
        },
        private_context={
            "status": private_status,
            "dir": "/home/hermes/.hermes/private/career",
            "files": private_files
            if private_files is not None
            else {
                "denis_vanyushkin_structured_resume_v1_1.json": {"present": True},
                "opportunity-thesis.md": {"present": True},
                "company_intelligence_architecture.md": {"present": True},
                "scoring_v3.md": {"present": True},
            },
        },
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "private_dir_checked": "/home/hermes/.hermes/private/career"},
    )


def test_builds_ready_inputs_from_ready_context() -> None:
    packet = build_recruiter_skill_input_packets(_context_packet())

    assert packet.status is RecruiterSkillInputStatus.READY
    assert packet.vacancy_evaluation_input["status"] == RecruiterSkillInputStatus.READY.value
    assert packet.positioning_evidence_input["status"] == RecruiterSkillInputStatus.READY.value
    assert packet.downstream_gates["document_writer"]["status"] == "READY"
    assert packet.provenance["writes_performed"] is False
    encoded = json.dumps(packet.to_dict(), sort_keys=True)
    assert "vacancy-evaluation" in encoded
    assert "positioning-and-evidence" in encoded


def test_private_context_missing_keeps_vacancy_ready_and_blocks_positioning() -> None:
    packet = build_recruiter_skill_input_packets(
        _context_packet(
            private_status="PRIVATE_CONTEXT_MISSING",
            private_files={
                "denis_vanyushkin_structured_resume_v1_1.json": {"present": False},
                "opportunity-thesis.md": {"present": False},
                "company_intelligence_architecture.md": {"present": False},
                "scoring_v3.md": {"present": False},
            },
        )
    )

    assert packet.status is RecruiterSkillInputStatus.PARTIAL
    assert packet.vacancy_evaluation_input["status"] == RecruiterSkillInputStatus.READY.value
    assert packet.positioning_evidence_input["status"] == RecruiterSkillInputStatus.BLOCKED_PRIVATE_CONTEXT_MISSING.value
    assert packet.downstream_gates["document_writer"]["status"] == "POSITIONING_REQUIRED"
    assert "private_context_missing" in packet.warnings


def test_partial_private_context_keeps_positioning_ready_with_metadata_only_warning() -> None:
    packet = build_recruiter_skill_input_packets(
        _context_packet(
            private_status="PARTIAL",
            private_files={
                "denis_vanyushkin_structured_resume_v1_1.json": {"present": True},
                "opportunity-thesis.md": {"present": False},
                "company_intelligence_architecture.md": {"present": True},
                "scoring_v3.md": {"present": True},
            },
        )
    )

    assert packet.status is RecruiterSkillInputStatus.PARTIAL
    assert packet.positioning_evidence_input["status"] == RecruiterSkillInputStatus.READY.value
    assert packet.positioning_evidence_input["private_context"]["status"] == "PARTIAL"
    assert packet.positioning_evidence_input["private_context"]["missing_files"] == ["opportunity-thesis.md"]
    assert "private_context_partial" in packet.warnings


@pytest.mark.parametrize(
    ("context_status", "expected_status"),
    [
        (RecruiterContextStatus.SOURCE_REQUIRED, RecruiterSkillInputStatus.BLOCKED_CONTEXT_NOT_READY),
        (RecruiterContextStatus.VACANCY_NOT_FOUND, RecruiterSkillInputStatus.BLOCKED_CONTEXT_NOT_READY),
    ],
)
def test_non_ready_context_is_blocked(
    context_status: RecruiterContextStatus,
    expected_status: RecruiterSkillInputStatus,
) -> None:
    packet = build_recruiter_skill_input_packets(_context_packet(status=context_status))

    assert packet.status is expected_status
    assert packet.vacancy_evaluation_input is None
    assert packet.positioning_evidence_input is None


def test_missing_vacancy_blocks_packet() -> None:
    packet = build_recruiter_skill_input_packets(_context_packet(vacancy=None))

    assert packet.status is RecruiterSkillInputStatus.BLOCKED_MISSING_VACANCY
    assert packet.vacancy_evaluation_input is None
    assert packet.positioning_evidence_input is None


def test_wrong_role_package_context_blocks_packet() -> None:
    packet = build_recruiter_skill_input_packets(_context_packet(role_id="engineer"))

    assert packet.status is RecruiterSkillInputStatus.BLOCKED_ROLE_PACKAGE_CONTEXT
    assert any("role_package_context" in item for item in packet.errors)


def test_accepts_plain_dict_input() -> None:
    context_dict = _context_packet().to_dict()

    packet = build_recruiter_skill_input_packets(context_dict)

    assert packet.status is RecruiterSkillInputStatus.READY
    assert packet.request["vacancy_id"] == 101


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_skill_inputs.py").read_text(encoding="utf-8")
    forbidden = [
        "import sqlite3",
        "from sqlite3",
        "RecruiterReadFacade",
        "JobIntelStore",
        "import gateway",
        "from gateway",
        "import orchestrator",
        "from orchestrator",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "provider/model",
        "crm_service",
        "crm_reconciler",
    ]
    for needle in forbidden:
        assert needle not in source


def test_forbidden_actions_and_boundary_flags_are_explicit() -> None:
    packet = build_recruiter_skill_input_packets(_context_packet())

    assert "call_provider_model" in packet.forbidden_actions
    assert "execute_recruiter_skill" in packet.forbidden_actions
    assert "read_private_file_contents" in packet.forbidden_actions
    assert "write_job_intel_db" in packet.forbidden_actions
    assert "write_crm" in packet.forbidden_actions
    assert "restart_gateway" in packet.forbidden_actions
    assert packet.vacancy_evaluation_input["boundaries"]["no_outbound"] is True
    assert packet.vacancy_evaluation_input["boundaries"]["no_private_file_content_read"] is True
    assert packet.positioning_evidence_input["boundaries"]["no_invented_facts"] is True
    assert packet.positioning_evidence_input["boundaries"]["gaps_must_be_explicit"] is True
