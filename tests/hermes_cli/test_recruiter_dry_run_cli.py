from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli._parser import build_top_level_parser
from hermes_cli.recruiter_dry_run import RecruiterDryRunReport, RecruiterDryRunStatus
from hermes_cli.recruiter_dry_run_cli import cmd_recruiter_context, register_recruiter_context_subparser


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_report(status: RecruiterDryRunStatus = RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT) -> RecruiterDryRunReport:
    return RecruiterDryRunReport(
        status=status,
        context_status="READY" if status is RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT else "SOURCE_REQUIRED",
        input={"vacancy_id": 123},
        readiness={"ready": status is RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT, "reason": "context_ready"},
        context_packet={"status": "READY"},
        missing_requirements=[],
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "dry_run": True},
        next_allowed_actions=["run_recruiter_vacancy_evaluation_skill_later"],
    )


def _parse_direct(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    register_recruiter_context_subparser(subparsers)
    return parser.parse_args(argv)


def _parse_main(argv: list[str]) -> argparse.Namespace:
    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=lambda args: None)
    register_recruiter_context_subparser(subparsers)
    return parser.parse_args(argv)


def test_subparser_exists_in_main_tree() -> None:
    args = _parse_main(["recruiter-context", "dry-run", "--vacancy-id", "123", "--json"])

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "dry-run"
    assert args.vacancy_id == 123
    assert args.json is True


def test_parses_vacancy_url() -> None:
    args = _parse_direct(["recruiter-context", "dry-run", "--vacancy-url", "https://example.com/job", "--json"])

    assert args.vacancy_url == "https://example.com/job"
    assert args.vacancy_id is None
    assert args.opportunity_id is None


def test_parses_opportunity_id() -> None:
    args = _parse_direct(["recruiter-context", "dry-run", "--opportunity-id", "42", "--json"])

    assert args.opportunity_id == 42
    assert args.vacancy_id is None
    assert args.vacancy_url is None


def test_json_output_matches_report(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured = {}

    def _fake_run(request):
        captured["request"] = request
        return _make_report()

    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_context_dry_run", _fake_run)

    args = _parse_direct([
        "recruiter-context",
        "dry-run",
        "--vacancy-id",
        "123",
        "--job-intel-db-path",
        "/tmp/job-intel.sqlite3",
        "--private-career-dir",
        "/tmp/private",
        "--repo-root",
        str(REPO_ROOT),
        "--stale-after-days",
        "30",
        "--json",
    ])

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == _make_report().to_dict()
    request = captured["request"]
    assert request.vacancy_id == 123
    assert request.job_intel_db_path == "/tmp/job-intel.sqlite3"
    assert request.private_career_dir == "/tmp/private"
    assert request.repo_root == REPO_ROOT
    assert request.stale_after_days == 30


def test_stdout_is_json_only(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_context_dry_run", lambda request: _make_report())

    args = _parse_direct(["recruiter-context", "dry-run", "--vacancy-id", "123", "--json"])

    with pytest.raises(SystemExit):
        cmd_recruiter_context(args)

    stdout = capsys.readouterr().out
    assert stdout.endswith("\n")
    assert stdout == json.dumps(_make_report().to_dict(), sort_keys=True) + "\n"


def test_ready_report_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_context_dry_run", lambda request: _make_report())
    args = _parse_direct(["recruiter-context", "dry-run", "--vacancy-id", "123", "--json"])

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0


def test_not_ready_report_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_context_dry_run", lambda request: _make_report(RecruiterDryRunStatus.CONTEXT_NOT_FOUND))
    args = _parse_direct(["recruiter-context", "dry-run", "--vacancy-id", "123", "--json"])

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1


def test_missing_identifier_is_controlled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_context_dry_run", lambda request: _make_report(RecruiterDryRunStatus.CONTEXT_SOURCE_REQUIRED))
    args = _parse_direct(["recruiter-context", "dry-run", "--json"])

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.CONTEXT_SOURCE_REQUIRED.value


def test_multiple_identifiers_is_controlled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_context_dry_run", lambda request: _make_report(RecruiterDryRunStatus.CONTEXT_INVALID_REQUEST))
    args = _parse_direct(["recruiter-context", "dry-run", "--vacancy-id", "123", "--opportunity-id", "9", "--json"])

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.CONTEXT_INVALID_REQUEST.value


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_dry_run_cli.py").read_text(encoding="utf-8")
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


def test_parse_evaluation_flow_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--json",
        ]
    )

    assert args.flow == "evaluate-vacancy"
    assert args.prompt == "Посмотри вакансию https://example.com/jobs/123"


def test_parse_allow_provider_execution_flag() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--allow-provider-execution",
            "--json",
        ]
    )

    assert args.allow_provider_execution is True


def test_parse_private_context_status_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    assert args.private_context_status == "PRIVATE_CONTEXT_AVAILABLE"


def test_evaluation_flow_cli_provider_blocked_without_private_context_available(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*, prompt, repo_root, private_context_status, allow_provider_execution):
        captured["prompt"] = prompt
        captured["repo_root"] = repo_root
        captured["private_context_status"] = private_context_status
        captured["allow_provider_execution"] = allow_provider_execution
        return RecruiterDryRunReport(
            status=RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED,
            context_status="READY",
            input={"prompt": prompt},
            readiness={"ready": False, "reason": "provider_execution_requires_private_context_available"},
            context_packet=None,
            evaluation_flow={"status": "READY"},
            evaluation_result=None,
            missing_requirements=[],
            warnings=[],
            errors=["private_context_not_ready_for_provider_execution"],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=["provision_private_career_context"],
            provider_called=False,
            provider_execution_enabled=True,
            executor_called=False,
            downstream_gates={"document_generation": {"enabled": False}},
        )

    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_evaluation_flow_dry_run", _fake_run)
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--allow-provider-execution",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED.value
    assert payload["provider_called"] is False
    assert payload["executor_called"] is False
    assert captured["private_context_status"] == "PRIVATE_CONTEXT_NOT_INSPECTED"
    assert captured["allow_provider_execution"] is True


def test_evaluation_flow_cli_provider_ready_with_private_context_available(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*, prompt, repo_root, private_context_status, allow_provider_execution):
        captured["prompt"] = prompt
        captured["repo_root"] = repo_root
        captured["private_context_status"] = private_context_status
        captured["allow_provider_execution"] = allow_provider_execution
        return RecruiterDryRunReport(
            status=RecruiterDryRunStatus.EVALUATION_READY,
            context_status="READY",
            input={"prompt": prompt},
            readiness={"ready": True, "reason": "provider_evaluation_completed"},
            context_packet=None,
            evaluation_flow={"status": "READY"},
            evaluation_result={
                "schema_version": "recruiter_vacancy_evaluation_packet_v1",
                "skill_id": "vacancy-evaluation",
                "status": "EVALUATION_READY",
            },
            missing_requirements=[],
            warnings=[],
            errors=[],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=["review_evaluation_packet_manually"],
            provider_called=True,
            provider_execution_enabled=True,
            executor_called=True,
            downstream_gates={"document_generation": {"enabled": False}},
        )

    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_evaluation_flow_dry_run", _fake_run)
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--allow-provider-execution",
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.EVALUATION_READY.value
    assert payload["provider_called"] is True
    assert payload["executor_called"] is True
    assert captured["private_context_status"] == "PRIVATE_CONTEXT_AVAILABLE"
    assert captured["allow_provider_execution"] is True


def test_evaluation_ready_report_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_recruiter_evaluation_flow_dry_run",
        lambda **kwargs: RecruiterDryRunReport(
            status=RecruiterDryRunStatus.EVALUATION_READY,
            context_status="READY",
            input={"prompt": kwargs["prompt"]},
            readiness={"ready": True, "reason": "provider_evaluation_completed"},
            context_packet=None,
            evaluation_flow={"status": "READY"},
            evaluation_result={"schema_version": "recruiter_vacancy_evaluation_packet_v1"},
            missing_requirements=[],
            warnings=[],
            errors=[],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=[],
            provider_called=True,
            provider_execution_enabled=True,
            executor_called=True,
            downstream_gates={"document_generation": {"enabled": False}},
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--allow-provider-execution",
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0


def test_evaluation_flow_stdout_is_json_only(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_recruiter_evaluation_flow_dry_run",
        lambda **kwargs: RecruiterDryRunReport(
            status=RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED,
            context_status="READY",
            input={"prompt": kwargs["prompt"]},
            readiness={"ready": False, "reason": "provider_execution_requires_private_context_available"},
            context_packet=None,
            evaluation_flow={"status": "READY"},
            evaluation_result=None,
            missing_requirements=[],
            warnings=[],
            errors=["private_context_not_ready_for_provider_execution"],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=["provision_private_career_context"],
            provider_called=False,
            provider_execution_enabled=True,
            executor_called=False,
            downstream_gates={"document_generation": {"enabled": False}},
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "evaluate-vacancy",
            "--prompt",
            "Посмотри вакансию https://example.com/jobs/123",
            "--allow-provider-execution",
            "--json",
        ]
    )

    with pytest.raises(SystemExit):
        cmd_recruiter_context(args)

    stdout = capsys.readouterr().out
    assert stdout.endswith("\n")
    payload = json.loads(stdout)
    assert payload["status"] == RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED.value
