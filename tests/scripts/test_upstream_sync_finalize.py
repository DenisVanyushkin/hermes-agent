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

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import upstream_sync_apply, upstream_sync_gate

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


def _manifest_receipt_preamble() -> str:
    """Make test doubles emit a receipt whose side comes from the checkout."""
    return f'''PYTHON={sys.executable!r}
GATE={str(REPO_ROOT / "scripts" / "upstream_sync_gate.py")!r}
SEL=""
WT=""
while [ $# -gt 0 ]; do case "$1" in
  --selection-from) SEL="$2"; shift 2 ;;
  --attempt-root|--boundary) shift 2 ;;
  *) WT="$1"; shift ;;
esac; done
HEAD="$(${{PYTHON}} -c 'import subprocess,sys; print(subprocess.check_output(["git","-C",sys.argv[1],"rev-parse","HEAD"], text=True).strip())' "$WT")"
SIDE="$(${{PYTHON}} - "$SEL" "$HEAD" <<'PY'
import json,sys
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text())
print("pre" if sys.argv[2] == m["before"] else "post" if sys.argv[2] == m["after"] else "wrong")
PY
)"
DIGEST="$(sha256sum "$SEL" | awk '{{print $1}}')"
"$PYTHON" "$GATE" receipt --source manifest --side "$SIDE" --digest "$DIGEST"
"$PYTHON" "$GATE" receipt --source manifest --side "$SIDE" --stage final --digest "$DIGEST"
echo 'fork test duration: seconds=2'
'''


def _manifest_receipt_after_parse_pre_only() -> str:
    """Receipt command for doubles that already parsed and retained WT/SEL."""
    python = str(sys.executable)
    gate = str(REPO_ROOT / "scripts" / "upstream_sync_gate.py")
    return f'''HEAD="$({python} -c 'import subprocess,sys; print(subprocess.check_output(["git","-C",sys.argv[1],"rev-parse","HEAD"], text=True).strip())' "$WT")"
SIDE="$({python} -c 'import json,sys; m=json.load(open(sys.argv[1])); print("pre" if sys.argv[2] == m["before"] else "post" if sys.argv[2] == m["after"] else "wrong")' "$SEL" "$HEAD")"
{python} {gate} receipt --source manifest --side "$SIDE" --digest "$(sha256sum "$SEL" | awk '{{print $1}}')"
'''


def _manifest_receipt_after_parse() -> str:
    python = str(sys.executable)
    gate = str(REPO_ROOT / "scripts" / "upstream_sync_gate.py")
    return (
        _manifest_receipt_after_parse_pre_only()
        + f'''{python} {gate} receipt --source manifest --side "$SIDE" --stage final --digest "$(sha256sum "$SEL" | awk '{{print $1}}')"\n'''
    )


def _stub_scripts(tmp_path: Path) -> tuple[Path, Path]:
    """Fake SCRIPTS_DIR whose scripts record invocations instead of acting.

    ``run-fork-tests.sh`` prints one fixed pytest-like log, so the baseline and
    post-merge runs compare equal and the apply-merge test gate passes unless a
    test overrides the stub. The two Python helpers are the real ones: the gate
    and the decision memory are pure functions worth exercising for real.
    """
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
    tests_stub = scripts / "run-fork-tests.sh"
    tests_stub.write_text(
        "#!/usr/bin/env bash\n"
        + _manifest_receipt_preamble()
        + "echo '0 failed, 5 passed in 2.00s'\n"
    )
    tests_stub.chmod(0o755)
    # Copied by pattern, not by name: a hand-kept list silently omits every new
    # helper, and the finalizer then fails at runtime with ModuleNotFoundError
    # inside a subprocess - far from the line that forgot to add it.
    for helper in sorted((REPO_ROOT / "scripts").glob("upstream_sync_*.py")):
        (scripts / helper.name).write_text(helper.read_text())
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
            # apply-decisions runs the python helpers with this interpreter and
            # must never reach a model or Slack from a test.
            "HERMES_PYTHON": sys.executable,
            "HERMES_SYNC_RESOLVER_CMD": env.get("HERMES_SYNC_RESOLVER_CMD", "false"),
            "HERMES_SYNC_SLACK_CMD": env.get("HERMES_SYNC_SLACK_CMD", "true"),
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


def _latest_attempt(state: Path) -> Path:
    attempts = sorted(
        path for path in (state / "attempts").glob("*/*") if path.is_dir()
    )
    assert attempts
    return attempts[-1]


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

    def test_finalize_receipt_check_ignores_duration_sidecar(self, tmp_path, state):
        repo = _make_repo(tmp_path)
        parent = _git(repo, "rev-parse", "HEAD~1")

        scripts, _ = _stub_scripts(tmp_path)
        _request(state, "finalize", parent)
        proc = _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "ok", proc.stderr


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

        assert proc.returncode == 0, proc.stderr
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
        # Обманка вместо прогона тестов форка: настоящий run-fork-tests.sh
        # посчитает набор собственных тестов в этом временном репозитории,
        # получит пустоту и откажется работать — гейт сработал бы не на том
        # предмете, который проверяет этот класс.
        inert = tmp_path / "inert-tests.sh"
        inert.write_text(
            "#!/usr/bin/env bash\n"
            + _manifest_receipt_preamble()
            + "echo FAILED tests/known.py::test_flaky - AssertionError\n"
            + "echo 1 failed, 5 passed in 2.00s\n"
        )
        inert.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(home),
                "HERMES_SYNC_TEST_CMD": str(inert),
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


def test_finalize_forwards_host_state_invariant_mode_to_prepare():
    text = FINALIZE.read_text(encoding="utf-8")
    assert "invariant-mode.json" in text
    assert "--invariant-mode" in text
    assert "apply-prepare.json" in text


def test_finalize_reports_manual_break_glass_in_detail():
    text = FINALIZE.read_text(encoding="utf-8")
    assert "BREAK_GLASS" in text
    assert "structural gate was not executed" in text


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


# ---------------------------------------------------------------------------
# apply-merge — ingesting a merge the sandboxed agent built in a scratch clone
# ---------------------------------------------------------------------------
#
# The live checkout is bind-mounted :ro into sandboxes (config.yaml), so the
# Mode B agent cannot create a backup ref or commit a merge in it — the
# 2026-08-12 apply died on exactly that. It now merges in a writable
# ``git clone --shared`` under the state dir and hands the host the resulting
# SHA; the host re-derives trust from the commit's parents rather than taking
# the agent's word, then fast-forwards.


def _make_divergent_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Repo on ``local/customizations`` plus a divergent ``up`` branch.

    Returns (repo, local_head, upstream_head) — the two sides a Mode B merge
    is expected to join.
    """
    repo = _make_repo(tmp_path)
    local_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "up", "HEAD~1")
    (repo / "g.txt").write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "upstream work")
    upstream_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "local/customizations")
    return repo, local_head, upstream_head


def _make_repo_with_deleted_upstream_test(tmp_path: Path) -> tuple[Path, str, str]:
    """Local fork plus an upstream tip that deletes one test and adds another."""
    repo = tmp_path / "manifest-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "local/customizations")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_upstream_kept.py").write_text("def test_kept(): pass\n")
    deleted = tests / "test_upstream_deleted.py"
    deleted.write_text("def test_deleted(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "upstream base")
    base = _git(repo, "rev-parse", "HEAD")

    (tests / "test_fork_only.py").write_text("def test_fork_only(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fork test")
    local_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-qb", "up", base)
    deleted.unlink()
    (tests / "test_upstream_added.py").write_text("def test_added(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "upstream replaces test")
    upstream_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "local/customizations")
    return repo, local_head, upstream_head


def _scratch_merge(repo: Path, state: Path, base: str, other: str, name="scratch") -> str:
    """Clone *repo* into the state dir, merge *other* into *base*, return the SHA."""
    scratch = state / name
    subprocess.run(
        ["git", "clone", "-q", "--shared", str(repo), str(scratch)],
        check=True,
        capture_output=True,
    )
    _git(scratch, "config", "user.email", "t@t")
    _git(scratch, "config", "user.name", "t")
    _git(scratch, "checkout", "-q", "--detach", base)
    _git(scratch, "-c", "rerere.enabled=false", "merge", "--no-edit", "-q", other)
    return _git(scratch, "rev-parse", "HEAD")


def _apply_request(state: Path, *, upstream_sha, merge_sha, scratch_repo="scratch"):
    (state / "finalize-request.json").write_text(
        json.dumps(
            {
                "action": "apply-merge",
                "upstream_sha": upstream_sha,
                "backup_ref": "",
                "merge_sha": merge_sha,
                "scratch_repo": scratch_repo,
            }
        )
    )


def _gate_only_request(state: Path, *, before, after, boundary, attempt_id="test-run"):
    (state / "finalize-request.json").write_text(
        json.dumps(
            {
                "action": "gate-only",
                "before": before,
                "after": after,
                "boundary": boundary,
                "attempt_id": attempt_id,
            }
        )
    )


class TestApplyMergeFromScratchClone:
    def test_merge_parented_on_head_and_upstream_is_fast_forwarded(
        self, tmp_path, state
    ):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        # The live branch actually advanced to the agent's merge...
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "local/customizations"
        # ...the host made the backup ref itself (the agent cannot), ...
        assert res["backup_ref"]
        assert _git(repo, "rev-parse", res["backup_ref"]) == local_head
        # ...and the post-update tail ran (scripts, push, restart) — not the
        # full sync, which would gate the push on the newer upstream tip.
        logged = calls.read_text()
        assert f"sync-local-customizations.sh --post-update-only {local_head}" in logged
        assert "upstream-sync-smoketest.sh" in logged
        detail = (state / "finalize-detail.log").read_text()
        assert f"run_gate seam: mode=apply before={local_head} after={merge_sha} boundary={upstream_head}" in detail

    def test_merge_not_parented_on_live_head_is_refused(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        stale_base = _git(repo, "rev-parse", "HEAD~1")
        merge_sha = _scratch_merge(repo, state, stale_base, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert "parent" in res["detail"]
        # Untouched: the branch did not move, nothing was pushed, and no
        # rollback fired (there is nothing to roll back from).
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert not calls.exists() or "rollback" not in calls.read_text()
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()

    def test_merge_of_the_wrong_upstream_point_is_refused(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        # A second upstream-ish commit the operator never saw or decided on.
        _git(repo, "checkout", "-q", "up")
        (repo / "h.txt").write_text("later\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "later upstream work")
        later = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")

        merge_sha = _scratch_merge(repo, state, local_head, later)
        scripts, calls = _stub_scripts(tmp_path)

        # The request still claims the approved head; the commit says otherwise.
        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert "parent" in res["detail"]
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()

    @pytest.mark.parametrize("name", ["../escape", "/etc", "nested/dir", ""])
    def test_scratch_repo_outside_the_state_dir_is_refused(self, tmp_path, state, name):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(
            state,
            upstream_sha=upstream_head,
            merge_sha=merge_sha,
            scratch_repo=name,
        )
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert "scratch_repo" in res["detail"]
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()

    def test_unfetchable_scratch_repo_leaves_the_repo_untouched(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(
            state,
            upstream_sha=upstream_head,
            merge_sha="0" * 40,
            scratch_repo="never-created",
        )
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert not calls.exists() or "rollback" not in calls.read_text()


class TestScratchCloneIsAdoptedBeforeReading:
    """The scratch clone is made INSIDE the sandbox: root-owned, and its
    ``objects/info/alternates`` names the sandbox mount
    ``/workspace/live-hermes``, which does not exist on the host. On
    2026-08-15 both would have killed the fetch (``dubious ownership``,
    ``unable to normalize alternate object path``). The finalizer must make
    the clone its own before reading it: chown via sudo, and point the
    alternates at its own object store.
    """

    def test_alternates_naming_the_sandbox_mount_are_repointed(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        alternates = state / "scratch" / ".git" / "objects" / "info" / "alternates"
        # What the sandbox leaves behind: a path that only exists in the container.
        alternates.write_text("/workspace/live-hermes/.git/objects\n")
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        # A --shared clone owns only the merge commit; every parent, tree and
        # blob is borrowed through that alternate. Serving the fetch at all is
        # therefore proof the path was repointed at a store that exists here —
        # a stronger check than reading the file back, which is impossible
        # anyway: the finalizer deletes the clone once the apply succeeds.
        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        assert (repo / "g.txt").read_text() == "upstream\n"   # borrowed blob arrived
        detail = (state / "finalize-detail.log").read_text()
        assert "unable to normalize alternate object path" not in detail, detail

    def test_clone_ownership_is_normalized_with_sudo(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        # A sudo shim: records the request, performs nothing (the test user
        # already owns the clone, so nothing needs to change for the fetch).
        shim = tmp_path / "shim"
        shim.mkdir()
        sudo_log = tmp_path / "sudo.log"
        (shim / "sudo").write_text(
            f'#!/usr/bin/env bash\necho "$@" >> "{sudo_log}"\nexit 0\n'
        )
        (shim / "sudo").chmod(0o755)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts, path_prepend=str(shim))

        assert _result(state)["status"] == "ok", proc.stderr
        logged = sudo_log.read_text() if sudo_log.exists() else ""
        assert "chown -R" in logged and str(state / "scratch") in logged, logged


class TestSharedGateSeam:
    @pytest.mark.parametrize("outcome", ["pass", "block", "unknown"])
    def test_gate_only_uses_the_shared_gate_seam_without_landing(
        self, tmp_path, state, outcome
    ):
        repo, local_head, upstream_head = _make_repo_with_deleted_upstream_test(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        _git(repo, "fetch", str(state / "scratch"), "HEAD")
        scripts, calls = _stub_scripts(tmp_path)
        if outcome == "block":
            TestApplyMergeIsGatedOnForkTests()._node_aware_block_stub(scripts)
        elif outcome == "unknown":
            runner = scripts / "run-fork-tests.sh"
            runner.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'collecting ...'\n"
                "exit 137\n"
            )
            runner.chmod(0o755)

        _gate_only_request(
            state,
            before=local_head,
            after=merge_sha,
            boundary=upstream_head,
        )
        proc = _run_finalize(repo, state, scripts)

        res = json.loads((_latest_attempt(state) / "attempt-result.json").read_text())
        expected_status = "ok" if outcome == "pass" else "failed"
        assert res["status"] == expected_status, proc.stderr + res.get("detail", "")
        assert res["gate_verdict"] == outcome
        assert _git(repo, "rev-parse", "HEAD") == local_head
        detail = res["detail"]
        assert f"run_gate seam: mode=gate-only before={local_head} after={merge_sha} boundary={upstream_head}" in detail
        assert f"run_gate outcome: mode=gate-only outcome={outcome}" in detail
        generations = list((state / "attempts").glob("*/*"))
        assert len(generations) == 1
        assert (generations[0] / "gate-selection.json").exists()
        assert (generations[0] / "attempt.json").exists()


class TestGateOnlyIsolation:
    def test_gate_only_preserves_all_production_state_and_writes_only_generation(
        self, tmp_path, state
    ):
        repo, local_head, upstream_head = _make_repo_with_deleted_upstream_test(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        _git(repo, "fetch", str(state / "scratch"), "HEAD")
        scripts, _ = _stub_scripts(tmp_path)

        # Seed every production-plane artifact so the assertion is about the
        # whole state directory, not only the three files named in T19.
        for name, content in {
            "apply-prepare.json": '{"old":"prepare"}\n',
            "pending.json": '{"old":"pending"}\n',
            "finalize-result.json": '{"old":"result"}\n',
            "finalize-detail.log": "old detail\n",
            "gate-baseline.log": "old baseline\n",
            "gate-post.log": "old post\n",
            "gate-failures.json": '{"merge_sha":"old","before":"old","blocking_failures":[]}\n',
            "gate-triage.json": '{"old":"triage"}\n',
        }.items():
            (state / name).write_text(content)

        def snapshot():
            out = {}
            for path in state.rglob("*"):
                rel = path.relative_to(state)
                if rel.parts[0] == "attempts" or path.name in {
                    "finalize-request.json",
                    "finalize-request.processing.json",
                    "finalize.lock",
                }:
                    continue
                out[str(rel)] = path.read_bytes() if path.is_file() else None
            return out

        before_state = snapshot()
        _gate_only_request(
            state, before=local_head, after=merge_sha, boundary=upstream_head
        )
        proc = _run_finalize(repo, state, scripts)

        assert proc.returncode == 0, proc.stderr
        assert snapshot() == before_state
        attempt = _latest_attempt(state)
        result = json.loads((attempt / "attempt-result.json").read_text())
        assert result["action"] == "gate-only"
        assert result["status"] == "ok"
        assert result["gate_verdict"] == "pass"

    def test_gate_only_retries_create_append_only_generations(self, tmp_path, state):
        repo, local_head, upstream_head = _make_repo_with_deleted_upstream_test(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        _git(repo, "fetch", str(state / "scratch"), "HEAD")
        scripts, _ = _stub_scripts(tmp_path)

        for _ in range(2):
            _gate_only_request(
                state, before=local_head, after=merge_sha, boundary=upstream_head
            )
            proc = _run_finalize(repo, state, scripts)
            assert _latest_attempt(state).joinpath("attempt-result.json").exists(), proc.stderr

        attempts = sorted(
            path for path in (state / "attempts").glob("*/*") if path.is_dir()
        )
        assert len(attempts) == 2
        assert attempts[0].name == "1"
        assert attempts[1].name == "2"
        assert (attempts[0] / "gate-selection.json").read_bytes()
        assert (attempts[1] / "gate-selection.json").read_bytes()


class TestApplyMergeIsGatedOnForkTests:
    """A plausible-looking resolution of a merge-both conflict can still be
    wrong, and the smoketest only proves the tree imports. The agent's merge is
    run through the fork's own tests before it becomes the live branch, exactly
    as the automatic merge is in the sync script.
    """

    def _breaking_tests_stub(self, scripts: Path) -> None:
        # Reports one extra failure whenever the checked-out tree is the merge
        # (it contains g.txt, which only the upstream side adds).
        (scripts / "run-fork-tests.sh").write_text(
            "#!/usr/bin/env bash\n"
            # Новый argv-контракт: граница обязательна и идёт опцией, поэтому
            # worktree больше не $1. Заодно записываем полученную границу —
            # оба прогона гейта обязаны увидеть один и тот же полный SHA.
            'WT=""; BND=""; SEL=""; ROOT=""\n'
            'while [ $# -gt 0 ]; do case "$1" in\n'
            '  --boundary) BND="$2"; shift 2 ;;\n'
            '  --selection-from) SEL="$2"; shift 2 ;;\n'
            '  --attempt-root) ROOT="$2"; shift 2 ;;\n'
            '  *) WT="$1"; shift ;;\n'
            'esac; done\n'
            'printf "%s\\n" "$BND" >> "$(dirname "$0")/boundary-calls.log"\n'
            'printf "%s\\n" "$SEL" >> "$(dirname "$0")/selection-calls.log"\n'
            'printf "%s\\n" "$ROOT" >> "$(dirname "$0")/attempt-root-calls.log"\n'
            + _manifest_receipt_after_parse()
            + "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
            'if [ -f "$WT/g.txt" ]; then echo "FAILED tests/new.py::test_broken_by_merge - E"; fi\n'
            "echo '2 failed, 4 passed in 2.00s'\n"
        )

    def _node_aware_block_stub(self, scripts: Path) -> None:
        """Emit valid structured evidence for a measured block.

        The upstream-parent probe must pass the newly-added upstream node;
        otherwise the gate correctly reports ``unknown`` rather than a block.
        This double therefore distinguishes the probe worktree from the
        baseline/post worktrees and uses paths that exist in the manifest.
        """
        python = str(sys.executable)
        gate = str(REPO_ROOT / "scripts" / "upstream_sync_gate.py")
        (scripts / "run-fork-tests.sh").write_text(
            "#!/usr/bin/env bash\n"
            'SEL=""; WT=""; PROBE=0\n'
            'while [ $# -gt 0 ]; do case "$1" in\n'
            '  --selection-from) SEL="$2"; shift 2 ;;\n'
            '  --attempt-root|--boundary) shift 2 ;;\n'
            '  --probe-nodeids-from) PROBE=1; shift 2 ;;\n'
            '  *) WT="$1"; shift ;;\n'
            'esac; done\n'
            'if [ "$PROBE" -eq 1 ]; then\n'
            "  echo '0 failed, 1 passed in 0.10s'\n"
            "  exit 0\n"
            "fi\n"
            f'HEAD="$({python} -c \'import subprocess,sys; print(subprocess.check_output(["git","-C",sys.argv[1],"rev-parse","HEAD"], text=True).strip())\' "$WT")"\n'
            f'SIDE="$({python} - "$SEL" "$HEAD" <<\'PY\'\n'
            'import json,sys\n'
            'from pathlib import Path\n'
            'm=json.loads(Path(sys.argv[1]).read_text())\n'
            'print("pre" if sys.argv[2] == m["before"] else "post" if sys.argv[2] == m["after"] else "wrong")\n'
            'PY\n'
            ')"\n'
            f'{python} {gate} receipt --source manifest --side "$SIDE" --digest "$(sha256sum "$SEL" | awk \'{{print $1}}\')"\n'
            f'{python} {gate} receipt --source manifest --side "$SIDE" --stage final --digest "$(sha256sum "$SEL" | awk \'{{print $1}}\')"\n'
            "echo 'FAILED tests/test_fork_only.py::test_fork_only - AssertionError'\n"
            'if [ -f "$WT/tests/test_upstream_added.py" ]; then\n'
            "  echo 'FAILED tests/test_upstream_added.py::test_added - AssertionError'\n"
            "  echo '2 failed, 1 passed in 0.10s'\n"
            "else\n"
            "  echo '1 failed, 1 passed in 0.10s'\n"
            "fi\n"
        )
        (scripts / "run-fork-tests.sh").chmod(0o755)

    def test_both_gate_runs_receive_the_same_upstream_boundary(self, tmp_path, state):
        """Обе половины гейта меряют от одной и той же границы.

        Граница решает, что считается тестом форка. Если два прогона получат
        разные значения — например, один полный SHA, а другой remote-tracking
        ref, который успел уехать, — сравнение «до и после» перестанет быть
        сравнением: разойдётся сам сенсор, а не поведение мержа. Поэтому
        проверяется не наличие опции, а совпадение значений и то, что это
        именно утверждённый upstream_head, а не что-то выведенное заново.
        """
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_tests_stub(scripts)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        recorded = (scripts / "boundary-calls.log").read_text().split()
        assert len(recorded) == 2, (
            f"expected one boundary per gate run, got {recorded}; stderr={proc.stderr}"
        )
        assert set(recorded) == {upstream_head}, (
            "the two gate runs did not measure from the approved upstream head; "
            f"expected {upstream_head}, recorded {recorded}"
        )
        selections = (scripts / "selection-calls.log").read_text().splitlines()
        assert len(selections) == 2
        assert len(set(selections)) == 1 and selections[0], (
            "both gate runs must consume the same persisted manifest; "
            f"recorded {selections}"
        )
        selection = Path(selections[0])
        assert selection.name == "gate-selection.json"
        assert selection.parent.parent.parent == state / "attempts"
        roots = (scripts / "attempt-root-calls.log").read_text().splitlines()
        assert roots == [str(state / "attempts")] * 2, (
            "both consumers must be confined to the finalizer's attempt root; "
            f"recorded {roots}"
        )

    def test_node_probe_scope_finalizer_replaces_legacy_log_difference(
        self, tmp_path, state
    ):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_tests_stub(scripts)

        gate_calls = tmp_path / "gate-helper-calls.log"
        real_gate = REPO_ROOT / "scripts" / "upstream_sync_gate.py"
        (scripts / "upstream_sync_gate.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            f"log = Path({str(gate_calls)!r})\n"
            f"real = {str(real_gate)!r}\n"
            "command = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "with log.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(command + '\\n')\n"
            "if command == 'probe-request':\n"
            "    print(json.dumps({'nodeids': [], 'paths': []}))\n"
            "    raise SystemExit(0)\n"
            "if command == 'classify-node-failures':\n"
            "    print(json.dumps({'common_path': [], 'post_only_path': [], 'pre_existing': [], 'unknown': [], 'blocking_failures': []}))\n"
            "    raise SystemExit(0)\n"
            "os.execv(sys.executable, [sys.executable, real, *sys.argv[1:]])\n"
        )

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "ok", proc.stderr
        commands = gate_calls.read_text().splitlines()
        assert "classify-node-failures" in commands, commands
        assert "new-failures" not in commands, commands

    def test_missing_runner_receipt_blocks_landing(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        (scripts / "run-fork-tests.sh").write_text(
            "#!/usr/bin/env bash\n"
            "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
            "echo '1 failed, 5 passed in 2.00s'\n"
        )
        (scripts / "run-fork-tests.sh").chmod(0o755)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        evidence = (proc.stderr + proc.stdout + res.get("detail", "")).lower()
        assert res["status"] == "failed", evidence
        assert res["failed_stage"] == "test-gate"
        assert "receipt" in evidence
        assert "preliminary receipt" in evidence
        assert "final receipt" not in evidence
        assert _git(repo, "rev-parse", "HEAD") == local_head

    def test_preliminary_receipt_without_final_receipt_is_unreadable(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        runner = scripts / "run-fork-tests.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            'WT=""; SEL=""\n'
            'while [ $# -gt 0 ]; do case "$1" in\n'
            '  --selection-from) SEL="$2"; shift 2 ;;\n'
            '  --attempt-root|--boundary) shift 2 ;;\n'
            '  *) WT="$1"; shift ;;\n'
            "esac; done\n"
            + _manifest_receipt_after_parse_pre_only()
            + "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
            + "echo '1 failed, 5 passed in 2.00s'\n"
            + "exit 137\n"
        )
        runner.chmod(0o755)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        evidence = proc.stderr + proc.stdout + res.get("detail", "")
        assert res["status"] == "failed", evidence
        assert res["failed_stage"] == "test-gate"
        failures = json.loads((state / "gate-failures.json").read_text())
        assert failures["unreadable_runs"] == [
            {"source": "baseline", "stage": "receipt"}
        ]
        assert failures["blocking_failures"] == []
        assert _git(repo, "rev-parse", "HEAD") == local_head

    def test_mismatched_runner_receipt_blocks_landing(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        (scripts / "run-fork-tests.sh").write_text(
            "#!/usr/bin/env bash\n"
            "echo 'fork test receipt: contract=v1 source=manifest manifest_sha256=wrong'\n"
            "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
            "echo '1 failed, 5 passed in 2.00s'\n"
        )
        (scripts / "run-fork-tests.sh").chmod(0o755)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        evidence = (proc.stderr + proc.stdout + res.get("detail", "")).lower()
        assert res["status"] == "failed", evidence
        assert res["failed_stage"] == "test-gate"
        assert "receipt" in evidence
        assert "preliminary receipt" in evidence
        assert "final receipt" not in evidence
        assert _git(repo, "rev-parse", "HEAD") == local_head

    def test_merge_introducing_failures_is_not_landed(self, tmp_path, state):
        repo, local_head, upstream_head = _make_repo_with_deleted_upstream_test(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        self._node_aware_block_stub(scripts)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "test-gate"
        assert "tests/test_upstream_added.py::test_added" in res["detail"]
        assert _git(repo, "rev-parse", "HEAD") == local_head          # not landed
        assert (state / "pending.json").exists()                      # decision kept
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()
        assert not calls.exists() or "rollback" not in calls.read_text()
        # The temporary test-gate worktree was removed.
        assert len(_git(repo, "worktree", "list").splitlines()) == 1
        assert "run_gate outcome: mode=apply outcome=block" in (
            state / "finalize-detail.log"
        ).read_text()

    def test_pre_existing_failures_do_not_block(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)   # same known failure before and after

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "ok", proc.stderr
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        assert "run_gate outcome: mode=apply outcome=pass" in (
            state / "finalize-detail.log"
        ).read_text()

    def test_an_unreadable_test_run_refuses_to_land(self, tmp_path, state):
        """No summary line means the run was killed, not clean — the gate must
        not read that as "no new failures" (the sync script draws the same
        distinction via exit code 2)."""
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        (scripts / "run-fork-tests.sh").write_text(
            "#!/usr/bin/env bash\necho 'collecting ...'\nexit 137\n"
        )

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "test-gate"
        assert "run_gate outcome: mode=apply outcome=unknown" in (
            state / "finalize-detail.log"
        ).read_text()
        assert _git(repo, "rev-parse", "HEAD") == local_head


class TestRunnerFinalizeReceiptSeam:
    def test_real_runner_receipt_matches_finalize_contract(self, tmp_path):
        """The production runner and finalize receipt protocol share one seam."""
        repo = tmp_path / "receipt-repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "local/customizations")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "README.md").write_text("base\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        before = _git(repo, "rev-parse", "HEAD")
        tests = repo / "tests"
        tests.mkdir()
        (tests / "test_receipt.py").write_text("def test_receipt(): pass\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "add test")
        after = _git(repo, "rev-parse", "HEAD")

        state = tmp_path / "state"
        report = upstream_sync_gate.prepare_selection_attempt(
            state,
            before=before,
            after=after,
            boundary=before,
            before_paths=[],
            after_paths=["tests/test_receipt.py"],
            boundary_paths=[],
            changed_paths=[],
        )
        manifest = Path(report["attempt_dir"]) / "gate-selection.json"
        stderr_log = tmp_path / "runner.stderr"
        env = {
            **os.environ,
            "HERMES_PYTHON": sys.executable,
            "HERMES_CONTROL_PYTHON": sys.executable,
            "HERMES_UPSTREAM_SYNC_GATE": str(
                REPO_ROOT / "scripts" / "upstream_sync_gate.py"
            ),
        }
        proc = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts" / "run-fork-tests.sh"),
                "--print-selection",
                "--boundary",
                before,
                "--selection-from",
                str(manifest),
                "--attempt-root",
                str(state / "attempts"),
                str(repo),
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        stderr_log.write_text(proc.stderr)

        expected = upstream_sync_gate.fork_test_receipt(
            source="manifest",
            side="post",
            digest=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )
        receipt_check = subprocess.run(
            ["grep", "-Fqx", expected, str(stderr_log)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert receipt_check.returncode == 0, proc.stderr
        assert proc.stdout == "tests/test_receipt.py\n"


class TestGateSelectionManifest:
    def test_attempt_json_is_the_generation_commit_marker(self, tmp_path, monkeypatch):
        """A generation is complete only after its binding metadata appears."""
        real_replace = upstream_sync_gate.os.replace

        def interrupt_commit_marker(source, destination):
            if Path(destination).name == "attempt.json":
                raise OSError("simulated interruption before generation commit")
            real_replace(source, destination)

        monkeypatch.setattr(
            upstream_sync_gate.os, "replace", interrupt_commit_marker
        )
        with pytest.raises(OSError, match="before generation commit"):
            upstream_sync_gate.prepare_selection_attempt(
                tmp_path,
                before="1" * 40,
                after="2" * 40,
                boundary="3" * 40,
                before_paths=["tests/test_before.py"],
                after_paths=["tests/test_after.py"],
                boundary_paths=[],
                changed_paths=[
                    "tests/test_before.py",
                    "tests/test_after.py",
                ],
            )

        attempts = list((tmp_path / "attempts").glob("*/*"))
        assert len(attempts) == 1
        attempt = attempts[0]
        assert (attempt / "gate-selection.json").exists()
        assert not (attempt / "attempt.json").exists()

    def test_interrupted_manifest_write_leaves_no_partial_file(
        self, tmp_path, monkeypatch
    ):
        writer = getattr(upstream_sync_gate, "write_json_atomic", None)
        assert callable(writer), "atomic selection-manifest writer is not implemented"
        attempt = tmp_path / "attempts" / "candidate" / "1"
        attempt.mkdir(parents=True)
        target = attempt / "gate-selection.json"

        def interrupt_replace(source, destination):
            assert Path(source).parent == target.parent
            assert Path(destination) == target
            raise OSError("simulated interruption before rename")

        monkeypatch.setattr(upstream_sync_gate.os, "replace", interrupt_replace)
        with pytest.raises(OSError, match="simulated interruption"):
            writer(target, {"schema_version": "test"})

        assert not target.exists()
        assert list(attempt.iterdir()) == []

    def test_manifest_reports_deleted_path_as_pre_only(self, tmp_path, state):
        repo, local_head, upstream_head = _make_repo_with_deleted_upstream_test(
            tmp_path
        )
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, _ = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        attempts = sorted(
            path
            for path in (state / "attempts").glob("*/*")
            if path.is_dir()
        )
        assert len(attempts) == 1, proc.stderr + (state / "finalize-detail.log").read_text()
        attempt = attempts[0]
        manifest = json.loads((attempt / "gate-selection.json").read_text())
        deleted = "tests/test_upstream_deleted.py"
        assert {item["path"]: item for item in manifest["tests"]}[deleted] == {
            "path": deleted,
            "exists_pre": True,
            "exists_post": False,
        }

        metadata = json.loads((attempt / "attempt.json").read_text())
        assert metadata["schema_version"] == "upstream-sync-gate-attempt/v1"
        assert metadata["before"] == local_head
        assert metadata["after"] == merge_sha
        assert metadata["boundary"] == upstream_head
        assert metadata["generation"] == 1
        assert metadata["run_id"] == f"{metadata['candidate_id']}:1"
        assert manifest["candidate_id"] == metadata["candidate_id"]
        assert manifest["generation"] == metadata["generation"]
        assert manifest["run_id"] == metadata["run_id"]
        assert attempt.parent.name == metadata["candidate_id"]
        assert attempt.name == str(metadata["generation"])
        assert (attempt / "gate-baseline.log").exists()
        assert (attempt / "gate-post.log").exists()

        detail = (state / "finalize-detail.log").read_text()
        report_line = next(
            line.removeprefix("gate selection report: ")
            for line in detail.splitlines()
            if line.startswith("gate selection report: ")
        )
        report = json.loads(report_line)
        assert report["pre_only_paths"] == [deleted]
        assert report["pre_only_path_count"] == 1


class TestHostRecordsDecisionsIntoMemory:
    """Recording the operator's decisions used to be the agent's last step, but
    its session dies with the gateway restart the smoketest triggers — on
    2026-08-12 the memory had to be rebuilt by hand. The host holds the archived
    pending file and outlives the restart, so the host records.
    """

    def test_successful_apply_records_the_decision(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip", "base"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, _ = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "ok", proc.stderr
        memory = json.loads((state / "decision-memory.json").read_text())
        assert len(memory["entries"]) == 1
        entry = memory["entries"][0]
        assert entry["decision"] == "merge-both"
        assert entry["files"] == ["g.txt"]
        assert entry["apply_count"] == 1

    def test_failed_apply_leaves_memory_alone(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        stale_base = _git(repo, "rev-parse", "HEAD~1")
        merge_sha = _scratch_merge(repo, state, stale_base, upstream_head)
        scripts, _ = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "failed"
        assert not (state / "decision-memory.json").exists()


class TestHugeDetailLogStillProducesAResult:
    """The detail was passed to python as an argv element, and Linux caps a
    single argument at 128 KiB. Two full fork-test runs blow past that, so the
    apply died with "Argument list too long" AFTER the merge had landed, been
    pushed and smoke-tested — leaving the decision unarchived, the memory
    unrecorded, and no result for anyone polling (2026-08-15). The truncation
    to 4000 chars already happened inside python, i.e. the oversized value only
    ever existed to be thrown away.
    """

    def test_a_result_is_written_when_the_log_exceeds_the_argv_limit(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        # 300 KiB of output from the publish step — well past MAX_ARG_STRLEN.
        (scripts / "sync-local-customizations.sh").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "sync-local-customizations.sh $@" >> "{calls}"\n'
            "python3 -c \"print('x' * 300000)\"\n"
            "exit 0\n"
        )

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "ok", proc.stderr
        assert len(res["detail"]) <= 4000
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        # The whole log is still on disk next to the result.
        assert (state / "finalize-detail.log").stat().st_size > 200000

    def test_the_decision_is_still_consumed_when_the_log_is_huge(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(json.dumps(
            {"schema": "upstream-sync-pending/v1", "upstream_head": upstream_head,
             "features": [{"id": "F1", "decision": "merge-both", "files": ["g.txt"],
                           "local_subjects": ["tip"]}]}))
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        (scripts / "upstream-sync-smoketest.sh").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "upstream-sync-smoketest.sh $@" >> "{calls}"\n'
            "python3 -c \"print('y' * 300000)\"\n"
            "exit 0\n"
        )

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "ok", proc.stderr
        assert not (state / "pending.json").exists()
        assert len(list(state.glob("pending.json.applied-*"))) == 1
        memory = json.loads((state / "decision-memory.json").read_text())
        assert memory["entries"][0]["decision"] == "merge-both"


class TestAnAlreadyAppliedMergeIsNotAFailure:
    """A second request for a merge that is already the branch tip means a
    duplicate hand-off, not a mismatch. Reporting it as a parent-mismatch
    failure overwrote the real outcome of the run that had just landed it
    (2026-08-15) — the operator was told the apply failed while it was live.
    """

    def test_a_duplicate_request_reports_already_applied_without_touching_anything(
        self, tmp_path, state
    ):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)
        # First apply: lands normally.
        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        _run_finalize(repo, state, scripts)
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        calls.unlink()

        # The duplicate, arriving after the fact.
        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "ok", proc.stderr
        assert "already applied" in res["detail"].lower()
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        # No second publish, no second gateway restart, no rollback.
        assert not calls.exists() or calls.read_text().strip() == "", calls.read_text()


def _decisions_request(state: Path):
    (state / "finalize-request.json").write_text(json.dumps({"action": "apply-decisions"}))


def _slack_recorder(tmp_path: Path) -> tuple[Path, Path]:
    """A HERMES_SYNC_SLACK_CMD that appends every payload to a log and prints a ts."""
    log = tmp_path / "slack.jsonl"
    cmd = tmp_path / "slack.sh"
    cmd.write_text(f'#!/usr/bin/env bash\ncat >> "{log}"; echo >> "{log}"\necho 1786.100\n')
    cmd.chmod(0o755)
    return cmd, log


def _resolver(tmp_path: Path, body: str) -> str:
    r = tmp_path / "resolver.py"
    r.write_text(body)
    return f"{sys.executable} {r}"


class TestAttemptInvariant:
    """A resumable apply attempt is bound to both sides of its input pair."""

    def _conflict_world(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        local_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-qb", "up", "HEAD~1")
        (repo / "f.txt").write_text("upstream conflict\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream conflict")
        upstream_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        return repo, local_head, upstream_head

    def _seed_stale_attempt(
        self, state: Path, repo: Path, *, local_head: str, upstream_head: str,
        pending_local_head: str, pending_upstream_head: str,
    ):
        scratch = state / "scratch"
        subprocess.run(
            ["git", "clone", "-q", "--shared", str(repo), str(scratch)],
            check=True,
            capture_output=True,
        )
        _git(scratch, "config", "user.email", "t@t")
        _git(scratch, "config", "user.name", "t")
        _git(scratch, "checkout", "-q", "--detach", local_head)
        merge = subprocess.run(
            ["git", "-c", "rerere.enabled=false", "merge", "--no-edit", upstream_head],
            cwd=scratch,
            capture_output=True,
            text=True,
        )
        assert merge.returncode != 0
        assert (scratch / ".git" / "MERGE_HEAD").read_text().strip() == upstream_head
        (state / "pending.json").write_text(json.dumps({
            "schema": "upstream-sync-pending/v1",
            "local_head": pending_local_head,
            "upstream_head": pending_upstream_head,
            "features": [],
        }))
        (state / "apply-prepare.json").write_text(json.dumps({
            "schema": "upstream-sync-apply/v1",
            "status": "ready",
            "local_base": local_head,
            "upstream_head": upstream_head,
            "scratch": str(scratch),
            "conflicts": ["f.txt"],
        }))

    def _prepare_or_resume(self, state: Path, repo: Path):
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "upstream_sync_apply.py"),
                "prepare-or-resume",
                "--state", str(state),
                "--live", str(repo),
                "--scratch", "scratch",
                "--in-flight-ok",
            ],
            capture_output=True,
            text=True,
        )

    def test_attempt_archive_recovery_uses_pending_as_commit_marker(self, tmp_path, state, monkeypatch):
        scratch = state / "scratch"
        scratch.mkdir()
        (scratch / "evidence.txt").write_text("old\n")
        (state / "apply-prepare.json").write_text("{\"status\": \"ready\"}\n")
        (state / "pending.json").write_text("{\"local_head\": \"old\"}\n")
        real_replace = upstream_sync_apply.os.replace

        def fail_after_scratch(source, destination):
            result = real_replace(source, destination)
            if Path(source).name == "scratch":
                raise OSError("simulated interruption before commit marker")
            return result

        monkeypatch.setattr(upstream_sync_apply.os, "replace", fail_after_scratch)
        with pytest.raises(OSError, match="commit marker"):
            upstream_sync_apply._archive_stale_attempt(state, scratch)
        assert (state / "pending.json").exists()
        assert list((state / "apply-attempts").glob(".*.tmp"))

        monkeypatch.setattr(upstream_sync_apply.os, "replace", real_replace)
        upstream_sync_apply._archive_stale_attempt(state, scratch)
        assert not (state / "pending.json").exists()
        assert not list((state / "apply-attempts").glob(".*.tmp"))
        archive = next((state / "apply-attempts").iterdir())
        assert (archive / "pending.json").exists()
        assert (archive / "scratch" / "evidence.txt").exists()

    def _seed_pair_only_attempt(
        self, state: Path, repo: Path, *, scratch_head: str,
        pending_local_head: str, pending_upstream_head: str,
        prep_local_base: str, prep_upstream_head: str, merge_head: str = "",
    ):
        scratch = state / "scratch"
        subprocess.run(
            ["git", "clone", "-q", "--shared", str(repo), str(scratch)],
            check=True,
            capture_output=True,
        )
        _git(scratch, "checkout", "-q", "--detach", scratch_head)
        if merge_head:
            (scratch / ".git" / "MERGE_HEAD").write_text(merge_head + "\n")
        (state / "pending.json").write_text(json.dumps({
            "schema": "upstream-sync-pending/v1",
            "local_head": pending_local_head,
            "upstream_head": pending_upstream_head,
            "features": [],
        }))
        (state / "apply-prepare.json").write_text(json.dumps({
            "schema": "upstream-sync-apply/v1",
            "status": "ready",
            "local_base": prep_local_base,
            "upstream_head": prep_upstream_head,
            "scratch": str(scratch),
            "conflicts": [],
        }))

    def test_attempt_invariant_local_identity_is_checked_independently(self, tmp_path, state):
        repo, stale_local, upstream_head = self._conflict_world(tmp_path)
        (repo / "live.txt").write_text("moved\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "live moved")
        live_head = _git(repo, "rev-parse", "HEAD")
        self._seed_pair_only_attempt(
            state, repo,
            scratch_head=live_head,
            pending_local_head=stale_local,
            pending_upstream_head=upstream_head,
            prep_local_base=live_head,
            prep_upstream_head=upstream_head,
        )

        proc = self._prepare_or_resume(state, repo)

        assert proc.returncode == 4, proc.stderr + proc.stdout
        assert json.loads((state / "apply-prepare.json").read_text())["status"] == "new_conflicts"
        assert list((state / "apply-attempts").glob("*/apply-prepare.json"))

    def test_attempt_invariant_upstream_identity_is_checked_independently(self, tmp_path, state):
        repo, local_head, old_upstream = self._conflict_world(tmp_path)
        _git(repo, "checkout", "-q", "up")
        (repo / "upstream-later.txt").write_text("later\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream moved")
        new_upstream = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        self._seed_pair_only_attempt(
            state, repo,
            scratch_head=local_head,
            pending_local_head=local_head,
            pending_upstream_head=new_upstream,
            prep_local_base=local_head,
            prep_upstream_head=old_upstream,
            merge_head=new_upstream,
        )

        proc = self._prepare_or_resume(state, repo)

        assert proc.returncode == 4, proc.stderr + proc.stdout
        assert json.loads((state / "apply-prepare.json").read_text())["status"] == "new_conflicts"
        assert list((state / "apply-attempts").glob("*/apply-prepare.json"))

    def test_attempt_invariant_stale_local_head_is_rotated_before_fresh_prepare(self, tmp_path, state):
        repo, old_local, upstream_head = self._conflict_world(tmp_path)
        self._seed_stale_attempt(
            state, repo,
            local_head=old_local,
            upstream_head=upstream_head,
            pending_local_head=old_local,
            pending_upstream_head=upstream_head,
        )
        (repo / "local.txt").write_text("live moved\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "live moved")
        live_head = _git(repo, "rev-parse", "HEAD")

        proc = self._prepare_or_resume(state, repo)

        assert proc.returncode == 4, proc.stderr + proc.stdout
        pending = json.loads((state / "pending.json").read_text())
        prep = json.loads((state / "apply-prepare.json").read_text())
        assert pending["local_head"] == live_head
        assert prep["local_base"] == live_head
        assert prep["upstream_head"] == upstream_head
        assert _git(state / "scratch", "rev-parse", "HEAD") == live_head
        assert not (state / "scratch" / ".git" / "MERGE_HEAD").exists()
        assert prep["status"] == "new_conflicts"
        archives = list((state / "apply-attempts").glob("*/apply-prepare.json"))
        assert len(archives) == 1
        archive = archives[0].parent
        assert (archive / "pending.json").exists()
        assert (archive / "scratch" / ".git" / "MERGE_HEAD").read_text().strip() == upstream_head

    def test_attempt_invariant_same_local_with_new_upstream_rotates_stale_resume(self, tmp_path, state):
        repo, local_head, old_upstream = self._conflict_world(tmp_path)
        _git(repo, "checkout", "-q", "up")
        (repo / "upstream-later.txt").write_text("later\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream moved")
        new_upstream = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        self._seed_stale_attempt(
            state, repo,
            local_head=local_head,
            upstream_head=old_upstream,
            pending_local_head=local_head,
            pending_upstream_head=new_upstream,
        )

        proc = self._prepare_or_resume(state, repo)

        assert proc.returncode == 4, proc.stderr + proc.stdout
        pending = json.loads((state / "pending.json").read_text())
        prep = json.loads((state / "apply-prepare.json").read_text())
        assert pending["local_head"] == local_head
        assert pending["upstream_head"] == new_upstream
        assert prep["local_base"] == local_head
        assert prep["upstream_head"] == new_upstream
        assert _git(state / "scratch", "rev-parse", "HEAD") == local_head
        assert not (state / "scratch" / ".git" / "MERGE_HEAD").exists()
        assert prep["status"] == "new_conflicts"
        archives = list((state / "apply-attempts").glob("*/apply-prepare.json"))
        assert len(archives) == 1
        archive = archives[0].parent
        assert (archive / "pending.json").exists()
        assert (archive / "scratch" / ".git" / "MERGE_HEAD").read_text().strip() == old_upstream


class TestApplyDecisions:
    """The host applies a decided pending.json end to end: clone, mechanical +
    model resolution, commit, gate, land, publish, archive, memory, and a
    summary in the Slack thread the report lives in. No sandbox, no agent."""

    def _pending(self, state, world, decision="merge-both", files=("f.txt",), thread="1786.001"):
        (state / "pending.json").write_text(json.dumps({
            "schema": "upstream-sync-pending/v1", "status": "auto_apply",
            "local_head": world[1], "upstream_head": world[2],
            "slack_channel": "C0TEST", "slack_thread_ts": thread,
            "features": [{"id": "F1", "status": "decided", "source": "policy", "decision": decision,
                          "files": list(files), "local_subjects": ["tip"]}],
        }))

    def _conflicting_repo(self, tmp_path):
        repo = _make_repo(tmp_path)                       # f.txt = "two" at tip, "one" at base
        local_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-qb", "up", "HEAD~1")
        (repo / "f.txt").write_text("three\n")           # both sides changed line 1 → conflict
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream edit")
        upstream_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        return repo, local_head, upstream_head

    def test_decided_pending_is_applied_end_to_end_and_reported_in_thread(self, tmp_path, state):
        world = self._conflicting_repo(tmp_path)
        repo, local_head, upstream_head = world
        self._pending(state, world)
        scripts, calls = _stub_scripts(tmp_path)
        slack_cmd, slack_log = _slack_recorder(tmp_path)
        resolver = _resolver(tmp_path, "import json,sys\nh=json.load(sys.stdin)\nsys.stdout.write('two\\nthree\\n')\n")

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts, extra_env={
            "HERMES_SYNC_RESOLVER_CMD": resolver, "HERMES_SYNC_SLACK_CMD": str(slack_cmd)})

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        assert res["action"] == "apply-decisions"
        head = _git(repo, "rev-parse", "HEAD")
        parents = _git(repo, "rev-list", "--parents", "-n1", head).split()[1:]
        assert parents == [local_head, upstream_head]
        assert (repo / "f.txt").read_text() == "two\nthree\n"
        assert res["backup_ref"] and _git(repo, "rev-parse", res["backup_ref"]) == local_head
        logged = calls.read_text()
        assert f"sync-local-customizations.sh --post-update-only {local_head}" in logged
        assert "upstream-sync-smoketest.sh" in logged
        # decision consumed + memory recorded
        assert not (state / "pending.json").exists()
        assert list(state.glob("pending.json.applied-*"))
        assert json.loads((state / "decision-memory.json").read_text())["entries"]
        # the summary went to the report's thread
        posts = [json.loads(l) for l in slack_log.read_text().splitlines() if l.strip()]
        assert posts and posts[-1]["channel"] == "C0TEST" and posts[-1]["thread_ts"] == "1786.001"
        assert "applied" in posts[-1]["text"].lower()
        assert "f.txt" in posts[-1]["text"]
        assert "*Fork test gate*" in posts[-1]["text"]
        assert "verdict: `PASS`" in posts[-1]["text"]
        assert "common_path: 0" in posts[-1]["text"]
        assert "post_only_path: 0" in posts[-1]["text"]
        assert not (state / "gate-failures.json").exists()
        # the clone is gone on success
        assert not (state / "scratch").exists()

    def test_success_report_is_not_hijacked_by_an_older_non_dated_archive(self, tmp_path, state):
        """A stale ``applied-manual-*`` archive must not steal the thread lookup.

        On success pending.json is archived BEFORE the report runs, so the report
        recovers channel and thread from the archive. Picking that archive by name
        sorts ``pending.json.applied-manual-20260724`` above every ``applied-2026…``
        — ``m`` outranks any digit — and a hand-made archive carries no channel, so
        the report exits 0 having posted nothing. Failures never hit this (their
        pending.json is still live), which is why it stayed invisible.
        """
        world = self._conflicting_repo(tmp_path)
        repo, local_head, upstream_head = world
        self._pending(state, world)
        (state / "pending.json.applied-manual-20260724").write_text(
            json.dumps({"upstream_head": "deadbeef"}), encoding="utf-8"
        )
        scripts, _calls = _stub_scripts(tmp_path)
        slack_cmd, slack_log = _slack_recorder(tmp_path)
        resolver = _resolver(
            tmp_path,
            "import json,sys\nh=json.load(sys.stdin)\nsys.stdout.write('two\\nthree\\n')\n",
        )

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts, extra_env={
            "HERMES_SYNC_RESOLVER_CMD": resolver, "HERMES_SYNC_SLACK_CMD": str(slack_cmd)})

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        raw = slack_log.read_text() if slack_log.exists() else ""
        posts = [json.loads(l) for l in raw.splitlines() if l.strip()]
        threaded = [q for q in posts if q.get("thread_ts")]
        assert threaded, "the success report never reached the operator's thread"
        assert threaded[-1]["channel"] == "C0TEST"
        assert threaded[-1]["thread_ts"] == "1786.001"
        assert "applied" in threaded[-1]["text"].lower()

    def _conflicting_py_repo(self, tmp_path):
        """Every line differs on both sides, so the whole module is one block.

        A resolution that answers the block with a single line therefore drops a
        module-level definition — the shape the structural gate exists to catch.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "local/customizations")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "mod.py").write_text("A = 1\nB = 2\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        (repo / "mod.py").write_text("A = 100\nB = 200\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "tip")
        local_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-qb", "up", "HEAD~1")
        (repo / "mod.py").write_text("A = 999\nB = 299\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream edit")
        upstream_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        return repo, local_head, upstream_head

    def test_a_resolution_that_drops_a_definition_stops_at_the_structural_gate(self, tmp_path, state):
        """Both sides still define B, so dropping it is a resolver defect.

        The whole point of the base going into the gate is that this case must
        keep failing while an accepted deletion stops failing.
        """
        world = self._conflicting_py_repo(tmp_path)
        repo, local_head, _ = world
        self._pending(state, world, files=("mod.py",))
        scripts, _calls = _stub_scripts(tmp_path)
        slack_cmd, _slack_log = _slack_recorder(tmp_path)
        resolver = _resolver(tmp_path, "import json,sys\njson.load(sys.stdin)\nsys.stdout.write('A = 100\\n')\n")

        _decisions_request(state)
        _run_finalize(repo, state, scripts, extra_env={
            "HERMES_SYNC_RESOLVER_CMD": resolver, "HERMES_SYNC_SLACK_CMD": str(slack_cmd)})

        res = _result(state)
        assert res["status"] == "failed"
        assert res["failed_stage"] == "invariants"
        assert "mod.py: lost_definition (B)" in res["detail"]
        assert _git(repo, "rev-parse", "HEAD") == local_head       # repo untouched
        assert (state / "scratch").exists()                        # clone preserved for repair
        assert (state / "pending.json").exists()                   # decision still armed

    def test_unresolvable_hunk_fails_at_resolve_keeps_clone_and_reports(self, tmp_path, state):
        world = self._conflicting_repo(tmp_path)
        repo, local_head, upstream_head = world
        self._pending(state, world)
        scripts, calls = _stub_scripts(tmp_path)
        slack_cmd, slack_log = _slack_recorder(tmp_path)
        leaky = _resolver(tmp_path, "import sys\nsys.stdout.write('<<<<<<< leaked\\n')\n")

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts, extra_env={
            "HERMES_SYNC_RESOLVER_CMD": leaky, "HERMES_SYNC_SLACK_CMD": str(slack_cmd)})

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "resolve"
        assert _git(repo, "rev-parse", "HEAD") == local_head          # untouched
        assert (state / "pending.json").exists()                      # still armed
        assert (state / "scratch" / "f.txt").exists()                 # clone preserved
        assert "<<<<<<< " in (state / "scratch" / "f.txt").read_text()
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()
        posts = [json.loads(l) for l in slack_log.read_text().splitlines() if l.strip()]
        assert posts and "f.txt" in posts[-1]["text"] and "scratch" in posts[-1]["text"]
        assert posts[-1]["thread_ts"] == "1786.001"

    def test_new_security_conflict_asks_instead_of_applying(self, tmp_path, state):
        world = self._conflicting_repo(tmp_path)
        repo, local_head, upstream_head = world
        # after the gate: both sides touch a security-named file → policy asks
        _git(repo, "checkout", "-q", "up")
        (repo / "auth_gate.py").write_text("upstream\n")
        _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "upstream auth")
        upstream_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        (repo / "auth_gate.py").write_text("local\n")
        _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "local auth")
        world = (repo, _git(repo, "rev-parse", "HEAD"), upstream_head)
        self._pending(state, world, decision="keep-local")
        scripts, calls = _stub_scripts(tmp_path)
        slack_cmd, slack_log = _slack_recorder(tmp_path)

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts, extra_env={"HERMES_SYNC_SLACK_CMD": str(slack_cmd)})

        res = _result(state)
        assert res["status"] == "awaiting_decision", proc.stderr + res.get("detail", "")
        assert _git(repo, "rev-parse", "HEAD") == world[1]
        pending = json.loads((state / "pending.json").read_text())
        assert pending["status"] == "awaiting_decision"
        asked = [f for f in pending["features"] if f["files"] == ["auth_gate.py"]]
        assert asked and asked[0]["decision"] is None
        posts = [json.loads(l) for l in slack_log.read_text().splitlines() if l.strip()]
        assert posts and "auth_gate.py" in posts[-1]["text"] and posts[-1]["thread_ts"] == "1786.001"
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()

    def test_resume_after_a_manual_fix_skips_prepare(self, tmp_path, state):
        """A human fixed the preserved clone by hand and re-requests: prepare must
        not wipe their work — the clone is taken as is when it holds no markers."""
        world = self._conflicting_repo(tmp_path)
        repo, local_head, upstream_head = world
        self._pending(state, world)
        scripts, calls = _stub_scripts(tmp_path)
        leaky = _resolver(tmp_path, "import sys\nsys.stdout.write('<<<<<<< leaked\\n')\n")
        _decisions_request(state)
        _run_finalize(repo, state, scripts, extra_env={"HERMES_SYNC_RESOLVER_CMD": leaky})
        assert _result(state)["failed_stage"] == "resolve"
        # the human resolves in the preserved clone
        (state / "scratch" / "f.txt").write_text("by hand\n")
        _git(state / "scratch", "add", "f.txt")

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts, extra_env={"HERMES_SYNC_RESOLVER_CMD": leaky})

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        assert (repo / "f.txt").read_text() == "by hand\n"


# ---------------------------------------------------------------------------
# rerere must not resolve an upstream merge from rebase-era recordings
# ---------------------------------------------------------------------------


class TestMergesDisableRerere:
    """``rerere.enabled=true`` lives in this repo's own config and
    ``.git/rr-cache`` holds hundreds of resolutions recorded back when the sync
    was a rebase — where "ours" and "theirs" are inverted relative to a merge.
    Replaying those into a merge resolves conflicts backwards and silently, so
    every merge that can conflict must opt out explicitly.

    The upstream merge runs in a throwaway *worktree*, which shares ``.git``
    with the live repo — so it inherits both the setting and the recordings.
    (A ``clone --shared``, by contrast, gets a fresh config and an empty
    rr-cache and is safe by construction.)
    """

    def _conflictable_merges(self, text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if " merge " not in f" {stripped} ":
                continue
            # Not merges: these neither create a commit nor touch the worktree.
            if any(
                token in stripped
                for token in (
                    "merge-base",
                    "merge-tree",
                    "merge --abort",
                    "merge --ff-only",
                    "--no-merges",
                )
            ):
                continue
            # Prose: an echo that merely names the command for the operator.
            if stripped.startswith("echo "):
                continue
            if not re.search(r"git\s+(-[cC]\s+\S+\s+)*merge\b", stripped):
                continue
            out.append(stripped)
        return out

    def test_sync_script_merges_opt_out_of_rerere(self):
        text = SYNC.read_text()
        merges = self._conflictable_merges(text)
        assert merges, "expected to find real merge invocations in the sync script"
        offenders = [m for m in merges if "rerere.enabled=false" not in m]
        assert not offenders, (
            "these merges can consult rebase-era rerere recordings and resolve "
            f"backwards: {offenders}"
        )


# ---------------------------------------------------------------------------
# The ACL self-heal must never walk outside the sandbox home
# ---------------------------------------------------------------------------


class TestAclHealStaysInsideHome:
    """On 2026-07-20 the heal's parent walk planted ``u:hermes:--x`` on ``/tmp``
    and blocked writes there for everyone. That was patched by making the heal
    run *less often* (only when access is actually broken) — the walk itself
    could still climb out of the sandbox home whenever it did run.

    An overridden ``HERMES_SYNC_STATE_DIR`` outside ``$HOME`` is a test or dev
    setup, never the production handoff dir; the heal declines to touch shared
    parents there instead of relying on never being triggered.
    """

    def _sudo_stub(self, tmp_path: Path) -> tuple[Path, Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = tmp_path / "sudo.log"
        stub = bin_dir / "sudo"
        stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log}"\nexit 0\n')
        stub.chmod(0o755)
        return bin_dir, log

    def _run_heal_only(self, tmp_path, state, home, scripts):
        """Run the finalizer with no request pending: the heal runs, then it
        exits on the missing request file without writing anything."""
        bin_dir, log = self._sudo_stub(tmp_path)
        state.chmod(0o500)  # not writable -> heal condition is met
        try:
            proc = subprocess.run(
                ["bash", str(FINALIZE)],
                env={
                    **os.environ,
                    "HOME": str(home),
                    "HERMES_SYNC_STATE_DIR": str(state),
                    "HERMES_SCRIPTS_DIR": str(scripts),
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                },
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            state.chmod(0o700)
        return proc, log

    def test_state_dir_outside_home_is_not_walked(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        state = tmp_path / "outside" / "state"
        state.mkdir(parents=True)
        scripts, _ = _stub_scripts(tmp_path)

        proc, log = self._run_heal_only(tmp_path, state, home, scripts)

        assert proc.returncode == 0, proc.stderr
        calls = log.read_text() if log.exists() else ""
        assert "setfacl" not in calls, (
            f"the heal climbed out of the sandbox home: {calls}"
        )

    def test_state_dir_inside_home_is_still_healed(self, tmp_path):
        home = tmp_path / "home"
        state = home / ".hermes" / "state" / "upstream-sync"
        state.mkdir(parents=True)
        scripts, _ = _stub_scripts(tmp_path)

        proc, log = self._run_heal_only(tmp_path, state, home, scripts)

        assert proc.returncode == 0, proc.stderr
        calls = log.read_text() if log.exists() else ""
        assert "setfacl" in calls, "the production handoff dir must still self-heal"
        # ...and never above the home it belongs to.
        assert f"{tmp_path}\n" not in calls


# ---------------------------------------------------------------------------
# The applied merge must join the point the operator actually decided about
# ---------------------------------------------------------------------------


class TestApplyMergeHonoursTheGatedUpstreamPoint:
    """``upstream_sha`` in the request is the agent's claim. ``pending.json``
    is the record written at gate time, before the operator answered — so when
    the two disagree, the request is applying a decision to a point the
    operator never saw. Upstream keeps moving while the operator sleeps on it
    (10 commits arrived during the 2026-08-12 gate alone), and new commits can
    change the conflict set the decision was made against.
    """

    def _pending(self, state: Path, upstream_head: str):
        (state / "pending.json").write_text(
            json.dumps(
                {
                    "schema": "upstream-sync-pending/v1",
                    "upstream_head": upstream_head,
                    "features": [{"id": "F1", "decision": "merge-both"}],
                }
            )
        )

    def test_merge_against_an_undecided_upstream_point_is_refused(
        self, tmp_path, state
    ):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        _git(repo, "checkout", "-q", "up")
        (repo / "h.txt").write_text("later\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream moved on")
        later = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")

        # The operator was gated on `upstream_head`; the agent merged `later`
        # and says so honestly — the parents match its own claim.
        self._pending(state, upstream_head)
        merge_sha = _scratch_merge(repo, state, local_head, later)
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=later, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert "pending" in res["detail"]
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()
        # The decision survives for a re-gate.
        assert (state / "pending.json").exists()

    def test_merge_against_the_gated_point_proceeds(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        self._pending(state, upstream_head)
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, calls = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        assert "sync-local-customizations.sh" in calls.read_text()


# ---------------------------------------------------------------------------
# A consumed decision is archived, not destroyed
# ---------------------------------------------------------------------------


class TestAppliedPendingIsArchived:
    """Recording the operator's decision into memory is the *last* step of
    Mode B, but the host cleared ``pending.json`` the moment the apply
    succeeded — so the input for that step was gone before it ran. On
    2026-08-12 the decision was lost exactly that way and had to be
    reconstructed by hand.

    Archiving instead of deleting keeps the retrigger protection (the file is
    no longer named ``pending.json``) while leaving the record to record from.
    """

    def test_successful_apply_archives_the_decision(self, tmp_path, state):
        repo, local_head, upstream_head = _make_divergent_repo(tmp_path)
        (state / "pending.json").write_text(
            json.dumps(
                {
                    "schema": "upstream-sync-pending/v1",
                    "upstream_head": upstream_head,
                    "features": [{"id": "F1", "decision": "merge-both"}],
                }
            )
        )
        merge_sha = _scratch_merge(repo, state, local_head, upstream_head)
        scripts, _ = _stub_scripts(tmp_path)

        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        assert _result(state)["status"] == "ok", proc.stderr
        # Consumed: a stray reply or the next scheduled sync finds nothing armed.
        assert not (state / "pending.json").exists()
        # ...but still recordable.
        archived = list(state.glob("pending.json.applied-*"))
        assert len(archived) == 1, archived
        kept = json.loads(archived[0].read_text())
        assert kept["features"][0]["decision"] == "merge-both"
        assert kept["upstream_head"] == upstream_head


# ---------------------------------------------------------------------------
# The documented Mode B recipe, walked end to end against a read-only source
# ---------------------------------------------------------------------------


class TestModeBRecipeAgainstReadOnlyCheckout:
    """Mode B exists for one case: a real conflict the operator has decided.
    This walks the recipe the skill documents — clone --shared a checkout the
    agent cannot write, resolve there, hand the host a SHA — and asserts the
    read-only constraint that makes the detour necessary in the first place.
    """

    @staticmethod
    def _chmod_tree(root: Path, writable: bool):
        mode_dir = 0o755 if writable else 0o555
        mode_file = 0o644 if writable else 0o444
        for path in sorted(root.rglob("*"), reverse=True):
            path.chmod(mode_dir if path.is_dir() else mode_file)
        root.chmod(mode_dir)

    def test_conflict_resolved_in_a_clone_lands_on_the_live_branch(
        self, tmp_path, state
    ):
        repo = _make_repo(tmp_path)
        # Both sides edit the same line — a genuine textual conflict.
        (repo / "f.txt").write_text("local change\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "local edit")
        local_head = _git(repo, "rev-parse", "HEAD")

        _git(repo, "checkout", "-qb", "up", "HEAD~1")
        (repo / "f.txt").write_text("upstream change\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream edit")
        upstream_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")

        scratch = state / "scratch"
        self._chmod_tree(repo, writable=False)
        try:
            # The constraint the whole detour exists for: the agent cannot make
            # a backup ref or commit in the live checkout.
            failed = subprocess.run(
                ["git", "-C", str(repo), "branch", "backup/attempt"],
                capture_output=True,
                text=True,
            )
            assert failed.returncode != 0, "expected the read-only checkout to refuse"

            subprocess.run(
                ["git", "clone", "-q", "--shared", str(repo), str(scratch)],
                check=True,
                capture_output=True,
            )
            _git(scratch, "config", "user.email", "t@t")
            _git(scratch, "config", "user.name", "t")
            _git(scratch, "checkout", "-q", "--detach", local_head)
            conflicted = subprocess.run(
                [
                    "git",
                    "-c",
                    "rerere.enabled=false",
                    "-c",
                    "merge.conflictStyle=zdiff3",
                    "merge",
                    "--no-edit",
                    upstream_head,
                ],
                cwd=scratch,
                capture_output=True,
                text=True,
            )
            assert conflicted.returncode != 0, "expected a conflict to resolve"
            # merge-both: keep both sides, the operator's decision.
            (scratch / "f.txt").write_text("local change\nupstream change\n")
            _git(scratch, "add", "f.txt")
            _git(scratch, "-c", "rerere.enabled=false", "commit", "--no-edit", "-q")
            merge_sha = _git(scratch, "rev-parse", "HEAD")
        finally:
            self._chmod_tree(repo, writable=True)

        scripts, calls = _stub_scripts(tmp_path)
        _apply_request(state, upstream_sha=upstream_head, merge_sha=merge_sha)
        proc = _run_finalize(repo, state, scripts)

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        assert _git(repo, "rev-parse", "HEAD") == merge_sha
        assert (repo / "f.txt").read_text() == "local change\nupstream change\n"
        assert _git(repo, "rev-parse", res["backup_ref"]) == local_head
        assert "upstream-sync-smoketest.sh" in calls.read_text()
        # The clone is disposable and the host cleans it up on success.
        assert not scratch.exists()


# ---------------------------------------------------------------------------
# A red test gate is triaged, not swallowed
# ---------------------------------------------------------------------------


class TestRedGateIsTriagedAndProposedToTheOperator:
    """The gate blocking a merge is the beginning of the work, not the end of
    it. Until now a red gate left the operator a log tail and a preserved clone
    and every occurrence — roughly every second or third sync — cost a manual
    session. The host now keeps the evidence, diagnoses, and proposes a test
    patch the operator can accept with one word. It never applies it itself: a
    red fork test is equally likely to mean the merge dropped local behaviour,
    and automation that edits the test in that case deletes the only alarm.
    """

    def _world(self, tmp_path, state, thread="1786.001"):
        """A conflicting repo that also carries a fork test file to patch."""
        repo = _make_repo(tmp_path)
        (repo / "tests").mkdir()
        (repo / "tests" / "new.py").write_text(
            "from mod import f\n\n\ndef test_broken_by_merge():\n    assert f() == 1\n")
        (repo / "mod.py").write_text("def f():\n    return 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "fork test")
        local_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-qb", "up", "HEAD~2")
        (repo / "f.txt").write_text("three\n")
        (repo / "g.txt").write_text("upstream only\n")   # marks a merged tree
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "upstream edit")
        upstream_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "local/customizations")
        (state / "pending.json").write_text(json.dumps({
            "schema": "upstream-sync-pending/v1", "status": "auto_apply",
            "local_head": local_head, "upstream_head": upstream_head,
            "slack_channel": "C0TEST", "slack_thread_ts": thread,
            "features": [{"id": "F1", "status": "decided", "source": "policy",
                          "decision": "merge-both", "files": ["f.txt"], "local_subjects": ["tip"]}],
        }))
        return repo, local_head, upstream_head

    def _breaking_stub(self, scripts: Path) -> None:
        """Red on a merged tree (g.txt is upstream-only) while the fork test
        still calls the OLD signature — which is exactly what the proposed patch
        changes, so the second run goes green for the real reason rather than
        for a sentinel we planted."""
        (scripts / "run-fork-tests.sh").write_text(
            "#!/usr/bin/env bash\n"
            # Новый argv-контракт: граница обязательна и идёт опцией, поэтому
            # worktree больше не $1. Заодно записываем полученную границу —
            # оба прогона гейта обязаны увидеть один и тот же полный SHA.
            'WT=""; BND=""; SEL=""; PROBE=0\n'
            'while [ $# -gt 0 ]; do case "$1" in\n'
            '  --boundary) BND="$2"; shift 2 ;;\n'
            '  --selection-from) SEL="$2"; shift 2 ;;\n'
            '  --probe-nodeids-from) PROBE=1; shift 2 ;;\n'
            '  *) WT="$1"; shift ;;\n'
            'esac; done\n'
            'printf "%s\\n" "$BND" >> "$(dirname "$0")/boundary-calls.log"\n'
            + _manifest_receipt_after_parse()
            + 'if [ "$PROBE" -eq 1 ]; then\n'
            "  echo 'FAILED tests/new.py::test_broken_by_merge - TypeError'\n"
            "  echo '1 failed, 5 passed in 2.00s'\n"
            "  exit 0\n"
            "fi\n"
            "echo 'FAILED tests/known.py::test_flaky - AssertionError'\n"
            'if [ -f "$WT/g.txt" ] && grep -q "f() ==" "$WT/tests/new.py"; then\n'
            "  echo '____ test_broken_by_merge ____'\n"
            "  echo 'E   TypeError: f() missing 1 required positional argument'\n"
            "  echo 'FAILED tests/new.py::test_broken_by_merge - TypeError'\n"
            "fi\n"
            "echo '2 failed, 4 passed in 2.00s'\n"
        )
        (scripts / "run-fork-tests.sh").chmod(0o755)

    PATCH = ("from mod import f\n\n\ndef test_broken_by_merge():\n"
             "    assert f(1) == 1\n    assert f(1) is not None\n")

    def _triage_cmd(self, tmp_path, verdict="test_outdated", patch=None):
        p = tmp_path / "triage_model.py"
        answer = {"verdict": verdict, "explanation": "upstream gave f() a required argument.",
                  "assertion_delta": "same assertion, new call signature",
                  "patch": self.PATCH if patch is None else patch}
        p.write_text("import json,sys\nsys.stdin.read()\n"
                     f"sys.stdout.write(json.dumps({answer!r}))\n")
        return f"{sys.executable} {p}"

    def _env(self, tmp_path, slack_cmd, **extra):
        ok = tmp_path / "pytest_ok.sh"
        ok.write_text("#!/usr/bin/env bash\nexit 0\n")
        ok.chmod(0o755)
        resolver = _resolver(tmp_path, "import sys\nsys.stdin.read()\nsys.stdout.write('two\\nthree\\n')\n")
        env = {"HERMES_SYNC_RESOLVER_CMD": resolver, "HERMES_SYNC_SLACK_CMD": str(slack_cmd),
               "HERMES_SYNC_TRIAGE_CMD": self._triage_cmd(tmp_path),
               "HERMES_SYNC_TRIAGE_PYTEST_CMD": str(ok)}
        env.update(extra)
        return env

    def test_a_red_gate_keeps_its_evidence_and_arms_a_proposal(self, tmp_path, state):
        repo, local_head, upstream_head = self._world(tmp_path, state)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_stub(scripts)
        slack_cmd, slack_log = _slack_recorder(tmp_path)

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))

        res = _result(state)
        assert res["status"] == "failed", proc.stderr + res.get("detail", "")
        assert res["failed_stage"] == "test-gate"
        assert _git(repo, "rev-parse", "HEAD") == local_head          # not landed

        # The two runs are kept side by side — until now both were mktemp'd and
        # deleted, so the only trace of WHY the gate blocked was a log tail.
        assert (state / "gate-baseline.log").exists()
        assert "test_broken_by_merge" in (state / "gate-post.log").read_text()
        failures = json.loads((state / "gate-failures.json").read_text())
        assert failures["new_failures"] == ["tests/new.py::test_broken_by_merge"]
        assert failures["before"] == local_head

        triage = json.loads((state / "gate-triage.json").read_text())
        assert triage["status"] == "awaiting_triage"
        assert triage["merge_sha"] == failures["merge_sha"]
        prop = triage["proposals"][0]
        assert prop["test_file"] == "tests/new.py"
        assert prop["verdict"] == "test_outdated"
        assert prop["patch"] == self.PATCH

        # The operator sees the proposal, in the report's thread, with the exact
        # words that will actually be parsed.
        posts = [json.loads(l) for l in slack_log.read_text().splitlines() if l.strip()]
        assert posts and posts[-1]["thread_ts"] == "1786.001"
        assert "apply fix" in posts[-1]["text"] and "keep test" in posts[-1]["text"]
        assert "tests/new.py" in posts[-1]["text"]

        # Nothing was applied and the clone survives for either answer.
        assert (state / "scratch" / ".git").exists()
        assert (state / "pending.json").exists()
        assert not calls.exists() or "sync-local-customizations.sh" not in calls.read_text()

    def test_apply_fix_amends_the_merge_reruns_the_gate_and_lands(self, tmp_path, state):
        repo, local_head, upstream_head = self._world(tmp_path, state)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_stub(scripts)
        slack_cmd, slack_log = _slack_recorder(tmp_path)
        _decisions_request(state)
        _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))
        assert _result(state)["failed_stage"] == "test-gate"
        first_merge = json.loads((state / "gate-triage.json").read_text())["merge_sha"]

        # The operator answers "apply fix"; the patched test now passes.
        sys.path.insert(0, str(REPO_ROOT))
        from hermes_cli.upstream_sync_reply import record_triage_decision
        out = record_triage_decision(state, "apply_fix", {"platform": "slack", "chat_id": "C0TEST",
                                                          "thread_id": "1786.001"})
        assert out["requested"] is True

        proc = _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))

        res = _result(state)
        assert res["status"] == "ok", proc.stderr + res.get("detail", "")
        assert res["action"] == "apply-triage-fixes"
        head = _git(repo, "rev-parse", "HEAD")
        assert head != local_head and head != first_merge          # amended, then landed
        # The amend preserved the parents the host insists on...
        parents = _git(repo, "rev-list", "--parents", "-n1", head).split()[1:]
        assert parents == [local_head, upstream_head]
        # ...and the patch itself is in the landed tree.
        assert (repo / "tests" / "new.py").read_text() == self.PATCH
        assert f"sync-local-customizations.sh --post-update-only {local_head}" in calls.read_text()
        assert json.loads((state / "gate-triage.json").read_text())["status"] == "applied"

    def test_a_still_red_gate_after_the_fix_is_not_triaged_again(self, tmp_path, state):
        """One attempt. A second proposal on top of a failed one is the loop
        where automation quietly rewrites tests until they pass."""
        repo, local_head, _ = self._world(tmp_path, state)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_stub(scripts)
        slack_cmd, slack_log = _slack_recorder(tmp_path)
        _decisions_request(state)
        _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))
        first = json.loads((state / "gate-triage.json").read_text())["created_at"]

        sys.path.insert(0, str(REPO_ROOT))
        from hermes_cli.upstream_sync_reply import record_triage_decision
        record_triage_decision(state, "apply_fix", {})
        # The proposed patch does not actually fix anything here (the stub keys
        # off a different file), so the gate stays red on the second run.
        triage = json.loads((state / "gate-triage.json").read_text())
        triage["proposals"][0]["patch"] = "def test_broken_by_merge():\n    assert f() == 1\n"
        (state / "gate-triage.json").write_text(json.dumps(triage))

        proc = _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "test-gate"
        assert _git(repo, "rev-parse", "HEAD") == local_head
        triage = json.loads((state / "gate-triage.json").read_text())
        assert triage["status"] == "exhausted"
        assert triage["created_at"] == first                       # not re-triaged
        assert (state / "scratch" / ".git").exists()               # clone kept for the human

    def test_a_patch_for_a_non_test_file_is_refused_at_apply_time(self, tmp_path, state):
        """The proposal is validated when it is made, but the state file is
        plain JSON on disk: the applier re-checks rather than trusting it."""
        repo, local_head, _ = self._world(tmp_path, state)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_stub(scripts)
        slack_cmd, slack_log = _slack_recorder(tmp_path)
        _decisions_request(state)
        _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))

        triage = json.loads((state / "gate-triage.json").read_text())
        triage["proposals"][0]["test_file"] = "mod.py"          # tampered after validation
        triage["status"] = "applying"
        (state / "gate-triage.json").write_text(json.dumps(triage))
        (state / "finalize-request.json").write_text(json.dumps({"action": "apply-triage-fixes"}))

        proc = _run_finalize(repo, state, scripts, extra_env=self._env(tmp_path, slack_cmd))

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert "test file" in res["detail"]
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert (repo / "mod.py").read_text() == "def f():\n    return 1\n"

    def test_a_triage_that_falls_over_does_not_change_the_gate_outcome(self, tmp_path, state):
        repo, local_head, _ = self._world(tmp_path, state)
        scripts, calls = _stub_scripts(tmp_path)
        self._breaking_stub(scripts)
        slack_cmd, slack_log = _slack_recorder(tmp_path)

        _decisions_request(state)
        proc = _run_finalize(repo, state, scripts,
                             extra_env=self._env(tmp_path, slack_cmd, HERMES_SYNC_TRIAGE_CMD="false"))

        res = _result(state)
        assert res["status"] == "failed", proc.stderr
        assert res["failed_stage"] == "test-gate"
        assert _git(repo, "rev-parse", "HEAD") == local_head
        # Still a diagnosis file naming the failing test, still no patch.
        triage = json.loads((state / "gate-triage.json").read_text())
        assert triage["proposals"][0]["verdict"] == "unsure"
        assert not triage["proposals"][0]["patch"]
