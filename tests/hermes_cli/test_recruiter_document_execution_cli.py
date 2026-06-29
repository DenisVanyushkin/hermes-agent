from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli.recruiter_document_execution_cli import (
    cmd_recruiter_document_execute,
    register_recruiter_document_subparser,
)
from hermes_cli.recruiter_skill_execution import RecruiterSkillExecutionReport, RecruiterSkillExecutionStatus


REPO_ROOT = Path(__file__).resolve().parents[2]


def _execution_report() -> RecruiterSkillExecutionReport:
    return RecruiterSkillExecutionReport(
        status=RecruiterSkillExecutionStatus.EXECUTION_READY,
        flow_id="evaluate-and-position",
        context_status="READY",
        skill_input_status="READY",
        execution_status="completed",
        provider_called=False,
        executor_called=True,
        vacancy_evaluation_result={
            "status": "SUCCESS",
            "skill_id": "vacancy-evaluation",
            "vacancy_evaluation_summary": "Strong fit for executive product role.",
            "fit_interpretation": "Match is strong on product leadership and scale.",
            "evidence_gaps": ["Exact team size not confirmed."],
            "recommendation_for_next_step": "Proceed to draft preparation.",
            "provenance": {"source": "fake-test"},
        },
        positioning_evidence_result={
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
                "status": "POSITIONING_AVAILABLE",
                "reason": "positioning packet available for downstream draft-only writer",
                "requires": ["positioning-and-evidence"],
                "references": ["role-packages/recruiter/skills/document-writer/SKILL.md"],
            }
        },
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "session_id": "fake-session"},
        forbidden_actions=[
            "call_provider_model",
            "send_outbound_message",
            "apply_to_job",
            "write_crm",
            "write_job_intel_db",
            "create_gmail_draft",
            "send_gmail",
            "read_private_file_contents",
            "mutate_live_config",
            "restart_gateway",
        ],
        planned_flow=["vacancy-evaluation", "positioning-and-evidence"],
    )


def _write_report(tmp_path: Path) -> Path:
    report_path = tmp_path / "execution-report.json"
    report_path.write_text(json.dumps(_execution_report().to_dict()), encoding="utf-8")
    return report_path


def test_registers_recruiter_document_execute_command() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_recruiter_document_subparser(subparsers)

    args = parser.parse_args(
        [
            "recruiter-document",
            "execute",
            "--execution-report-json",
            "/tmp/report.json",
            "--document-type",
            "cover_letter",
            "--json",
        ]
    )

    assert args.command == "recruiter-document"
    assert args.recruiter_document_command == "execute"
    assert args.execution_report_json == "/tmp/report.json"
    assert args.document_type == "cover_letter"
    assert args.json is True


def test_cli_requires_json_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        execution_report_json=str(_write_report(tmp_path)),
        document_type="cover_letter",
        audience=None,
        purpose=None,
        json=False,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_document_execute(args)

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--json is required" in captured.err


def test_cli_missing_execution_report_file_returns_controlled_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(
        execution_report_json=str(tmp_path / "missing.json"),
        document_type="cover_letter",
        audience=None,
        purpose=None,
        json=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_document_execute(args)

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "EXECUTION_REPORT_FILE_NOT_FOUND"
    assert payload["writer_called"] is False
    assert payload["reviewer_called"] is False
    assert payload["provider_called"] is False


def test_cli_invalid_json_returns_controlled_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report_path = tmp_path / "execution-report.json"
    report_path.write_text("{not-json", encoding="utf-8")
    args = argparse.Namespace(
        execution_report_json=str(report_path),
        document_type="cover_letter",
        audience=None,
        purpose=None,
        json=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_document_execute(args)

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "EXECUTION_REPORT_JSON_INVALID"


@pytest.mark.parametrize("payload", [["x"], "x", None])
def test_cli_non_object_json_returns_controlled_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    payload: object,
) -> None:
    report_path = tmp_path / "execution-report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    args = argparse.Namespace(
        execution_report_json=str(report_path),
        document_type="cover_letter",
        audience=None,
        purpose=None,
        json=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_document_execute(args)

    assert excinfo.value.code == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "EXECUTION_REPORT_JSON_NOT_OBJECT"


def test_cli_valid_execution_report_stays_fail_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        execution_report_json=str(_write_report(tmp_path)),
        document_type="cover_letter",
        audience=None,
        purpose=None,
        json=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_document_execute(args)

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCUMENT_EXECUTION_BLOCKED"
    assert payload["writer_called"] is False
    assert payload["reviewer_called"] is False
    assert payload["provider_called"] is False
    assert payload["document_packet"] is None
    assert "call_provider_model" in payload["forbidden_actions"]


def test_cli_unsupported_document_type_stays_blocked(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(
        execution_report_json=str(_write_report(tmp_path)),
        document_type="unsupported_document",
        audience=None,
        purpose=None,
        json=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_document_execute(args)

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCUMENT_INPUT_NOT_READY"
    assert payload["writer_input_status"] == "BLOCKED_UNSUPPORTED_DOCUMENT_TYPE"
    assert payload["writer_called"] is False
    assert payload["reviewer_called"] is False
    assert payload["provider_called"] is False


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_document_execution_cli.py").read_text(encoding="utf-8")
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
        "private/career",
        "/var/lib/job-intel/state",
    ]
    for needle in forbidden:
        assert needle not in source
