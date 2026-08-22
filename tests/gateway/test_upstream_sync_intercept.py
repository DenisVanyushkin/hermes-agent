"""Task 2: gateway intercept of upstream-sync decision replies + reporter spawn."""

import json
import types

import pytest

from gateway.run import GatewayRunner
from gateway.config import Platform


def _source(platform="slack"):
    return types.SimpleNamespace(
        platform=platform, chat_id="C0B3X1E5SJZ",
        thread_id="1783420000.000", user_id="U123",
    )


def _write_invariant_pending(tmp_path):
    (tmp_path / "invariants-pending.json").write_text(json.dumps({
        "schema": "upstream-sync-invariants-pending/v1",
        "status": "awaiting_ack",
        "origin": {
            "platform": "slack", "chat_id": "C0B3X1E5SJZ",
            "thread_id": "1783420000.000", "user_id": "U123",
        },
        "findings": [{
            "finding_id": "INV-abc123456789",
            "kind": "lost_definition",
            "path": "mod.py",
            "symbol": "gone",
            "fingerprint": {"sha256": "f" * 64},
        }],
        "receipts": [],
    }))


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


@pytest.mark.asyncio
async def test_real_handler_passes_an_unarmed_plain_message_to_the_normal_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._get_proxy_url = lambda: "http://ordinary-pipeline"
    called = {"n": 0}

    async def ordinary(**_kwargs):
        called["n"] += 1
        return {"final_response": "ordinary pipeline"}

    runner._run_agent_via_proxy = ordinary
    for name in (
        "_pipeline_controlled_final_response",
        "_pipeline_engineering_plan_only_response",
        "_pipeline_autonomous_terminal_response",
        "_pipeline_clarification_response",
        "_pipeline_engineering_agent_resolution_prompt",
        "_pipeline_autonomous_preflight_block_response",
        "_pipeline_router_infra_degraded_notice",
        "_pipeline_autonomous_fail_closed_response",
        "_pipeline_controlled_block_response",
    ):
        setattr(runner, name, lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
    monkeypatch.setattr("gateway.run._pipeline_platform_allowed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "gateway.run._resolve_gateway_engineering_task_context",
        lambda **_kwargs: None,
    )

    out = await runner._run_agent_inner(
        "hello", "", [], _source(Platform.SLACK), "session-1", raw_message="hello",
    )
    assert out["final_response"] == "ordinary pipeline"
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_real_handler_consumes_an_armed_invariant_ack_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SYNC_STATE_DIR", str(tmp_path))
    _write_invariant_pending(tmp_path)
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._get_proxy_url = lambda: "http://ordinary-pipeline"
    called = {"n": 0}

    async def ordinary(**_kwargs):
        called["n"] += 1
        return {"final_response": "ordinary pipeline"}

    runner._run_agent_via_proxy = ordinary
    for name in (
        "_pipeline_controlled_final_response",
        "_pipeline_engineering_plan_only_response",
        "_pipeline_autonomous_terminal_response",
        "_pipeline_clarification_response",
        "_pipeline_engineering_agent_resolution_prompt",
        "_pipeline_autonomous_preflight_block_response",
        "_pipeline_router_infra_degraded_notice",
        "_pipeline_autonomous_fail_closed_response",
        "_pipeline_controlled_block_response",
    ):
        setattr(runner, name, lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
    monkeypatch.setattr("gateway.run._pipeline_platform_allowed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "gateway.run._resolve_gateway_engineering_task_context",
        lambda **_kwargs: None,
    )

    out = await runner._run_agent_inner(
        "ack INV-abc123456789", "", [], _source(Platform.SLACK), "session-1",
        raw_message="ack INV-abc123456789",
    )
    assert "Receipt recorded" in out["final_response"]
    assert called["n"] == 0


def test_gate_order_comment_preserves_the_ops_first_rationale():
    import inspect
    source = inspect.getsource(GatewayRunner._run_agent_inner)
    assert "право первого отказа" in source
    assert "пересечение" in source


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
