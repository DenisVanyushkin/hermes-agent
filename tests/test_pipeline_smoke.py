from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import pipeline_smoke


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")
PYTHON = REPO_ROOT / "venv/bin/python"


def _run_smoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-m", "hermes_cli.pipeline_smoke", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_default_scenario_is_fake_approval() -> None:
    result = _run_smoke()

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["scenario"] == "approval"
    assert payload["status"] == "completed"
    assert payload["candidate_complete"] is True
    assert payload["completion_allowed"] is False
    assert payload["blocked_reason"] == "loop_harness_not_live_final"
    assert payload["runner_call_order"] == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]
    assert payload["report"]["safety"]["prompts_redacted"] is True
    assert payload["report"]["safety"]["environment_redacted"] is True


def test_blocker_then_approval_preserves_append_only_rework_context() -> None:
    result = _run_smoke("--scenario", "blocker_then_approval")

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["scenario"] == "blocker_then_approval"
    assert payload["review_iterations_completed"] == 2
    assert payload["runner_call_order"] == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]
    assert payload["appended_rework_context"] == [
        "Reviewer blockers after iteration 1: missing regression test",
    ]


def test_loop_limit_exceeded_returns_user_action_required() -> None:
    result = _run_smoke("--scenario", "loop_limit_exceeded")

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["scenario"] == "loop_limit_exceeded"
    assert payload["user_action_required"] is True
    assert payload["blocked_reason"] == "review_loop_limit_exceeded"
    assert payload["candidate_complete"] is False


def test_invalid_reviewer_fails_closed() -> None:
    result = _run_smoke("--scenario", "invalid_reviewer")

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["scenario"] == "invalid_reviewer"
    assert payload["user_action_required"] is True
    assert payload["blocked_reason"] == "reviewer_result_invalid"
    assert payload["iteration_history"][0]["reviewer_evaluation_status"] == "invalid_structured_output"


def test_fake_mode_never_reports_tool_or_file_mutation_execution() -> None:
    result = _run_smoke("--scenario", "approval")

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["fuse"]["tools_allowed"] is False
    assert payload["fuse"]["file_mutation_allowed"] is False
    assert payload["fuse"]["model_escalation_allowed"] is False
    assert payload["report"]["usage"]["tool_calls"] == 0


def test_real_runner_mode_fails_closed_without_real_execution() -> None:
    result = _run_smoke("--runner-mode", "real")

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "real_runner_mode_unsupported"
    assert payload["runner_call_order"] == []


def test_output_does_not_echo_secret_like_task_text() -> None:
    result = _run_smoke("--task", "token=topsecret please patch this narrowly")

    assert result.returncode == 0
    assert "topsecret" not in result.stdout
    payload = _json_output(result)
    assert payload["scenario"] == "approval"


def test_controlled_engineering_e2e_manual_dry_run_writes_safe_report(tmp_path: Path) -> None:
    workspace = tmp_path / "manual-controlled-e2e"
    report_out = tmp_path / "report.json"

    result = _run_smoke(
        "--scenario",
        "controlled-engineering-e2e",
        "--workspace",
        str(workspace),
        "--report-out",
        str(report_out),
    )

    assert result.returncode == 0
    payload = _json_output(result)
    written_payload = json.loads(report_out.read_text(encoding="utf-8"))

    assert payload == written_payload
    assert payload["scenario"] == "controlled-engineering-e2e"
    assert payload["status"] == "completed"
    assert payload["completion_allowed"] is True
    assert payload["blocked_reason"] is None
    assert payload["reviewer_approved"] is True
    assert payload["mutation_summary"]["applied_count"] == 1
    assert payload["test_summary"]["status"] == "passed"
    assert payload["git_gate"]["changed_files"] == ["tests/test_generated_example.py"]
    assert payload["report"]["completion"]["blocked_reason"] is None
    assert payload["report"]["review"]["blocked_reason"] is None
    assert payload["report"]["final_response"]["placeholder_reason"] is None
    assert payload["report"]["git_gate"]["completion_blocked_reason"] is None
    assert workspace.joinpath("tests/test_generated_example.py").exists()
    encoded = json.dumps(payload, sort_keys=True)
    assert str(workspace) not in encoded
    assert "engineer runtime completed" not in encoded
    assert "reviewer runtime approved" not in encoded
    assert "def test_generated_example" not in encoded


def test_controlled_engineering_e2e_unknown_scenario_fails_safely() -> None:
    result = _run_smoke("--scenario", "unknown-scenario")

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_controlled_engineering_e2e_requires_explicit_workspace() -> None:
    result = _run_smoke("--scenario", "controlled-engineering-e2e")

    assert result.returncode == 0
    payload = _json_output(result)
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "workspace_required"
    assert payload["runner_call_order"] == []


def test_controlled_engineering_e2e_run_smoke_scenario_uses_fake_provider_factory_only(tmp_path: Path) -> None:
    workspace = tmp_path / "manual-controlled-e2e"
    payload = pipeline_smoke.run_smoke_scenario(
        scenario="controlled-engineering-e2e",
        runner_mode="fake",
        workspace=workspace,
    )

    assert payload["provider_execution_mode"] == "fake_real_provider_client"
    assert payload["network_access"] == "disabled"
    assert payload["sdk_import_mode"] == "not_used"


def test_controlled_engineering_e2e_rejects_repo_root_workspace() -> None:
    payload = pipeline_smoke.run_smoke_scenario(
        scenario="controlled-engineering-e2e",
        runner_mode="fake",
        workspace=REPO_ROOT,
    )

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "workspace_matches_repo_root"
