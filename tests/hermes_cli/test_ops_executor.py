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


def test_remote_operations_get_a_transient_credential_helper(tmp_path, monkeypatch):
    env_dir = tmp_path / ".hermes"
    env_dir.mkdir()
    (env_dir / ".env").write_text("GITHUB_TOKEN=ghp_example\n")
    monkeypatch.setenv("HERMES_HOME", str(env_dir))

    from hermes_cli.ops_executor import _git_credential_env

    env = _git_credential_env("git_push")
    assert env["GITHUB_TOKEN"] == "ghp_example"


def test_local_operations_get_no_credentials(tmp_path, monkeypatch):
    env_dir = tmp_path / ".hermes"
    env_dir.mkdir()
    (env_dir / ".env").write_text("GITHUB_TOKEN=ghp_example\n")
    monkeypatch.setenv("HERMES_HOME", str(env_dir))

    from hermes_cli.ops_executor import _git_credential_env

    # Токен не раздаётся операциям, которым он не нужен.
    assert _git_credential_env("git_status") == {}


def test_credential_helper_is_passed_per_invocation_not_persisted(tmp_path, monkeypatch):
    # A fake GITHUB_TOKEN source is required here: without it,
    # _git_credential_env legitimately returns {} (no .env found), the
    # helper is correctly omitted, and this test would degrade into
    # (accidentally) exercising the host's real ~/.hermes/.env instead of a
    # controlled fixture.
    env_dir = tmp_path / ".hermes"
    env_dir.mkdir()
    (env_dir / ".env").write_text("GITHUB_TOKEN=ghp_example\n")
    monkeypatch.setenv("HERMES_HOME", str(env_dir))

    repo = _init_repo(tmp_path, "main")
    calls = []

    def fake_runner(argv, **kwargs):
        # NOTE: execute_operation issues two subprocess calls per invocation —
        # the branch check (`git rev-parse ...`, no credentials involved) and
        # then the operation's own argv. Recording every call (rather than
        # just the first, as a naive fake would) lets us assert against the
        # actual push invocation instead of the unrelated branch check. The
        # branch check must resolve to a real (non-run) branch name, or
        # execute_operation fails closed before the push is ever attempted.
        calls.append(argv)
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    execute_operation(
        resolve_operation("git_push", {"remote": "origin", "branch": "main"}),
        cwd=repo, subprocess_runner=fake_runner,
    )
    push_argv = calls[-1]
    joined = " ".join(push_argv)
    assert "credential.helper" in joined
    # Секрет передаётся окружением, а не аргументом командной строки: argv виден в ps.
    assert "ghp_" not in joined


def test_local_operations_strip_an_ambient_github_token(tmp_path, monkeypatch):
    # Regression test for: env=None means "inherit the ambient process
    # environment as-is". In production, hermes_cli.env_loader.load_hermes_dotenv()
    # already loaded GITHUB_TOKEN into os.environ at import time (it's called
    # from cli.py, main.py, gateway/run.py -- the very processes that host
    # this executor). So a bare inherit would leak the token into a
    # non-remote operation's subprocess too, even though _git_credential_env
    # correctly returns {} for it -- the "only 3 operations get the token"
    # invariant would only look enforced, not actually be enforced. Simulate
    # that by setting GITHUB_TOKEN ambiently ourselves.
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ambient_example")

    repo = _init_repo(tmp_path, "main")
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    execute_operation(resolve_operation("git_status", {}), cwd=repo, subprocess_runner=fake_runner)

    # calls[-1] is the operation's own invocation (git_status), not the
    # branch check -- that's the one whose env matters here.
    op_argv, op_kwargs = calls[-1]
    assert "status" in op_argv
    op_env = op_kwargs.get("env")
    assert op_env is not None
    assert "GITHUB_TOKEN" not in op_env


@pytest.mark.parametrize("op_id, params", [
    ("git_fetch", {"remote": "origin"}),
    ("git_push_force_with_lease", {"remote": "origin", "branch": "main"}),
])
def test_other_remote_operations_also_receive_credentials(tmp_path, monkeypatch, op_id, params):
    # Coverage for _REMOTE_OPERATIONS beyond git_push, so a future edit that
    # silently drops git_fetch or git_push_force_with_lease from the set
    # gets caught here instead of failing quietly in production.
    env_dir = tmp_path / ".hermes"
    env_dir.mkdir()
    (env_dir / ".env").write_text("GITHUB_TOKEN=ghp_example\n")
    monkeypatch.setenv("HERMES_HOME", str(env_dir))

    repo = _init_repo(tmp_path, "main")
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "main\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    execute_operation(resolve_operation(op_id, params), cwd=repo, subprocess_runner=fake_runner)

    op_argv, op_kwargs = calls[-1]
    joined = " ".join(op_argv)
    assert "credential.helper" in joined
    assert "ghp_" not in joined
    assert op_kwargs.get("env", {}).get("GITHUB_TOKEN") == "ghp_example"
