"""Composition coverage for pending-ack notes on a WhatsApp LID turn."""

import json
import os
import subprocess
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
from fam import acks, db


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


def test_pending_ack_candidates_fan_out_quote_and_due_medication():
    snapshot = {
        "items": [
            {"kind": "event", "ref_id": 66, "wa_message_ids": ["wa-rem-66"]},
            {"kind": "med_intake", "ref_id": 46, "current_state": "pending",
             "wa_message_ids": ["wa-med-46"]},
        ]
    }
    candidates = gateway_run._pending_ack_candidates(snapshot, "wa-rem-66")
    assert [(item["kind"], item["ref_id"]) for item in candidates] == [
        ("event", 66), ("med_intake", 46)
    ]
    assert gateway_run._pending_ack_candidates(snapshot, None) is None


@pytest.mark.asyncio
async def test_partial_pending_ack_preserves_applied_sidecar_with_residual(
    monkeypatch, tmp_path
):
    snapshot = {
        "target": "whatsapp:+77011102626",
        "items": [
            {"kind": "event", "ref_id": 66, "wa_message_ids": ["wa-rem-66"]},
            {"kind": "med_intake", "ref_id": 46, "current_state": "pending",
             "wa_message_ids": ["wa-med-46"]},
        ],
    }
    partial = {
        "status": "partial",
        "residual": True,
        "applied": [{"kind": "event", "ref_id": 66,
                     "disposition": "cancel_occurrence"}],
        "unresolved": 1,
        "trusted_sidecar": "[Trusted FAM resolution: event 66 applied and verified; do not repeat.]",
    }

    monkeypatch.setattr(gateway_run, "_pending_acks_note", lambda *_args: "note")
    monkeypatch.setattr(
        gateway_run,
        "_pending_ack_candidates",
        lambda *_args: snapshot["items"],
    )

    async def fake_to_thread(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["fam"], returncode=0,
            stdout=json.dumps(partial), stderr="",
        )

    monkeypatch.setattr(gateway_run.asyncio, "to_thread", fake_to_thread)
    result = await gateway_run._resolve_pending_ack_turn(
        _quoted_event(), _source(), snapshot, {"fam_db_path": str(tmp_path / "assistant.db")}
    )

    assert result["status"] == "partial"
    assert result["residual"] is True
    assert "Trusted FAM resolution" in result["trusted_sidecar"]


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


@pytest.mark.asyncio
async def test_pending_ack_s2_composition_applies_event_and_med(
    monkeypatch, tmp_path
):
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
        ("Тренировка", "2099-09-04T12:00:00+00:00", now, now),
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
        "INSERT INTO meds(name,times,created_at,updated_at) VALUES(?,?,?,?)",
        ("Мисол", json.dumps(["10:00"]), now, now),
    )
    conn.execute(
        "INSERT INTO med_intakes(med_id,plan_ts_utc,status,created_at) VALUES(?,?,?,?)",
        (1, now, "pending", now),
    )
    conn.execute(
        "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
        "VALUES('wa-rem-1','reminder',1,1,?)", (now,),
    )
    conn.execute(
        "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
        "VALUES('wa-med-1','med',1,1,?)", (now,),
    )
    conn.execute(
        "INSERT INTO plans(title,status,prep_for_event_id,prep_when,created_at) VALUES(?,?,?,?,?)",
        ("Взять форму", "open", 1, "departure", now),
    )
    conn.commit()
    acks.write(conn, cfg={"target": "whatsapp:+77011102626"},
               path=tmp_path / "pending-acks.json", now_utc=now)
    conn.close()

    classifier = tmp_path / "classifier-s2.py"
    classifier.write_text(
        "import json\n"
        "print(json.dumps({'dispositions':["
        "{'kind':'event','ref_id':1,'disposition':'cancel_occurrence'},"
        "{'kind':'med_intake','ref_id':1,'disposition':'taken'}]}))\n",
        encoding="utf-8",
    )
    fam_cfg = tmp_path / "fam-config-s2.json"
    fam_cfg.write_text(json.dumps({
        "gate_model": "test-model", "gate_provider": "test-provider",
        "classifier_command": [sys.executable, str(classifier)],
        "pending_acks_path": str(tmp_path / "pending-acks.json"),
    }), encoding="utf-8")

    runner = _runner(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gateway_run, "_load_gateway_config",
        lambda: {"pending_acks_file": str(tmp_path / "pending-acks.json"),
                 "fam_db_path": str(db_path), "fam_config_path": str(fam_cfg)},
    )
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model", {"api_key": "test-key", "base_url": "http://provider.test/v1",
                        "provider": "openai", "api_mode": "chat_completions"}
    )
    runner._session_db = None
    captured = {}

    def fake_model_call(self, api_kwargs, **_kwargs):
        captured["api_messages"] = api_kwargs["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Записала оба", tool_calls=None),
            finish_reason="stop")], usage=None)

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call)
    event = MessageEvent(
        text="Сегодня пропущу тренировку\nМисол приняла",
        source=_source(), message_id="wamid-inbound-s2",
        reply_to_message_id="wa-rem-1", reply_to_text="Напоминание о тренировке",
    )

    await runner._handle_message_with_agent(event, _source(), SESSION_KEY, 1)

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT status FROM events WHERE id=1").fetchone()[0] == "cancelled"
    assert check.execute("SELECT status FROM med_intakes WHERE id=1").fetchone()[0] == "taken"
    assert check.execute(
        "SELECT status FROM plans WHERE prep_for_event_id=1"
    ).fetchone()[0] == "dropped"
    assert check.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=1 AND status='pending'"
    ).fetchone()[0] == 0
    assert json.loads((tmp_path / "pending-acks.json").read_text(encoding="utf-8"))["items"] == []
    api_content = "\n".join(
        message.get("content", "") for message in captured["api_messages"]
    )
    assert "Trusted FAM resolution: event 1" in api_content
    assert "Trusted FAM resolution: med_intake 1" in api_content
    assert "Сегодня пропущу тренировку" in api_content
    assert "Мисол приняла" in api_content
    assert gateway_run._PENDING_ACK_RESIDUAL not in api_content
    check.close()


@pytest.mark.asyncio
async def test_pending_ack_s2_composition_partial_preserves_applied_sidecar_with_residual(
    monkeypatch, tmp_path
):
    """A real partial FAM result reaches the model and the user exactly once."""
    import sqlite3

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
        ("Тренировка", "2099-09-04T12:00:00+00:00", now, now),
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
        "INSERT INTO meds(name,times,created_at,updated_at) VALUES(?,?,?,?)",
        ("Мисол", json.dumps(["10:00"]), now, now),
    )
    conn.execute(
        "INSERT INTO med_intakes(med_id,plan_ts_utc,status,created_at) VALUES(?,?,?,?)",
        (1, now, "pending", now),
    )
    conn.execute(
        "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
        "VALUES('wa-rem-1','reminder',1,1,?)", (now,),
    )
    conn.execute(
        "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
        "VALUES('wa-med-1','med',1,1,?)", (now,),
    )
    conn.commit()
    snapshot_path = tmp_path / "pending-acks.json"
    acks.write(conn, cfg={"target": "whatsapp:+77011102626"},
               path=snapshot_path, now_utc=now)
    conn.close()

    classifier = tmp_path / "classifier-s2-partial.py"
    classifier.write_text(
        "import json\n"
        "print(json.dumps({'dispositions':["
        "{'kind':'event','ref_id':1,'disposition':'cancel_occurrence'},"
        "{'kind':'med_intake','ref_id':1,'disposition':'not-a-med-disposition'}]}))\n",
        encoding="utf-8",
    )
    fam_cfg = tmp_path / "fam-config-s2-partial.json"
    fam_cfg.write_text(json.dumps({
        "gate_model": "test-model", "gate_provider": "test-provider",
        "classifier_command": [sys.executable, str(classifier)],
        "pending_acks_path": str(snapshot_path),
    }), encoding="utf-8")

    runner = _runner(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gateway_run, "_load_gateway_config",
        lambda: {"pending_acks_file": str(snapshot_path),
                 "fam_db_path": str(db_path), "fam_config_path": str(fam_cfg)},
    )
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model", {"api_key": "test-key", "base_url": "http://provider.test/v1",
                        "provider": "openai", "api_mode": "chat_completions"}
    )
    runner._session_db = None
    captured = {}

    def fake_model_call(self, api_kwargs, **_kwargs):
        captured["api_messages"] = api_kwargs["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Записала тренировку", tool_calls=None),
            finish_reason="stop")], usage=None)

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call)
    event = MessageEvent(
        text="Сегодня пропущу тренировку\nМисол приняла",
        source=_source(), message_id="wamid-inbound-s2-partial",
        reply_to_message_id="wa-rem-1", reply_to_text="Напоминание о тренировке",
    )

    response = await runner._handle_message_with_agent(event, _source(), SESSION_KEY, 1)

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT status FROM events WHERE id=1").fetchone()[0] == "cancelled"
    assert check.execute("SELECT status FROM med_intakes WHERE id=1").fetchone()[0] == "pending"
    api_content = "\n".join(
        message.get("content", "") for message in captured["api_messages"]
    )
    assert "Trusted FAM resolution: event 1" in api_content
    assert response.count(gateway_run._PENDING_ACK_RESIDUAL) == 1
    check.close()


def test_pending_ack_candidates_without_quote_returns_all_open_event_candidates():
    snapshot = {
        "target": "whatsapp:+77011102626",
        "items": [
            {"kind": "event", "ref_id": 66, "current_state": "active",
             "wa_message_ids": ["wa-rem-66"]},
            {"kind": "event", "ref_id": 67, "current_state": "active",
             "wa_message_ids": ["wa-rem-67"]},
            {"kind": "med_intake", "ref_id": 46, "current_state": "pending",
             "wa_message_ids": ["wa-med-46"]},
        ],
    }

    candidates = gateway_run._pending_ack_candidates(snapshot, None)

    assert [(item["kind"], item["ref_id"]) for item in candidates] == [
        ("event", 66), ("event", 67)
    ]


@pytest.mark.asyncio
async def test_pending_ack_s3_composition_resolves_without_quote(
    monkeypatch, tmp_path
):
    """A no-quote event crosses the real fam CLI and disappears after cancel."""
    import sqlite3

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
        ("Тренировка", "2099-09-04T12:00:00+00:00", now, now),
    )
    conn.execute(
        "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
        "VALUES(1,'leave',?,?,?,?)",
        (now, "sent", now, now),
    )
    conn.execute(
        "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
        "VALUES('wa-rem-s3','reminder',1,1,?)",
        (now,),
    )
    conn.commit()
    snapshot_path = tmp_path / "pending-acks.json"
    acks.write(
        conn, cfg={"target": "whatsapp:+77011102626"},
        path=snapshot_path, now_utc=now,
    )
    initial_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert [(item["kind"], item["ref_id"]) for item in initial_snapshot["items"]] == [
        ("event", 1)
    ]
    conn.close()

    classifier = tmp_path / "classifier-s3.py"
    classifier.write_text(
        "import json\n"
        "print(json.dumps({'dispositions':["
        "{'kind':'event','ref_id':1,'disposition':'cancel_occurrence'}]}))\n",
        encoding="utf-8",
    )
    fam_cfg = tmp_path / "fam-config-s3.json"
    fam_cfg.write_text(json.dumps({
        "gate_model": "test-model", "gate_provider": "test-provider",
        "classifier_command": [sys.executable, str(classifier)],
        "pending_acks_path": str(snapshot_path),
    }), encoding="utf-8")

    runner = _runner(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gateway_run, "_load_gateway_config",
        lambda: {"pending_acks_file": str(snapshot_path),
                 "fam_db_path": str(db_path), "fam_config_path": str(fam_cfg)},
    )
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model", {"api_key": "test-key", "base_url": "http://provider.test/v1",
                        "provider": "openai", "api_mode": "chat_completions"}
    )
    runner._session_db = None
    captured = {}

    def fake_model_call(self, api_kwargs, **_kwargs):
        captured["api_messages"] = api_kwargs["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Отменила тренировку", tool_calls=None),
            finish_reason="stop")], usage=None)

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call)
    event = MessageEvent(
        text="Тренировки в этот раз не будет",
        source=_source(), message_id="wamid-inbound-s3",
    )
    assert event.reply_to_message_id is None

    response = await runner._handle_message_with_agent(event, _source(), SESSION_KEY, 1)

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT status FROM events WHERE id=1").fetchone()[0] == "cancelled"
    assert check.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='resolve.turn'"
    ).fetchone()[0] == 1
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["items"] == []
    api_content = "\n".join(
        message.get("content", "") for message in captured["api_messages"]
    )
    assert "Trusted FAM resolution: event 1" in api_content
    assert gateway_run._PENDING_ACK_RESIDUAL not in response
    check.close()
def test_pending_ack_residual_plan_aggregates_unresolved_candidates_once():
    receipt = {
        "status": "partial",
        "residual": True,
        "unresolved_refs": [
            {"kind": "event", "ref_id": 1, "reason": "ambiguous", "title": "Врач"},
            {"kind": "med_intake", "ref_id": 3, "reason": "classifier_failure", "name": "Мисол"},
        ],
    }

    plan = gateway_run._pending_ack_residual_plan(receipt, "Готово")

    assert plan["message"] == gateway_run._PENDING_ACK_RESIDUAL
    assert plan["candidate_keys"] == ["event:1", "med_intake:3"]


def test_pending_ack_residual_plan_reuses_main_clarification():
    receipt = {
        "status": "unresolved",
        "residual": True,
        "unresolved_refs": [
            {"kind": "event", "ref_id": 1, "reason": "ambiguous", "title": "Врач"},
        ],
    }

    assert gateway_run._pending_ack_residual_plan(
        receipt, "Уточни, отменять ли событие «Врач»?"
    ) is None
    assert gateway_run._pending_ack_residual_plan(receipt, "Приняла, спасибо") is not None


@pytest.mark.parametrize("reason", ["unrelated", "snooze", "defer"])
def test_pending_ack_residual_plan_suppresses_nonterminal_non_actionable(reason):
    receipt = {
        "status": "unresolved",
        "residual": True,
        "unresolved_refs": [
            {"kind": "med_intake", "ref_id": 3, "reason": reason, "name": "Мисол"},
        ],
    }

    assert gateway_run._pending_ack_residual_plan(receipt, "Готово") is None


class _PostDeliveryRecordingAdapter:
    def __init__(self):
        self.sent = []
        self.callbacks = {}

    def register_post_delivery_callback(self, session_key, callback, *, generation=None):
        if not callable(callback):
            return
        existing = self.callbacks.get(session_key)
        if existing is None:
            self.callbacks[session_key] = callback
            return

        async def chained():
            import inspect
            for item in (existing, callback):
                result = item()
                if inspect.isawaitable(result):
                    await result

        self.callbacks[session_key] = chained

    def get_pending_message(self, session_key):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append(content)
        return SimpleNamespace(success=True, message_id=f"m-{len(self.sent)}")


@pytest.mark.asyncio
async def test_pending_ack_residual_is_separate_post_delivery_message(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    adapter = _PostDeliveryRecordingAdapter()
    runner.adapters = {Platform.WHATSAPP: adapter}
    plan = {"message": gateway_run._PENDING_ACK_RESIDUAL, "candidate_keys": ["event:1"]}

    assert await runner._defer_pending_ack_residual_after_delivery(
        _source(), SESSION_KEY, plan
    ) is True
    await adapter.send(_source().chat_id, "Основной ответ")
    await adapter.callbacks[SESSION_KEY]()


@pytest.mark.asyncio
async def test_pending_ack_s4_composition_delivers_main_then_one_residual(
    monkeypatch, tmp_path
):
    """Real fam partial state is delivered as MAIN followed by one residual."""
    import sqlite3

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
    conn = db.connect(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for event_id, title, message_id in (
        (1, "Врач", "wa-rem-s4-1"), (2, "Тренировка", "wa-rem-s4-2")
    ):
        conn.execute(
            "INSERT INTO events(id,title,start_utc,created_at,updated_at) VALUES(?,?,?,?,?)",
            (event_id, title, "2099-09-04T12:00:00+00:00", now, now),
        )
        conn.execute(
            "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
            "VALUES(?,?,?,?,?,?)",
            (event_id, "leave", now, "sent", now, now),
        )
        conn.execute(
            "INSERT INTO sent_messages(wa_message_id,kind,ref_id,event_id,created_at) "
            "VALUES(?,?,?,?,?)",
            (message_id, "reminder", 1, event_id, now),
        )
    conn.commit()
    snapshot_path = tmp_path / "pending-acks.json"
    acks.write(conn, cfg={"target": "whatsapp:+77011102626"},
               path=snapshot_path, now_utc=now)
    conn.close()

    classifier = tmp_path / "classifier-s4.py"
    classifier.write_text(
        "import json\n"
        "print(json.dumps({'dispositions':["
        "{'kind':'event','ref_id':1,'disposition':'cancel_occurrence'},"
        "{'kind':'event','ref_id':2,'disposition':'not-a-disposition'}]}))\n",
        encoding="utf-8",
    )
    fam_cfg = tmp_path / "fam-config-s4.json"
    fam_cfg.write_text(json.dumps({
        "gate_model": "test-model", "gate_provider": "test-provider",
        "classifier_command": [sys.executable, str(classifier)],
        "pending_acks_path": str(snapshot_path),
    }), encoding="utf-8")

    runner = _runner(monkeypatch, tmp_path)
    adapter = _PostDeliveryRecordingAdapter()
    runner.adapters = {Platform.WHATSAPP: adapter}
    monkeypatch.setattr(
        gateway_run, "_load_gateway_config",
        lambda: {"pending_acks_file": str(snapshot_path),
                 "fam_db_path": str(db_path), "fam_config_path": str(fam_cfg)},
    )
    runner._resolve_session_agent_runtime = lambda **_kwargs: (
        "test-model", {"api_key": "test-key", "base_url": "http://provider.test/v1",
                        "provider": "openai", "api_mode": "chat_completions"}
    )
    runner._session_db = None


    def fake_model_call(self, api_kwargs, **_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Основной ответ", tool_calls=None),
            finish_reason="stop")], usage=None)

    monkeypatch.setattr("run_agent.AIAgent._interruptible_api_call", fake_model_call)
    monkeypatch.setattr("run_agent.AIAgent._interruptible_streaming_api_call", fake_model_call)
    event = MessageEvent(
        text="Отмени одно событие, второе пока неясно",
        source=_source(), message_id="wamid-inbound-s4",
    )

    response = await runner._handle_message_with_agent(event, _source(), SESSION_KEY, 1)
    await adapter.send(_source().chat_id, response)
    assert adapter.callbacks, (response, runner._session_state(SESSION_KEY).conversation.pending_ack_residuals)
    assert callable(adapter.callbacks[SESSION_KEY])
    await adapter.callbacks[SESSION_KEY]()

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT status FROM events WHERE id=1").fetchone()[0] == "cancelled"
    assert check.execute("SELECT status FROM events WHERE id=2").fetchone()[0] == "active"
    assert adapter.sent == ["Основной ответ", gateway_run._PENDING_ACK_RESIDUAL]
    assert adapter.sent.count(gateway_run._PENDING_ACK_RESIDUAL) == 1
    assert gateway_run._PENDING_ACK_RESIDUAL not in response
    check.close()
