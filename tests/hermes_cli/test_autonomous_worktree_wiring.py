"""Slice 2: the autonomous run actually happens in its per-run worktree.

Slice 1 added the capability; these tests pin the wiring. The behaviour change
they encode is deliberate and is the whole point of the redesign: a dirty repo
root no longer decides whether the engineering pipeline may run, and the
operator's working tree is never stashed or reverted underneath them.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import pipeline_autonomous_execution as pae
from hermes_cli.baseline_git import classify_dirty

CONFIG = {"pipelines": {"execution": {"allow_real_provider_execution": True}}}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    # Configured on the repo, not per-command: linked worktrees inherit it, and
    # the commit gate commits inside the worktree.
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.py").write_text("x = 1\n")
    (root / "venv").mkdir()
    _git(root, "add", "a.py")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def build(repo: Path, tmp_path: Path, monkeypatch):
    """build_autonomous_helper_context against a throwaway repo."""
    monkeypatch.setattr(
        pae, "autonomous_workspace", lambda **_kw: tmp_path / "runs" / "run1"
    )
    # Everything downstream of workspace preparation needs the real config tree
    # and provider bridges; this slice is about the workspace, so stub them out.
    plan = SimpleNamespace(errors=[], to_safe_dict=lambda: {})
    monkeypatch.setattr(pae, "load_pipeline_specs", lambda **_kw: object())
    monkeypatch.setattr(
        pae,
        "_build_bridge_runtime_plans",
        lambda **_kw: {pae.ENGINEER_SUBAGENT_ID: plan, pae.REVIEWER_SUBAGENT_ID: plan},
    )
    monkeypatch.setattr(pae, "AIAgentSubagentExecutorBridge", lambda **_kw: object())
    monkeypatch.setattr(pae, "AIAgentReviewerExecutorBridge", lambda **_kw: object())

    def _build():
        return pae.build_autonomous_helper_context(
            config=CONFIG,
            user_message="task",
            session_id="session",
            pipeline_session_id="run1",
            repo_root=repo,
        )

    return _build


def test_run_happens_in_a_worktree_not_in_the_repo_root(build, repo: Path):
    ctx = build()
    workspace = Path(ctx["repo_path"])
    assert workspace != repo.resolve()
    assert (workspace / ".git").exists()
    assert classify_dirty(workspace) == []


def test_dirty_repo_root_no_longer_vetoes_the_run(build, repo: Path):
    """The behaviour this whole redesign replaces."""
    (repo / "a.py").write_text("x = 2\n")
    (repo / "junk.txt").write_text("untracked\n")

    ctx = build()
    runtime = ctx["controlled_runtime_context"]

    assert runtime["blocked_reason"] is None
    assert runtime["real_executor_ready"] is True
    workspace = Path(ctx["repo_path"])
    assert classify_dirty(workspace) == []
    assert (workspace / "a.py").read_text() == "x = 1\n"


def test_the_operators_working_tree_is_never_touched(build, repo: Path):
    """Today's auto-heal stashes their edits and reverts the file. This must not."""
    (repo / "a.py").write_text("x = 2\n")

    build()

    assert (repo / "a.py").read_text() == "x = 2\n"
    assert _git(repo, "stash", "list") == ""


def test_mutation_and_test_workspaces_follow_the_worktree(build):
    ctx = build()
    runtime = ctx["controlled_runtime_context"]
    assert runtime["mutation_workspace"] == ctx["repo_path"]
    assert runtime["test_workspace"] == ctx["repo_path"]


def test_worktree_failure_fails_closed_instead_of_using_the_live_tree(
    build, repo: Path, monkeypatch
):
    """If isolation cannot be established the run must stop, not fall back."""
    def _boom(**_kwargs):
        raise ValueError("worktree_add_failed")

    monkeypatch.setattr(pae, "prepare_run_worktree", _boom)

    ctx = build()
    runtime = ctx["controlled_runtime_context"]

    assert runtime["blocked_reason"] == "workspace_worktree_failed"
    assert runtime["real_executor_ready"] is False
    assert runtime["allow_mutations"] is False
    assert runtime["allow_test_commands"] is False
    assert runtime["mutation_workspace"] != str(repo.resolve())


def test_baseline_head_is_the_worktree_head(build):
    ctx = build()
    runtime = ctx["controlled_runtime_context"]
    workspace = Path(ctx["repo_path"])
    assert runtime["workspace_baseline_head"] == _git(workspace, "rev-parse", "HEAD")


# ── Push guard ──────────────────────────────────────────────────────────────
# A run branch must not reach origin on its own. `_push` falls back to
# `git push -u origin <branch>` when there is no upstream, which would publish a
# hermes-run/* branch per run. Landing the work is the integration step, gated
# on an approving reviewer verdict.


def test_run_branch_prefix_agrees_across_modules():
    from hermes_cli import commit_gate_service, pipeline_aiagent_executor

    assert commit_gate_service.RUN_BRANCH_PREFIX == pae.RUN_BRANCH_PREFIX
    assert pipeline_aiagent_executor.RUN_BRANCH_PREFIX == pae.RUN_BRANCH_PREFIX


def test_push_refuses_a_run_branch(repo: Path, tmp_path: Path):
    from hermes_cli import commit_gate_service

    info = pae.prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r9", run_id="r9"
    )
    (info.path / "a.py").write_text("engineer edit\n")

    result = commit_gate_service.apply_commit(
        repo=info.path, changed_files=["a.py"], commit_message="wip", push=True
    )

    assert result["committed"] is True          # committing locally is fine
    assert result["pushed"] is False            # publishing it is not
    assert "review" in result["push_detail"].lower()


def test_push_still_works_on_an_ordinary_branch(repo: Path, tmp_path: Path):
    """The guard must be scoped to run branches only."""
    from hermes_cli import commit_gate_service

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 3\n")

    result = commit_gate_service.apply_commit(
        repo=repo, changed_files=["a.py"], commit_message="wip", push=True
    )

    assert result["committed"] is True
    assert result["pushed"] is True, result.get("push_detail")
