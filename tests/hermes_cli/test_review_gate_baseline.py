"""Review gate must attribute only the current session's worktree changes.

Regression guard for the 2026-07-19 incident: the ``hermes-rebase-local-customizations``
cron was blocked because the review gate reviewed an unrelated uncommitted diff
(``job_intel/.../calibration.py``) that was already dirty before the run started.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli.profile_execution import build_role_execution_plan
from hermes_cli.review_gate import (
    detect_material_engineering_change,
    snapshot_material_dirty_paths,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "job_intel").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "job_intel" / "calibration.py").write_text("original\n")
    (root / "job_intel" / "scoring.py").write_text("original\n")
    (root / "docs" / "notes.md").write_text("original\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial")
    return root


@pytest.fixture()
def plan():
    """A git_remote_mutation plan — the category the rebase cron runs under."""
    built = build_role_execution_plan("Сделай git commit и git push")
    assert built.operation_category in {"repo_mutation", "git_remote_mutation"}
    return built


def _messages_with_unattributable_mutation() -> list[dict]:
    """A file-mutation tool call whose path the gate cannot map to the repo.

    This is what makes the gate fall back to asking git what is dirty.
    """
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path": "/root/.hermes/state/upstream-sync/pending.json"}',
                    }
                }
            ],
        }
    ]


def test_snapshot_material_dirty_paths_lists_only_material_files(repo, monkeypatch):
    monkeypatch.setattr("hermes_cli.review_gate._repo_root", lambda: repo)
    (repo / "job_intel" / "calibration.py").write_text("changed\n")
    (repo / "docs" / "notes.md").write_text("changed\n")

    assert snapshot_material_dirty_paths() == ["job_intel/calibration.py"]


def test_preexisting_dirty_paths_are_not_attributed_to_the_session(repo, plan, monkeypatch):
    monkeypatch.setattr("hermes_cli.review_gate._repo_root", lambda: repo)
    (repo / "job_intel" / "calibration.py").write_text("dirty before the run\n")
    baseline = snapshot_material_dirty_paths()

    detected, paths = detect_material_engineering_change(
        plan,
        _messages_with_unattributable_mutation(),
        baseline_dirty_paths=baseline,
    )

    assert detected is False
    assert paths == []


def test_paths_dirtied_during_the_session_are_still_detected(repo, plan, monkeypatch):
    monkeypatch.setattr("hermes_cli.review_gate._repo_root", lambda: repo)
    (repo / "job_intel" / "calibration.py").write_text("dirty before the run\n")
    baseline = snapshot_material_dirty_paths()

    (repo / "job_intel" / "scoring.py").write_text("written by the agent\n")

    detected, paths = detect_material_engineering_change(
        plan,
        _messages_with_unattributable_mutation(),
        baseline_dirty_paths=baseline,
    )

    assert detected is True
    assert paths == ["job_intel/scoring.py"]


def test_without_a_baseline_behaviour_is_unchanged(repo, plan, monkeypatch):
    """Callers that pass no baseline keep the old (conservative) behaviour."""
    monkeypatch.setattr("hermes_cli.review_gate._repo_root", lambda: repo)
    (repo / "job_intel" / "calibration.py").write_text("dirty before the run\n")

    detected, paths = detect_material_engineering_change(
        plan,
        _messages_with_unattributable_mutation(),
    )

    assert detected is True
    assert paths == ["job_intel/calibration.py"]
