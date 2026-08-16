"""Slice 3: landing a run branch, and cleaning up after it.

A run now commits to hermes-run/<id> inside its own worktree, which means the
work is safe but stranded. Integration is the step that lands it -- and it is the
natural place to enforce Task 6: the mainline only moves on an approving
reviewer verdict.

Two deliberate conservatisms encoded here:

* fast-forward only. The mainline is a live branch that the resident agent
  commits to; a merge commit or a rebase performed behind the operator's back is
  not something this step should be doing.
* never lose the run branch. Every refusal path leaves hermes-run/<id> exactly
  where it was, so a refused integration is always recoverable by hand.
"""
import subprocess
from pathlib import Path

import pytest

from hermes_cli.pipeline_autonomous_execution import (
    build_autonomous_helper_context,
    integrate_run_branch,
    prepare_run_worktree,
    release_run_worktree,
    sweep_run_worktrees,
)
from hermes_cli.pipeline_change_artifacts import persist_change_artifacts, verify_change_artifact


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
    """A run worktree with one committed change, as after an approved commit gate."""
    info = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )
    (info.path / "a.py").write_text("x = 2\n")
    _git(info.path, "add", "a.py")
    _git(info.path, "commit", "-qm", "engineer change")
    return info


def test_approved_run_fast_forwards_the_mainline(repo: Path, run):
    result = integrate_run_branch(repo_root=repo, branch=run.branch, approved=True)

    assert result.integrated is True
    assert result.target == "main"
    assert _git(repo, "rev-parse", "main") == _git(run.path, "rev-parse", "HEAD")
    assert (repo / "a.py").read_text() == "x = 2\n"


def test_unapproved_run_is_refused_and_the_mainline_does_not_move(repo: Path, run):
    """The Task 6 gate: a reviewer verdict is what moves the mainline."""
    before = _git(repo, "rev-parse", "main")

    result = integrate_run_branch(repo_root=repo, branch=run.branch, approved=False)

    assert result.integrated is False
    assert result.reason == "review_not_approved"
    assert _git(repo, "rev-parse", "main") == before
    assert (repo / "a.py").read_text() == "x = 1\n"


def test_diverged_mainline_is_refused_rather_than_merged(repo: Path, run):
    """The resident agent commits to the mainline too; do not paper over that."""
    (repo / "b.py").write_text("y = 1\n")
    _git(repo, "add", "b.py")
    _git(repo, "commit", "-qm", "someone else's commit")
    before = _git(repo, "rev-parse", "main")

    result = integrate_run_branch(repo_root=repo, branch=run.branch, approved=True)

    assert result.integrated is False
    assert result.reason == "not_fast_forward"
    assert _git(repo, "rev-parse", "main") == before
    # The work is still recoverable.
    assert _git(repo, "rev-parse", "--verify", run.branch)


def test_unrelated_dirt_in_the_mainline_tree_does_not_block(repo: Path, run):
    (repo / "untouched.txt").write_text("operator scratch\n")

    result = integrate_run_branch(repo_root=repo, branch=run.branch, approved=True)

    assert result.integrated is True
    assert (repo / "untouched.txt").read_text() == "operator scratch\n"


def test_dirt_on_a_file_the_run_touched_is_refused(repo: Path, run):
    """Refusing beats silently clobbering the operator's edit."""
    (repo / "a.py").write_text("operator was editing this\n")

    result = integrate_run_branch(repo_root=repo, branch=run.branch, approved=True)

    assert result.integrated is False
    assert (repo / "a.py").read_text() == "operator was editing this\n"


def test_missing_run_branch_is_reported(repo: Path):
    result = integrate_run_branch(
        repo_root=repo, branch="hermes-run/nope", approved=True
    )
    assert result.integrated is False
    assert result.reason == "run_branch_missing"


def test_detached_mainline_is_refused(repo: Path, run):
    _git(repo, "checkout", "-q", "--detach", "HEAD")

    result = integrate_run_branch(repo_root=repo, branch=run.branch, approved=True)

    assert result.integrated is False
    assert result.reason == "target_detached"


# ── Cleanup ─────────────────────────────────────────────────────────────────


def test_release_removes_the_worktree_and_deregisters_it(repo: Path, run):
    release_run_worktree(repo_root=repo, workspace=run.path, delete_branch=False)

    assert not run.path.exists()
    assert str(run.path) not in _git(repo, "worktree", "list")
    # Branch survives: releasing the checkout must not throw the work away.
    assert _git(repo, "rev-parse", "--verify", run.branch)


def test_release_can_drop_the_branch_once_it_is_integrated(repo: Path, run):
    integrate_run_branch(repo_root=repo, branch=run.branch, approved=True)
    release_run_worktree(repo_root=repo, workspace=run.path, delete_branch=True)

    assert run.branch not in _git(repo, "branch", "--list", "hermes-run/*")


def test_sweep_removes_only_worktrees_older_than_the_cutoff(repo: Path, tmp_path: Path):
    runs_root = tmp_path / "runs"
    old = prepare_run_worktree(
        repo_root=repo, workspace=runs_root / "old", run_id="old"
    )
    fresh = prepare_run_worktree(
        repo_root=repo, workspace=runs_root / "fresh", run_id="fresh"
    )
    import os

    os.utime(old.path, (1000, 1000))

    removed = sweep_run_worktrees(
        repo_root=repo, runs_root=runs_root, max_age_seconds=3600, now=100_000
    )

    assert [Path(p).name for p in removed] == ["old"]
    assert not old.path.exists()
    assert fresh.path.exists()


def test_sweep_leaves_a_worktree_with_uncommitted_work(repo: Path, tmp_path: Path):
    """Age is not evidence that the work inside is disposable."""
    import os

    runs_root = tmp_path / "runs"
    stale = prepare_run_worktree(
        repo_root=repo, workspace=runs_root / "stale", run_id="stale"
    )
    (stale.path / "a.py").write_text("unfinished engineer edit\n")
    os.utime(stale.path, (1000, 1000))

    removed = sweep_run_worktrees(
        repo_root=repo, runs_root=runs_root, max_age_seconds=3600, now=100_000
    )

    assert removed == []
    assert stale.path.exists()


def test_sweep_keeps_clean_unlanded_commits_until_artifact_is_verified(repo: Path, tmp_path: Path):
    runs_root = tmp_path / "runs"
    stale = prepare_run_worktree(
        repo_root=repo, workspace=runs_root / "stale-committed", run_id="stale-committed"
    )
    (stale.path / "a.py").write_text("committed engineer work\n")
    _git(stale.path, "add", "a.py")
    _git(stale.path, "commit", "-qm", "engineer change")
    import os

    os.utime(stale.path, (1000, 1000))

    removed_without_artifact = sweep_run_worktrees(
        repo_root=repo,
        runs_root=runs_root,
        max_age_seconds=3600,
        now=100_000,
        durable_root=tmp_path / "durable",
    )
    assert removed_without_artifact == []
    assert stale.path.exists()

    baseline = _git(repo, "rev-parse", "HEAD")
    head = _git(stale.path, "rev-parse", "HEAD")
    persist_change_artifacts(
        repo_path=stale.path,
        canonical_repo_path=repo,
        durable_run_root=tmp_path / "durable" / "stale-committed",
        baseline_head_sha=baseline,
        run_head_sha=head,
        branch=stale.branch,
        changed_files=["a.py"],
        tracked_changed_files=["a.py"],
        material_changes_present=True,
    )
    os.utime(stale.path, (1000, 1000))

    removed_with_artifact = sweep_run_worktrees(
        repo_root=repo,
        runs_root=runs_root,
        max_age_seconds=3600,
        now=100_000,
        durable_root=tmp_path / "durable",
    )
    assert [Path(path).name for path in removed_with_artifact] == ["stale-committed"]
    assert not stale.path.exists()
    assert _git(repo, "rev-parse", "--verify", stale.branch)
    verified, reason = verify_change_artifact(
        metadata_path=tmp_path / "durable" / "stale-committed" / "change-artifact.json",
        repo_path=repo,
        canonical_repo_path=repo,
    )
    assert verified is True, reason


def test_autonomous_helper_context_resolves_main_checkout_from_linked_worktree(repo: Path, tmp_path: Path):
    linked = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "linked", run_id="linked"
    )
    try:
        helper = build_autonomous_helper_context(
            config={"pipelines": {"enabled": False}},
            user_message="inspect linked context",
            session_id="session-linked",
            pipeline_session_id="pipeline-linked",
            repo_root=linked.path,
        )
        assert helper["canonical_repo_path"] == str(repo.resolve())
    finally:
        release_run_worktree(repo_root=repo, workspace=linked.path, delete_branch=False)
