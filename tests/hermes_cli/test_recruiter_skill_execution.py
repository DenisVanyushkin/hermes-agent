from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.recruiter_context import RecruiterContextPacket, RecruiterContextStatus
from hermes_cli.recruiter_skill_execution import (
    REAL_PROVIDER_EXECUTOR_NOT_WIRED_ERROR,
    REQUIRED_POSITIONING_FIELDS,
    REQUIRED_VACANCY_EVALUATION_FIELDS,
    RecruiterSkillExecutionRequest,
    RecruiterSkillExecutionStatus,
    RecruiterSkillExecutor,
    SkillExecutionResult,
    run_recruiter_skill_execution,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _context_packet(
    *,
    status: RecruiterContextStatus = RecruiterContextStatus.READY,
    private_status: str = "PRIVATE_CONTEXT_AVAILABLE",
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
        },
        opportunity={"id": 501, "vacancy_id": 101, "stage": "new"},
        company_context=[
            {"company": "Acme", "summary": "Category leader", "provenance": {"source_table": "company_intelligence"}}
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
            "role_id": "hermes_recruiter",
            "skills_by_id": {
                "vacancy-evaluation": {
                    "id": "vacancy-evaluation",
                    "path": "role-packages/recruiter/skills/vacancy-evaluation/SKILL.md",
                },
                "positioning-and-evidence": {
                    "id": "positioning-and-evidence",
                    "path": "role-packages/recruiter/skills/positioning-and-evidence/SKILL.md",
                },
                "document-writer": {
                    "id": "document-writer",
                    "path": "role-packages/recruiter/skills/document-writer/SKILL.md",
                },
            },
            "bundles_by_id": {
                "evaluate-vacancy": {"id": "evaluate-vacancy", "skills": ["vacancy-evaluation", "positioning-and-evidence"]}
            },
        },
        private_context={
            "status": private_status,
            "dir": "/home/hermes/.hermes/private/career",
            "files": {
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


class _FakeExecutor(RecruiterSkillExecutor):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, object],
        skill_markdown_path: str,
        expected_schema: list[str],
    ) -> SkillExecutionResult:
        self.calls.append((skill_id, skill_input))
        if skill_id == "vacancy-evaluation":
            payload = {
                "vacancy_evaluation_summary": "Strong fit with clear product leadership match.",
                "fit_interpretation": "High-confidence match for executive product scope.",
                "evidence_gaps": ["Exact team size not confirmed."],
                "recommendation_for_next_step": "Proceed to positioning synthesis.",
            }
        else:
            payload = {
                "positioning_summary": "Lead with B2B product leadership and scaling evidence.",
                "evidence_map": {"leadership": ["Scaled B2B platform teams."]},
                "proven_facts": ["Built product orgs."],
                "derived_positioning": ["Position as operator-executive with marketplace depth."],
                "gaps": ["Need explicit domain depth proof for this company."],
                "risks_and_mitigations": ["Avoid overstating prior company-stage similarity."],
            }
        return SkillExecutionResult(
            status="SUCCESS",
            skill_id=skill_id,
            output=payload,
            warnings=[],
            errors=[],
            provenance={"skill_markdown_path": skill_markdown_path, "expected_schema": expected_schema},
            provider_called=False,
        )


def test_default_path_blocks_provider_execution_without_calling_executor() -> None:
    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT),
        context_builder=lambda request: _context_packet(),
    )

    assert report.status is RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED
    assert report.provider_called is False
    assert report.executor_called is False
    assert report.execution_status == "blocked_by_provider_fuse"
    assert report.vacancy_evaluation_result is None
    assert report.positioning_evidence_result is None
    assert report.downstream_gates["document_writer"]["status"] == "POSITIONING_REQUIRED"
    assert "call_provider_model" in report.forbidden_actions
    assert "execute_recruiter_skill" in report.forbidden_actions


def test_provider_enabled_without_executor_fails_closed() -> None:
    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: _context_packet(),
    )

    assert report.status is RecruiterSkillExecutionStatus.REAL_PROVIDER_EXECUTOR_NOT_WIRED
    assert report.provider_called is False
    assert report.executor_called is False
    assert REAL_PROVIDER_EXECUTOR_NOT_WIRED_ERROR in report.errors


def test_provider_enabled_executes_both_skills_with_fake_executor() -> None:
    executor = _FakeExecutor()

    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: _context_packet(),
        executor=executor,
    )

    assert report.status is RecruiterSkillExecutionStatus.EXECUTION_READY
    assert report.provider_called is False
    assert report.executor_called is True
    assert [call[0] for call in executor.calls] == ["vacancy-evaluation", "positioning-and-evidence"]
    assert report.vacancy_evaluation_result["status"] == "SUCCESS"
    assert report.positioning_evidence_result["status"] == "SUCCESS"
    assert "risks_and_mitigations" in report.positioning_evidence_result
    assert report.downstream_gates["document_writer"]["status"] == "POSITIONING_AVAILABLE"
    assert "call_provider_model" not in report.forbidden_actions
    assert "execute_recruiter_skill" not in report.forbidden_actions
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert "positioning_summary" in encoded


def test_missing_positioning_input_blocks_before_any_execution() -> None:
    executor = _FakeExecutor()
    context = _context_packet().to_dict()
    context["private_context"]["status"] = "PRIVATE_CONTEXT_MISSING"
    for meta in context["private_context"]["files"].values():
        meta["present"] = False

    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: context,
        executor=executor,
    )

    assert report.status is RecruiterSkillExecutionStatus.CONTEXT_OR_INPUT_BLOCKED
    assert report.execution_status == "blocked_by_skill_input"
    assert report.executor_called is False
    assert report.provider_called is False
    assert report.vacancy_evaluation_result is None
    assert report.positioning_evidence_result is None
    assert executor.calls == []
    assert any("positioning-and-evidence" in item for item in report.errors)
    assert report.downstream_gates["document_writer"]["status"] == "POSITIONING_REQUIRED"


def test_blocked_positioning_status_prevents_vacancy_execution() -> None:
    executor = _FakeExecutor()

    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: _context_packet(private_status="PRIVATE_CONTEXT_MISSING"),
        executor=executor,
    )

    assert report.status is RecruiterSkillExecutionStatus.CONTEXT_OR_INPUT_BLOCKED
    assert report.execution_status == "blocked_by_skill_input"
    assert report.executor_called is False
    assert report.provider_called is False
    assert report.vacancy_evaluation_result is None
    assert report.positioning_evidence_result is None
    assert executor.calls == []
    assert "positioning-and-evidence input not ready:BLOCKED_PRIVATE_CONTEXT_MISSING" in report.errors


def test_invalid_vacancy_output_blocks_downstream_gate() -> None:
    class _InvalidVacancyExecutor(RecruiterSkillExecutor):
        def execute(self, **_: object) -> SkillExecutionResult:
            return SkillExecutionResult(
                status="SUCCESS",
                skill_id="vacancy-evaluation",
                output={"fit_interpretation": "missing required fields"},
                warnings=[],
                errors=[],
                provenance={},
                provider_called=False,
            )

    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: _context_packet(),
        executor=_InvalidVacancyExecutor(),
    )

    assert report.status is RecruiterSkillExecutionStatus.SKILL_OUTPUT_INVALID
    assert report.executor_called is True
    assert report.positioning_evidence_result is None
    assert report.vacancy_evaluation_result is not None
    assert "missing_required_skill_output_fields:vacancy_evaluation_summary,evidence_gaps,recommendation_for_next_step" in report.errors
    assert report.downstream_gates["document_writer"]["status"] == "POSITIONING_REQUIRED"


def test_invalid_positioning_output_is_preserved_in_report() -> None:
    class _InvalidPositioningExecutor(RecruiterSkillExecutor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def execute(
            self,
            *,
            skill_id: str,
            skill_input: dict[str, object],
            skill_markdown_path: str,
            expected_schema: list[str],
        ) -> SkillExecutionResult:
            self.calls.append(skill_id)
            if skill_id == "vacancy-evaluation":
                return SkillExecutionResult(
                    status="SUCCESS",
                    skill_id=skill_id,
                    output={
                        "vacancy_evaluation_summary": "Strong fit with clear product leadership match.",
                        "fit_interpretation": "High-confidence match for executive product scope.",
                        "evidence_gaps": ["Exact team size not confirmed."],
                        "recommendation_for_next_step": "Proceed to positioning synthesis.",
                    },
                    warnings=[],
                    errors=[],
                    provenance={},
                    provider_called=False,
                )
            return SkillExecutionResult(
                status="SUCCESS",
                skill_id=skill_id,
                output={"positioning_summary": "Missing the rest of the schema."},
                warnings=[],
                errors=[],
                provenance={},
                provider_called=False,
            )

    executor = _InvalidPositioningExecutor()
    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: _context_packet(),
        executor=executor,
    )

    assert report.status is RecruiterSkillExecutionStatus.SKILL_OUTPUT_INVALID
    assert executor.calls == ["vacancy-evaluation", "positioning-and-evidence"]
    assert report.vacancy_evaluation_result is not None
    assert report.positioning_evidence_result is not None
    assert report.positioning_evidence_result["skill_id"] == "positioning-and-evidence"
    assert report.positioning_evidence_result["positioning_summary"] == "Missing the rest of the schema."
    assert (
        "missing_required_skill_output_fields:"
        "evidence_map,proven_facts,derived_positioning,gaps,risks_and_mitigations"
    ) in report.errors
    assert report.downstream_gates["document_writer"]["status"] == "POSITIONING_REQUIRED"


def test_missing_risks_and_mitigations_blocks_document_writer_readiness() -> None:
    class _MissingRisksExecutor(RecruiterSkillExecutor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def execute(
            self,
            *,
            skill_id: str,
            skill_input: dict[str, object],
            skill_markdown_path: str,
            expected_schema: list[str],
        ) -> SkillExecutionResult:
            self.calls.append(skill_id)
            if skill_id == "vacancy-evaluation":
                return SkillExecutionResult(
                    status="SUCCESS",
                    skill_id=skill_id,
                    output={
                        "vacancy_evaluation_summary": "Strong fit with clear product leadership match.",
                        "fit_interpretation": "High-confidence match for executive product scope.",
                        "evidence_gaps": ["Exact team size not confirmed."],
                        "recommendation_for_next_step": "Proceed to positioning synthesis.",
                    },
                    warnings=[],
                    errors=[],
                    provenance={},
                    provider_called=False,
                )
            return SkillExecutionResult(
                status="SUCCESS",
                skill_id=skill_id,
                output={
                    "positioning_summary": "Lead with B2B product leadership and scaling evidence.",
                    "evidence_map": {"leadership": ["Scaled B2B platform teams."]},
                    "proven_facts": ["Built product orgs."],
                    "derived_positioning": ["Position as operator-executive with marketplace depth."],
                    "gaps": ["Need explicit domain depth proof for this company."],
                },
                warnings=[],
                errors=[],
                provenance={},
                provider_called=False,
            )

    executor = _MissingRisksExecutor()
    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(vacancy_id=101, repo_root=REPO_ROOT, allow_provider_execution=True),
        context_builder=lambda request: _context_packet(),
        executor=executor,
    )

    assert report.status is RecruiterSkillExecutionStatus.SKILL_OUTPUT_INVALID
    assert report.execution_status == "invalid_skill_output"
    assert executor.calls == ["vacancy-evaluation", "positioning-and-evidence"]
    assert report.vacancy_evaluation_result is not None
    assert report.positioning_evidence_result is not None
    assert "risks_and_mitigations" not in report.positioning_evidence_result
    assert "missing_required_skill_output_fields:risks_and_mitigations" in report.errors
    assert report.downstream_gates["document_writer"]["status"] != "POSITIONING_AVAILABLE"
    assert report.downstream_gates["document_writer"]["status"] == "POSITIONING_REQUIRED"


def test_required_output_field_lists_are_stable() -> None:
    assert REQUIRED_VACANCY_EVALUATION_FIELDS == [
        "vacancy_evaluation_summary",
        "fit_interpretation",
        "evidence_gaps",
        "recommendation_for_next_step",
    ]
    assert REQUIRED_POSITIONING_FIELDS == [
        "positioning_summary",
        "evidence_map",
        "proven_facts",
        "derived_positioning",
        "gaps",
        "risks_and_mitigations",
    ]
