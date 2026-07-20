"""Host-side upstream-sync hardening — regression coverage for 2026-07-20.

Three defects from the 2026-07-20 sync incident:

1. ``upstream-sync-finalize.sh`` executed ``action=finalize`` even when the
   repo was NOT rebased onto ``upstream_sha`` (the Mode B agent had aborted its
   rebase but still requested finalize) — the rebase script then replayed
   hundreds of commits and died on conflicts, triggering a pointless rollback
   and gateway restart.
2. The finalize outcome was only observable through the requesting agent
   session, which the finalizer's own gateway restart kills — the operator got
   silence.  The finalizer now posts the outcome to Slack directly.
3. Concurrent git writers (host rebase vs. anything else) killed each other via
   ``.git/index.lock``.  The rebase script now serializes on a repo-level flock.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FINALIZE = REPO_ROOT / "scripts" / "upstream-sync-finalize.sh"
REBASE = REPO_ROOT / "scripts" / "rebase-local-customizations.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "local/customizations")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "f.txt").write_text("two\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "tip")
    return repo


def _stub_scripts(tmp_path: Path) -> tuple[Path, Path]:
    """Fake SCRIPTS_DIR whose scripts record invocations instead of acting."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    calls = tmp_path / "calls.log"
    for name in (
        "rebase-local-customizations.sh",
        "upstream-sync-smoketest.sh",
        "upstream-sync-rollback.sh",
    ):
        p = scripts / name
        p.write_text(f'#!/usr/bin/env bash\necho "{name} $@" >> "{calls}"\nexit 0\n')
        p.chmod(0o755)
    return scripts, calls


def _run_finalize(repo, state, scripts, extra_env=None, path_prepend=None):
    env = dict(os.environ)
    env.update(
        {
            "HERMES_SYNC_STATE_DIR": str(state),
            "HERMES_SCRIPTS_DIR": str(scripts),
            "HERMES_REPO": str(repo),
            # Keep the ACL self-heal inert in the test sandbox.
            "SUDO_ASKPASS": "/bin/false",
        }
    )
    if extra_env:
        env.update(extra_env)
    if path_prepend:
        env["PATH"] = f"{path_prepend}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(FINALIZE)], env=env, capture_output=True, text=True, timeout=60
    )


@pytest.fixture()
def state(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


def _request(state: Path, action: str, upstream_sha: str, backup_ref: str = "backup/x"):
    (state / "finalize-request.json").write_text(
        json.dumps(
            {"action": action, "upstream_sha": upstream_sha, "backup_ref": backup_ref}
        )
    )


def _result(state: Path) -> dict:
    return json.loads((state / "finalize-result.json").read_text())


class TestFinalizeRequiresRebasedHead:
    def test_finalize_with_unrebased_head_fails_without_rollback(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        # A sha the current HEAD does NOT descend from: a divergent branch tip.
        _git(repo, "checkout", "-qb", "divergent", "HEAD~1")
        (repo / "g.txt").write_text("x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "divergent tip")
        divergent = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")

        scripts, calls = _stub_scripts(tmp_path)
        _request(state, "finalize", divergent)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert "not rebased" in res["detail"]
        # The repo was left untouched: no rebase attempt, no rollback, and the
        # pending decision state survives for a retry.
        assert not calls.exists() or "rollback" not in calls.read_text()
        assert not calls.exists() or "rebase-local" not in calls.read_text()

    def test_finalize_with_rebased_head_runs_pipeline(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")

        scripts, calls = _stub_scripts(tmp_path)
        _request(state, "finalize", parent)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        logged = calls.read_text()
        assert "rebase-local-customizations.sh" in logged
        assert "upstream-sync-smoketest.sh" in logged


class TestFinalizeNotifiesSlack:
    def _curl_stub(self, tmp_path: Path) -> tuple[Path, Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "curl.log"
        stub = bin_dir / "curl"
        stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log}"\nexit 0\n')
        stub.chmod(0o755)
        return bin_dir, log

    def test_outcome_posted_to_slack_independent_of_agent_session(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")
        scripts, _ = _stub_scripts(tmp_path)
        bin_dir, curl_log = self._curl_stub(tmp_path)
        env_file = tmp_path / "hermes.env"
        env_file.write_text("SLACK_BOT_TOKEN=xoxb-test-token\n")

        _request(state, "finalize", parent)
        proc = _run_finalize(
            repo,
            state,
            scripts,
            extra_env={"HERMES_ENV_FILE": str(env_file)},
            path_prepend=str(bin_dir),
        )

        assert _result(state)["status"] == "ok", proc.stderr
        logged = curl_log.read_text()
        assert "chat.postMessage" in logged
        assert "ok" in logged

    def test_missing_token_does_not_break_the_run(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")
        scripts, _ = _stub_scripts(tmp_path)

        _request(state, "finalize", parent)
        proc = _run_finalize(
            repo, state, scripts, extra_env={"HERMES_ENV_FILE": str(tmp_path / "absent.env")}
        )
        assert _result(state)["status"] == "ok", proc.stderr


class TestRepoLock:
    def test_rebase_script_refuses_to_run_while_repo_lock_is_held(self, tmp_path):
        # Minimal layout so the script resolves REPO to cwd.
        repo = _make_repo(tmp_path)
        (repo / "agent").mkdir()
        (repo / "gateway").mkdir()

        lock_path = repo / ".git" / "hermes-repo.lock"
        holder = subprocess.Popen(
            ["flock", str(lock_path), "sleep", "30"],
        )
        try:
            # Give the holder a moment to acquire.
            subprocess.run(["sleep", "0.5"])
            proc = subprocess.run(
                ["bash", str(REBASE)],
                cwd=repo,
                env={**os.environ, "HERMES_REPO_LOCK_TIMEOUT": "1"},
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            holder.terminate()
            holder.wait()
        assert proc.returncode != 0
        assert "repo lock" in (proc.stderr + proc.stdout).lower()
