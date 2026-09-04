"""Composition coverage for pending-ack notes on a WhatsApp LID turn."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource


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
