from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hermes_cli.profile_approval import classify_engineer_approval
from hermes_cli.profile_handoff import (
    HandoffError,
    build_scribe_handoff,
    decision_to_dict,
    preview_scribe_handoff,
    render_handoff_markdown,
    write_scribe_handoff,
)
from hermes_cli.profile_routing import route_task


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "preview_scribe_handoff.py"


def _deploy_route_and_approval():
    task = "Deploy WebUI and document the result"
    route_decision = route_task(task)
    approval_preview = classify_engineer_approval(task, route_decision=route_decision)
    return task, route_decision, approval_preview


def _read_json_output(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_preview_mode_writes_nothing(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    result = preview_scribe_handoff(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=tmp_path,
        write=False,
    )

    assert result.write_performed is False
    assert result.write_verified is False
    assert result.handoff.scribe_status == "handoff_incomplete"
    assert result.handoff.scribe_failure_reason == "hook_skipped"
    assert not any(tmp_path.rglob("*.md"))


def test_write_mode_creates_artifact_under_allowed_output_root(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    result = write_scribe_handoff(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=tmp_path,
        timestamp_utc="2026-06-08T14:22:33Z",
        task_id="deploy-webui-doc-result",
        decisions=["Proceed with deployment after approval"],
        evidence=["route preview confirmed engineer ownership"],
    )

    artifact_path = Path(result.artifact_path)
    assert result.write_performed is True
    assert result.write_verified is True
    assert result.handoff.scribe_status == "complete"
    assert artifact_path.exists()
    assert artifact_path.read_text(encoding="utf-8").startswith("---\n")
    assert artifact_path.is_relative_to(tmp_path)


def test_real_tests_use_tmp_path_not_repo_docs(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    result = preview_scribe_handoff(task, route_decision=route_decision, approval_preview=approval_preview, output_root=tmp_path)
    assert result.artifact_path.startswith(str(tmp_path))
    assert not Path(result.artifact_path).exists()


def test_path_allowlist_rejects_symlink_escape_outside_root(tmp_path: Path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (docs_root / "profile-handoffs").symlink_to(outside, target_is_directory=True)

    task, route_decision, approval_preview = _deploy_route_and_approval()
    result = write_scribe_handoff(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=docs_root,
        timestamp_utc="2026-06-08T14:22:33Z",
    )

    assert result.write_verified is False
    assert result.handoff.scribe_status == "handoff_incomplete"
    assert result.handoff.scribe_failure_reason == "write_failed"


def test_existing_artifact_is_not_overwritten_silently(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    first = write_scribe_handoff(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=tmp_path,
        timestamp_utc="2026-06-08T14:22:33Z",
        task_id="same-id",
    )
    second = write_scribe_handoff(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=tmp_path,
        timestamp_utc="2026-06-08T14:22:33Z",
        task_id="same-id",
    )

    assert first.write_verified is True
    assert second.write_verified is False
    assert second.handoff.scribe_status == "handoff_incomplete"
    assert second.handoff.scribe_failure_reason == "write_failed"


def test_route_and_approval_metadata_are_serialized(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    result = preview_scribe_handoff(task, route_decision=route_decision, approval_preview=approval_preview, output_root=tmp_path)
    payload = decision_to_dict(result.handoff)

    assert payload["route_decision"]["primary_profile"] == "engineer"
    assert payload["approval_preview"]["requires_approval"] is True
    assert payload["from_profile"] == "engineer"
    assert payload["to_profile"] == "scribe"


def test_approval_required_task_can_produce_complete_handoff_with_blocked_task_execution(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    result = write_scribe_handoff(task, route_decision=route_decision, approval_preview=approval_preview, output_root=tmp_path)

    assert result.handoff.task_execution_status == "blocked_pending_approval"
    assert result.handoff.scribe_status == "complete"
    assert result.handoff.scribe_failure_reason is None


def test_write_failure_produces_handoff_incomplete(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    output_root = tmp_path / "docs-root-file"
    output_root.write_text("not a directory", encoding="utf-8")
    result = write_scribe_handoff(task, route_decision=route_decision, approval_preview=approval_preview, output_root=output_root)

    assert result.handoff.scribe_status == "handoff_incomplete"
    assert result.handoff.scribe_failure_reason in {"path_missing", "write_failed"}


def test_no_update_required_requires_rationale():
    with pytest.raises(HandoffError):
        build_scribe_handoff("Check WebUI status and inspect logs", no_update_required=True)


def test_no_update_required_complete_handoff_still_writes_rationale(tmp_path: Path):
    task = "Check WebUI status and inspect logs"
    route_decision = route_task(task)
    approval_preview = classify_engineer_approval(task, route_decision=route_decision)
    result = write_scribe_handoff(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=tmp_path,
        no_update_required=True,
        no_update_rationale="Read-only inspection; no durable state change.",
        task_execution_status="no_update_required",
        timestamp_utc="2026-06-08T14:25:00Z",
    )

    assert result.handoff.scribe_status == "complete"
    assert result.handoff.task_execution_status == "no_update_required"
    assert result.handoff.no_update_required is True
    assert "Read-only inspection; no durable state change." in result.markdown


def test_markdown_contains_required_metadata_and_sections(tmp_path: Path):
    task, route_decision, approval_preview = _deploy_route_and_approval()
    handoff = build_scribe_handoff(task, route_decision=route_decision, approval_preview=approval_preview, timestamp_utc="2026-06-08T14:22:33Z")
    markdown = render_handoff_markdown(handoff)

    for section in [
        "## Summary",
        "## Route Decision",
        "## Approval Preview",
        "## Evidence",
        "## Changed State",
        "## Changed Files",
        "## Decisions",
        "## Open Follow-ups",
        "## Scribe Status",
    ]:
        assert section in markdown
    for field in [
        "schema_version: 1",
        "task_id:",
        "timestamp_utc:",
        "from_profile:",
        "to_profile:",
        "scribe_status:",
        "task_execution_status:",
        "no_update_required:",
    ]:
        assert field in markdown


def test_import_boundary_does_not_pull_runtime_modules():
    code = textwrap.dedent(
        r'''
        import sys
        import hermes_cli.profile_handoff  # noqa: F401
        forbidden = [name for name in sys.modules if name.startswith(('agent', 'gateway', 'cron.scheduler', 'run_agent'))]
        print('\n'.join(sorted(forbidden)))
        '''
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    forbidden = [line for line in result.stdout.splitlines() if line.strip()]
    assert forbidden == []


def test_cli_preview_prints_json(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task",
            "Deploy WebUI and document the result",
            "--json",
            "--output-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    payload = _read_json_output(result)
    assert payload["write_performed"] is False
    assert payload["handoff"]["task_summary"] == "Deploy WebUI and document the result"
    assert payload["handoff"]["scribe_failure_reason"] == "hook_skipped"


def test_cli_write_prints_artifact_path_and_json_summary(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task",
            "Check WebUI status and inspect logs",
            "--json",
            "--write",
            "--no-update-required",
            "--no-update-rationale",
            "Read-only inspection; no durable state change.",
            "--output-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    payload = _read_json_output(result)
    assert payload["write_performed"] is True
    assert payload["write_verified"] is True
    assert Path(payload["artifact_path"]).exists()
    assert payload["handoff"]["task_execution_status"] == "no_update_required"


def test_invalid_input_exits_non_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--task", "   ", "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "must not be empty" in result.stderr
