from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli.pipeline_git_delta import (
    GitMaterialChangeResult,
    GitSnapshot,
    capture_git_snapshot,
    compare_git_snapshots,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _write(repo: Path, relative_path: str, content: str) -> Path:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _write(repo, "tracked.txt", "baseline\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_clean_repo_baseline_and_post_snapshot_has_no_material_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    baseline = capture_git_snapshot(repo)
    post = capture_git_snapshot(repo)
    result = compare_git_snapshots(baseline, post)

    assert result.status == "no_material_changes"
    assert result.material_changes_present is False
    assert result.review_required is False
    assert result.changed_files == []
    assert result.head_changed is False


def test_tracked_file_modified_after_baseline_requires_review(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)

    _write(repo, "tracked.txt", "changed\n")

    result = compare_git_snapshots(baseline, capture_git_snapshot(repo))

    assert result.status == "material_changes_detected"
    assert result.material_changes_present is True
    assert result.review_required is True
    assert result.changed_files == ["tracked.txt"]
    assert result.unstaged_files == ["tracked.txt"]


def test_untracked_file_after_baseline_requires_review(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)

    _write(repo, "new.txt", "untracked\n")

    result = compare_git_snapshots(baseline, capture_git_snapshot(repo))

    assert result.status == "material_changes_detected"
    assert result.review_required is True
    assert result.untracked_files == ["new.txt"]
    assert result.changed_files == ["new.txt"]


def test_staged_file_after_baseline_requires_review(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)

    _write(repo, "tracked.txt", "staged\n")
    _git(repo, "add", "tracked.txt")

    result = compare_git_snapshots(baseline, capture_git_snapshot(repo))

    assert result.status == "material_changes_detected"
    assert result.review_required is True
    assert result.staged_files == ["tracked.txt"]
    assert result.changed_files == ["tracked.txt"]


def test_deleted_file_after_baseline_requires_review(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)

    (repo / "tracked.txt").unlink()

    result = compare_git_snapshots(baseline, capture_git_snapshot(repo))

    assert result.status == "material_changes_detected"
    assert result.review_required is True
    assert result.changed_files == ["tracked.txt"]


def test_dirty_baseline_is_explicit_and_not_attributed_as_new_engineer_change(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, "tracked.txt", "pre-existing dirty state\n")

    baseline = capture_git_snapshot(repo)
    post = capture_git_snapshot(repo)
    result = compare_git_snapshots(baseline, post)

    assert baseline.is_dirty is True
    assert result.status == "baseline_invalid"
    assert result.material_changes_present is False
    assert result.review_required is True
    assert result.changed_files == []
    assert result.blocked_reason == "baseline_dirty"


def test_head_change_between_snapshots_requires_review(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)

    _write(repo, "tracked.txt", "committed change\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "second")

    result = compare_git_snapshots(baseline, capture_git_snapshot(repo))

    assert result.status == "material_changes_detected"
    assert result.review_required is True
    assert result.head_changed is True
    assert result.changed_files == ["tracked.txt"]


def test_invalid_repo_path_fails_closed() -> None:
    result = capture_git_snapshot(Path("/definitely/not/a/repo"))

    assert result.capture_status == "invalid_repo"
    assert result.error_type == "invalid_repo"


def test_compare_fails_closed_when_post_snapshot_is_invalid(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)
    post = GitSnapshot(
        repo_path=str(repo),
        head_sha=None,
        branch=None,
        status_porcelain=(),
        tracked_changed_files=(),
        untracked_files=(),
        staged_files=(),
        unstaged_files=(),
        is_dirty=False,
        capture_status="git_unavailable",
        error_type="git_unavailable",
        error_message_redacted="git command failed",
    )

    result = compare_git_snapshots(baseline, post)

    assert result.status == "post_snapshot_invalid"
    assert result.review_required is True
    assert result.blocked_reason == "post_snapshot_invalid"


def test_safe_dict_is_json_serializable_and_omits_diff_content(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = capture_git_snapshot(repo)
    _write(repo, "tracked.txt", "diff body should not appear\n")
    result = compare_git_snapshots(baseline, capture_git_snapshot(repo))

    payload = result.to_safe_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert json.loads(encoded)["status"] == "material_changes_detected"
    assert "diff body should not appear" not in encoded
    assert "+++" not in encoded


def test_snapshot_safe_dict_is_json_serializable() -> None:
    snapshot = GitSnapshot(
        repo_path="/tmp/repo",
        head_sha="abc123",
        branch="main",
        status_porcelain=(" M tracked.txt", "?? new.txt"),
        tracked_changed_files=("tracked.txt",),
        untracked_files=("new.txt",),
        staged_files=(),
        unstaged_files=("tracked.txt",),
        is_dirty=True,
        capture_status="captured",
        error_type=None,
        error_message_redacted=None,
    )

    encoded = json.dumps(snapshot.to_safe_dict(), sort_keys=True)

    assert json.loads(encoded)["repo_path"] == "/tmp/repo"


def test_result_safe_dict_is_json_serializable() -> None:
    result = GitMaterialChangeResult(
        status="git_unavailable",
        material_changes_present=False,
        review_required=True,
        changed_files=(),
        untracked_files=(),
        staged_files=(),
        unstaged_files=(),
        baseline_head_sha=None,
        post_head_sha=None,
        head_changed=False,
        blocked_reason="git_unavailable",
        baseline_dirty=False,
        safe_summary="git unavailable",
    )

    encoded = json.dumps(result.to_safe_dict(), sort_keys=True)

    assert json.loads(encoded)["review_required"] is True
