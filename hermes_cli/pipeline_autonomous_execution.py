"""Autonomous engineering pipeline runtime context construction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hermes_cli.pipeline_aiagent_executor import AIAgentReviewerExecutorBridge, AIAgentSubagentExecutorBridge
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory, build_runtime_factory_plan
from hermes_cli.subagent_runner import SubagentRunner

ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
REVIEWER_SUBAGENT_ID = "hermes_code_reviewer"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
AUTONOMOUS_MODE = "autonomous"
AUTONOMOUS_WORKSPACE_ROOT = Path("/tmp/hermes-gateway-autonomous-runs")


def build_autonomous_helper_context(
    *,
    config: dict[str, Any] | None,
    user_message: str,
    session_id: str | None,
    pipeline_session_id: str | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    inferred_repo_root = Path(__file__).resolve().parent.parent
    base_repo_root = inferred_repo_root if repo_root is None else Path(repo_root)
    workspace = base_repo_root.resolve()
    runtime_context = _default_runtime_context(workspace)
    helper_context = {
        "runtime_factory": RuntimeFactory(repo_root=base_repo_root),
        "runner": SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        "user_message": user_message,
        "repo_path": str(workspace),
        "allow_completion_after_review": True,
        "controlled_runtime_context": runtime_context,
    }
    if not _allow_real_provider_execution(config):
        return helper_context

    runtime_context.update(
        allow_real_provider_execution=True,
        request_real_provider_execution=True,
        allow_mutations=True,
        allow_test_commands=True,
    )
    try:
        workspace = prepare_autonomous_workspace(
            repo_root=base_repo_root,
            workspace=workspace,
            expected_repo_root=inferred_repo_root if repo_root is None else None,
        )
    except ValueError as exc:
        runtime_context["blocked_reason"] = str(exc)
        return helper_context

    helper_context["repo_path"] = str(workspace)
    runtime_context["mutation_workspace"] = str(workspace)
    runtime_context["test_workspace"] = str(workspace)
    runtime_context["workspace_baseline_head"] = _git_stdout(workspace, "rev-parse", "HEAD")
    loaded_specs = load_pipeline_specs(repo_root=base_repo_root)
    plans = _build_bridge_runtime_plans(
        loaded_specs=loaded_specs,
        pipeline_session_id=pipeline_session_id,
        user_message=user_message,
        config=config,
    )
    runtime_context["bridge_runtime_plans"] = {key: value.to_safe_dict() for key, value in plans.items()}
    for subagent_id in (ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID):
        if plans[subagent_id].errors:
            runtime_context["blocked_reason"] = f"runtime_plan_blocked:{subagent_id}"
            return helper_context

    runtime_context["executor_bridge"] = {
        ENGINEER_SUBAGENT_ID: AIAgentSubagentExecutorBridge(workspace_root=workspace, repo_root=base_repo_root),
        REVIEWER_SUBAGENT_ID: AIAgentReviewerExecutorBridge(workspace_root=workspace, repo_root=base_repo_root),
    }
    runtime_context["real_executor_ready"] = True
    runtime_context["blocked_reason"] = None
    return helper_context


def autonomous_workspace(*, session_id: str | None, pipeline_session_id: str | None) -> Path:
    slug = pipeline_session_id or session_id or "autonomous"
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(slug)).strip("-_") or "autonomous"
    return (AUTONOMOUS_WORKSPACE_ROOT / slug).resolve()


def prepare_autonomous_workspace(*, repo_root: Path, workspace: Path, expected_repo_root: Path | None = None) -> Path:
    resolved_repo_root = repo_root.resolve()
    resolved_workspace = workspace.resolve() if workspace.exists() else workspace.resolve(strict=False)
    if resolved_workspace == resolved_repo_root:
        _validate_repo_root_workspace(repo_root=resolved_repo_root, expected_repo_root=expected_repo_root)
        return resolved_repo_root
    if workspace.exists():
        if not workspace.is_dir() or not (workspace / ".git").exists():
            raise ValueError("workspace_not_git_repo")
        _ensure_workspace_layout(workspace=workspace, repo_root=repo_root)
        return workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.name", "Hermes Autonomous Pipeline")
    _git(workspace, "config", "user.email", "hermes-autonomous@example.com")
    (workspace / ".gitignore").write_text("tests/__pycache__/\nvenv\n", encoding="utf-8")
    (workspace / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(workspace, "add", ".gitignore", "tracked.txt")
    _git(workspace, "commit", "-m", "initial")
    _ensure_workspace_layout(workspace=workspace, repo_root=repo_root)
    return workspace.resolve()


def _ensure_workspace_layout(*, workspace: Path, repo_root: Path) -> None:
    expected_venv = (repo_root / "venv").resolve(strict=False)
    workspace_venv = workspace / "venv"
    if workspace_venv.is_symlink():
        if workspace_venv.resolve(strict=False) == expected_venv:
            return
        workspace_venv.unlink()
    elif workspace_venv.exists():
        raise ValueError("workspace_venv_conflict")
    workspace_venv.symlink_to(repo_root / "venv")


def _validate_repo_root_workspace(*, repo_root: Path, expected_repo_root: Path | None) -> None:
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError("workspace_repo_missing")
    if expected_repo_root is not None and repo_root != expected_repo_root.resolve():
        raise ValueError("workspace_repo_root_mismatch")
    if not (repo_root / ".git").exists():
        raise ValueError("workspace_not_git_repo")
    try:
        _git_stdout(repo_root, "rev-parse", "--is-inside-work-tree")
        _git_stdout(repo_root, "rev-parse", "HEAD")
    except ValueError as exc:
        raise ValueError("workspace_not_git_repo") from exc
    status = _git_stdout(repo_root, "status", "--short", "--untracked-files=all")
    # The pipeline writes its own controlled_execution_report.json into the
    # workspace root after every run; that artifact must not poison the next
    # run's clean-baseline check.
    meaningful_lines = [
        line
        for line in status.splitlines()
        if line.strip() and line.split(maxsplit=1)[-1] != "controlled_execution_report.json"
    ]
    if meaningful_lines:
        raise ValueError("workspace_dirty_baseline")


def _build_bridge_runtime_plans(*, loaded_specs: Any, pipeline_session_id: str | None, user_message: str, config: dict[str, Any] | None) -> dict[str, Any]:
    session = SimpleNamespace(
        pipeline_session_id=pipeline_session_id or "autonomous",
        trace_id=pipeline_session_id or "autonomous",
        pipeline_id=ENGINEERING_PIPELINE_ID,
        user_message=user_message,
    )
    return {
        subagent_id: build_runtime_factory_plan(
            session=session,
            planned_step=SimpleNamespace(subagent_id=subagent_id, step_kind=step_kind),
            subagent_spec=loaded_specs.subagent_specs.get(subagent_id),
            config=config,
        )
        for subagent_id, step_kind in ((ENGINEER_SUBAGENT_ID, "engineer"), (REVIEWER_SUBAGENT_ID, "reviewer"))
    }


def _default_runtime_context(workspace: Path) -> dict[str, Any]:
    return {
        "real_executor_ready": False,
        "blocked_reason": "real_subagent_executor_missing",
        "allow_real_provider_execution": False,
        "request_real_provider_execution": False,
        "allowed_real_providers": (),
        "allowed_real_models": (),
        "allowed_real_providers_by_role": {},
        "allowed_real_models_by_role": {},
        "allowed_real_providers_by_subagent": {},
        "allowed_real_models_by_subagent": {},
        "allow_mutations": False,
        "mutation_workspace": str(workspace),
        "allow_test_commands": False,
        "test_workspace": str(workspace),
    }


def _allow_real_provider_execution(config: dict[str, Any] | None) -> bool:
    return bool((((config or {}).get("pipelines") or {}).get("execution") or {}).get("allow_real_provider_execution", False))


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError("workspace_git_setup_failed")


def _git_stdout(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError("workspace_git_setup_failed")
    return result.stdout.strip()
