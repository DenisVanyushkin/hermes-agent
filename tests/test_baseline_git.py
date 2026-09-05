import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli.baseline_git import DirtyEntry, classify_dirty


_NODEIDS = (
    "tests/test_baseline_git.py::test_clean_repo_returns_empty",
    "tests/test_baseline_git.py::test_untracked_file_classified",
    "tests/test_baseline_git.py::test_modified_tracked_file_classified",
    "tests/test_baseline_git.py::test_report_artifact_is_ignored",
)


def _pytest_statuses(nodeids: tuple[str, ...]) -> dict[str, bool]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-vv", *nodeids],
        cwd=Path(__file__).parent.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    statuses = {}
    for nodeid in nodeids:
        if f"{nodeid} PASSED" in output:
            statuses[nodeid] = True
        elif f"{nodeid} FAILED" in output:
            statuses[nodeid] = False
        else:
            raise AssertionError(f"pytest did not report {nodeid}:\n{output}")
    return statuses


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    # tests/conftest.py places the substituted HERMES_HOME directly under
    # tmp_path. Keep the git fixture in a sibling directory: hermes home
    # initialization creates SOUL.md, state.db, and a cache, and those files
    # must not be classified as repository dirt.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tracked.txt").write_text("base\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "base")
    return repo


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


def test_baseline_git_nodes_are_order_independent() -> None:
    direct = _pytest_statuses(_NODEIDS)
    reverse = _pytest_statuses(tuple(reversed(_NODEIDS)))
    solo = {nodeid: _pytest_statuses((nodeid,))[nodeid] for nodeid in _NODEIDS}

    for nodeid in (
        _NODEIDS[0],
        _NODEIDS[-1],
    ):
        assert solo[nodeid] == direct[nodeid] == reverse[nodeid], {
            "nodeid": nodeid,
            "solo": solo[nodeid],
            "direct": direct[nodeid],
            "reverse": reverse[nodeid],
        }
