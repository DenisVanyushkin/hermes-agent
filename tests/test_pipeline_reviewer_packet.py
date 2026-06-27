from __future__ import annotations

import importlib
import json
import subprocess

import pytest

from hermes_cli.pipeline_git_delta import GitMaterialChangeResult, GitSnapshot


def _snapshot(
    *,
    head_sha: str = "abc123",
    repo_path: str = "/tmp/repo",
    dirty: bool = False,
    capture_status: str = "captured",
    untracked_files: tuple[str, ...] = (),
    staged_files: tuple[str, ...] = (),
    unstaged_files: tuple[str, ...] = (),
) -> GitSnapshot:
    return GitSnapshot(
        repo_path=repo_path,
        head_sha=head_sha,
        branch="main",
        status_porcelain=(),
        tracked_changed_files=tuple(sorted(set(staged_files) | set(unstaged_files))),
        untracked_files=untracked_files,
        staged_files=staged_files,
        unstaged_files=unstaged_files,
        is_dirty=dirty,
        capture_status=capture_status,
        error_type=None if capture_status == "captured" else capture_status,
        error_message_redacted=None,
    )


def _git_result(
    *,
    status: str = "material_changes_detected",
    material_changes_present: bool = True,
    review_required: bool = True,
    changed_files: list[str] | None = None,
    untracked_files: list[str] | None = None,
    staged_files: list[str] | None = None,
    unstaged_files: list[str] | None = None,
    baseline_head_sha: str | None = "abc123",
    post_head_sha: str | None = "def456",
    head_changed: bool = True,
    blocked_reason: str | None = "material_changes_detected",
    baseline_dirty: bool = False,
    safe_summary: str | None = "Material repository changes were detected.",
) -> GitMaterialChangeResult:
    return GitMaterialChangeResult(
        status=status,
        material_changes_present=material_changes_present,
        review_required=review_required,
        changed_files=changed_files or [],
        untracked_files=untracked_files or [],
        staged_files=staged_files or [],
        unstaged_files=unstaged_files or [],
        baseline_head_sha=baseline_head_sha,
        post_head_sha=post_head_sha,
        head_changed=head_changed,
        blocked_reason=blocked_reason,
        baseline_dirty=baseline_dirty,
        safe_summary=safe_summary,
    )


def _engineer_output(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "succeeded",
        "summary": "Prepared a focused patch for reviewer handoff.",
        "requires_review": False,
        "validation_status": "valid",
        "blockers": [],
        "changes": [
            {"path": "b.py", "kind": "modify"},
            {"path": "a.py", "kind": "modify"},
        ],
        "artifacts": [
            {"artifact_id": "patch-1", "kind": "diff", "path": "/tmp/patch.diff", "redacted": True},
            {"artifact_id": "log-1", "kind": "log", "path": "/tmp/run.log", "redacted": False},
        ],
    }
    payload.update(overrides)
    return payload


def test_material_changes_build_ready_for_review_packet() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        session_id="sess-1",
        task_summary="Implement reviewer packet builder",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(
            head_sha="def456",
            untracked_files=("z.txt", "a.txt"),
            staged_files=("b.py",),
            unstaged_files=("c.py",),
        ),
        git_result=_git_result(
            changed_files=["c.py", "a.txt", "b.py"],
            untracked_files=["z.txt", "a.txt"],
            staged_files=["b.py"],
            unstaged_files=["c.py"],
        ),
        test_summary={"status": "failed", "command": "pytest -q", "summary": "2 failed"},
        risk_flags=["needs_reviewer", "tests_failed"],
        artifacts=[{"artifact_id": "report-1", "kind": "report", "path": "/tmp/report.json"}],
    )

    payload = packet.to_safe_dict()

    assert packet.packet_status == "ready_for_review"
    assert packet.review_required is True
    assert packet.completion_allowed_without_review is False
    assert packet.user_action_required is False
    assert payload["git"]["changed_files"] == ["a.txt", "b.py", "c.py"]
    assert payload["git"]["untracked_files"] == ["a.txt", "z.txt"]
    assert payload["git"]["review_reason"] == "material_changes_detected"
    assert payload["tests"]["status"] == "failed"


def test_no_material_changes_marks_review_not_required() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        session_id=None,
        task_summary="No-op verification",
        engineer_output=_engineer_output(summary="No code changes required."),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="abc123"),
        git_result=_git_result(
            status="no_material_changes",
            material_changes_present=False,
            review_required=False,
            changed_files=[],
            untracked_files=[],
            staged_files=[],
            unstaged_files=[],
            baseline_head_sha="abc123",
            post_head_sha="abc123",
            head_changed=False,
            blocked_reason=None,
            baseline_dirty=False,
            safe_summary="No material repository changes were detected.",
        ),
        test_summary={"status": "passed", "command": "pytest -q", "summary": "12 passed"},
    )

    assert packet.packet_status == "review_not_required"
    assert packet.review_required is False
    assert packet.completion_allowed_without_review is True
    assert packet.user_action_required is False


def test_dirty_baseline_blocks_completion_and_requires_user_action() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Investigate dirty baseline",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123", dirty=True, unstaged_files=("dirty.py",)),
        post_snapshot=_snapshot(head_sha="abc123", dirty=True, unstaged_files=("dirty.py",)),
        git_result=_git_result(
            status="baseline_invalid",
            material_changes_present=False,
            review_required=True,
            changed_files=[],
            unstaged_files=[],
            head_changed=False,
            blocked_reason="baseline_dirty",
            baseline_dirty=True,
            safe_summary="Baseline snapshot was already dirty.",
        ),
    )

    assert packet.packet_status == "blocked"
    assert packet.review_required is True
    assert packet.completion_allowed_without_review is False
    assert packet.user_action_required is True
    assert packet.blocked_reason == "baseline_dirty"


def test_invalid_git_result_blocks_and_fails_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Git unavailable",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123", capture_status="captured"),
        post_snapshot=_snapshot(head_sha="abc123", capture_status="git_unavailable"),
        git_result=_git_result(
            status="git_unavailable",
            material_changes_present=False,
            review_required=True,
            changed_files=[],
            head_changed=False,
            blocked_reason="git_unavailable",
            safe_summary="Git commands could not be completed.",
        ),
    )

    assert packet.packet_status == "blocked"
    assert packet.review_required is True
    assert packet.user_action_required is True
    assert packet.blocked_reason == "git_unavailable"


def test_safe_dict_is_json_serializable_and_redacts_diff_like_content() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="User prompt with diff\n+++ secret.env\nAPI_KEY=123",
        engineer_output=_engineer_output(summary="Applied patch\n@@\n+++ prod.env"),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("b.py",)),
        git_result=_git_result(changed_files=["b.py"], staged_files=["b.py"]),
        test_summary={
            "status": "failed",
            "command": "pytest -q",
            "summary": "Traceback:\n+++ prod.env\npassword=123",
            "stderr": "secret output",
        },
    )

    encoded = json.dumps(packet.to_safe_dict(), sort_keys=True)

    assert json.loads(encoded)["pipeline_id"] == "engineering_review_pipeline"
    assert "password=123" not in encoded
    assert "API_KEY=123" not in encoded
    assert "secret output" not in encoded
    assert "+++ prod.env" not in encoded
    assert "@@" not in encoded


def test_invalid_test_summary_status_is_preserved_for_reviewer_forensics() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Malformed test payload should reach reviewer as warning",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("b.py",)),
        git_result=_git_result(changed_files=["b.py"], staged_files=["b.py"]),
        test_summary={
            "status": "invalid",
            "summary": "test evidence unavailable: malformed_test_payload",
            "results": [
                {
                    "command": ["[invalid]"],
                    "status": "invalid",
                    "cwd": "repo",
                    "denied_command_raw_sanitized": "{status: observed, summary: workspace only contains tracked.txt}",
                }
            ],
        },
    )

    payload = packet.to_safe_dict()

    assert payload["tests"]["status"] == "invalid"
    assert payload["tests"]["results"][0]["status"] == "invalid"
    assert payload["tests"]["results"][0]["denied_command_raw_sanitized"] == "{status: observed, summary: workspace only contains tracked.txt}"


def test_long_text_is_bounded() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")
    long_text = "x" * 5000

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary=long_text,
        engineer_output=_engineer_output(summary=long_text),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("b.py",)),
        git_result=_git_result(changed_files=["b.py"], staged_files=["b.py"]),
        test_summary={"status": "passed", "command": "pytest -q", "summary": long_text},
    )

    payload = packet.to_safe_dict()

    assert len(payload["task_summary"]) <= 2000
    assert len(payload["engineer_summary"]) <= 2000
    assert len(payload["tests"]["summary"]) <= 2000


@pytest.mark.parametrize(
    ("input_summary", "expected_status"),
    [
        ({"status": "passed"}, "passed"),
        ({"status": "failed"}, "failed"),
        ({"status": "timeout"}, "timeout"),
        ({"status": "not_run"}, "not_run"),
        ({"status": "not_requested"}, "not_requested"),
        ({"status": "requested_not_executed"}, "requested_not_executed"),
        ({}, "unknown"),
        (None, "unknown"),
    ],
)
def test_test_summary_statuses_normalize_correctly(input_summary: dict[str, object] | None, expected_status: str) -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    normalized = module.normalize_test_summary(input_summary)

    assert normalized["status"] == expected_status


def test_timeout_test_summary_is_preserved_in_reviewer_packet() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Timeout test evidence",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456"),
        git_result=_git_result(),
        test_summary={
            "status": "timeout",
            "command": "venv/bin/pytest -q tests/test_smoke_square.py",
            "summary": "timed out after 30s",
            "source": "allowed_tool",
            "results": [
                {
                    "command": ["venv/bin/pytest", "-q", "tests/test_smoke_square.py"],
                    "status": "timeout",
                    "cwd": "hermes-agent",
                    "stdout_excerpt": "collecting...",
                    "stderr_excerpt": "",
                }
            ],
        },
    )

    payload = packet.to_safe_dict()
    assert payload["tests"]["status"] == "timeout"
    assert payload["tests"]["source"] == "allowed_tool"


def test_invalid_engineer_output_blocks_and_requires_review() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Engineer output invalid",
        engineer_output=_engineer_output(status="failed", validation_status="invalid_structured_output"),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="abc123"),
        git_result=_git_result(
            status="no_material_changes",
            material_changes_present=False,
            review_required=False,
            changed_files=[],
            staged_files=[],
            unstaged_files=[],
            baseline_head_sha="abc123",
            post_head_sha="abc123",
            head_changed=False,
            blocked_reason=None,
            safe_summary="No material repository changes were detected.",
        ),
    )

    assert packet.packet_status == "blocked"
    assert packet.review_required is True
    assert packet.user_action_required is True
    assert packet.engineer_status == "failed"


def test_material_changes_with_invalid_engineer_output_still_build_ready_for_review_packet() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Engineer output invalid but repo changed",
        engineer_output=_engineer_output(
            status="failed",
            summary="plain text diagnostic summary",
            validation_status="invalid_structured_output",
            validation_errors=[{"field": "status", "message": "missing required enum"}],
            changes=[{"path": "docs/reports/smoke/autonomous-workspace-011.md", "kind": "modify"}],
        ),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("docs/reports/smoke/autonomous-workspace-011.md",)),
        git_result=_git_result(
            changed_files=["docs/reports/smoke/autonomous-workspace-011.md"],
            staged_files=["docs/reports/smoke/autonomous-workspace-011.md"],
        ),
        engineer_evaluation_status="invalid_structured_output",
        test_summary={"status": "invalid", "summary": "malformed test evidence"},
    )

    payload = packet.to_safe_dict()

    assert packet.packet_status == "ready_for_review"
    assert packet.review_required is True
    assert packet.user_action_required is False
    assert packet.blocked_reason is None
    assert payload["engineer_output_valid"] is False
    assert payload["engineer_output_validation_status"] == "invalid_structured_output"
    assert payload["engineer_output_evaluation_status"] == "invalid_structured_output"
    assert payload["engineer_output_warning"] is not None
    assert payload["engineer_validation_errors"] == [{"field": "status", "message": "missing required enum"}]
    assert payload["engineer_sanitized_output"]["summary"] == "plain text diagnostic summary"
    assert payload["engineer_sanitized_output"]["changes"] == [
        {"path": "docs/reports/smoke/autonomous-workspace-011.md", "kind": "modify"}
    ]
    assert payload["tests"]["status"] == "invalid"


def test_missing_structured_output_gets_explicit_block_reason() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Engineer output missing structured payload",
        engineer_output=_engineer_output(status="failed", validation_status="missing_structured_output"),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="abc123"),
        git_result=_git_result(
            status="no_material_changes",
            material_changes_present=False,
            review_required=False,
            changed_files=[],
            staged_files=[],
            unstaged_files=[],
            baseline_head_sha="abc123",
            post_head_sha="abc123",
            head_changed=False,
            blocked_reason=None,
            safe_summary="No material repository changes were detected.",
        ),
        test_summary={"status": "not_requested", "results": []},
    )

    assert packet.blocked_reason == "invalid_engineer_output"
    assert packet.blocked_reason_detail == "missing_structured_output"
    assert packet.tests["status"] == "not_requested"


def test_max_iterations_plain_text_missing_structured_output_gets_precise_block_reason_detail() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Engineer hit max iterations without structured output",
        engineer_output=_engineer_output(
            status="failed",
            summary="plain text diagnostic summary",
            validation_status="missing_structured_output",
            validation_errors=[
                {"field": "payload", "message": "engineer_max_iterations_without_structured_output"}
            ],
        ),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="abc123"),
        git_result=_git_result(
            status="no_material_changes",
            material_changes_present=False,
            review_required=False,
            changed_files=[],
            staged_files=[],
            unstaged_files=[],
            baseline_head_sha="abc123",
            post_head_sha="abc123",
            head_changed=False,
            blocked_reason=None,
            safe_summary="No material repository changes were detected.",
        ),
        test_summary={"status": "not_requested", "results": []},
    )

    assert packet.blocked_reason == "invalid_engineer_output"
    assert packet.blocked_reason_detail == "engineer_max_iterations_without_structured_output"
    assert packet.engineer_summary == "plain text diagnostic summary"


def test_module_does_not_run_git_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")
    seen: list[list[str]] = []

    def _unexpected_run(*args, **kwargs):
        seen.append(list(args[0]) if args else [])
        raise AssertionError("pipeline_reviewer_packet must not invoke subprocesses")

    monkeypatch.setattr(subprocess, "run", _unexpected_run)

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="No subprocess",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("b.py",)),
        git_result=_git_result(changed_files=["b.py"], staged_files=["b.py"]),
    )

    assert packet.review_required is True
    assert seen == []


def test_test_summary_results_are_sanitized_and_path_safe() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Include safe test evidence",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("b.py",)),
        git_result=_git_result(changed_files=["b.py"], staged_files=["b.py"]),
        test_summary={
            "status": "failed",
            "summary": "1 failed",
            "results": [
                {
                    "command": ["venv/bin/pytest", "-q", "tests/test_example.py"],
                    "status": "failed",
                    "stdout_excerpt": "password=123\nFAILED tests/test_example.py",
                    "stderr_excerpt": "",
                    "cwd": "tmp-worktree",
                }
            ],
        },
    )

    payload = packet.to_safe_dict()

    assert payload["tests"]["status"] == "failed"
    assert payload["tests"]["results"][0]["command"] == ["venv/bin/pytest", "-q", "tests/test_example.py"]
    assert payload["tests"]["results"][0]["cwd"] == "tmp-worktree"
    assert "password=123" not in json.dumps(payload["tests"], sort_keys=True)


def test_requested_not_executed_test_summary_is_preserved_with_command() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Preserve explicit pytest request when execution evidence is missing",
        engineer_output=_engineer_output(
            status="failed",
            summary="plain text diagnostic summary",
            validation_status="invalid_structured_output",
        ),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456", staged_files=("b.py",)),
        git_result=_git_result(changed_files=["b.py"], staged_files=["b.py"]),
        engineer_evaluation_status="invalid_structured_output",
        test_summary={
            "status": "requested_not_executed",
            "command": "venv/bin/pytest -q tests/test_smoke_square.py",
            "summary": "requested test command was preserved but not executed",
        },
    )

    payload = packet.to_safe_dict()

    assert payload["tests"]["status"] == "requested_not_executed"
    assert payload["tests"]["command"] == "venv/bin/pytest -q tests/test_smoke_square.py"
    assert "preserved but not executed" in (payload["tests"]["summary"] or "")


def test_executed_test_summary_preserves_exit_code_source_and_results() -> None:
    module = importlib.import_module("hermes_cli.pipeline_reviewer_packet")

    packet = module.build_reviewer_packet(
        pipeline_id="engineering_review_pipeline",
        task_summary="Preserve executed test evidence",
        engineer_output=_engineer_output(),
        baseline_snapshot=_snapshot(head_sha="abc123"),
        post_snapshot=_snapshot(head_sha="def456"),
        git_result=_git_result(),
        test_summary={
            "status": "passed",
            "command": "venv/bin/pytest -q tests/test_smoke_square.py",
            "exit_code": 0,
            "summary": "5 passed",
            "source": "allowed_tool",
            "results": [
                {
                    "command": ["venv/bin/pytest", "-q", "tests/test_smoke_square.py"],
                    "status": "passed",
                    "exit_code": 0,
                    "cwd": "hermes-agent",
                    "stdout_excerpt": ".....\n5 passed\n",
                    "stderr_excerpt": "",
                }
            ],
        },
    )

    payload = packet.to_safe_dict()

    assert payload["tests"]["status"] == "passed"
    assert payload["tests"]["command"] == "venv/bin/pytest -q tests/test_smoke_square.py"
    assert payload["tests"]["exit_code"] == 0
    assert payload["tests"]["summary"] == "5 passed"
    assert payload["tests"]["source"] == "allowed_tool"
    assert payload["tests"]["results"][0]["exit_code"] == 0
