"""Guard: autonomous engineering pipeline preflight blocks must fail closed.

When the router selects the engineering pipeline but the controller blocks
before any execution is invoked (e.g. workspace_dirty_baseline), the turn
must terminate with a controlled response instead of falling through to the
normal conversational agent with full tools.
"""
from __future__ import annotations

from types import SimpleNamespace

from gateway.run import GatewayRunner


def _runner() -> GatewayRunner:
    return object.__new__(GatewayRunner)


def _report(*, router_status: str = "selected", blocked_reason: str | None = "workspace_dirty_baseline", invoked: bool = False):
    controller = SimpleNamespace(
        actual_execution_invoked=invoked,
        subagent_execution_invoked=invoked,
        real_provider_bridge_invoked=invoked,
        blocked_reason=blocked_reason,
        final_response_text=None,
    )
    state = SimpleNamespace(router_status=router_status, pipeline_id="engineering_review_pipeline")
    return SimpleNamespace(state=state, pipeline_execution_controller=controller)


def test_preflight_block_terminates_turn_for_selected_pipeline() -> None:
    runner = _runner()
    response = runner._pipeline_autonomous_preflight_block_response(
        _report(),
        orchestrator_mode="autonomous",
    )
    assert response is not None
    assert "workspace_dirty_baseline" in response
    assert "normal_agent_fallback_blocked: true" in response


def test_preflight_guard_skips_when_execution_was_invoked() -> None:
    runner = _runner()
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(invoked=True),
            orchestrator_mode="autonomous",
        )
        is None
    )


def test_preflight_guard_skips_without_blocked_reason() -> None:
    runner = _runner()
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(blocked_reason=None),
            orchestrator_mode="autonomous",
        )
        is None
    )


def test_preflight_guard_skips_routing_failed_and_non_autonomous() -> None:
    runner = _runner()
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(router_status="routing_failed"),
            orchestrator_mode="autonomous",
        )
        is None
    )
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(),
            orchestrator_mode="controlled_manual",
        )
        is None
    )


# --- baseline-doctor additions ---------------------------------------------

import subprocess
from pathlib import Path

from hermes_cli.baseline_git import DirtyEntry
from hermes_cli.pipeline_autonomous_execution import (
    DirtyBaselineError,
    _validate_repo_root_workspace,
)


def _git(repo, *a):
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


def _seed_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("base\n")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_dirty_baseline_error_str_is_backward_compatible(tmp_path):
    repo = _seed_repo(tmp_path)
    (repo / "stray.py").write_text("x\n")
    try:
        _validate_repo_root_workspace(repo_root=repo, expected_repo_root=None)
    except DirtyBaselineError as err:
        assert str(err) == "workspace_dirty_baseline"
        assert [e.path for e in err.entries] == ["stray.py"]
        assert err.entries[0].category == "untracked"
    else:
        raise AssertionError("expected DirtyBaselineError")


def _report_with(reason, entries):
    controller = SimpleNamespace(
        blocked_reason=reason,
        blocked_dirty_entries=entries,
        actual_execution_invoked=False,
        subagent_execution_invoked=False,
        real_provider_bridge_invoked=False,
        final_response_text=None,
    )
    state = SimpleNamespace(router_status="selected", pipeline_id="engineering_review_pipeline")
    return SimpleNamespace(state=state, pipeline_execution_controller=controller)


def test_block_message_lists_dirty_files(monkeypatch):
    # The renderer classifies the working tree at render time; patch that.
    import hermes_cli.baseline_git as bg
    monkeypatch.setattr(bg, "classify_dirty", lambda repo: [
        DirtyEntry("untracked", "scripts/idle_idea_context.py"),
        DirtyEntry("root_owned", "agent/foo.pyc"),
    ])
    runner = _runner()
    report = _report_with("workspace_dirty_baseline", [])
    text = runner._pipeline_autonomous_preflight_block_response(
        report, orchestrator_mode="autonomous"
    )
    assert "dirty_files:" in text
    assert "[untracked] scripts/idle_idea_context.py" in text
    assert "[root_owned] agent/foo.pyc" in text
    assert "React 🧹 to run baseline-doctor." in text
