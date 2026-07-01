from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli._parser import build_top_level_parser
from hermes_cli.recruiter_dry_run import RecruiterDryRunReport, RecruiterDryRunStatus
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

    def _fake_run(*, evaluation_packet, repo_root, private_context_status, allow_provider_execution):
        captured["evaluation_packet"] = evaluation_packet
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
