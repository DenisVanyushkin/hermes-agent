from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess

from hermes_cli.pipeline_aiagent_executor import AIAgentReviewerExecutorBridge, AIAgentSubagentExecutorBridge
from hermes_cli.pipeline_controlled_dry_run import (
    ENGINEER_SUBAGENT_ID,
    REVIEWER_SUBAGENT_ID,
    build_controlled_manual_helper_context,
)


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _init_git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True, text=True, capture_output=True)
    (path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True, text=True, capture_output=True)
    return path


def test_build_controlled_manual_helper_context_defaults_to_fail_closed(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_controlled_dry_run", fromlist=["unused"])
    monkeypatch.setattr(module, "GATEWAY_WORKSPACE_ROOT", tmp_path / "workspaces")

    context = build_controlled_manual_helper_context(
        user_message="HERMES CONTROLLED PIPELINE VALIDATION - default blocked",
        session_id="session-default",
        pipeline_session_id="pipe-default",
        repo_root=_copy_spec_tree(tmp_path),
    )

    controlled_context = context["controlled_runtime_context"]
    assert controlled_context["real_executor_ready"] is False
    assert controlled_context["blocked_reason"] == "real_subagent_executor_missing"
    assert controlled_context["allow_real_provider_execution"] is False
    assert "executor_bridge" not in controlled_context


def test_autonomous_context_blocks_before_bridge_construction_when_provider_gate_is_false(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["build_autonomous_helper_context"])
    monkeypatch.setattr(module, "autonomous_workspace", lambda **_kwargs: tmp_path / "workspace")
    monkeypatch.setattr(module, "AIAgentSubagentExecutorBridge", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("engineer bridge must not be built")))
    monkeypatch.setattr(module, "AIAgentReviewerExecutorBridge", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("reviewer bridge must not be built")))
    context = module.build_autonomous_helper_context(
        config={"pipelines": {"execution": {"allow_real_provider_execution": False}}},
        user_message="task",
        session_id="session",
        pipeline_session_id="pipeline",
        repo_root=tmp_path,
    )
    assert context["controlled_runtime_context"]["real_executor_ready"] is False
    assert context["controlled_runtime_context"]["allow_real_provider_execution"] is False


def test_autonomous_context_builds_engineer_and_reviewer_bridges_after_provider_gate(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["build_autonomous_helper_context"])
    repo_root = _init_git_repo(tmp_path / "repo")
    (repo_root / "config").mkdir()
    (repo_root / "prompts").mkdir()
    monkeypatch.setattr(module, "autonomous_workspace", lambda **_kwargs: tmp_path / "synthetic-workspace")
    plan = SimpleNamespace(errors=[], to_safe_dict=lambda: {"status": "ready"})
    monkeypatch.setattr(module, "_build_bridge_runtime_plans", lambda **_kwargs: {module.ENGINEER_SUBAGENT_ID: plan, module.REVIEWER_SUBAGENT_ID: plan})
    monkeypatch.setattr(module, "load_pipeline_specs", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(module, "AIAgentSubagentExecutorBridge", lambda **_kwargs: "engineer-bridge")
    monkeypatch.setattr(module, "AIAgentReviewerExecutorBridge", lambda **_kwargs: "reviewer-bridge")
    context = module.build_autonomous_helper_context(
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
        user_message="task",
        session_id="session",
        pipeline_session_id="pipeline",
        repo_root=repo_root,
    )
    runtime_context = context["controlled_runtime_context"]
    assert runtime_context["real_executor_ready"] is True
    assert context["repo_path"] == str(repo_root.resolve())
    assert runtime_context["mutation_workspace"] == str(repo_root.resolve())
    assert runtime_context["test_workspace"] == str(repo_root.resolve())
    assert runtime_context["executor_bridge"] == {
        "hermes_engineer_core": "engineer-bridge",
        "hermes_code_reviewer": "reviewer-bridge",
    }


def test_autonomous_context_fails_closed_when_repo_worktree_is_dirty(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["build_autonomous_helper_context"])
    repo_root = _init_git_repo(tmp_path / "repo")
    (repo_root / "config").mkdir()
    (repo_root / "prompts").mkdir()
    (repo_root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr(module, "autonomous_workspace", lambda **_kwargs: tmp_path / "synthetic-workspace")
    monkeypatch.setattr(module, "load_pipeline_specs", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(module, "_build_bridge_runtime_plans", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("runtime plans must not be built for dirty baseline")))
    monkeypatch.setattr(module, "AIAgentSubagentExecutorBridge", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("engineer bridge must not be built for dirty baseline")))
    monkeypatch.setattr(module, "AIAgentReviewerExecutorBridge", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("reviewer bridge must not be built for dirty baseline")))

    context = module.build_autonomous_helper_context(
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
        user_message="task",
        session_id="session",
        pipeline_session_id="pipeline",
        repo_root=repo_root,
    )

    runtime_context = context["controlled_runtime_context"]
    assert runtime_context["real_executor_ready"] is False
    assert runtime_context["blocked_reason"] == "workspace_dirty_baseline"


def test_prepare_autonomous_workspace_accepts_clean_repo_root_and_records_head(tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["prepare_autonomous_workspace"])
    repo_root = _init_git_repo(tmp_path / "repo")

    workspace = module.prepare_autonomous_workspace(repo_root=repo_root, workspace=repo_root)

    assert workspace == repo_root.resolve()


def test_prepare_autonomous_workspace_rejects_dirty_repo_root(tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["prepare_autonomous_workspace"])
    repo_root = _init_git_repo(tmp_path / "repo")
    (repo_root / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    try:
        module.prepare_autonomous_workspace(repo_root=repo_root, workspace=repo_root)
    except ValueError as exc:
        assert str(exc) == "workspace_dirty_baseline"
    else:
        raise AssertionError("expected dirty repo root to fail closed")


def test_build_controlled_manual_helper_context_builds_executor_bridge_mapping_when_gate_enabled(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_controlled_dry_run", fromlist=["unused"])
    monkeypatch.setattr(module, "GATEWAY_WORKSPACE_ROOT", tmp_path / "workspaces")

    context = build_controlled_manual_helper_context(
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
        user_message="HERMES CONTROLLED PIPELINE VALIDATION - bridge ready",
        session_id="session-ready",
        pipeline_session_id="pipe-ready",
        repo_root=_copy_spec_tree(tmp_path),
    )

    controlled_context = context["controlled_runtime_context"]
    bridge_mapping = controlled_context["executor_bridge"]
    assert controlled_context["real_executor_ready"] is True
    assert controlled_context["blocked_reason"] is None
    assert controlled_context["allow_real_provider_execution"] is True
    assert sorted(bridge_mapping) == sorted([ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID])
    assert isinstance(bridge_mapping[ENGINEER_SUBAGENT_ID], AIAgentSubagentExecutorBridge)
    assert isinstance(bridge_mapping[REVIEWER_SUBAGENT_ID], AIAgentReviewerExecutorBridge)
    assert (Path(context["repo_path"]) / ".git").exists()


def test_build_controlled_manual_helper_context_fails_closed_when_engineer_runtime_spec_is_invalid(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_controlled_dry_run", fromlist=["unused"])
    monkeypatch.setattr(module, "GATEWAY_WORKSPACE_ROOT", tmp_path / "workspaces")
    repo_root = _copy_spec_tree(tmp_path)
    engineer_spec = repo_root / "config" / "subagents" / "hermes_engineer_core.yaml"
    engineer_spec.write_text(
        engineer_spec.read_text(encoding="utf-8").replace("model: gpt-5.4\n", "model: \n"),
        encoding="utf-8",
    )

    context = build_controlled_manual_helper_context(
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
        user_message="HERMES CONTROLLED PIPELINE VALIDATION - invalid engineer runtime",
        session_id="session-engineer-invalid",
        pipeline_session_id="pipe-engineer-invalid",
        repo_root=repo_root,
    )

    controlled_context = context["controlled_runtime_context"]
    assert controlled_context["real_executor_ready"] is False
    assert controlled_context["blocked_reason"] == "runtime_plan_blocked:hermes_engineer_core"
    assert controlled_context["bridge_runtime_plans"][ENGINEER_SUBAGENT_ID]["errors"]
    assert "executor_bridge" not in controlled_context


def test_build_controlled_manual_helper_context_fails_closed_when_reviewer_runtime_spec_is_invalid(monkeypatch, tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_controlled_dry_run", fromlist=["unused"])
    monkeypatch.setattr(module, "GATEWAY_WORKSPACE_ROOT", tmp_path / "workspaces")
    repo_root = _copy_spec_tree(tmp_path)
    reviewer_spec = repo_root / "config" / "subagents" / "hermes_code_reviewer.yaml"
    reviewer_spec.write_text(
        reviewer_spec.read_text(encoding="utf-8").replace("path: prompts/subagents/hermes_code_reviewer.md", "path: "),
        encoding="utf-8",
    )

    context = build_controlled_manual_helper_context(
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
        user_message="HERMES CONTROLLED PIPELINE VALIDATION - invalid reviewer runtime",
        session_id="session-reviewer-invalid",
        pipeline_session_id="pipe-reviewer-invalid",
        repo_root=repo_root,
    )

    controlled_context = context["controlled_runtime_context"]
    assert controlled_context["real_executor_ready"] is False
    assert controlled_context["blocked_reason"] == "runtime_plan_blocked:hermes_code_reviewer"
    assert controlled_context["bridge_runtime_plans"][REVIEWER_SUBAGENT_ID]["errors"]
    assert "executor_bridge" not in controlled_context


def test_prepare_autonomous_workspace_ignores_own_report_artifact(tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["prepare_autonomous_workspace"])
    repo_root = _init_git_repo(tmp_path / "repo")
    (repo_root / "controlled_execution_report.json").write_text("{}\n", encoding="utf-8")

    workspace = module.prepare_autonomous_workspace(repo_root=repo_root, workspace=repo_root)

    assert workspace == repo_root.resolve()


def test_prepare_autonomous_workspace_still_rejects_tracked_dirt_alongside_report(tmp_path: Path) -> None:
    module = __import__("hermes_cli.pipeline_autonomous_execution", fromlist=["prepare_autonomous_workspace"])
    repo_root = _init_git_repo(tmp_path / "repo")
    (repo_root / "controlled_execution_report.json").write_text("{}\n", encoding="utf-8")
    (repo_root / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    try:
        module.prepare_autonomous_workspace(repo_root=repo_root, workspace=repo_root)
    except ValueError as exc:
        assert str(exc) == "workspace_dirty_baseline"
    else:
        raise AssertionError("expected dirty repo root to fail closed")
