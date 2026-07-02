from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli._parser import build_top_level_parser
from hermes_cli.recruiter_dry_run import (
    REQUIRED_APPLICATION_MATERIAL_TARGETS,
    RecruiterE2EApplicationMaterialsStatus,
    RecruiterE2EApplicationMaterialsReport,
    RecruiterApplicationMaterialsSmokeReport,
    RecruiterApplicationMaterialsSmokeStatus,
    RecruiterDryRunReport,
    RecruiterDryRunStatus,
    RecruiterPositioningSmokeReport,
    RecruiterPositioningSmokeStatus,
)
from hermes_cli.recruiter_dry_run_cli import cmd_recruiter_context, register_recruiter_context_subparser


REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_full_downstream_gates(payload: dict[str, object]) -> None:
    assert payload["downstream_gates"] == {
        "outbound": {"enabled": False},
        "db_write": {"enabled": False},
        "crm_write": {"enabled": False},
        "document_generation": {"enabled": False},
    }


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


def test_candidate_facts_subparser_exists_in_main_tree() -> None:
    args = _parse_main(["recruiter-context", "candidate-facts", "--json"])

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "candidate-facts"
    assert args.json is True


def test_smoke_positioning_subparser_exists_in_main_tree() -> None:
    args = _parse_main(
        [
            "recruiter-context",
            "smoke-positioning",
            "--evaluation-packet-json",
            "/tmp/evaluation.json",
            "--candidate-facts-packet-json",
            "/tmp/candidate-facts.json",
            "--json",
        ]
    )

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "smoke-positioning"
    assert args.json is True


def test_smoke_application_materials_subparser_accepts_all_required_targets() -> None:
    args = _parse_main(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            "/tmp/positioning.json",
            "--all-required-targets",
            "--json",
        ]
    )

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "smoke-application-materials"
    assert args.all_required_targets is True
    assert args.document_target is None


def test_smoke_application_materials_subparser_exists_in_main_tree() -> None:
    args = _parse_main(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            "/tmp/positioning.json",
            "--document-target",
            "recruiter_message_draft",
            "--json",
        ]
    )

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "smoke-application-materials"
    assert args.document_target == "recruiter_message_draft"
    assert args.json is True


def test_smoke_e2e_application_materials_subparser_exists_in_main_tree() -> None:
    args = _parse_main(
        [
            "recruiter-context",
            "smoke-e2e-application-materials",
            "--evaluation-packet-json",
            "/tmp/evaluation.json",
            "--candidate-facts-packet-json",
            "/tmp/candidate-facts.json",
            "--all-required-targets",
            "--json",
        ]
    )

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "smoke-e2e-application-materials"
    assert args.all_required_targets is True
    assert args.json is True


def test_preflight_real_application_materials_subparser_exists_in_main_tree() -> None:
    args = _parse_main(
        [
            "recruiter-context",
            "preflight-real-application-materials",
            "--vacancy-source-type",
            "vacancy_payload",
            "--vacancy-source-approved",
            "--career-source-spec-json",
            '{"source_id":"career-facts-1","source_kind":"career_fact","source_type":"career_fact_packet","approved":true}',
            "--permitted-source-type",
            "vacancy_payload",
            "--permitted-source-type",
            "career_fact_packet",
            "--draft-only",
            "--json",
        ]
    )

    assert args.command == "recruiter-context"
    assert args.recruiter_context_command == "preflight-real-application-materials"
    assert args.vacancy_source_type == "vacancy_payload"
    assert args.vacancy_source_approved is True
    assert args.draft_only is True
    assert args.json is True


def test_smoke_e2e_application_materials_allow_provider_help_text_is_safety_aligned() -> None:
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    recruiter_context_parser = register_recruiter_context_subparser(subparsers)
    nested_action = next(action for action in recruiter_context_parser._actions if isinstance(action, argparse._SubParsersAction))
    smoke_e2e_parser = nested_action.choices["smoke-e2e-application-materials"]
    allow_provider_action = next(
        action for action in smoke_e2e_parser._actions if "--allow-provider-execution" in getattr(action, "option_strings", [])
    )

    assert allow_provider_action.help == "Explicitly allow positioning and application-materials executor invocation"
    assert "fake executor-backed e2e harness execution" not in allow_provider_action.help


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


def test_parse_candidate_facts_fixture_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "candidate-facts",
            "--fixture-safe-facts-json",
            "/tmp/recruiter-safe-facts-fixture.json",
            "--json",
        ]
    )

    assert args.fixture_safe_facts_json == "/tmp/recruiter-safe-facts-fixture.json"


def test_candidate_facts_cli_outputs_json_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_candidate_facts_cli",
        lambda **kwargs: type(
            "Packet",
            (),
            {
                "status": "BLOCKED_PRIVATE_CONTEXT_MISSING",
                "to_dict": lambda self: {
                    "schema_version": "recruiter_candidate_facts_packet_v1",
                    "status": "BLOCKED_PRIVATE_CONTEXT_MISSING",
                    "provider_visibility_status": "BLOCKED_PRIVATE_CONTEXT_MISSING",
                    "errors": ["private_context_missing"],
                },
            },
        )(),
    )
    args = _parse_direct(["recruiter-context", "candidate-facts", "--json"])

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    stdout = capsys.readouterr().out
    assert stdout.endswith("\n")
    payload = json.loads(stdout)
    assert payload["status"] == "BLOCKED_PRIVATE_CONTEXT_MISSING"


def test_candidate_facts_cli_does_not_echo_fixture_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_candidate_facts_cli",
        lambda **kwargs: type(
            "Packet",
            (),
            {
                "status": "READY_PROVIDER_VISIBLE",
                "to_dict": lambda self: {
                    "schema_version": "recruiter_candidate_facts_packet_v1",
                    "status": "READY_PROVIDER_VISIBLE",
                    "provider_visibility_status": "READY_PROVIDER_VISIBLE",
                    "source_references": [
                        {
                            "source_ref_id": "src-1",
                            "source_label": "safe_fixture",
                            "source_id_hash": "fixture-hash",
                            "section_label": "safe_section",
                        }
                    ],
                },
            },
        )(),
    )
    fixture_path = "/tmp/recruiter-safe-facts-fixture.json"
    args = _parse_direct(
        [
            "recruiter-context",
            "candidate-facts",
            "--fixture-safe-facts-json",
            fixture_path,
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0
    stdout = capsys.readouterr().out
    assert fixture_path not in stdout


def test_candidate_facts_cli_unsafe_fixture_output_redacts_raw_strings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_candidate_facts_cli",
        lambda **kwargs: type(
            "Packet",
            (),
            {
                "status": "BLOCKED_UNSAFE_CONTENT",
                "to_dict": lambda self: {
                    "schema_version": "recruiter_candidate_facts_packet_v1",
                    "status": "BLOCKED_UNSAFE_CONTENT",
                    "provider_visibility_status": "BLOCKED_UNSAFE_CONTENT",
                    "errors": ["unsafe_path_detected", "unsafe_contact_detected"],
                    "facts": [],
                    "source_references": [],
                    "allowed_claims": [],
                    "claims_to_avoid": [],
                    "unsupported_claims": [],
                    "next_step": "CANDIDATE_FACTS_UNSAFE_CONTENT_REDACTED",
                },
            },
        )(),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "candidate-facts",
            "--fixture-safe-facts-json",
            "/tmp/recruiter-unsafe-facts-review-fixture.json",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    stdout = capsys.readouterr().out
    assert "/home/hermes" not in stdout
    assert ".hermes/private" not in stdout
    assert "candidate@example.com" not in stdout
    assert "+7 701 110 2626" not in stdout
    assert "~/" not in stdout


def test_candidate_facts_cli_candidate_ref_redactions_bypass_output_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_candidate_facts_cli",
        lambda **kwargs: type(
            "Packet",
            (),
            {
                "status": "BLOCKED_UNSAFE_CONTENT",
                "to_dict": lambda self: {
                    "schema_version": "recruiter_candidate_facts_packet_v1",
                    "status": "BLOCKED_UNSAFE_CONTENT",
                    "provider_visibility_status": "BLOCKED_UNSAFE_CONTENT",
                    "candidate_ref": "candidate-redacted",
                    "errors": ["unsafe_path_detected", "unsafe_contact_detected"],
                    "facts": [],
                    "source_references": [],
                    "allowed_claims": [],
                    "claims_to_avoid": [],
                    "unsupported_claims": [],
                    "redactions": ["unsafe_fixture_content_redacted:fact_count=1"],
                    "next_step": "CANDIDATE_FACTS_UNSAFE_CONTENT_REDACTED",
                },
            },
        )(),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "candidate-facts",
            "--fixture-safe-facts-json",
            "/tmp/recruiter-candidate-ref-redactions-bypass-fixture.json",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    stdout = capsys.readouterr().out
    json.loads(stdout)
    assert "/home/hermes" not in stdout
    assert ".hermes/private" not in stdout
    assert "candidate@example.com" not in stdout
    assert "+7 701 110 2626" not in stdout


def test_parse_positioning_flow_and_evaluation_packet_json_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            "/tmp/evaluation-packet.json",
            "--json",
        ]
    )

    assert args.flow == "positioning-and-evidence"
    assert args.evaluation_packet_json == "/tmp/evaluation-packet.json"


def test_parse_positioning_flow_candidate_facts_packet_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            "/tmp/evaluation-packet.json",
            "--candidate-facts-packet-json",
            "/tmp/candidate-facts-packet.json",
            "--json",
        ]
    )

    assert args.candidate_facts_packet_json == "/tmp/candidate-facts-packet.json"


def test_parse_positioning_flow_fake_output_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            "/tmp/evaluation-packet.json",
            "--candidate-facts-packet-json",
            "/tmp/candidate-facts-packet.json",
            "--fake-positioning-output",
            "--json",
        ]
    )

    assert args.fake_positioning_output is True


def test_parse_application_materials_flow_and_positioning_packet_json_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            "/tmp/positioning-packet.json",
            "--json",
        ]
    )

    assert args.flow == "application-materials"
    assert args.positioning_packet_json == "/tmp/positioning-packet.json"


def test_parse_application_materials_document_target_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            "/tmp/positioning-packet.json",
            "--document-target",
            "recruiter_message_draft",
            "--json",
        ]
    )

    assert args.document_target == "recruiter_message_draft"


def test_parse_application_materials_all_required_targets_option() -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            "/tmp/positioning.json",
            "--all-required-targets",
            "--json",
        ]
    )

    assert args.all_required_targets is True
    assert args.document_target is None


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


def test_application_materials_flow_cli_returns_controlled_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "positioning-packet.json"
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "positioning-and-evidence",
                "status": "POSITIONING_READY",
                "positioning_summary": "Lead with executive product leadership.",
                "target_narrative": "Operator for scaling product organizations.",
                "evidence": ["Scaled multi-team product orgs."],
                "gaps": [],
                "risks_and_mitigations": [],
                "recommended_angle": "Scale-stage executive operator.",
                "claims_to_use": ["Led product organizations."],
                "claims_to_avoid": [],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "provenance": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.run_recruiter_application_materials_flow_dry_run",
        lambda **kwargs: RecruiterDryRunReport(
            status=RecruiterDryRunStatus.APPLICATION_MATERIALS_READY,
            context_status="READY",
            input={"flow": "application-materials"},
            readiness={"ready": True, "reason": "application_materials_ready"},
            context_packet=None,
            evaluation_flow=None,
            evaluation_result=None,
            positioning_result={"schema_version": "recruiter_positioning_packet_v1"},
            application_materials_result={"schema_version": "recruiter_application_materials_packet_v1"},
            missing_requirements=[],
            warnings=[],
            errors=[],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=["review_application_materials_packet_manually"],
            provider_called=True,
            provider_execution_enabled=True,
            executor_called=True,
            downstream_gates={
                "outbound": {"enabled": False},
                "db_write": {"enabled": False},
                "crm_write": {"enabled": False},
                "document_generation": {"enabled": False},
                "gmail_draft": {"enabled": False},
                "linkedin_send": {"enabled": False},
                "controlled_document_dry_run": {"enabled": True},
            },
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            str(packet_path),
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
    assert payload["status"] == RecruiterDryRunStatus.APPLICATION_MATERIALS_READY.value
    assert payload["application_materials_result"]["schema_version"] == "recruiter_application_materials_packet_v1"


def test_positioning_flow_cli_fake_output_path_returns_controlled_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluation_path = tmp_path / "evaluation-packet.json"
    candidate_facts_path = tmp_path / "candidate-facts-packet.json"
    evaluation_path.write_text(
        json.dumps(
            {
                "schema_version": "recruiter_vacancy_evaluation_packet_v1",
                "skill_id": "vacancy-evaluation",
                "status": "EVALUATION_READY",
                "recommendation": "APPLY",
                "fit_assessment": "Strong fit.",
                "strengths": ["Executive product leadership match."],
                "risks": ["Team size not confirmed."],
                "evidence": ["Prompt contained a vacancy URL."],
                "missing_information": [],
                "next_step": "PROCEED_TO_POSITIONING",
                "provenance": {},
            }
        ),
        encoding="utf-8",
    )
    candidate_facts_path.write_text(
        json.dumps(
            {
                "schema_version": "recruiter_candidate_facts_packet_v1",
                "skill_id": "candidate-facts",
                "status": "READY_PROVIDER_VISIBLE",
                "candidate_ref": "candidate-test",
                "generated_at": "2026-07-01T00:00:00+00:00",
                "source_policy": {},
                "requires_user_approval": False,
                "provider_visibility_status": "READY_PROVIDER_VISIBLE",
                "facts": [
                    {
                        "fact_id": "fact-1",
                        "category": "domain",
                        "safe_summary": "Product and commercial leadership experience",
                        "provider_text": "Candidate has product and commercial leadership experience in digital services.",
                        "support_level": "explicit",
                        "source_ref_ids": ["src-1"],
                        "forbidden_expansions": ["Do not infer revenue ownership"],
                        "approval_required": False,
                        "provider_visible": True,
                        "log_visible": False,
                    }
                ],
                "source_references": [
                    {
                        "source_ref_id": "src-1",
                        "source_type": "test_fixture",
                        "source_label": "safe-fixture",
                        "source_id_hash": "fixture-hash",
                        "section_label": "safe-section",
                        "content_hash": "fixture-content-hash",
                        "sensitivity": "private_sanitized",
                        "provider_visible": True,
                        "log_visible": True,
                    }
                ],
                "allowed_claims": [
                    {
                        "claim_id": "claim-1",
                        "claim_text": "Product and commercial leadership experience in digital services.",
                        "source_fact_ids": ["fact-1"],
                        "support_level": "explicit",
                    }
                ],
                "claims_to_avoid": ["Do not claim revenue ownership."],
                "unsupported_claims": [],
                "redactions": [],
                "support_summary": {"explicit": 1, "derived_safe": 0, "weak": 0, "unsupported": 0},
                "role_target_context": {},
                "privacy_notes": ["sanitized fixture packet"],
                "next_step": "CANDIDATE_FACTS_READY_FOR_POSITIONING",
                "errors": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            str(evaluation_path),
            "--candidate-facts-packet-json",
            str(candidate_facts_path),
            "--fake-positioning-output",
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.POSITIONING_READY.value
    assert payload["provider_called"] is False
    assert payload["executor_called"] is True
    assert payload["positioning_result"]["generation_mode"] == "deterministic_fake"
    assert payload["positioning_result"]["provider_called"] is False
    assert payload["positioning_result"]["executor_called"] is False
    encoded = json.dumps(payload, sort_keys=True)
    assert "provider_text" not in encoded
    assert "\"candidate_facts_packet\"" not in encoded
    assert "/home/" not in encoded
    assert "/Users/" not in encoded


def test_positioning_flow_cli_blocks_unsafe_evaluation_packet_without_echoing_raw_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluation_path = tmp_path / "evaluation-packet.json"
    evaluation_path.write_text(
        json.dumps(
            {
                "schema_version": "recruiter_vacancy_evaluation_packet_v1",
                "skill_id": "vacancy-evaluation",
                "status": "EVALUATION_READY",
                "recommendation": "APPLY",
                "fit_assessment": "Unsafe /Users/testleak/private/career leaktest@example.com",
                "strengths": ["Executive product leadership match."],
                "risks": ["Team size not confirmed."],
                "evidence": ["Prompt contained a vacancy URL."],
                "missing_information": [],
                "next_step": "PROCEED_TO_POSITIONING",
                "provenance": {},
            }
        ),
        encoding="utf-8",
    )

    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            str(evaluation_path),
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED.value
    assert payload["errors"] == ["evaluation_packet_unsafe"]
    assert payload["provider_called"] is False
    assert payload["executor_called"] is False
    assert payload["evaluation_result"] is None
    encoded = json.dumps(payload, sort_keys=True)
    assert "/Users/testleak" not in encoded
    assert "private/career" not in encoded
    assert "leaktest@example.com" not in encoded


def test_application_materials_flow_cli_passes_document_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "positioning-packet.json"
    packet_path.write_text(json.dumps({"schema_version": "recruiter_positioning_packet_v1"}), encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)
        return RecruiterDryRunReport(
            status=RecruiterDryRunStatus.APPLICATION_MATERIALS_PROVIDER_EXECUTION_BLOCKED,
            context_status="READY",
            input={"flow": "application-materials"},
            readiness={"ready": True, "reason": "provider_execution_requires_explicit_opt_in"},
            context_packet=None,
            evaluation_flow=None,
            evaluation_result=None,
            positioning_result={"schema_version": "recruiter_positioning_packet_v1"},
            application_materials_result=None,
            missing_requirements=[],
            warnings=[],
            errors=[],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=["rerun_with_allow_provider_execution"],
            provider_called=False,
            provider_execution_enabled=False,
            executor_called=False,
            downstream_gates={"document_generation": {"enabled": False}},
        )

    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_application_materials_flow_dry_run", _fake_run)
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            str(packet_path),
            "--document-target",
            "recruiter_message_draft",
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit):
        cmd_recruiter_context(args)

    json.loads(capsys.readouterr().out)
    assert captured["document_target"] == "recruiter_message_draft"


def test_application_materials_flow_cli_invalid_document_target_is_parseable_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "positioning-packet.json"
    packet_path.write_text(json.dumps({"schema_version": "recruiter_positioning_packet_v1"}), encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            str(packet_path),
            "--document-target",
            "invalid_target",
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED.value
    assert payload["provider_called"] is False
    assert payload["executor_called"] is False
    assert payload["errors"] == ["invalid_document_target"]


def test_application_materials_flow_cli_rejects_positioning_claim_without_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "positioning-packet.json"
    packet_path.write_text(
        json.dumps(
            {
                "schema_version": "recruiter_positioning_packet_v1",
                "skill_id": "positioning-and-evidence",
                "status": "POSITIONING_READY",
                "positioning_summary": "Lead with executive product leadership.",
                "target_narrative": "Operator for scaling product organizations.",
                "evidence": ["Scaled multi-team product orgs."],
                "gaps": [],
                "risks_and_mitigations": [],
                "recommended_angle": "Scale-stage executive operator.",
                "claims_to_use": ["Led product organizations."],
                "claims_to_avoid": [],
                "missing_information": [],
                "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
                "allowed_claims": [{"claim_id": "claim-1", "claim_text": "Led product organizations."}],
                "evidence_items": [
                    {
                        "claim_text": "Led product organizations.",
                        "source_fact_ids": ["fact-1"],
                        "source_ref_ids": ["src-1"],
                        "support_level": "explicit",
                        "category": "leadership",
                        "safe_summary": "Scaled multi-team product orgs.",
                    }
                ],
                "unsupported_claims": [],
                "source_references": [
                    {
                        "source_ref_id": "src-1",
                        "source_label": "safe-fixture",
                        "source_id_hash": "fixture-hash",
                        "section_label": "safe-section",
                        "support_level": "explicit",
                        "category": "test_fixture",
                    }
                ],
                "support_summary": {"explicit": 1, "derived_safe": 0, "weak": 0, "unsupported": 0},
                "privacy_notes": ["sanitized fixture packet"],
                "generation_mode": "deterministic_fake",
                "source_kind": "fake_candidate_facts",
                "provider_called": False,
                "executor_called": False,
                "provenance": {},
            }
        ),
        encoding="utf-8",
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            str(packet_path),
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--allow-provider-execution",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED.value
    assert payload["errors"] == ["positioning_packet_claim_without_source"]


def test_application_materials_flow_cli_blocks_unsafe_packet_without_echoing_raw_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "positioning-packet.json"
    packet = {
        "schema_version": "recruiter_positioning_packet_v1",
        "skill_id": "positioning-and-evidence",
        "status": "POSITIONING_READY",
        "positioning_summary": "Unsafe /Users/testleak/private/career leaktest@example.com",
        "target_narrative": "Operator for scaling product organizations.",
        "evidence": ["Scaled multi-team product orgs."],
        "gaps": [],
        "risks_and_mitigations": [],
        "recommended_angle": "Scale-stage executive operator.",
        "claims_to_use": ["Led product organizations."],
        "claims_to_avoid": [],
        "missing_information": [],
        "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
        "allowed_claims": [
            {
                "claim_id": "claim-1",
                "claim_text": "Led product organizations.",
                "source_fact_ids": ["fact-1"],
                "support_level": "explicit",
            }
        ],
        "evidence_items": [
            {
                "claim_text": "Led product organizations.",
                "source_fact_ids": ["fact-1"],
                "source_ref_ids": ["src-1"],
                "support_level": "explicit",
                "category": "leadership",
                "safe_summary": "Scaled multi-team product orgs.",
            }
        ],
        "unsupported_claims": [],
        "source_references": [
            {
                "source_ref_id": "src-1",
                "source_label": "safe-fixture",
                "source_id_hash": "fixture-hash",
                "section_label": "safe-section",
                "support_level": "explicit",
                "category": "test_fixture",
            }
        ],
        "support_summary": {"explicit": 1, "derived_safe": 0, "weak": 0, "unsupported": 0},
        "privacy_notes": ["sanitized fixture packet"],
        "generation_mode": "deterministic_fake",
        "source_kind": "fake_candidate_facts",
        "provider_called": False,
        "executor_called": False,
        "provenance": {},
    }
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "application-materials",
            "--positioning-packet-json",
            str(packet_path),
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--allow-provider-execution",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED.value
    assert payload["errors"] == ["positioning_packet_unsafe"]
    assert payload["positioning_result"] in (None, {})
    encoded = json.dumps(payload, sort_keys=True)
    assert "Unsafe /Users/testleak/private/career leaktest@example.com" not in encoded
    assert "/Users/testleak/private/career" not in encoded
    assert "leaktest@example.com" not in encoded


def test_preflight_real_application_materials_cli_outputs_report_safe_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hermes_cli.recruiter_real_data_privacy_gate import RealDataPrivacyGateReport, RealDataPrivacyGateStatus

    captured = {}

    def _fake_run(request):
        captured["request"] = request
        return RealDataPrivacyGateReport(
            status=RealDataPrivacyGateStatus.READY,
            ready=True,
            blocked_reason=None,
            required_approvals=[],
            safe_to_retry_after_user_approval=False,
            approved_source_count=2,
            approved_source_types=["career_fact_packet", "vacancy_payload"],
            capability_flags={
                "draft_only": True,
                "outbound_disabled": True,
                "crm_writes_disabled": True,
                "job_intel_writes_disabled": True,
                "browser_automation_disabled": True,
                "private_file_access_disabled": True,
            },
            warnings=["metadata_only_preflight"],
            errors=[],
            provenance={"writes_performed": False, "flow": "application-materials-real-data-preflight"},
        )

    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli.evaluate_real_data_application_materials_privacy_gate",
        _fake_run,
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "preflight-real-application-materials",
            "--vacancy-source-type",
            "vacancy_payload",
            "--vacancy-source-approved",
            "--career-source-spec-json",
            '{"source_id":"career-facts-1","source_kind":"career_fact","source_type":"career_fact_packet","approved":true}',
            "--permitted-source-type",
            "vacancy_payload",
            "--permitted-source-type",
            "career_fact_packet",
            "--draft-only",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "REAL_DATA_PRIVACY_GATE_READY"
    assert payload["approved_source_count"] == 2
    assert captured["request"].vacancy_source_type == "vacancy_payload"
    assert captured["request"].draft_only is True


def test_preflight_real_application_materials_cli_blocks_invalid_source_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "preflight-real-application-materials",
            "--vacancy-source-type",
            "vacancy_payload",
            "--vacancy-source-approved",
            "--career-source-spec-json",
            '{"source_id":',
            "--permitted-source-type",
            "vacancy_payload",
            "--draft-only",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "REAL_DATA_PRIVACY_GATE_BLOCKED"
    assert payload["blocked_reason"] == "career_source_spec_json_invalid"


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


def test_positioning_flow_cli_reads_only_the_provided_packet_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "evaluation-packet.json"
    packet_path.write_text(json.dumps({"schema_version": "recruiter_vacancy_evaluation_packet_v1"}), encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_run(*, evaluation_packet, candidate_facts_packet=None, repo_root, private_context_status, allow_provider_execution):
        captured["evaluation_packet"] = evaluation_packet
        captured["candidate_facts_packet"] = candidate_facts_packet
        captured["repo_root"] = repo_root
        captured["private_context_status"] = private_context_status
        captured["allow_provider_execution"] = allow_provider_execution
        return RecruiterDryRunReport(
            status=RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED,
            context_status="READY",
            input={"flow": "positioning-and-evidence"},
            readiness={"ready": True, "reason": "provider_execution_requires_explicit_opt_in"},
            context_packet=None,
            evaluation_flow=None,
            evaluation_result=None,
            positioning_result=None,
            missing_requirements=[],
            warnings=[],
            errors=[],
            provenance={"writes_performed": False, "dry_run": True},
            next_allowed_actions=["rerun_with_allow_provider_execution"],
            provider_called=False,
            provider_execution_enabled=False,
            executor_called=False,
            downstream_gates={
                "outbound": {"enabled": False},
                "db_write": {"enabled": False},
                "crm_write": {"enabled": False},
                "document_generation": {"enabled": False},
            },
        )

    monkeypatch.setattr("hermes_cli.recruiter_dry_run_cli.run_recruiter_positioning_flow_dry_run", _fake_run)
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            str(packet_path),
            "--private-context-status",
            "PRIVATE_CONTEXT_AVAILABLE",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED.value
    assert captured["evaluation_packet"] == {"schema_version": "recruiter_vacancy_evaluation_packet_v1"}
    assert captured["candidate_facts_packet"] is None
    assert captured["private_context_status"] == "PRIVATE_CONTEXT_AVAILABLE"
    _assert_full_downstream_gates(payload)


def test_positioning_flow_cli_rejects_invalid_json_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet_path = tmp_path / "evaluation-packet.json"
    packet_path.write_text("{not-json}", encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            str(packet_path),
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED.value
    assert payload["errors"] == ["evaluation_packet_json_invalid"]
    _assert_full_downstream_gates(payload)


def test_positioning_flow_cli_rejects_missing_json_file_with_full_gates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _parse_direct(
        [
            "recruiter-context",
            "dry-run",
            "--flow",
            "positioning-and-evidence",
            "--evaluation-packet-json",
            "/tmp/does-not-exist-evaluation-packet.json",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED.value
    assert payload["errors"] == ["evaluation_packet_json_missing"]
    _assert_full_downstream_gates(payload)


def test_smoke_positioning_cli_provider_blocked_without_executor_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluation_path = tmp_path / "evaluation-packet.json"
    candidate_facts_path = tmp_path / "candidate-facts-packet.json"
    evaluation_path.write_text(json.dumps({"schema_version": "recruiter_vacancy_evaluation_packet_v1"}), encoding="utf-8")
    candidate_facts_path.write_text(json.dumps({"schema_version": "recruiter_candidate_facts_packet_v1"}), encoding="utf-8")

    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli._run_positioning_smoke_report",
        lambda args, repo_root: RecruiterPositioningSmokeReport(
            schema_version="recruiter_positioning_smoke_report_v1",
            status=RecruiterPositioningSmokeStatus.READY_PROVIDER_BLOCKED,
            readiness_reason="provider_execution_requires_explicit_opt_in",
            provider_allowed=False,
            provider_called=False,
            executor_called=False,
            input_validation={
                "ready": True,
                "evaluation_packet_ready": True,
                "candidate_facts_packet_ready": True,
                "errors": [],
            },
            output_validation={"ready": False, "status": "not_run", "errors": []},
            errors=[],
            warnings=[],
            next_allowed_actions=["rerun_with_allow_provider_execution"],
            provenance={"writes_performed": False, "dry_run": True},
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-positioning",
            "--evaluation-packet-json",
            str(evaluation_path),
            "--candidate-facts-packet-json",
            str(candidate_facts_path),
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterPositioningSmokeStatus.READY_PROVIDER_BLOCKED.value
    assert payload["provider_called"] is False
    assert payload["executor_called"] is False
    assert payload["output_validation"]["status"] == "not_run"


def test_smoke_positioning_cli_invalid_evaluation_does_not_echo_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluation_path = tmp_path / "evaluation-packet.json"
    candidate_facts_path = tmp_path / "candidate-facts-packet.json"
    evaluation_path.write_text("{not-json}", encoding="utf-8")
    candidate_facts_path.write_text(json.dumps({"schema_version": "recruiter_candidate_facts_packet_v1"}), encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-positioning",
            "--evaluation-packet-json",
            str(evaluation_path),
            "--candidate-facts-packet-json",
            str(candidate_facts_path),
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["status"] == RecruiterPositioningSmokeStatus.INPUT_BLOCKED.value
    assert payload["errors"] == ["evaluation_packet_json_invalid"]
    assert str(evaluation_path) not in encoded


def test_smoke_application_materials_cli_provider_blocked_without_executor_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    positioning_path = tmp_path / "positioning-packet.json"
    positioning_path.write_text(json.dumps({"schema_version": "recruiter_positioning_packet_v1"}), encoding="utf-8")

    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli._run_application_materials_smoke_report",
        lambda args, repo_root: RecruiterApplicationMaterialsSmokeReport(
            schema_version="recruiter_application_materials_smoke_report_v1",
            status=RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED,
            readiness_reason="provider_execution_requires_explicit_opt_in",
            target_mode="single_target",
            document_target="recruiter_message_draft",
            required_targets=list(REQUIRED_APPLICATION_MATERIAL_TARGETS),
            target_results={"recruiter_message_draft": {"status": "provider_blocked"}},
            provider_allowed=False,
            provider_called=False,
            executor_called=False,
            reviewer_called=False,
            input_validation={
                "ready": True,
                "positioning_packet_ready": True,
                "document_target_ready": True,
                "target_mode_ready": True,
                "errors": [],
            },
            output_validation={"ready": False, "status": "not_run", "errors": []},
            document_summary=None,
            review_summary=None,
            errors=[],
            warnings=[],
            next_allowed_actions=["rerun_with_allow_provider_execution"],
            provenance={"writes_performed": False, "dry_run": True},
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            str(positioning_path),
            "--document-target",
            "recruiter_message_draft",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED.value
    assert payload["provider_called"] is False
    assert payload["executor_called"] is False
    assert payload["reviewer_called"] is False
    assert payload["output_validation"]["status"] == "not_run"


def test_smoke_application_materials_cli_invalid_positioning_does_not_echo_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    positioning_path = tmp_path / "positioning-packet.json"
    positioning_path.write_text("{not-json}", encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            str(positioning_path),
            "--document-target",
            "recruiter_message_draft",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["status"] == RecruiterApplicationMaterialsSmokeStatus.INPUT_BLOCKED.value
    assert payload["errors"] == ["positioning_packet_json_invalid"]
    assert str(positioning_path) not in encoded


def test_smoke_application_materials_cli_conflicting_target_mode_is_parseable_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    positioning_path = tmp_path / "positioning-packet.json"
    positioning_path.write_text(json.dumps({"schema_version": "recruiter_positioning_packet_v1"}), encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            str(positioning_path),
            "--all-required-targets",
            "--document-target",
            "recruiter_message_draft",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterApplicationMaterialsSmokeStatus.INPUT_BLOCKED.value
    assert payload["errors"] == ["application_materials_target_mode_conflict"]
    assert "Traceback" not in json.dumps(payload, sort_keys=True)


def test_smoke_application_materials_cli_all_required_targets_provider_blocked_without_executor_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    positioning_path = tmp_path / "positioning-packet.json"
    positioning_path.write_text(json.dumps({"schema_version": "recruiter_positioning_packet_v1"}), encoding="utf-8")

    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli._run_application_materials_smoke_report",
        lambda args, repo_root: RecruiterApplicationMaterialsSmokeReport(
            schema_version="recruiter_application_materials_smoke_report_v1",
            status=RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED,
            readiness_reason="provider_execution_requires_explicit_opt_in",
            target_mode="all_required_targets",
            document_target=None,
            required_targets=list(REQUIRED_APPLICATION_MATERIAL_TARGETS),
            target_results={target: {"status": "provider_blocked"} for target in REQUIRED_APPLICATION_MATERIAL_TARGETS},
            provider_allowed=False,
            provider_called=False,
            executor_called=False,
            reviewer_called=False,
            input_validation={
                "ready": True,
                "positioning_packet_ready": True,
                "document_target_ready": True,
                "target_mode_ready": True,
                "errors": [],
            },
            output_validation={"ready": False, "status": "not_run", "errors": []},
            document_summary=None,
            review_summary=None,
            errors=[],
            warnings=[],
            next_allowed_actions=["rerun_with_allow_provider_execution"],
            provenance={"writes_performed": False, "dry_run": True},
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-application-materials",
            "--positioning-packet-json",
            str(positioning_path),
            "--all-required-targets",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED.value
    assert payload["target_mode"] == "all_required_targets"
    assert payload["required_targets"] == list(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert sorted(payload["target_results"]) == sorted(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert payload["provider_called"] is False
    assert payload["executor_called"] is False
    assert payload["reviewer_called"] is False


def test_smoke_e2e_application_materials_cli_provider_blocked_without_executor_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluation_path = tmp_path / "evaluation-packet.json"
    candidate_facts_path = tmp_path / "candidate-facts-packet.json"
    evaluation_path.write_text(json.dumps({"schema_version": "recruiter_vacancy_evaluation_packet_v1"}), encoding="utf-8")
    candidate_facts_path.write_text(json.dumps({"schema_version": "recruiter_candidate_facts_packet_v1"}), encoding="utf-8")

    monkeypatch.setattr(
        "hermes_cli.recruiter_dry_run_cli._run_e2e_application_materials_smoke_report",
        lambda args, repo_root: RecruiterE2EApplicationMaterialsReport(
            schema_version="recruiter_e2e_application_materials_report_v1",
            status=RecruiterE2EApplicationMaterialsStatus.READY_PROVIDER_BLOCKED,
            readiness_reason="provider_execution_requires_explicit_opt_in",
            provider_allowed=False,
            provider_called=False,
            positioning_provider_called=False,
            positioning_executor_called=False,
            application_materials_provider_called=False,
            application_materials_executor_called=False,
            reviewer_called=False,
            input_validation={
                "ready": True,
                "evaluation_packet_ready": True,
                "candidate_facts_packet_ready": True,
                "errors": [],
            },
            positioning_summary={"status": RecruiterPositioningSmokeStatus.READY_PROVIDER_BLOCKED.value},
            application_materials_summary={"status": RecruiterApplicationMaterialsSmokeStatus.READY_PROVIDER_BLOCKED.value},
            required_targets=list(REQUIRED_APPLICATION_MATERIAL_TARGETS),
            target_results={target: {"status": "provider_blocked"} for target in REQUIRED_APPLICATION_MATERIAL_TARGETS},
            errors=[],
            warnings=[],
            forbidden_actions=["call_provider_model"],
            next_allowed_actions=["rerun_with_allow_provider_execution"],
            provenance={"writes_performed": False, "dry_run": True},
        ),
    )
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-e2e-application-materials",
            "--evaluation-packet-json",
            str(evaluation_path),
            "--candidate-facts-packet-json",
            str(candidate_facts_path),
            "--all-required-targets",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["schema_version"] == "recruiter_e2e_application_materials_report_v1"
    assert payload["status"] == RecruiterE2EApplicationMaterialsStatus.READY_PROVIDER_BLOCKED.value
    assert payload["provider_called"] is False
    assert payload["positioning_executor_called"] is False
    assert payload["application_materials_executor_called"] is False
    assert payload["reviewer_called"] is False
    assert payload["required_targets"] == list(REQUIRED_APPLICATION_MATERIAL_TARGETS)
    assert str(evaluation_path) not in encoded
    assert str(candidate_facts_path) not in encoded


def test_smoke_e2e_application_materials_cli_invalid_evaluation_does_not_echo_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evaluation_path = tmp_path / "evaluation-packet.json"
    candidate_facts_path = tmp_path / "candidate-facts-packet.json"
    evaluation_path.write_text("{not-json}", encoding="utf-8")
    candidate_facts_path.write_text(json.dumps({"schema_version": "recruiter_candidate_facts_packet_v1"}), encoding="utf-8")
    args = _parse_direct(
        [
            "recruiter-context",
            "smoke-e2e-application-materials",
            "--evaluation-packet-json",
            str(evaluation_path),
            "--candidate-facts-packet-json",
            str(candidate_facts_path),
            "--all-required-targets",
            "--json",
        ]
    )

    with pytest.raises(SystemExit) as exc:
        cmd_recruiter_context(args)

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["status"] == RecruiterE2EApplicationMaterialsStatus.INPUT_BLOCKED.value
    assert payload["errors"] == ["evaluation_packet_json_invalid"]
    assert str(evaluation_path) not in encoded
