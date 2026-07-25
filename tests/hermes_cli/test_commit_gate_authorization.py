"""What the operator's «коммить» may actually do, given the session's review debt.

Two different blast radii, so two different answers:

* a commit inside a run worktree is contained -- it lands on hermes-run/<id> and
  moves nothing. Debt does not need to stop it; it needs to stop the *landing*.
* a commit in the live repository moves the mainline immediately. That is the
  25 July shape (09ebaa2dd reached origin with a changes_requested outstanding),
  and debt has to stop it outright.

Collapsing the two would either strand the pipeline's own work or leave the hole
that started all of this open.
"""
import subprocess
from pathlib import Path

import pytest

from hermes_cli.pipeline_autonomous_execution import (
    commit_gate_authorization,
    prepare_run_worktree,
)
from hermes_cli.review_gate import ReviewGateState


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
def debt_root(tmp_path: Path) -> Path:
    root = tmp_path / "debt"
    root.mkdir()
    return root


def _with_debt(session: str, root: Path) -> None:
    state = ReviewGateState(session=session)
    state.record_verdict(
        "changes_requested",
        changed_paths=["tools/approval.py"],
        findings=["cron_mode=smart is undocumented"],
    )
    state.save(root)


def test_debt_blocks_a_commit_straight_into_the_live_repo(repo: Path, debt_root: Path):
    _with_debt("s1", debt_root)

    auth = commit_gate_authorization(workspace=repo, session_id="s1", debt_root=debt_root)

    assert auth.is_run_worktree is False
    assert auth.allow_commit is False
    assert "tools/approval.py" in auth.detail
    assert "cron_mode=smart is undocumented" in auth.detail


def test_debt_allows_the_contained_commit_but_not_the_landing(
    repo: Path, debt_root: Path, tmp_path: Path
):
    _with_debt("s1", debt_root)
    run = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )

    auth = commit_gate_authorization(workspace=run.path, session_id="s1", debt_root=debt_root)

    assert auth.is_run_worktree is True
    assert auth.allow_commit is True          # contained on hermes-run/r1
    assert auth.approved_for_landing is False  # but the mainline stays put


def test_a_settled_session_may_commit_and_land(repo: Path, debt_root: Path, tmp_path: Path):
    state = ReviewGateState(session="s1")
    state.record_verdict("changes_requested", changed_paths=["tools/approval.py"])
    state.record_verdict("approved", changed_paths=["tools/approval.py"])
    state.save(debt_root)
    run = prepare_run_worktree(
        repo_root=repo, workspace=tmp_path / "runs" / "r1", run_id="r1"
    )

    auth = commit_gate_authorization(workspace=run.path, session_id="s1", debt_root=debt_root)

    assert auth.allow_commit is True
    assert auth.approved_for_landing is True


def test_a_session_that_was_never_reviewed_is_not_treated_as_indebted(
    repo: Path, debt_root: Path
):
    auth = commit_gate_authorization(workspace=repo, session_id="never-seen", debt_root=debt_root)
    assert auth.allow_commit is True
    assert auth.approved_for_landing is True


def test_a_missing_session_id_does_not_invent_a_block(repo: Path, debt_root: Path):
    """Absent evidence is not evidence of debt; blocking on it would wedge the gate."""
    auth = commit_gate_authorization(workspace=repo, session_id="", debt_root=debt_root)
    assert auth.allow_commit is True


def test_an_unreadable_debt_store_fails_open_and_says_so(repo: Path, tmp_path: Path):
    """A cache problem must not become an un-diagnosable refusal to commit."""
    auth = commit_gate_authorization(
        workspace=repo, session_id="s1", debt_root=tmp_path / "does-not-exist"
    )
    assert auth.allow_commit is True
