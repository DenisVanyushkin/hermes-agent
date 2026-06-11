from __future__ import annotations

from types import SimpleNamespace

from agent.conversation_loop import (
    _should_preflight_block_for_profile_context,
    run_conversation,
)
from hermes_cli.profile_context import build_role_context_for_task


class _DummyToolGuardrails:
    def reset_for_turn(self):
        self.reset_called = True


class _ApprovalGateAgent:
    def __init__(self, *, api_mode: str = "codex_app_server"):
        self.session_id = "session-action-gated-preflight"
        self.provider = "openrouter"
        self.model = "gpt-5.4-mini"
        self.base_url = "https://example.invalid"
        self.api_key = ""
        self.api_mode = api_mode
        self.platform = "slack"
        self._memory_write_origin = "assistant_tool"
        self.max_iterations = 3
        self._compression_warning = None
        self._tool_guardrails = _DummyToolGuardrails()
        self._user_turn_count = 0
        self._turns_since_memory = 0
        self._memory_nudge_interval = 0
        self._tool_guardrail_halt_decision = None
        self._todo_store = SimpleNamespace(has_items=lambda: False)
        self._persisted = []
        self._ensure_db_session_called = False
        self._restore_primary_runtime_called = False
        self._execute_tool_calls_called = False
        self._run_codex_app_server_turn_called = False
        self._emit_interim_assistant_message_called = False
        self._requested_model = None
        self._cached_system_prompt = "SYSTEM PROMPT"
        self._memory_store = None
        self._memory_manager = None
        self._interrupt_requested = False
        self._interrupt_message = None
        self._interrupt_thread_signal_pending = False
        self.quiet_mode = True
        self.valid_tool_names = []
        self.tools = []
        self.compression_enabled = False

    def _ensure_db_session(self):
        self._ensure_db_session_called = True

    def _restore_primary_runtime(self):
        self._restore_primary_runtime_called = True

    def _persist_session(self, messages, conversation_history):
        self._persisted.append((messages, conversation_history))

    def _execute_tool_calls(self, *args, **kwargs):
        self._execute_tool_calls_called = True
        raise AssertionError("preflight should not stop before tool execution")

    def _run_codex_app_server_turn(self, **kwargs):
        self._run_codex_app_server_turn_called = True
        return {
            "final_response": "normal path reached",
            "last_reasoning": None,
            "messages": kwargs.get("messages", []),
            "api_calls": 0,
            "completed": True,
            "failed": False,
            "partial": False,
            "interrupted": False,
            "response_transformed": False,
            "response_previewed": False,
            "turn_exit_reason": "codex_app_server_stub",
            "model": self.model,
            "requested_model": self._requested_model,
            "provider": self.provider,
            "base_url": self.base_url,
            "session_id": self.session_id,
        }

    def _emit_interim_assistant_message(self, *_args, **_kwargs):
        self._emit_interim_assistant_message_called = True
        raise AssertionError("preflight should not stop before interim assistant callbacks")

    def _cleanup_dead_connections(self):
        return False

    def _emit_status(self, *_args, **_kwargs):
        return None

    def _replay_compression_warning(self):
        return None

    def _hydrate_todo_store(self, _history):
        return None

    def _safe_print(self, *_args, **_kwargs):
        return None


def test_critical_metadata_remains_metadata_only():
    result = build_role_context_for_task(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    )

    assert result.critical_approval_required is True
    assert _should_preflight_block_for_profile_context(result) is False


def test_prompt_lookup_with_sensitive_terms_skips_preflight(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent()

    result = run_conversation(
        agent,
        "дай полный промпт market-intelligence-14day-pilot-status. "
        "Внутри могут быть .env, auth.json, secrets, provider config, do not write files.",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_restart_plan_skips_preflight(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent()

    result = run_conversation(
        agent,
        "составь план рестарта hermes-gateway, но ничего не выполняй",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_pending_gate_still_blocks_until_explicit_approval(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    agent = _ApprovalGateAgent()

    conversation_history = [
        {"role": "user", "content": "Set up Cloudflare Tunnel for Hermes WebUI"},
        {
            "role": "assistant",
            "content": "I need explicit approval before any mutation-capable changes.",
            "_approval_gate": {
                "required": True,
                "critical_hard_stop": True,
                "selected_role": "engineer",
                "canonical_role": "engineer",
                "operation_category": "security_critical_mutation",
                "reviewer_profile": "security_auditor",
                "approval_reason": "Cloudflare/public exposure changes require explicit approval",
                "task": "Set up Cloudflare Tunnel for Hermes WebUI",
                "model_selection": {"policy_name": "approval_critical"},
            },
        },
    ]

    result = run_conversation(
        agent,
        '[Replying to: "approve"]\nNo, do not proceed yet.',
        conversation_history=conversation_history,
    )

    assert result["turn_exit_reason"] == "approval_required_preflight"
    assert "explicit approval" in result["final_response"].lower()
    assert agent._run_codex_app_server_turn_called is False
