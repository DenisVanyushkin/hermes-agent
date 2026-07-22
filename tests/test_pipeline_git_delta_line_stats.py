"""collect_line_stats: deterministic +N/-M per file for the commit-gate message."""
from __future__ import annotations

import subprocess
from pathlib import Path

from hermes_cli.pipeline_git_delta import collect_line_stats


def _git(repo, *a):
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.py").write_text("a\nb\nc\n")
    _git(tmp_path, "add", "tracked.py")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_counts_modified_tracked_file(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.py").write_text("a\nB\nc\nd\n")
    stats = collect_line_stats(repo, ["tracked.py"])
    assert stats["tracked.py"] == {"added": 2, "removed": 1}


def test_counts_untracked_file_as_all_added(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new.py").write_text("x\ny\n")
    stats = collect_line_stats(repo, ["new.py"])
    assert stats["new.py"] == {"added": 2, "removed": 0}


def test_missing_file_is_omitted_not_fatal(tmp_path):
    repo = _repo(tmp_path)
    assert collect_line_stats(repo, ["gone.py"]) == {}


def test_broken_repo_returns_empty_instead_of_raising(tmp_path):
    # Rendering a commit gate must never fail because git misbehaved.
    assert collect_line_stats(tmp_path / "nope", ["a.py"]) == {}
