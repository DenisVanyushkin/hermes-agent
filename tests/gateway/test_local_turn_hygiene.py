"""Fork: clarify internals stay out of the chat, a clarify timeout says what to do,
the memory monitor is started by the gateway (gateway/local_turn_hygiene.py,
gateway/local_startup.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.context_compressor import _is_clarify_non_response
from gateway.local_turn_hygiene import assistant_turn_has_tool_call_named
from gateway.run import _prepare_gateway_status_message
from gateway.run_turn_runner_clarify_delivery import _clarify_send_then_wait


def _timed_out_clarify():
    fut = MagicMock()
    fut.result.return_value = SimpleNamespace(success=True, error=None)
    clarify_mod = MagicMock()
    clarify_mod.get_clarify_timeout.return_value = 600
    clarify_mod.wait_for_response.return_value = None
    return _clarify_send_then_wait(fut, clarify_id="cid", session_key="sk", clarify_mod=clarify_mod)


def test_clarify_timeout_tells_the_agent_to_proceed_with_a_default():
    text, answered = _timed_out_clarify()

    assert answered is False
    assert text.startswith("[user did not respond within 10m]")
    assert "best judgement" in text
    assert "say what you assumed" in text


def test_clarify_timeout_prose_still_reads_as_a_non_response_to_the_compressor():
    # Both ends against the SAME produced string, not a shared constant.
    text, _ = _timed_out_clarify()

    assert _is_clarify_non_response(text) is True


@pytest.mark.parametrize("label", [
    "clarify",
    "clarify: which day?",
    "Executing tool: clarify",
    "Tool completed: clarify",
    "⏳ waiting for user clarify response",
])
def test_clarify_status_labels_never_reach_a_chat(label):
    assert _prepare_gateway_status_message("telegram", "tool.started", label) is None


def test_ordinary_status_still_reaches_a_chat():
    assert _prepare_gateway_status_message("telegram", "tool.started", "Reading the file") == "Reading the file"


def test_detects_clarify_call_on_object_and_dict_turns():
    obj = SimpleNamespace(tool_calls=[
        SimpleNamespace(function=SimpleNamespace(name="memory")),
        SimpleNamespace(function=SimpleNamespace(name="clarify")),
    ])
    as_dict = {"role": "assistant", "tool_calls": [{"function": {"name": "clarify"}}]}

    assert assistant_turn_has_tool_call_named(obj, "clarify") is True
    assert assistant_turn_has_tool_call_named(as_dict, "clarify") is True
    assert assistant_turn_has_tool_call_named(as_dict, "browser") is False
    assert assistant_turn_has_tool_call_named({"role": "assistant"}, "clarify") is False


def test_memory_monitor_starts_with_the_configured_interval(monkeypatch):
    from gateway import local_startup, memory_monitor

    started = []
    monkeypatch.setattr(memory_monitor, "start_memory_monitoring",
                        lambda interval_seconds: started.append(interval_seconds) or True)
    monkeypatch.setattr("hermes_cli.config.load_config",
                        lambda: {"logging": {"memory_monitor": {"interval_seconds": 120}}})

    assert local_startup.start_memory_monitor() is True
    assert started == [120.0]


def test_memory_monitor_honours_enabled_false(monkeypatch):
    from gateway import local_startup, memory_monitor

    started = []
    monkeypatch.setattr(memory_monitor, "start_memory_monitoring",
                        lambda interval_seconds: started.append(interval_seconds) or True)
    monkeypatch.setattr("hermes_cli.config.load_config",
                        lambda: {"logging": {"memory_monitor": {"enabled": False}}})

    assert local_startup.start_memory_monitor() is False
    assert started == []


def test_review_gate_config_is_logged_at_startup(monkeypatch, caplog):
    from gateway import local_startup

    monkeypatch.setattr("hermes_cli.config.read_raw_config", lambda: {"review_gate": {"mode": "enforce"}})
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {"review_gate": {"mode": "enforce"}})
    with caplog.at_level("INFO", logger="gateway.run"):
        local_startup.log_review_gate_config()

    line = next(r.getMessage() for r in caplog.records if "review gate config" in r.getMessage())
    assert "config_loaded_ok=True" in line
    assert "fallback_config_used=False" in line
