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
SYNC = REPO_ROOT / "scripts" / "sync-local-customizations.sh"


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
        "sync-local-customizations.sh",
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
        assert "sync-local-customizations.sh" in logged
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


def _stub_bin(tmp_path: Path) -> Path:
    """PATH shim so the script never touches the real gateway."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    hermes = bin_dir / "hermes"
    hermes.write_text('#!/usr/bin/env bash\nexit 0\n')
    hermes.chmod(0o755)
    return bin_dir


class TestPersonalRemoteIntegrationAfterHistoryRewrite:
    """2026-07-27: the sync destroyed its own finished work.

    The script rebased the fresh commits back onto its own pre-rewrite
    lineage, hit a conflict, and rolled back a good rebase. The sync now
    merges instead of rebasing, so it cannot rewrite what it was handed: a
    conflict stops the run with the work intact.
    """

    def _world(
        self, tmp_path: Path, local_touches_core: bool = False
    ) -> tuple[Path, Path, Path, str]:
        """upstream(main: u1,u2) / repo(branch off u1 + local commit) /
        personal(shared branch + another host's commit).

        ``local_touches_core`` gives the local branch a commit on the same file
        upstream changed — the shape of every feature the operator is asked to
        decide (F2/F3 in the 2026-07-27 run). Its resolution only exists on the
        rewritten branch, so replaying it onto the pre-rewrite shared lineage
        conflicts, exactly as production did.
        """
        up_work = tmp_path / "upstream-work"
        up_work.mkdir()
        _git(up_work, "init", "-q", "-b", "main")
        _git(up_work, "config", "user.email", "u@u")
        _git(up_work, "config", "user.name", "u")
        (up_work / "core.py").write_text("VERSION = 1\n")
        _git(up_work, "add", "-A")
        _git(up_work, "commit", "-qm", "u1")
        u1 = _git(up_work, "rev-parse", "HEAD")
        (up_work / "core.py").write_text("VERSION = 2\n")
        _git(up_work, "add", "-A")
        _git(up_work, "commit", "-qm", "u2")
        u2 = _git(up_work, "rev-parse", "HEAD")
        upstream = tmp_path / "upstream.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(up_work), str(upstream)], check=True
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "local/customizations")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "fetch", "-q", str(upstream), "main")
        _git(repo, "reset", "-q", "--hard", u1)
        # Layout the script's REPO heuristic keys off, plus the sync helper it
        # calls after a successful rebase.
        (repo / "agent").mkdir()
        (repo / "gateway").mkdir()
        (repo / "scripts").mkdir()
        helper = repo / "scripts" / "sync-runtime-scripts.sh"
        helper.write_text("#!/usr/bin/env bash\nexit 0\n")
        helper.chmod(0o755)
        (repo / "agent" / "__init__.py").write_text("")
        (repo / "gateway" / "__init__.py").write_text("")
        (repo / "custom.py").write_text("LOCAL = True\n")
        if local_touches_core:
            (repo / "core.py").write_text("VERSION = 1\nLOCAL_PATCH = True\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "local customization")

        personal = tmp_path / "personal.git"
        subprocess.run(["git", "init", "-q", "--bare", str(personal)], check=True)
        _git(repo, "remote", "add", "origin", str(personal))
        _git(repo, "push", "-q", "origin", "local/customizations")

        other = tmp_path / "other-host"
        subprocess.run(
            ["git", "clone", "-q", "-b", "local/customizations", str(personal), str(other)],
            check=True,
        )
        _git(other, "config", "user.email", "o@o")
        _git(other, "config", "user.name", "o")
        (other / "fam.py").write_text("OTHER_HOST = True\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", "fix(fam): another host's work")
        _git(other, "push", "-q", "origin", "local/customizations")

        return repo, personal, upstream, u2

    def _run(self, repo: Path, personal: Path, upstream: Path, tmp_path: Path):
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(home),
                "HERMES_PERSONAL_REMOTE_URL": str(personal),
                "HERMES_UPSTREAM_FETCH_URL": str(upstream),
                # Any non-empty value: pushing to a local bare repo never
                # consults the askpass helper, but an empty token makes
                # push_personal_branch skip the push it is here to exercise.
                "GITHUB_TOKEN": "dummy",
                "PATH": f"{_stub_bin(tmp_path)}:{os.environ['PATH']}",
            }
        )
        return subprocess.run(
            ["bash", str(SYNC)],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_a_rewritten_branch_is_never_destroyed_by_integration(self, tmp_path):
        """Работу, которую нам передали, интеграция обязана сохранить.

        В 2026-07-27 скрипт принял собственную дорефрешенную линию за 742
        чужих коммита, переложил на неё 428 свежих, упёрся в конфликт и
        откатил законченный ребейз. Теперь синхронизация сливает, а не
        переписывает: конфликт между внешне переписанной веткой и общей
        линией останавливает прогон, а не уничтожает работу. Ветка остаётся
        ровно там, где была, дерево — чистым, оператор получает инструкцию.
        """
        repo, personal, upstream, u2 = self._world(tmp_path, local_touches_core=True)
        # Кто-то переписал ветку снаружи: SHA сменились, разрешение
        # both-modified файла существует только на новой линии.
        subprocess.run(["git", "rebase", u2], cwd=repo, capture_output=True, text=True)
        (repo / "core.py").write_text("VERSION = 2\nLOCAL_PATCH = True\n")
        _git(repo, "add", "-A")
        subprocess.run(
            ["git", "rebase", "--continue"],
            cwd=repo,
            env={**os.environ, "GIT_EDITOR": "true"},
            check=True,
            capture_output=True,
            text=True,
        )
        handed_in = _git(repo, "rev-parse", "HEAD")

        proc = self._run(repo, personal, upstream, tmp_path)

        assert proc.returncode != 0, "конфликт интеграции обязан быть заметен"
        assert _git(repo, "rev-parse", "HEAD") == handed_in, (
            "переданную работу переписали или откатили\n"
            f"{_git(repo, 'log', '--oneline', '--graph', '-12')}\n"
            f"stderr:\n{proc.stderr}"
        )
        assert _git(repo, "status", "--porcelain") == "", "дерево осталось в конфликте"
        assert (repo / "core.py").read_text() == "VERSION = 2\nLOCAL_PATCH = True\n"
        assert (repo / "custom.py").exists()
        assert "integrate manually" in proc.stderr

    def test_unrewritten_branch_still_integrates_the_other_host_work(self, tmp_path):
        # Mode A: nobody rewrote anything; the shared branch is simply ahead.
        repo, personal, upstream, _u2 = self._world(tmp_path)

        proc = self._run(repo, personal, upstream, tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert (repo / "fam.py").exists()
        assert (repo / "custom.py").exists()
        assert (repo / "core.py").read_text() == "VERSION = 2\n"


class TestStaleLeaseIsReintegratedNotFatal:
    """The push comes last, after the rebase has already landed locally. If the
    other host pushes in the window between our fetch and our push, the
    --force-with-lease check rejects us — and a bare `push_personal_branch`
    under `set -e` killed the script there, so the finalizer rolled back a
    rebase that was fine. Same shape as the 2026-07-27 outage: a late, benign
    failure destroying finished work. Re-integrate and push once instead.
    """

    def test_a_push_landing_mid_run_is_absorbed(self, tmp_path):
        world = TestPersonalRemoteIntegrationAfterHistoryRewrite()
        repo, personal, upstream, _u2 = world._world(tmp_path)
        other = tmp_path / "other-host"

        # The sync helper runs immediately before the push — a precise stand-in
        # for "the other host committed while we were working".
        helper = repo / "scripts" / "sync-runtime-scripts.sh"
        helper.write_text(
            "#!/usr/bin/env bash\n"
            "set -e\n"
            f'cd "{other}"\n'
            'echo "LATE = True" > late.py\n'
            "git add -A\n"
            'git commit -qm "fix(fam): committed while the sync was running"\n'
            "git push -q origin local/customizations\n"
        )
        helper.chmod(0o755)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "test: racing sync helper")

        proc = world._run(repo, personal, upstream, tmp_path)

        assert proc.returncode == 0, proc.stderr
        # Their late commit is ours now...
        assert (repo / "late.py").exists(), proc.stderr
        # ...and the shared branch carries what we hold: the push went through.
        remote_tip = subprocess.run(
            ["git", "rev-parse", "local/customizations"],
            cwd=personal,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert remote_tip == _git(repo, "rev-parse", "HEAD"), proc.stderr


class TestFinalizeReportsWhichStageFailed:
    """The 2026-07-27 rollback was announced as "smoketest failed" when the
    rebase stage had died first — ``if rebase && smoketest`` collapses two
    stages into one status, and the operator-facing summary guessed."""

    def _stubs(self, tmp_path: Path, rebase_rc: int, smoketest_rc: int) -> Path:
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for name, rc in (
            ("sync-local-customizations.sh", rebase_rc),
            ("upstream-sync-smoketest.sh", smoketest_rc),
            ("upstream-sync-rollback.sh", 0),
        ):
            p = scripts / name
            p.write_text(f"#!/usr/bin/env bash\nexit {rc}\n")
            p.chmod(0o755)
        return scripts

    def test_rebase_stage_failure_is_named_as_such(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")
        scripts = self._stubs(tmp_path, rebase_rc=1, smoketest_rc=0)

        _request(state, "finalize", parent)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "rebase"

    def test_smoketest_stage_failure_is_named_as_such(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")
        scripts = self._stubs(tmp_path, rebase_rc=0, smoketest_rc=1)

        _request(state, "finalize", parent)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "smoketest"


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
                ["bash", str(SYNC)],
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


class TestActionAliasKeepsOldRequestsWorking:
    """Скилл живёт в репозитории и в рантайм-копии; между их обновлением есть
    окно, в котором запрос выписывается старым скиллом. Такой запрос обязан
    отработать, а не упереться в unknown action."""

    def test_sync_action_runs_the_sync_script(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        scripts, calls = _stub_scripts(tmp_path)
        _request(state, "sync", _git(repo, "rev-parse", "HEAD"))
        _run_finalize(repo, state, scripts)
        assert "sync-local-customizations.sh" in calls.read_text()

    def test_legacy_rebase_action_runs_the_same_script(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        scripts, calls = _stub_scripts(tmp_path)
        _request(state, "rebase", _git(repo, "rev-parse", "HEAD"))
        _run_finalize(repo, state, scripts)
        assert "sync-local-customizations.sh" in calls.read_text()
