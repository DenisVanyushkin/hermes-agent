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


def test_decision_reply_with_pending_enqueues_and_acks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_pending(tmp_path)
    captured = {}

    def fake_create_job(**kwargs):
        captured.update(kwargs)
        return {"id": "deadbeef0001", **kwargs}

    monkeypatch.setattr("cron.jobs.create_job", fake_create_job)
    spawned = _patch_no_spawn(monkeypatch)

    ack = GatewayRunner._build_upstream_sync_decision_ack(
        "1: merge both, 2: merge both, 3: merge both", _source()
    )
    assert ack is not None
    assert "merge both" in ack.lower()
    assert captured["skills"] == ["upstream-sync"]
    assert captured["role"] == "engineer"
    assert captured["origin"]["thread_id"] == "1783420000.000"
    # reporter spawned with the thread target
    assert spawned, "progress reporter should be spawned"
    flat = " ".join(str(x) for x in spawned[0])
    assert "upstream-sync-progress-reporter.py" in flat
    assert "slack:C0B3X1E5SJZ:1783420000.000" in flat


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
