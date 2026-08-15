"""Mode B mechanics as code — regression coverage for 2026-08-15.

The staleness check lived in SKILL.md prose as ``git merge-tree HEAD
origin/main`` run inside a ``git clone --shared`` of the live checkout — where
``origin/main`` is the fork's own stale ``main``, not upstream. Every apply
therefore concluded "the gate went stale" whenever that branch merged cleanly.
The mechanics now live in a script, path-parameterized, and are exercised
against temporary repositories.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLY = REPO_ROOT / "scripts" / "upstream_sync_apply.py"
FINALIZE = REPO_ROOT / "scripts" / "upstream-sync-finalize.sh"

sys.path.insert(0, str(REPO_ROOT))
from scripts.upstream_sync_apply import resolve_merge_both_text  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _run(cmd: str, state: Path, live: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(APPLY), cmd, "--state", str(state), "--live", str(live), *extra],
        capture_output=True, text=True, timeout=120,
    )


def _out(proc: subprocess.CompletedProcess) -> dict:
    assert proc.stdout.strip(), proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture()
def world(tmp_path: Path):
    """A live checkout on local/customizations plus a divergent upstream commit.

    ``f.txt`` conflicts (both sides changed line 1); ``g.txt`` is added upstream
    only. A stale ``main`` branch sits at the merge base — merging THAT is
    trivially clean, which is exactly the trap the old prose check fell into.
    """
    live = tmp_path / "live"
    live.mkdir()
    _git(live, "init", "-q", "-b", "local/customizations")
    _git(live, "config", "user.email", "t@t")
    _git(live, "config", "user.name", "t")
    (live / "f.txt").write_text("base\n")
    (live / "keep.txt").write_text("keep\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "base")
    base = _git(live, "rev-parse", "HEAD")
    _git(live, "branch", "main", base)  # the fork's own stale main
    (live / "f.txt").write_text("local\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "local change")
    local_head = _git(live, "rev-parse", "HEAD")
    _git(live, "checkout", "-qb", "up", base)
    (live / "f.txt").write_text("upstream\n")
    (live / "g.txt").write_text("new upstream file\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "upstream change")
    upstream_head = _git(live, "rev-parse", "HEAD")
    _git(live, "checkout", "-q", "local/customizations")

    state = tmp_path / "state"
    state.mkdir()
    return {"live": live, "state": state, "base": base,
            "local_head": local_head, "upstream_head": upstream_head}


def _pending(world, decision="keep-local", files=("f.txt",), local_head=None):
    (world["state"] / "pending.json").write_text(json.dumps({
        "schema": "upstream-sync-pending/v1",
        "status": "awaiting_decision",
        "local_head": local_head or world["local_head"],
        "upstream_head": world["upstream_head"],
        "features": [{"id": "F1", "decision": decision, "files": list(files),
                      "local_subjects": ["local change"]}],
    }))


class TestPrepareChecksTheGatedPointNotTheForksMain:
    def test_conflicts_are_computed_against_pending_upstream_head(self, world):
        _pending(world)
        proc = _run("prepare", world["state"], world["live"])
        out = _out(proc)
        assert proc.returncode == 0, proc.stderr
        assert out["status"] == "ready"
        assert out["conflicts"] == ["f.txt"]
        assert out["upstream_head"] == world["upstream_head"]
        # The clone's origin/main IS the stale fork main — and it is ignored.
        scratch = world["state"] / "scratch"
        assert _git(scratch, "rev-parse", "origin/main") == world["base"]

    def test_missing_decision_stops_before_cloning(self, world):
        _pending(world, decision="")
        proc = _run("prepare", world["state"], world["live"])
        assert proc.returncode == 3
        assert _out(proc)["status"] == "missing_decisions"
        assert not (world["state"] / "scratch").exists()

    def test_new_conflicting_file_is_reported_not_merged(self, world):
        # Live moved after the gate: keep.txt now conflicts too, undecided.
        live = world["live"]
        _git(live, "checkout", "-q", "up")
        (live / "keep.txt").write_text("upstream keep\n")
        _git(live, "add", "-A")
        _git(live, "commit", "-qm", "upstream touches keep")
        world["upstream_head"] = _git(live, "rev-parse", "HEAD")
        _git(live, "checkout", "-q", "local/customizations")
        (live / "keep.txt").write_text("local keep\n")
        _git(live, "add", "-A")
        _git(live, "commit", "-qm", "local touches keep")
        _pending(world, local_head=world["local_head"])  # gate recorded the OLD local head

        proc = _run("prepare", world["state"], world["live"])
        out = _out(proc)
        assert proc.returncode == 4, proc.stderr
        assert out["status"] == "new_conflicts"
        assert out["new_conflicts"] == ["keep.txt"]
        assert out["local_base"] == _git(live, "rev-parse", "HEAD")  # live HEAD, not the snapshot
        assert not (world["state"] / "finalize-request.json").exists()

    def test_prepare_refuses_while_a_finalize_is_in_flight(self, world):
        _pending(world)
        (world["state"] / "finalize-request.json").write_text("{}")
        proc = _run("prepare", world["state"], world["live"])
        assert proc.returncode == 2
        assert not (world["state"] / "scratch").exists()


class TestPrepareBuildsTheMergeOnTheLiveHead:
    def test_local_moved_but_same_conflict_set_is_ready_on_live_head(self, world):
        live = world["live"]
        (live / "unrelated.txt").write_text("later local work\n")
        _git(live, "add", "-A")
        _git(live, "commit", "-qm", "later local work")
        moved = _git(live, "rev-parse", "HEAD")
        _pending(world, local_head=world["local_head"])  # snapshot is stale

        proc = _run("prepare", world["state"], world["live"])
        out = _out(proc)
        assert out["status"] == "ready", proc.stderr
        assert out["local_base"] == moved
        assert out["pending_local_head"] == world["local_head"]

    def test_keep_local_and_take_upstream_are_applied_mechanically(self, world):
        scratch = world["state"] / "scratch"
        _pending(world, decision="keep-local")
        out = _out(_run("prepare", world["state"], world["live"]))
        assert out["auto_resolved"] == ["f.txt"] and out["needs_manual"] == []
        assert (scratch / "f.txt").read_text() == "local\n"
        assert _git(scratch, "ls-files", "-u") == ""  # staged, nothing unmerged

        _pending(world, decision="take-upstream")
        out = _out(_run("prepare", world["state"], world["live"]))
        assert out["auto_resolved"] == ["f.txt"]
        assert (scratch / "f.txt").read_text() == "upstream\n"

    def test_merge_both_is_left_for_manual_resolution_with_zdiff3_markers(self, world):
        _pending(world, decision="merge-both")
        out = _out(_run("prepare", world["state"], world["live"]))
        assert out["status"] == "ready"
        assert [m["path"] for m in out["needs_manual"]] == ["f.txt"]
        text = (world["state"] / "scratch" / "f.txt").read_text()
        assert "<<<<<<< " in text and "||||||| " in text and ">>>>>>> " in text


class TestHandoff:
    def _resolve_manually(self, world, content="both\n"):
        scratch = world["state"] / "scratch"
        (scratch / "f.txt").write_text(content)
        _git(scratch, "add", "f.txt")

    def test_handoff_writes_a_request_for_a_merge_parented_on_live_head_and_gate(self, world):
        _pending(world, decision="merge-both")
        assert _out(_run("prepare", world["state"], world["live"]))["status"] == "ready"
        self._resolve_manually(world)

        proc = _run("handoff", world["state"], world["live"])
        out = _out(proc)
        assert proc.returncode == 0, proc.stderr
        req = json.loads((world["state"] / "finalize-request.json").read_text())
        assert req["action"] == "apply-merge"
        assert req["upstream_sha"] == world["upstream_head"]
        assert req["scratch_repo"] == "scratch"
        assert req["merge_sha"] == out["merge_sha"]
        parents = _git(world["state"] / "scratch", "rev-list", "--parents", "-n1",
                       req["merge_sha"]).split()[1:]
        assert parents == [world["local_head"], world["upstream_head"]]
        prep = json.loads((world["state"] / "apply-prepare.json").read_text())
        assert prep["handed_off_at"] and prep["merge_sha"] == req["merge_sha"]

    def test_leftover_conflict_markers_block_the_handoff(self, world):
        _pending(world, decision="merge-both")
        _run("prepare", world["state"], world["live"])
        scratch = world["state"] / "scratch"
        _git(scratch, "add", "f.txt")  # staged WITH markers still inside

        proc = _run("handoff", world["state"], world["live"])
        assert proc.returncode == 6
        assert _out(proc)["status"] == "unresolved"
        assert not (world["state"] / "finalize-request.json").exists()

    def test_unstaged_conflict_blocks_the_handoff(self, world):
        _pending(world, decision="merge-both")
        _run("prepare", world["state"], world["live"])
        proc = _run("handoff", world["state"], world["live"])
        assert proc.returncode == 6
        assert not (world["state"] / "finalize-request.json").exists()

    def test_live_head_moving_after_prepare_blocks_the_handoff(self, world):
        _pending(world, decision="keep-local")
        _run("prepare", world["state"], world["live"])
        live = world["live"]
        (live / "race.txt").write_text("resident agent commit\n")
        _git(live, "add", "-A")
        _git(live, "commit", "-qm", "moved under us")

        proc = _run("handoff", world["state"], world["live"])
        assert proc.returncode == 7
        assert _out(proc)["status"] == "live_moved"
        assert not (world["state"] / "finalize-request.json").exists()


class TestWait:
    def _result(self, world, status, finished_at):
        (world["state"] / "finalize-result.json").write_text(json.dumps(
            {"action": "apply-merge", "status": status, "finished_at": finished_at,
             "detail": "x", "backup_ref": "backup/x"}))

    def test_ok_result_newer_than_handoff_returns_zero(self, world):
        self._result(world, "ok", "2026-08-15T12:00:05+00:00")
        proc = _run("wait", world["state"], world["live"],
                    "--after", "2026-08-15T12:00:00+00:00", "--timeout", "2", "--interval", "0.1")
        assert proc.returncode == 0, proc.stderr
        assert _out(proc)["status"] == "ok"

    def test_failed_result_returns_eight(self, world):
        self._result(world, "failed", "2026-08-15T12:00:05+00:00")
        proc = _run("wait", world["state"], world["live"],
                    "--after", "2026-08-15T12:00:00+00:00", "--timeout", "2", "--interval", "0.1")
        assert proc.returncode == 8

    def test_stale_result_is_ignored_until_timeout(self, world):
        self._result(world, "ok", "2026-08-15T11:00:00+00:00")  # older than the hand-off
        proc = _run("wait", world["state"], world["live"],
                    "--after", "2026-08-15T12:00:00+00:00", "--timeout", "1", "--interval", "0.1")
        assert proc.returncode == 9
        assert _out(proc)["status"] == "timeout"


class TestEndToEndWithTheHostFinalizer:
    """prepare -> (manual resolution) -> handoff -> the real finalizer, with the
    finalizer's downstream scripts stubbed. Proves the two halves agree on the
    contract: scratch_repo name, parents, gated point, and the alternates the
    sandbox leaves behind."""

    def _stub_scripts(self, tmp_path: Path) -> Path:
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for name in ("sync-local-customizations.sh", "upstream-sync-smoketest.sh",
                     "upstream-sync-rollback.sh"):
            p = scripts / name
            p.write_text("#!/usr/bin/env bash\nexit 0\n")
            p.chmod(0o755)
        tests_stub = scripts / "run-fork-tests.sh"
        tests_stub.write_text("#!/usr/bin/env bash\necho '0 failed, 1 passed in 0.10s'\n")
        tests_stub.chmod(0o755)
        for helper in ("upstream_sync_gate.py", "upstream_sync_decisions.py"):
            (scripts / helper).write_text((REPO_ROOT / "scripts" / helper).read_text())
        return scripts

    def test_the_merge_lands_on_the_live_branch(self, world, tmp_path):
        _pending(world, decision="merge-both")
        assert _out(_run("prepare", world["state"], world["live"]))["status"] == "ready"
        scratch = world["state"] / "scratch"
        (scratch / "f.txt").write_text("local\nupstream\n")
        _git(scratch, "add", "f.txt")
        out = _out(_run("handoff", world["state"], world["live"]))
        assert out["status"] == "handed_off"
        # Only now simulate what the sandbox leaves behind for the host: inside
        # the container that alternate resolves, which is why the agent's own
        # git commands work and only the host's later reads break.
        (scratch / ".git" / "objects" / "info" / "alternates").write_text(
            "/workspace/live-hermes/.git/objects\n")

        env = dict(os.environ)
        env.update({"HERMES_SYNC_STATE_DIR": str(world["state"]),
                    "HERMES_SCRIPTS_DIR": str(self._stub_scripts(tmp_path)),
                    "HERMES_REPO": str(world["live"]),
                    "SUDO_ASKPASS": "/bin/false"})
        proc = subprocess.run(["bash", str(FINALIZE)], env=env, capture_output=True,
                              text=True, timeout=180)

        result = json.loads((world["state"] / "finalize-result.json").read_text())
        assert result["status"] == "ok", (result, proc.stderr)
        assert _git(world["live"], "rev-parse", "HEAD") == out["merge_sha"]
        assert (world["live"] / "f.txt").read_text() == "local\nupstream\n"
        assert list(world["state"].glob("pending.json.applied-*"))
        assert not (world["state"] / "pending.json").exists()
        # The host recorded the decision the operator gave.
        memory = json.loads((world["state"] / "decision-memory.json").read_text())
        assert memory["entries"][0]["decision"] == "merge-both"
