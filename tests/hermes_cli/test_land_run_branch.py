"""Landing a run branch from the commit-gate handlers.

Slice 2 left the pipeline committing to hermes-run/<id> and nothing landing it,
which is a regression against the old behaviour of committing straight to the
live branch. This is the step that closes that, without giving up the isolation.

On the approval source, deliberately: `approved` is a parameter, and the commit
gate currently fills it from the *operator's* «коммить». That is exactly as
permissive as the behaviour it replaces -- no weaker -- and it is the seam Task 6
fills with the reviewer's verdict. Inventing an approval signal out of the
nearest available payload would have been the one thing the plan forbids.
"""
import subprocess
from pathlib import Path

import pytest

from hermes_cli.pipeline_autonomous_execution import (
    land_run_branch_after_commit,
    prepare_run_worktree,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.py").write_text("x = 1\n")
    (root / "venv").mkdir()
    _git(root, "add", "a.py")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def run(repo: Path, tmp_path: Path):
    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )
    (info.path / "a.py").write_text("x = 2\n")
    _git(info.path, "add", "a.py")
    _git(info.path, "commit", "-qm", "engineer change")
    return info


def test_approved_landing_moves_the_mainline_and_releases_the_worktree(repo: Path, run):
    result = land_run_branch_after_commit(workspace=run.path, approved=True)

    assert result is not None and result.integrated is True
    assert (repo / "a.py").read_text() == "x = 2\n"
    assert not run.path.exists()
    assert _git(repo, "branch", "--list", "hermes-run/*") == ""


def test_refused_landing_keeps_both_the_worktree_and_the_branch(repo: Path, run):
    result = land_run_branch_after_commit(workspace=run.path, approved=False)

    assert result is not None and result.integrated is False
    assert result.reason == "review_not_approved"
    assert run.path.exists()
    assert _git(repo, "rev-parse", "--verify", run.branch)
    assert (repo / "a.py").read_text() == "x = 1\n"


def test_a_diverged_mainline_leaves_the_work_recoverable(repo: Path, run):
    (repo / "b.py").write_text("y = 1\n")
    _git(repo, "add", "b.py")
    _git(repo, "commit", "-qm", "resident agent commit")

    result = land_run_branch_after_commit(workspace=run.path, approved=True)

    assert result.integrated is False
    assert run.path.exists()
    assert _git(repo, "rev-parse", "--verify", run.branch)


def test_an_ordinary_checkout_is_not_a_run_and_is_left_alone(repo: Path):
    """The commit gate also fires for in-place work; that path must be untouched."""
    assert land_run_branch_after_commit(workspace=repo, approved=True) is None


def test_repo_root_is_derived_from_the_worktree(repo: Path, run):
    """Call sites only know the workspace, so the helper must find the repo itself."""
    result = land_run_branch_after_commit(workspace=run.path, approved=True)
    assert result.target == "main"
