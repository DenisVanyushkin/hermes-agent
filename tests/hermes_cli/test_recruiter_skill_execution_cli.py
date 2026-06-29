from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli.recruiter_skill_execution import (
    RecruiterSkillExecutionReport,
    RecruiterSkillExecutionStatus,
)
from hermes_cli.recruiter_skill_execution_cli import (
    cmd_recruiter_skill_execute,
    register_recruiter_skill_subparser,
)


def _report(status: RecruiterSkillExecutionStatus) -> RecruiterSkillExecutionReport:
    return RecruiterSkillExecutionReport(
        status=status,
        flow_id="evaluate-and-position",
        context_status="READY",
        skill_input_status="READY",
        execution_status="blocked_by_provider_fuse",
        provider_called=False,
        executor_called=False,
        vacancy_evaluation_result=None,
        positioning_evidence_result=None,
        downstream_gates={"document_writer": {"status": "POSITIONING_REQUIRED"}},
        warnings=[],
        errors=[],
        provenance={"writes_performed": False},
        forbidden_actions=["call_provider_model", "execute_recruiter_skill"],
        planned_flow=["vacancy-evaluation", "positioning-and-evidence"],
    )


def test_registers_recruiter_skill_execute_command() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_recruiter_skill_subparser(subparsers)

    args = parser.parse_args(
        [
            "recruiter-skill",
            "execute",
            "--vacancy-id",
            "5274",
            "--flow",
            "evaluate-and-position",
            "--json",
        ]
    )

    assert args.command == "recruiter-skill"
    assert args.recruiter_skill_command == "execute"
    assert args.vacancy_id == 5274
    assert args.flow == "evaluate-and-position"
    assert args.json is True


def test_cli_command_prints_json_and_uses_deterministic_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        vacancy_id=5274,
        vacancy_url=None,
        opportunity_id=None,
        job_intel_db_path="/var/lib/job-intel/state/job_intel.sqlite3",
        private_career_dir="/home/hermes/.hermes/private/career",
        repo_root=str(Path("/home/hermes/.hermes/hermes-agent")),
        stale_after_days=14,
        flow="evaluate-and-position",
        allow_provider_execution=False,
        json=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_skill_execute(args, runner=lambda request: _report(RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED))

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PROVIDER_EXECUTION_BLOCKED"
    assert payload["planned_flow"] == ["vacancy-evaluation", "positioning-and-evidence"]


def test_cli_command_returns_zero_for_successful_fake_execution(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        vacancy_id=5274,
        vacancy_url=None,
        opportunity_id=None,
        job_intel_db_path="/var/lib/job-intel/state/job_intel.sqlite3",
        private_career_dir="/home/hermes/.hermes/private/career",
        repo_root=None,
        stale_after_days=14,
        flow="evaluate-and-position",
        allow_provider_execution=True,
        json=True,
    )

    report = _report(RecruiterSkillExecutionStatus.EXECUTION_READY)
    report.execution_status = "completed"
    report.executor_called = True
    report.downstream_gates = {"document_writer": {"status": "POSITIONING_AVAILABLE"}}

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_skill_execute(args, runner=lambda request: report)

    assert excinfo.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "EXECUTION_READY"
    assert payload["executor_called"] is True


def test_cli_command_requires_json_flag(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        vacancy_id=5274,
        vacancy_url=None,
        opportunity_id=None,
        job_intel_db_path="/var/lib/job-intel/state/job_intel.sqlite3",
        private_career_dir="/home/hermes/.hermes/private/career",
        repo_root=None,
        stale_after_days=14,
        flow="evaluate-and-position",
        allow_provider_execution=False,
        json=False,
    )

    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_skill_execute(args, runner=lambda request: _report(RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED))

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--json is required" in captured.err
