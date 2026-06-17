"""Git snapshot capture and material-change comparison for pipeline gating."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any


_GIT_TIMEOUT_SECONDS = 2.0
_CAPTURED = "captured"
_INVALID_REPO = "invalid_repo"
_GIT_UNAVAILABLE = "git_unavailable"


@dataclass(frozen=True)
class GitSnapshot:
    repo_path: str
    head_sha: str | None
    branch: str | None
    status_porcelain: tuple[str, ...] = ()
    tracked_changed_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    staged_files: tuple[str, ...] = ()
    unstaged_files: tuple[str, ...] = ()
    is_dirty: bool = False
    capture_status: str = _CAPTURED
    error_type: str | None = None
    error_message_redacted: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "head_sha": self.head_sha,
            "branch": self.branch,
            "status_porcelain": list(self.status_porcelain),
            "tracked_changed_files": list(self.tracked_changed_files),
            "untracked_files": list(self.untracked_files),
            "staged_files": list(self.staged_files),
            "unstaged_files": list(self.unstaged_files),
            "is_dirty": self.is_dirty,
            "capture_status": self.capture_status,
            "error_type": self.error_type,
            "error_message_redacted": self.error_message_redacted,
        }


@dataclass(frozen=True)
class GitMaterialChangeResult:
    status: str
    material_changes_present: bool
    review_required: bool
    changed_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    unstaged_files: list[str] = field(default_factory=list)
    baseline_head_sha: str | None = None
    post_head_sha: str | None = None
    head_changed: bool = False
    blocked_reason: str | None = None
    baseline_dirty: bool = False
    safe_summary: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "material_changes_present": self.material_changes_present,
            "review_required": self.review_required,
            "changed_files": list(self.changed_files),
            "untracked_files": list(self.untracked_files),
            "staged_files": list(self.staged_files),
            "unstaged_files": list(self.unstaged_files),
            "baseline_head_sha": self.baseline_head_sha,
            "post_head_sha": self.post_head_sha,
            "head_changed": self.head_changed,
            "blocked_reason": self.blocked_reason,
            "baseline_dirty": self.baseline_dirty,
            "safe_summary": self.safe_summary,
        }


def capture_git_snapshot(repo_path: Path | str) -> GitSnapshot:
    repo = Path(repo_path)
    repo_text = str(repo)
    if not repo.exists() or not repo.is_dir():
        return GitSnapshot(
            repo_path=repo_text,
            head_sha=None,
            branch=None,
            capture_status=_INVALID_REPO,
            error_type=_INVALID_REPO,
            error_message_redacted="repository path is missing or not a directory",
        )

    toplevel = _run_git(repo, "rev-parse", "--show-toplevel")
    if not toplevel.ok:
        return GitSnapshot(
            repo_path=repo_text,
            head_sha=None,
            branch=None,
            capture_status=_INVALID_REPO if toplevel.invalid_repo else _GIT_UNAVAILABLE,
            error_type=_INVALID_REPO if toplevel.invalid_repo else _GIT_UNAVAILABLE,
            error_message_redacted=toplevel.error_message_redacted,
        )

    head = _run_git(repo, "rev-parse", "HEAD")
    branch = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    status = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if not head.ok or not branch.ok or not status.ok:
        failing = next(item for item in (head, branch, status) if not item.ok)
        return GitSnapshot(
            repo_path=repo_text,
            head_sha=None,
            branch=None,
            capture_status=_GIT_UNAVAILABLE,
            error_type=_GIT_UNAVAILABLE,
            error_message_redacted=failing.error_message_redacted,
        )

    parsed = _parse_status_lines(status.lines)
    return GitSnapshot(
        repo_path=str(Path(toplevel.stdout)),
        head_sha=head.stdout,
        branch=branch.stdout,
        status_porcelain=tuple(status.lines),
        tracked_changed_files=tuple(parsed["tracked"]),
        untracked_files=tuple(parsed["untracked"]),
        staged_files=tuple(parsed["staged"]),
        unstaged_files=tuple(parsed["unstaged"]),
        is_dirty=bool(status.lines),
        capture_status=_CAPTURED,
        error_type=None,
        error_message_redacted=None,
    )


def compare_git_snapshots(
    baseline: GitSnapshot,
    post_snapshot: GitSnapshot,
) -> GitMaterialChangeResult:
    if baseline.capture_status != _CAPTURED:
        return GitMaterialChangeResult(
            status="baseline_invalid",
            material_changes_present=False,
            review_required=True,
            baseline_head_sha=baseline.head_sha,
            post_head_sha=post_snapshot.head_sha,
            head_changed=baseline.head_sha != post_snapshot.head_sha,
            blocked_reason="baseline_invalid",
            baseline_dirty=baseline.is_dirty,
            safe_summary="Baseline git snapshot is invalid or unavailable.",
        )
    if post_snapshot.capture_status != _CAPTURED:
        return GitMaterialChangeResult(
            status="post_snapshot_invalid",
            material_changes_present=False,
            review_required=True,
            baseline_head_sha=baseline.head_sha,
            post_head_sha=post_snapshot.head_sha,
            head_changed=baseline.head_sha != post_snapshot.head_sha,
            blocked_reason="post_snapshot_invalid",
            baseline_dirty=baseline.is_dirty,
            safe_summary="Post-run git snapshot is invalid or unavailable.",
        )
    if baseline.repo_path != post_snapshot.repo_path:
        return GitMaterialChangeResult(
            status="git_unavailable",
            material_changes_present=False,
            review_required=True,
            baseline_head_sha=baseline.head_sha,
            post_head_sha=post_snapshot.head_sha,
            head_changed=baseline.head_sha != post_snapshot.head_sha,
            blocked_reason="repo_path_mismatch",
            baseline_dirty=baseline.is_dirty,
            safe_summary="Git snapshot comparison requires the same repository path.",
        )

    new_untracked = _new_files(baseline.untracked_files, post_snapshot.untracked_files)
    new_staged = _new_files(baseline.staged_files, post_snapshot.staged_files)
    new_unstaged = _new_files(baseline.unstaged_files, post_snapshot.unstaged_files)
    head_changed = baseline.head_sha != post_snapshot.head_sha

    head_diff_files: list[str] = []
    if head_changed and baseline.head_sha and post_snapshot.head_sha:
        head_diff = _git_diff_changed_files(Path(post_snapshot.repo_path), baseline.head_sha, post_snapshot.head_sha)
        if head_diff is None:
            return GitMaterialChangeResult(
                status="git_unavailable",
                material_changes_present=False,
                review_required=True,
                baseline_head_sha=baseline.head_sha,
                post_head_sha=post_snapshot.head_sha,
                head_changed=True,
                blocked_reason="git_diff_failed",
                baseline_dirty=baseline.is_dirty,
                safe_summary="Git diff between baseline and post-run HEAD could not be computed.",
            )
        head_diff_files = head_diff

    changed_files = sorted(set(new_untracked) | set(new_staged) | set(new_unstaged) | set(head_diff_files))
    material_changes_present = bool(changed_files or head_changed)

    if baseline.is_dirty:
        return GitMaterialChangeResult(
            status="baseline_invalid",
            material_changes_present=material_changes_present,
            review_required=True,
            changed_files=changed_files,
            untracked_files=new_untracked,
            staged_files=new_staged,
            unstaged_files=new_unstaged,
            baseline_head_sha=baseline.head_sha,
            post_head_sha=post_snapshot.head_sha,
            head_changed=head_changed,
            blocked_reason="baseline_dirty",
            baseline_dirty=True,
            safe_summary="Baseline snapshot was already dirty; automatic completion must remain blocked.",
        )

    if not material_changes_present:
        return GitMaterialChangeResult(
            status="no_material_changes",
            material_changes_present=False,
            review_required=False,
            changed_files=[],
            untracked_files=[],
            staged_files=[],
            unstaged_files=[],
            baseline_head_sha=baseline.head_sha,
            post_head_sha=post_snapshot.head_sha,
            head_changed=False,
            blocked_reason=None,
            baseline_dirty=False,
            safe_summary="No material repository changes were detected.",
        )

    return GitMaterialChangeResult(
        status="material_changes_detected",
        material_changes_present=True,
        review_required=True,
        changed_files=changed_files,
        untracked_files=new_untracked,
        staged_files=new_staged,
        unstaged_files=new_unstaged,
        baseline_head_sha=baseline.head_sha,
        post_head_sha=post_snapshot.head_sha,
        head_changed=head_changed,
        blocked_reason="material_changes_detected",
        baseline_dirty=False,
        safe_summary="Material repository changes were detected and require reviewer involvement.",
    )


@dataclass(frozen=True)
class _GitCommandResult:
    ok: bool
    stdout: str | None = None
    lines: tuple[str, ...] = ()
    error_message_redacted: str | None = None
    invalid_repo: bool = False


def _run_git(repo: Path, *args: str) -> _GitCommandResult:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return _GitCommandResult(ok=False, error_message_redacted="git executable is unavailable")
    except subprocess.TimeoutExpired:
        return _GitCommandResult(ok=False, error_message_redacted="git command timed out")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip().lower()
        invalid_repo = "not a git repository" in stderr
        return _GitCommandResult(
            ok=False,
            error_message_redacted="invalid git repository" if invalid_repo else "git command failed",
            invalid_repo=invalid_repo,
        )

    stdout = completed.stdout.strip()
    lines = tuple(line.rstrip("\n") for line in completed.stdout.splitlines())
    return _GitCommandResult(ok=True, stdout=stdout, lines=lines)


def _parse_status_lines(lines: tuple[str, ...]) -> dict[str, list[str]]:
    tracked: set[str] = set()
    untracked: set[str] = set()
    staged: set[str] = set()
    unstaged: set[str] = set()

    for line in lines:
        if line.startswith("?? "):
            path = line[3:].strip()
            if path:
                untracked.add(path)
            continue
        if len(line) < 4:
            continue
        staged_code = line[0]
        unstaged_code = line[1]
        path = _parse_status_path(line[3:])
        if not path:
            continue
        tracked.add(path)
        if staged_code != " ":
            staged.add(path)
        if unstaged_code != " ":
            unstaged.add(path)

    return {
        "tracked": sorted(tracked),
        "untracked": sorted(untracked),
        "staged": sorted(staged),
        "unstaged": sorted(unstaged),
    }


def _parse_status_path(path_text: str) -> str:
    path = path_text.strip()
    if " -> " in path:
        return path.split(" -> ", 1)[1].strip()
    return path


def _new_files(before: tuple[str, ...], after: tuple[str, ...]) -> list[str]:
    return sorted(set(after) - set(before))


def _git_diff_changed_files(repo: Path, baseline_head: str, post_head: str) -> list[str] | None:
    result = _run_git(repo, "diff", "--name-status", "--find-renames", baseline_head, post_head)
    if not result.ok:
        return None

    changed: set[str] = set()
    for line in result.lines:
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path = parts[-1].strip()
        if path:
            changed.add(path)
    return sorted(changed)
