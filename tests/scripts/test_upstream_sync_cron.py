"""The scheduled run, host-owned: preflight → decide → act. No agent.

Every branch is driven by a fake preflight (HERMES_SYNC_PREFLIGHT_CMD prints
the markdown+JSON the real script prints) and a fake Slack, and asserts what
lands in the state dir: a finalize request, a pending.json, a posted report,
or nothing at all."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRON = REPO_ROOT / "scripts" / "upstream_sync_cron.py"


def _preflight_cmd(tmp_path: Path, payload: dict) -> str:
    body = "## Upstream preflight\n\nsome markdown\n\n### Machine-readable preflight data\n```json\n" \
           + json.dumps(payload, indent=1) + "\n```\n"
    f = tmp_path / "preflight.txt"
    f.write_text(body)
    cmd = tmp_path / "preflight.sh"
    cmd.write_text(f'#!/usr/bin/env bash\ncat "{f}"\n')
    cmd.chmod(0o755)
    return str(cmd)


def _slack_recorder(tmp_path: Path):
    log = tmp_path / "slack.jsonl"
    cmd = tmp_path / "slack.sh"
    cmd.write_text(f'#!/usr/bin/env bash\ncat >> "{log}"; echo >> "{log}"\necho 1786.500\n')
    cmd.chmod(0o755)
    return str(cmd), log


def _run(tmp_path, state, payload, *args, extra_env=None):
    slack_cmd, slack_log = _slack_recorder(tmp_path)
    env = dict(os.environ)
    env.update({
        "HERMES_SYNC_STATE_DIR": str(state),
        "HERMES_SYNC_PREFLIGHT_CMD": _preflight_cmd(tmp_path, payload),
        "HERMES_SYNC_SLACK_CMD": slack_cmd,
        "HERMES_SYNC_SLACK_CHANNEL": "C0TEST",
    })
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run([sys.executable, str(CRON), *args], env=env, capture_output=True,
                          text=True, timeout=120)
    posts = [json.loads(l) for l in slack_log.read_text().splitlines() if l.strip()] if slack_log.exists() else []
    return proc, posts


def _preflight(**over):
    base = {"schema": "upstream-sync-preflight/v1", "head": "aaaa1111", "upstream_head": "bbbb2222",
            "merge_base": "cccc3333", "upstream_ahead": 12, "local_ahead": 900,
            "pending_decision_present": False, "worktree_dirty": False, "dirty_files": [],
            "conflicts": [], "overlap_files": [], "risk": "clean"}
    base.update(over)
    return base


def _conflict(path, subjects):
    return {"file": path, "local_commits": [{"sha": "1", "subject": s} for s in subjects],
            "upstream_commits": [{"sha": "2", "subject": "upstream work"}]}


@pytest.fixture()
def state(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


class TestCleanUpstream:
    def test_clean_run_requests_a_sync_and_stays_quiet(self, tmp_path, state):
        proc, posts = _run(tmp_path, state, _preflight())
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == ""                       # cron delivers nothing
        req = json.loads((state / "finalize-request.json").read_text())
        assert req["action"] == "sync" and req["upstream_sha"] == "bbbb2222"
        assert posts == []
        assert not (state / "pending.json").exists()

    def test_nothing_to_do_when_upstream_is_already_merged(self, tmp_path, state):
        proc, posts = _run(tmp_path, state, _preflight(upstream_ahead=0, risk="clean"))
        assert proc.returncode == 0
        assert not (state / "finalize-request.json").exists()
        assert posts == []


class TestConflicts:
    def test_plain_conflicts_are_decided_by_policy_and_applied_without_asking(self, tmp_path, state):
        pf = _preflight(risk="conflicts", conflicts=[
            _conflict("gateway/run.py", ["fix(gateway): x"]),
            _conflict("cron/scheduler.py", ["fix(cron): y"]),
        ])
        proc, posts = _run(tmp_path, state, pf)
        assert proc.returncode == 0, proc.stderr
        pending = json.loads((state / "pending.json").read_text())
        assert pending["status"] == "auto_apply"
        assert pending["upstream_head"] == "bbbb2222" and pending["local_head"] == "aaaa1111"
        assert all(f["decision"] == "merge-both" and f["source"] == "policy" for f in pending["features"])
        assert [f["id"] for f in pending["features"]] == ["F1", "F2"]
        req = json.loads((state / "finalize-request.json").read_text())
        assert req["action"] == "apply-decisions"
        # one informational post, threaded nowhere yet; its ts becomes the thread
        assert len(posts) == 1 and posts[0]["channel"] == "C0TEST" and "thread_ts" not in posts[0]
        assert "automatically" in posts[0]["text"].lower()
        assert pending["slack_thread_ts"] == "1786.500" and pending["slack_channel"] == "C0TEST"

    def test_security_conflict_posts_a_question_and_does_not_apply(self, tmp_path, state):
        pf = _preflight(risk="conflicts", conflicts=[
            _conflict("gateway/run.py", ["fix(gateway): x"]),
            _conflict("tools/approval.py", ["feat: smart approvals"]),
        ])
        proc, posts = _run(tmp_path, state, pf)
        assert proc.returncode == 0, proc.stderr
        pending = json.loads((state / "pending.json").read_text())
        assert pending["status"] == "awaiting_decision"
        by = {f["files"][0]: f for f in pending["features"]}
        assert by["gateway/run.py"]["decision"] == "merge-both"
        assert by["tools/approval.py"]["decision"] is None
        assert not (state / "finalize-request.json").exists()
        assert len(posts) == 1
        text = posts[0]["text"]
        assert "tools/approval.py" in text and f"{by['tools/approval.py']['id']}: merge-both" in text
        assert pending["slack_thread_ts"] == "1786.500"

    def test_remembered_decision_is_used_for_plain_paths(self, tmp_path, state):
        from scripts.upstream_sync_decisions import feature_fingerprint  # noqa
        files, subs = ("cron/scheduler.py",), ("fix(cron): y",)
        (state / "decision-memory.json").write_text(json.dumps({
            "schema": "upstream-sync-decisions/v1", "updated_at": None,
            "entries": [{"fingerprint": feature_fingerprint(files, subs), "files": list(files),
                         "local_subjects": list(subs), "decision": "take-upstream",
                         "apply_count": 1, "last_applied_at": "2026-08-01T00:00:00Z"}]}))
        pf = _preflight(risk="conflicts", conflicts=[_conflict("cron/scheduler.py", ["fix(cron): y"])])
        _run(tmp_path, state, pf)
        pending = json.loads((state / "pending.json").read_text())
        assert pending["features"][0]["decision"] == "take-upstream"
        assert pending["features"][0]["source"] == "memory"


class TestExistingGate:
    def _armed(self, state, status="awaiting_decision", decided=False, reminded_at=None):
        feats = [{"id": "F1", "status": "decided" if decided else "awaiting_decision",
                  "source": "operator" if decided else None,
                  "decision": "merge-both" if decided else None,
                  "files": ["tools/approval.py"], "local_subjects": ["feat: x"]}]
        p = {"schema": "upstream-sync-pending/v1", "status": status, "local_head": "aaaa1111",
             "upstream_head": "bbbb2222", "features": feats,
             "slack_channel": "C0TEST", "slack_thread_ts": "1786.001"}
        if reminded_at:
            p["reminded_at"] = reminded_at
        (state / "pending.json").write_text(json.dumps(p))

    def test_awaiting_gate_gets_a_threaded_reminder_once_a_day(self, tmp_path, state):
        self._armed(state)
        proc, posts = _run(tmp_path, state, _preflight(pending_decision_present=True, risk="conflicts"))
        assert proc.returncode == 0, proc.stderr
        assert len(posts) == 1 and posts[0]["thread_ts"] == "1786.001"
        assert "F1: merge-both" in posts[0]["text"]
        assert not (state / "finalize-request.json").exists()
        pending = json.loads((state / "pending.json").read_text())
        assert pending["reminded_at"]

    def test_reminder_is_throttled(self, tmp_path, state):
        self._armed(state, reminded_at="2999-01-01T00:00:00+00:00")   # "just now"
        proc, posts = _run(tmp_path, state, _preflight(pending_decision_present=True))
        assert proc.returncode == 0
        assert posts == []

    def test_fully_decided_gate_is_resumed_not_re_asked(self, tmp_path, state):
        self._armed(state, status="awaiting_decision", decided=True)
        proc, posts = _run(tmp_path, state, _preflight(pending_decision_present=True))
        assert proc.returncode == 0, proc.stderr
        req = json.loads((state / "finalize-request.json").read_text())
        assert req["action"] == "apply-decisions"
        assert posts == []

    def test_in_flight_finalize_is_left_alone(self, tmp_path, state):
        self._armed(state, decided=True)
        (state / "finalize-request.processing.json").write_text("{}")
        proc, posts = _run(tmp_path, state, _preflight(pending_decision_present=True))
        assert proc.returncode == 0
        assert not (state / "finalize-request.json").exists()
        assert posts == []


class TestDryRunAndFailures:
    def test_dry_run_writes_nothing_and_prints_the_plan(self, tmp_path, state):
        pf = _preflight(risk="conflicts", conflicts=[_conflict("gateway/run.py", ["fix"])])
        proc, posts = _run(tmp_path, state, pf, "--dry-run")
        assert proc.returncode == 0, proc.stderr
        assert "apply-decisions" in proc.stdout
        assert not (state / "pending.json").exists()
        assert not (state / "finalize-request.json").exists()
        assert posts == []

    def test_preflight_failure_is_a_nonzero_exit_with_a_message(self, tmp_path, state):
        bad = tmp_path / "bad.sh"
        bad.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 3\n")
        bad.chmod(0o755)
        proc, posts = _run(tmp_path, state, _preflight(), extra_env={"HERMES_SYNC_PREFLIGHT_CMD": str(bad)})
        assert proc.returncode != 0
        assert "preflight" in (proc.stdout + proc.stderr).lower()

import datetime as _dt


class TestArmedTriageGate:
    """A proposal waiting for `apply fix` / `keep test` is an armed gate exactly
    like an undecided conflict: the sync must not start another one on top of
    it, and the operator gets the same once-a-day nudge."""

    def _triage(self, state, status="awaiting_triage", reminded_at=None):
        payload = {"schema": "upstream-sync-triage/v1", "status": status, "merge_sha": "dddd4444",
                   "slack_channel": "C0TEST", "slack_thread_ts": "1786.001",
                   "proposals": [{"test_file": "tests/new.py", "verdict": "test_outdated",
                                  "patch": "def test_x():\n    assert 1\n"}]}
        if reminded_at:
            payload["reminded_at"] = reminded_at
        (state / "gate-triage.json").write_text(json.dumps(payload))

    def test_an_armed_proposal_is_reminded_not_restarted(self, tmp_path, state):
        self._triage(state)
        proc, posts = _run(tmp_path, state, _preflight())

        assert proc.returncode == 0, proc.stderr
        assert posts and "apply fix" in posts[-1]["text"] and "keep test" in posts[-1]["text"]
        assert posts[-1]["thread_ts"] == "1786.001"
        # No new sync was started while the operator holds the answer.
        assert not (state / "finalize-request.json").exists()
        assert not (state / "pending.json").exists()

    def test_the_reminder_is_throttled_like_the_decision_one(self, tmp_path, state):
        self._triage(state, reminded_at=_dt.datetime.now(_dt.timezone.utc).isoformat())
        proc, posts = _run(tmp_path, state, _preflight())

        assert proc.returncode == 0, proc.stderr
        assert posts == []

    @pytest.mark.parametrize("status", ["applied", "rejected", "exhausted", "applying"])
    def test_an_answered_proposal_does_not_block_the_next_sync(self, tmp_path, state, status):
        self._triage(state, status=status)
        proc, posts = _run(tmp_path, state, _preflight(conflicts=[]))

        assert proc.returncode == 0, proc.stderr
        req = json.loads((state / "finalize-request.json").read_text())
        assert req["action"] == "sync"


class TestStateDirOutsideSandbox:
    """The live state must not sit under the sandbox mirror.

    ``$HERMES_HOME/sandboxes/docker/default/home`` is provisioned as root; a
    chmod there recalculates the POSIX ACL mask and silently voids the
    ``user:hermes:--x`` traverse grant, so the host-owned cron dies on mkdir
    before it does any work (2026-08-18). Nothing in the sync path runs in the
    sandbox any more, so no default may point into it.
    """

    def _scripts(self):
        root = Path(__file__).resolve().parents[2] / "scripts"
        names = [
            "upstream_sync_cron.py",
            "preflight-local-customizations-update.sh",
            "upstream-sync-finalize.sh",
            "upstream-sync-rollback.sh",
            "upstream-sync-smoketest.sh",
        ]
        return [root / n for n in names]

    def test_no_script_defaults_into_the_sandbox_mirror(self):
        offenders = [
            p.name
            for p in self._scripts()
            if "sandboxes/docker/default" in p.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_every_script_defaults_to_the_host_state_dir(self):
        missing = [
            p.name
            for p in self._scripts()
            if "state/upstream-sync" not in p.read_text(encoding="utf-8")
        ]
        assert missing == []
