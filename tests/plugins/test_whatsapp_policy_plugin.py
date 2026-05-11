import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.session_context import clear_session_vars, set_session_vars


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    yield hermes_home


@pytest.fixture
def wa_plugin():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "whatsapp-policy"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.whatsapp_policy",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.whatsapp_policy"
    mod.__path__ = [str(plugin_dir)]
    sys.modules["hermes_plugins.whatsapp_policy"] = mod
    spec.loader.exec_module(mod)
    return mod


def _wa_source(user_id: str, chat_id: str | None = None, user_name: str = "sender"):
    return SimpleNamespace(
        platform=SimpleNamespace(value="whatsapp"),
        chat_type="dm",
        user_id=user_id,
        chat_id=chat_id or user_id,
        user_name=user_name,
        chat_name=user_name,
        thread_id=None,
    )


def _wa_event(text: str, source, message_id: str = "m1"):
    return SimpleNamespace(
        source=source,
        text=text,
        message_id=message_id,
        is_command=lambda: False,
    )


def _gateway_with_home(chat_id: str, thread_id: str | None = None):
    home = SimpleNamespace(chat_id=chat_id, thread_id=thread_id)
    config = SimpleNamespace(get_home_channel=lambda platform: home)
    return SimpleNamespace(config=config, adapters={})


def test_unknown_inbound_creates_pending_event_and_telegram_escalation(wa_plugin):
    sent = []
    wa_plugin._schedule_telegram_control_message = lambda gateway, text: sent.append(text) or True

    event = _wa_event("Здравствуйте, вы кто?", _wa_source("+77770001122"))
    result = wa_plugin.on_pre_gateway_dispatch(event=event, gateway=object(), session_store=None)

    assert result["action"] == "skip"
    state = wa_plugin._load_state()
    pending = list(state["pending_events"].values())
    assert len(pending) == 1
    assert pending[0]["kind"] == "unknown_inbound"
    assert pending[0]["sender"] == "77770001122"
    assert sent and "Unknown WhatsApp inbound" in sent[0]


def test_outbound_whatsapp_send_from_telegram_auto_opens_thread_and_sandbox(wa_plugin):
    tokens = set_session_vars(
        platform="telegram",
        chat_id="tg-chat",
        user_id="denis-user",
        user_name="Denis",
        session_key="telegram:tg-chat",
    )
    try:
        wa_plugin.on_post_tool_call(
            tool_name="send_message",
            args={"target": "whatsapp:+77782110625", "message": "Здравствуйте, хочу записаться."},
            result={"success": True, "message_id": "mid-1"},
        )
    finally:
        clear_session_vars(tokens)

    state = wa_plugin._load_state()
    active = state["active_threads"]
    assert len(active) == 1
    thread_id, thread = next(iter(active.items()))
    assert thread_id.startswith("wt_")
    assert thread["target"] == "77782110625"

    sandbox_dir = wa_plugin._sandbox_dir("77782110625")
    assert sandbox_dir.exists()
    transcript_lines = (sandbox_dir / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(transcript_lines) == 1
    row = json.loads(transcript_lines[0])
    assert row["direction"] == "outbound"
    assert row["source_type"] == "from_denis"
    assert "whatsapp" not in thread["aliases"]
    assert "denis-user" not in thread["aliases"]

    profile = json.loads((sandbox_dir / "profile.json").read_text(encoding="utf-8"))
    assert "whatsapp" not in profile["aliases"]
    assert "denis-user" not in profile["aliases"]


def test_failed_whatsapp_send_does_not_open_thread_or_sandbox(wa_plugin):
    tokens = set_session_vars(
        platform="telegram",
        chat_id="tg-chat",
        user_id="denis-user",
        user_name="Denis",
        session_key="telegram:tg-chat",
    )
    try:
        wa_plugin.on_post_tool_call(
            tool_name="send_message",
            args={"target": "whatsapp:+77782110625", "message": "Здравствуйте, хочу записаться."},
            result={"error": "bridge unavailable", "success": False},
        )
    finally:
        clear_session_vars(tokens)

    state = wa_plugin._load_state()
    assert not state["active_threads"]
    assert not wa_plugin._sandbox_dir("77782110625").exists()


def test_send_message_result_aliases_include_bridge_lid_metadata(wa_plugin):
    aliases = wa_plugin._send_message_result_aliases(
        {
            "success": True,
            "platform": "whatsapp",
            "chat_id": "+77782110625",
            "bridge_chat_id": "100820196565244@lid",
            "normalized_chat_id": "77782110625@s.whatsapp.net",
            "remote_jid": "100820196565244@lid",
            "message_id": "mid-1",
        }
    )

    assert "100820196565244@lid" in aliases
    assert "77782110625@s.whatsapp.net" in aliases
    assert "+77782110625" not in aliases


def test_outbound_send_remembers_bridge_chat_alias_for_lid_reply_without_mapping(wa_plugin):
    tokens = set_session_vars(
        platform="telegram",
        chat_id="tg-chat",
        user_id="denis-user",
        user_name="Denis",
        session_key="telegram:tg-chat",
    )
    try:
        wa_plugin.on_post_tool_call(
            tool_name="send_message",
            args={"target": "whatsapp:+77782110625", "message": "Здравствуйте, хочу записаться."},
            result={
                "success": True,
                "platform": "whatsapp",
                "chat_id": "+77782110625",
                "bridge_chat_id": "100820196565244@lid",
                "normalized_chat_id": "77782110625@s.whatsapp.net",
                "remote_jid": "100820196565244@lid",
                "message_id": "mid-1",
            },
        )
    finally:
        clear_session_vars(tokens)

    state = wa_plugin._load_state()
    thread_id, thread = next(iter(state["active_threads"].items()))
    assert "100820196565244@lid" in thread["aliases"]

    event = _wa_event(
        "Да, давайте созвонимся завтра.",
        _wa_source("", chat_id="100820196565244@lid", user_name="Test Contact"),
        message_id="m-lid-outbound-1",
    )
    result = wa_plugin.on_pre_gateway_dispatch(event=event, gateway=object(), session_store=None)

    assert result["action"] == "rewrite"
    assert result["bypass_auth"] is True
    assert thread_id in result["text"]


def test_inbound_lid_chat_alias_matches_active_thread_without_mapping(wa_plugin):
    state = wa_plugin._load_state()
    thread_id, thread, _ = wa_plugin._open_or_refresh_thread(
        state,
        target="+77782110625",
        purpose="Schedule a meeting",
        contact_name="Test Contact",
        opened_via="test",
    )
    thread["aliases"] = ["100820196565244@lid"]
    wa_plugin._save_state(state)

    event = _wa_event(
        "Да, давайте созвонимся завтра.",
        _wa_source("", chat_id="100820196565244@lid", user_name="Test Contact"),
        message_id="m-lid-1",
    )
    result = wa_plugin.on_pre_gateway_dispatch(event=event, gateway=object(), session_store=None)

    assert result["action"] == "rewrite"
    assert result["bypass_auth"] is True
    assert thread_id in result["text"]

    state = wa_plugin._load_state()
    assert not state["pending_events"]

    sandbox_dir = wa_plugin._sandbox_dir("77782110625")
    transcript = [json.loads(line) for line in (sandbox_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row["direction"] == "inbound" and row["message_id"] == "m-lid-1" for row in transcript)


def test_active_question_escalates_clarification_and_records_sandbox_data(wa_plugin):
    sent = []
    wa_plugin._schedule_telegram_control_message = lambda gateway, text: sent.append(text) or True

    state = wa_plugin._load_state()
    thread_id, thread, _ = wa_plugin._open_or_refresh_thread(
        state,
        target="+77015550000",
        purpose="Book a haircut",
        contact_name="Barber",
        opened_via="test",
    )
    wa_plugin._save_state(state)

    event = _wa_event("Какая модель мотоцикла?", _wa_source("+77015550000", user_name="Barber"), message_id="m42")
    result = wa_plugin.on_pre_gateway_dispatch(event=event, gateway=object(), session_store=None)

    assert result["action"] == "skip"
    assert sent and "clarification needed" in sent[0].lower()

    state = wa_plugin._load_state()
    pending = [p for p in state["pending_events"].values() if p["kind"] == "clarification_needed"]
    assert len(pending) == 1
    assert pending[0]["thread_id"] == thread_id

    sandbox_dir = wa_plugin._sandbox_dir("77015550000")
    transcript = (sandbox_dir / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    facts = (sandbox_dir / "facts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert transcript
    assert facts
    status = json.loads((sandbox_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "waiting_on_denis"
    assert pending[0]["event_id"] in status["pending_question_ids"]
    assert thread["purpose"] == "Book a haircut"


def test_answer_command_saves_owner_fact_and_queues_whatsapp_reply(wa_plugin):
    state = wa_plugin._load_state()
    thread_id, thread, _ = wa_plugin._open_or_refresh_thread(
        state,
        target="+77015550000",
        purpose="Book a haircut",
        contact_name="Barber",
        opened_via="test",
    )
    event_id = wa_plugin._create_pending_event(
        state,
        kind="clarification_needed",
        sender="+77015550000",
        text="Какая модель мотоцикла?",
        thread_id=thread_id,
    )
    wa_plugin._save_state(state)

    queued = []
    wa_plugin._set_last_gateway(object())
    wa_plugin._schedule_whatsapp_message = lambda gateway, target, text: queued.append((target, text)) or True

    tokens = set_session_vars(
        platform="telegram",
        chat_id="tg-chat",
        user_id="denis-user",
        user_name="Denis",
        session_key="telegram:tg-chat",
    )
    try:
        result = wa_plugin._handle_slash(f"answer {event_id} BMW R 1250 GS")
    finally:
        clear_session_vars(tokens)

    assert "queued WhatsApp reply" in result
    assert queued == [("77015550000", "BMW R 1250 GS")]

    sandbox_dir = wa_plugin._sandbox_dir("77015550000")
    facts = [json.loads(line) for line in (sandbox_dir / "facts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(f["fact_type"] == "clarification_answer" and f["source_type"] == "from_denis" for f in facts)

    state = wa_plugin._load_state()
    assert state["pending_events"][event_id]["status"] == "resolved"
