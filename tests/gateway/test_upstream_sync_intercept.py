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


# ---------------------------------------------------------------------------
# The triage gate: one word, whole message, checked before the decision parser
# ---------------------------------------------------------------------------


def _write_triage(tmp_path, status="awaiting_triage", patch="def test_x():\n    assert 1\n"):
    (tmp_path / "gate-triage.json").write_text(json.dumps({
        "schema": "upstream-sync-triage/v1", "status": status, "merge_sha": "abc1234567",
        "proposals": [{"test_file": "tests/new.py", "verdict": "test_outdated",
                       "explanation": "upstream changed the signature", "patch": patch}],
    }))


def test_apply_fix_arms_the_finalizer_and_acks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_triage(tmp_path)

    ack = GatewayRunner._build_upstream_sync_triage_ack("apply fix", _source())

    assert ack is not None
    req = json.loads((tmp_path / "finalize-request.json").read_text())
    assert req["action"] == "apply-triage-fixes"
    assert req["origin"]["thread_id"] == "1783420000.000"
    assert json.loads((tmp_path / "gate-triage.json").read_text())["status"] == "applying"


def test_keep_test_closes_the_gate_without_touching_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_triage(tmp_path)

    ack = GatewayRunner._build_upstream_sync_triage_ack("keep test", _source())

    assert ack is not None
    assert not (tmp_path / "finalize-request.json").exists()
    assert json.loads((tmp_path / "gate-triage.json").read_text())["status"] == "rejected"


def test_a_quoted_word_is_not_an_answer(tmp_path, monkeypatch):
    """The whole-message rule: quoted speech and sentences containing the word
    must not approve anything — the same rule the ops gate enforces."""
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_triage(tmp_path)

    assert GatewayRunner._build_upstream_sync_triage_ack("он написал: apply fix", _source()) is None
    assert GatewayRunner._build_upstream_sync_triage_ack("ok, apply fix", _source()) is None
    assert not (tmp_path / "finalize-request.json").exists()


def test_the_word_does_nothing_without_an_armed_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_triage(tmp_path, status="applied")

    assert GatewayRunner._build_upstream_sync_triage_ack("apply fix", _source()) is None
    assert not (tmp_path / "finalize-request.json").exists()


def test_a_decision_reply_is_left_to_the_decision_intercept(tmp_path, monkeypatch):
    """Both gates can be armed at once and both answer to plain text. The strict
    parser runs first precisely because it cannot steal the other's answer."""
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_triage(tmp_path)

    assert GatewayRunner._build_upstream_sync_triage_ack("1: merge both", _source()) is None


def test_a_diagnosis_without_a_patch_says_so_instead_of_arming(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_triage(tmp_path, patch="")

    ack = GatewayRunner._build_upstream_sync_triage_ack("apply fix", _source())

    assert ack is not None and "no patch" in ack.lower()
    assert not (tmp_path / "finalize-request.json").exists()


# ---------------------------------------------------------------------------
# Acknowledging a structural finding
# ---------------------------------------------------------------------------
#
# The gate that refuses a merge for a lost definition is answered here or not at
# all: the finalizer is started by a systemd path unit, so nothing the operator
# types arrives as an environment variable.


def _write_auto_apply_pending(tmp_path):
    (tmp_path / "pending.json").write_text(json.dumps({
        "schema": "upstream-sync-pending/v1", "status": "auto_apply", "upstream_head": "bbbb2222",
        "features": [{"id": "F1", "files": ["a.py"], "local_subjects": ["x"],
                      "status": "decided", "decision": "merge-both"}],
    }))


def test_an_ack_reply_rearms_the_apply_carrying_the_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_auto_apply_pending(tmp_path)

    ack = GatewayRunner._build_upstream_sync_ack_findings_ack(
        "ack mod.py:local_only", _source())

    assert ack is not None
    req = json.loads((tmp_path / "finalize-request.json").read_text())
    assert req["action"] == "apply-decisions"
    assert req["ack_findings"] == ["mod.py:local_only"]
    assert req["origin"]["thread_id"] == "1783420000.000"


def test_a_plain_message_is_not_an_acknowledgement(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_auto_apply_pending(tmp_path)

    assert GatewayRunner._build_upstream_sync_ack_findings_ack("looks fine", _source()) is None
    assert not (tmp_path / "finalize-request.json").exists()


def test_the_other_gates_answers_are_left_alone(tmp_path, monkeypatch):
    """Four gates in this pipeline answer to plain text in the same thread."""
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_auto_apply_pending(tmp_path)

    for other in ("F1: merge-both", "apply fix", "keep test", "выполни"):
        assert GatewayRunner._build_upstream_sync_ack_findings_ack(other, _source()) is None


def test_an_ack_without_an_armed_decision_says_so_instead_of_arming(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))

    ack = GatewayRunner._build_upstream_sync_ack_findings_ack(
        "ack mod.py:local_only", _source())

    assert ack is not None
    assert "\u26a0" in ack or "not" in ack.lower() or "нет" in ack.lower()
    assert not (tmp_path / "finalize-request.json").exists()
