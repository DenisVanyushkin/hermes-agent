"""Composition coverage for pending-ack notes on a WhatsApp LID turn."""

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "custom" / "fam"))
from fam import acks


NOW = "2026-07-23T04:25:00+00:00"
SNAPSHOT = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "target": "whatsapp:+77011102626",
    "items": [{
        "kind": "med_intake",
        "id": 3,
        "name": "мисол",
        "dose": "1 таблетка",
        "plan_ts_utc": "2026-07-23T04:00:00+00:00",
        "due_local": "09:00",
        "ack_cmd": "fam med taken 3",
        "skip_cmd": "fam med skip 3",
    }],
}
SESSION_KEY = "agent:main:whatsapp:dm:77011102626"


def _source():
    return SessionSource(
        platform=Platform.WHATSAPP,
        chat_id="77011102626",
        chat_type="dm",
        user_id="244882006364348@lid",
    )


def _event():
    return MessageEvent(
        text="Приняла",
        source=_source(),
        message_id="wamid-inbound-1",
    )


def _quoted_event(reply_id="wa-rem-1"):
    return MessageEvent(
        text="отмени это",
        source=_source(),
        message_id="wamid-inbound-real-1",
        reply_to_message_id=reply_id,
        reply_to_text="Врач в 17:00",
    )


def _runner(monkeypatch, tmp_path):
    runner = gateway_run.GatewayRunner(GatewayConfig())
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._handle_active_session_busy_message = AsyncMock(return_value=False)
    runner._session_db = MagicMock()
    runner._recover_telegram_topic_thread_id = lambda _source: None
    runner._cache_session_source = lambda _key, _source: None
    runner._is_session_run_current = lambda _key, _generation: True
    runner._reply_anchor_for_event = lambda _event: None
    runner._get_guild_id = lambda _event: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()

    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key=SESSION_KEY,
        session_id="sess-acks",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.WHATSAPP,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = [
        {"role": "assistant", "content": "Напоминание"},
    ]
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    mapping_dir = tmp_path / "whatsapp" / "session"
    mapping_dir.mkdir(parents=True)
    (mapping_dir / "lid-mapping-244882006364348_reverse.json").write_text(
        json.dumps("77011102626"), encoding="utf-8"
    )
    monkeypatch.setattr(
        "gateway.whatsapp_identity.get_hermes_dir",
        lambda *_args: mapping_dir,
    )
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"pending_acks_file": str(tmp_path / "pending-acks.json")},
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )
    return runner


@pytest.mark.asyncio
async def test_pending_acks_api_content_contains_note_for_lid_turn(
    monkeypatch, tmp_path
):
    """The handler's staged note reaches the API-content sidecar."""
    runner = _runner(monkeypatch, tmp_path)
    monkeypatch.setattr(gateway_run, "_read_pending_acks", lambda _path: SNAPSHOT)
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model",
        {
            "api_key": "test-key",
            "base_url": "http://provider.test/v1",
            "provider": "openai",
            "api_mode": "chat_completions",
        },
    )
    runner._session_db = None

    captured = {}

    from agent import turn_context

    real_consume_gateway_notes = turn_context.consume_gateway_turn_context_notes

    def record_gateway_notes(agent):
        captured["gateway_notes"] = agent._gateway_turn_context_notes
        return real_consume_gateway_notes(agent)

    monkeypatch.setattr(
        turn_context,
        "consume_gateway_turn_context_notes",
        record_gateway_notes,
    )

    def fake_model_call(self, api_kwargs, **_kwargs):
        captured["api_messages"] = api_kwargs["messages"]
        captured["session_messages"] = self._session_messages
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Готово", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr(
        "run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call
    )

    await runner._handle_message_with_agent(
        _event(), _source(), SESSION_KEY, 1
    )

    assert "мисол" in captured["gateway_notes"]

    session_user_messages = [
        message
        for message in captured["session_messages"]
        if message.get("role") == "user"
    ]
    assert session_user_messages, captured
    assert "мисол" in session_user_messages[-1]["api_content"]

    user_messages = [
        message
        for message in captured["api_messages"]
        if message.get("role") == "user"
    ]
    assert user_messages, captured
    assert "мисол" in user_messages[-1]["content"]
    assert "Приняла" in user_messages[-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["classifier_failure", "postcondition_failed"])
async def test_pending_ack_residual_does_not_block_delivery(monkeypatch, tmp_path, reason):
    import subprocess

    repo = __import__("pathlib").Path(__file__).resolve().parents[2]
    db_path = tmp_path / "assistant.db"
    env = dict(os.environ)
    env.update({"FAM_DB": str(db_path), "PYTHONPATH": str(repo / "custom" / "fam")})
    initialized = subprocess.run(
        [sys.executable, "-m", "fam", "init", "--json"], cwd=repo,
        env=env, capture_output=True, text=True,
    )
    assert initialized.returncode == 0, initialized.stderr
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": "whatsapp:+77011102626",
        "items": [{
            "kind": "event", "ref_id": 1, "event_id": 1,
            "title": "Врач", "current_state": "active",
            "wa_message_ids": ["wa-rem-1"],
        }],
    }
    classifier = tmp_path / "classifier.py"
    if reason == "classifier_failure":
        classifier.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    else:
        classifier.write_text(
            "import json\n"
            "print(json.dumps({'dispositions':[{'kind':'event','ref_id':1,"
            "'disposition':'cancel_occurrence'}]}))\n",
            encoding="utf-8",
        )
    fam_cfg = tmp_path / "fam-config.json"
    fam_cfg.write_text(json.dumps({
        "gate_model": "test-model", "gate_provider": "test-provider",
        "classifier_command": [sys.executable, str(classifier)],
    }), encoding="utf-8")

    runner = _runner(monkeypatch, tmp_path)
    monkeypatch.setattr(gateway_run, "_read_pending_acks", lambda _path: snapshot)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"pending_acks_file": str(tmp_path / "pending-acks.json"),
                 "fam_db_path": str(db_path), "fam_config_path": str(fam_cfg)},
    )
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model", {
            "api_key": "test-key", "base_url": "http://provider.test/v1",
            "provider": "openai", "api_mode": "chat_completions",
        },
    )
    runner._session_db = None
    captured = {}

    def fake_model_call(self, api_kwargs, **_kwargs):
        captured["api_messages"] = api_kwargs["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Готово", tool_calls=None),
            finish_reason="stop")], usage=None)

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call)

    response = await runner._handle_message_with_agent(
        _quoted_event(), _source(), SESSION_KEY, 1
    )

    user_messages = [
        message["content"] for message in captured["api_messages"]
        if message.get("role") == "user"
    ]
    assert user_messages
    assert response.count(gateway_run._PENDING_ACK_RESIDUAL) == 1



@pytest.mark.asyncio
async def test_pending_ack_composition_uses_real_fam_cli_and_db(
    monkeypatch, tmp_path
):
    """The gateway path crosses a real fam CLI and verifies its DB effect."""
    import sqlite3
    import subprocess

    repo = __import__("pathlib").Path(__file__).resolve().parents[2]
    db_path = tmp_path / "private" / "amina" / "assistant.db"
    db_path.parent.mkdir(parents=True)
    env = dict(os.environ)
    env.update({"FAM_DB": str(db_path), "PYTHONPATH": str(repo / "custom" / "fam")})
    initialized = subprocess.run(
        [sys.executable, "-m", "fam", "init", "--json"], cwd=repo,
        env=env, capture_output=True, text=True,
    )
    assert initialized.returncode == 0, initialized.stderr
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO events(title,start_utc,created_at,updated_at) VALUES(?,?,?,?)",
        ("Врач", "2099-09-04T12:00:00+00:00", now, now),
    )
    conn.execute(
        "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
        "VALUES(1,'leave',?,?,?,?)", (now, "sent", now, now),
    )
    conn.execute(
        "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
        "VALUES(1,'leave',?,?,?,NULL)", ("2099-09-04T12:00:00+00:00", "pending", now),
    )
    conn.execute(
        "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
        "VALUES('wa-rem-1','reminder',1,1,?)", (now,),
    )
    conn.execute(
        "INSERT INTO plans(title,status,prep_for_event_id,prep_when,created_at) VALUES(?,?,?,?,?)",
        ("Взять документы", "open", 1, "departure", now),
    )
    conn.commit()
    snapshot_path = tmp_path / "pending-acks.json"
    acks.write(conn, cfg={"target": "whatsapp:+77011102626"},
               path=snapshot_path, now_utc=now)
    assert any(item.get("wa_message_ids") == ["wa-rem-1"]
               for item in json.loads(snapshot_path.read_text(encoding="utf-8"))["items"])
    conn.close()

    classifier = tmp_path / "classifier.py"
    classifier.write_text(
        "import json\n"
        "print(json.dumps({'dispositions':[{'kind':'event','ref_id':1,"
        "'disposition':'cancel_occurrence'}]}))\n",
        encoding="utf-8",
    )
    fam_cfg = tmp_path / "fam-config.json"
    fam_cfg.write_text(json.dumps({
        "gate_model": "test-model", "gate_provider": "test-provider",
        "classifier_command": [sys.executable, str(classifier)],
        "pending_acks_path": str(snapshot_path),
    }), encoding="utf-8")

    runner = _runner(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gateway_run, "_load_gateway_config",
        lambda: {"pending_acks_file": str(snapshot_path),
                 "fam_db_path": str(db_path),
                 "fam_config_path": str(fam_cfg)},
    )
    monkeypatch.setattr(runner, "_reply_anchor_for_event",
                        lambda event: event.reply_to_message_id)
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model", {"api_key": "test-key", "base_url": "http://provider.test/v1",
                        "provider": "openai", "api_mode": "chat_completions"})
    runner._session_db = None
    captured = {}

    from agent import turn_context
    real_consume = turn_context.consume_gateway_turn_context_notes
    def capture_notes(agent):
        captured["notes"] = agent._gateway_turn_context_notes
        return real_consume(agent)
    monkeypatch.setattr(turn_context, "consume_gateway_turn_context_notes", capture_notes)

    def fake_model_call(self, api_kwargs, **_kwargs):
        captured["api_messages"] = api_kwargs["messages"]
        captured["session_messages"] = self._session_messages
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Приняла", tool_calls=None),
            finish_reason="stop")], usage=None)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call)

    await runner._handle_message_with_agent(_quoted_event(), _source(), SESSION_KEY, 1)

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT status FROM events WHERE id=1").fetchone()[0] == "cancelled"
    assert check.execute("SELECT status FROM plans WHERE prep_for_event_id=1").fetchone()[0] == "dropped"
    assert check.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=1 AND status='pending'"
    ).fetchone()[0] == 0
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["items"] == []
    api_content = "\n".join(message.get("content", "")
                               for message in captured["api_messages"])
    assert "событие «Врач»" in api_content
    assert "Trusted FAM resolution" in api_content
    assert "отмени это" in captured["api_messages"][-1]["content"]
    assert gateway_run._PENDING_ACK_RESIDUAL not in api_content
    check.close()
