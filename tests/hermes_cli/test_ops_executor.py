import subprocess
from pathlib import Path

import pytest

from hermes_cli.ops_catalog import resolve_operation
from hermes_cli.ops_executor import OpsExecutionError, execute_operation


def _init_repo(tmp_path: Path, branch: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", branch], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo


def test_executes_a_read_operation_and_returns_output(tmp_path):
    repo = _init_repo(tmp_path, "main")
    result = execute_operation(resolve_operation("git_status", {}), cwd=repo)
    assert result["status"] == 0
    assert "main" in result["output"]


def test_refuses_to_run_inside_a_per_run_worktree(tmp_path):
    repo = _init_repo(tmp_path, "hermes-run/abc123")
    with pytest.raises(OpsExecutionError) as exc:
        execute_operation(resolve_operation("git_status", {}), cwd=repo)
    assert "run_branch" in str(exc.value)


def test_never_invokes_a_shell(tmp_path):
    repo = _init_repo(tmp_path, "main")
    seen = {}

    def fake_runner(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    execute_operation(resolve_operation("git_status", {}), cwd=repo, subprocess_runner=fake_runner)
    assert isinstance(seen["argv"], list)
    assert seen["kwargs"].get("shell") is not True


def test_truncates_very_long_output(tmp_path):
    repo = _init_repo(tmp_path, "main")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "x" * 20000, "")

    result = execute_operation(resolve_operation("git_status", {}), cwd=repo, subprocess_runner=fake_runner)
    assert result["truncated"] is True
    assert len(result["output"]) <= 8000


def test_timeout_is_reported_not_raised_as_a_bare_exception(tmp_path):
    repo = _init_repo(tmp_path, "main")

    def fake_runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    with pytest.raises(OpsExecutionError) as exc:
        execute_operation(resolve_operation("git_status", {}), cwd=repo, subprocess_runner=fake_runner)
    assert "timeout" in str(exc.value)


def test_fails_closed_when_the_branch_cannot_be_resolved(tmp_path):
    repo = _init_repo(tmp_path, "main")
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        # Simulate `git rev-parse` failing (not a repo, lock contention,
        # permission error, ...): non-zero return code, no usable stdout.
        return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")

    with pytest.raises(OpsExecutionError):
        execute_operation(resolve_operation("git_status", {}), cwd=repo, subprocess_runner=fake_runner)

    # Only the branch-check call happened; the operation's own command must
    # never be invoked once the branch cannot be resolved.
    assert len(calls) == 1
