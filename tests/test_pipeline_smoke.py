from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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
