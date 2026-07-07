import subprocess
from pathlib import Path

import pytest

from hermes_cli.baseline_git import DirtyEntry, classify_dirty


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("base\n")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_clean_repo_returns_empty(repo: Path) -> None:
    assert classify_dirty(repo) == []


def test_untracked_file_classified(repo: Path) -> None:
    (repo / "new.py").write_text("x\n")
    assert classify_dirty(repo) == [DirtyEntry("untracked", "new.py")]


def test_modified_tracked_file_classified(repo: Path) -> None:
    (repo / "tracked.txt").write_text("changed\n")
    assert classify_dirty(repo) == [DirtyEntry("modified", "tracked.txt")]


def test_report_artifact_is_ignored(repo: Path) -> None:
    (repo / "controlled_execution_report.json").write_text("{}\n")
    assert classify_dirty(repo) == []
