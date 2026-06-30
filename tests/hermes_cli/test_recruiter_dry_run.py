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
    run_recruiter_context_dry_run,
    run_recruiter_evaluation_flow_dry_run,
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

    assert report.status is RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT
    assert report.readiness["ready"] is True
    assert report.evaluation_flow["status"] == "READY"
    assert report.evaluation_flow["vacancy_source_status"] == "AVAILABLE_URL"


def test_evaluation_flow_dry_run_blocks_missing_source() -> None:
    report = run_recruiter_evaluation_flow_dry_run(
        prompt="Оцени вакансию",
        repo_root=REPO_ROOT,
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
    )

    assert report.status is RecruiterDryRunStatus.CONTEXT_SOURCE_REQUIRED
    assert report.readiness["ready"] is False
    assert report.evaluation_flow["status"] == "BLOCKED_SOURCE_REQUIRED"
