"""A per-run git worktree for the autonomous engineering pipeline.

Today the pipeline operates directly on the live repo root, so any dirt in that
tree -- including dirt the pipeline did not create -- decides whether it may run
at all. `_auto_heal_dirty_baseline` papers over the easy case by stashing it
(silently reverting the operator's working tree), and refuses outright on
root-owned or conflicted paths, which is what the sandbox containers keep
leaving behind.

A worktree cut from a *commit* is clean by construction, so the whole question
disappears rather than being classified. These tests pin the two properties that
make that true: the run workspace starts clean, and the main tree is not touched.
"""
import subprocess
from pathlib import Path

import pytest

from hermes_cli.baseline_git import classify_dirty
from hermes_cli.pipeline_autonomous_execution import (
    RUN_BRANCH_PREFIX,
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
    (root / "a.py").write_text("x = 1\n")
    (root / "venv").mkdir()
    _git(root, "add", "a.py")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def test_worktree_is_clean_even_when_the_repo_root_is_dirty(repo: Path, tmp_path: Path):
    (repo / "a.py").write_text("x = 2\n")           # tracked + modified
    (repo / "junk.txt").write_text("untracked\n")   # untracked

    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )

    assert classify_dirty(info.path) == []
    assert (info.path / "a.py").read_text() == "x = 1\n"
    assert not (info.path / "junk.txt").exists()


def test_the_main_tree_is_left_exactly_as_it_was(repo: Path, tmp_path: Path):
    """The current auto-heal stashes the operator's edits away. This must not."""
    (repo / "a.py").write_text("x = 2\n")

    prepare_run_worktree(repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1")

    assert (repo / "a.py").read_text() == "x = 2\n"
    assert _git(repo, "stash", "list") == ""


def test_run_gets_its_own_branch(repo: Path, tmp_path: Path):
    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "abc123", run_id="abc123"
    )
    assert info.branch == f"{RUN_BRANCH_PREFIX}abc123"
    assert _git(info.path, "rev-parse", "--abbrev-ref", "HEAD") == info.branch
    # The main tree keeps its own branch -- git forbids sharing one anyway.
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_worktree_starts_at_the_repo_head_commit(repo: Path, tmp_path: Path):
    head = _git(repo, "rev-parse", "HEAD")
    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )
    assert info.head == head
    assert _git(info.path, "rev-parse", "HEAD") == head


def test_venv_is_symlinked_so_tests_can_run_in_the_worktree(repo: Path, tmp_path: Path):
    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )
    venv = info.path / "venv"
    assert venv.is_symlink()
    assert venv.resolve() == (repo / "venv").resolve()


def test_preparing_twice_reuses_the_same_worktree(repo: Path, tmp_path: Path):
    """A retried turn inside one run must not orphan the first worktree."""
    first = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )
    (first.path / "a.py").write_text("engineer edit\n")

    second = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )

    assert second.path == first.path
    assert second.created is False
    assert (second.path / "a.py").read_text() == "engineer edit\n"


def test_run_id_is_sanitised_into_the_branch_name(repo: Path, tmp_path: Path):
    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r2", run_id="a/b c:d"
    )
    assert info.branch == f"{RUN_BRANCH_PREFIX}a-b-c-d"
    assert _git(info.path, "rev-parse", "--abbrev-ref", "HEAD") == info.branch
