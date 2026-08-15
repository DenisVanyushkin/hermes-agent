"""Task 2: gateway intercept of upstream-sync decision replies + reporter spawn."""

import json
import types

import pytest

from gateway.run import GatewayRunner


def _source():
    return types.SimpleNamespace(
        platform="slack", chat_id="C0B3X1E5SJZ",
        thread_id="1783420000.000", user_id="U123",
    )


def _write_pending(tmp_path, status="awaiting_decision"):
    (tmp_path / "pending.json").write_text(
        json.dumps({"schema": "upstream-sync-pending/v1", "status": status})
    )


def _patch_no_spawn(monkeypatch):
    """Capture reporter spawns instead of launching a real detached process."""
    spawned = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: spawned.append(argv))
    return spawned


def _write_full_pending(tmp_path):
    (tmp_path / "pending.json").write_text(json.dumps({
        "schema": "upstream-sync-pending/v1", "status": "awaiting_decision", "upstream_head": "bbbb2222",
        "features": [
            {"id": "F1", "files": ["a.py"], "local_subjects": ["x"], "status": "awaiting_decision", "decision": None},
            {"id": "F2", "files": ["b.py"], "local_subjects": ["y"], "status": "awaiting_decision", "decision": None},
            {"id": "F3", "files": ["c.py"], "local_subjects": ["z"], "status": "awaiting_decision", "decision": None},
        ]}))


def test_decision_reply_with_pending_records_requests_and_acks(tmp_path, monkeypatch):
    """No one-shot agent, no progress reporter: the intercept writes the answers
    into pending.json and hands the host an apply-decisions request."""
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_full_pending(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr("cron.jobs.create_job", lambda **k: called.__setitem__("n", called["n"] + 1))
    spawned = _patch_no_spawn(monkeypatch)

    ack = GatewayRunner._build_upstream_sync_decision_ack(
        "1: merge both, 2: merge both, 3: merge both", _source()
    )
    assert ack is not None
    assert "merge both" in ack.lower()
    assert "host" in ack.lower() or "applying" in ack.lower()
    assert called["n"] == 0, "no one-shot job any more"
    assert spawned == [], "no progress reporter any more"
    pending = json.loads((tmp_path / "pending.json").read_text())
    assert all(f["decision"] == "merge-both" and f["source"] == "operator" for f in pending["features"])
    assert pending["slack_thread_ts"] == "1783420000.000"
    req = json.loads((tmp_path / "finalize-request.json").read_text())
    assert req["action"] == "apply-decisions"


def test_partial_decision_reply_acks_what_is_still_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_full_pending(tmp_path)
    monkeypatch.setattr("cron.jobs.create_job", lambda **k: None)
    _patch_no_spawn(monkeypatch)
    ack = GatewayRunner._build_upstream_sync_decision_ack("1: merge both", _source())
    assert ack is not None
    assert "F2" in ack and "F3" in ack
    assert not (tmp_path / "finalize-request.json").exists()


def test_plain_message_returns_none_and_no_job(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_pending(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr("cron.jobs.create_job", lambda **k: called.__setitem__("n", called["n"] + 1))
    _patch_no_spawn(monkeypatch)
    ack = GatewayRunner._build_upstream_sync_decision_ack("please rebase now", _source())
    assert ack is None
    assert called["n"] == 0


def test_decision_but_no_pending_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))  # no pending.json
    called = {"n": 0}
    monkeypatch.setattr("cron.jobs.create_job", lambda **k: called.__setitem__("n", called["n"] + 1))
    _patch_no_spawn(monkeypatch)
    ack = GatewayRunner._build_upstream_sync_decision_ack("1: merge both", _source())
    assert ack is None
    assert called["n"] == 0
