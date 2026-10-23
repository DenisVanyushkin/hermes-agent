"""The turn prologue applies the fork's role setup (agent/local_roles.py).

Drives upstream's real ``build_turn_context`` with the minimal agent stand-in
of tests/agent/test_api_content_sidecar.py: the role context must reach what
is sent to the model (``api_content``) and never the persisted ``content``,
ahead of plugin context; the turn must move to its role's model and effort.
"""
import types
from unittest.mock import patch

import pytest

from agent.turn_context import build_turn_context


class _FakeTodoStore:
    def has_items(self):
        return True


class _FakeGuardrails:
    def reset_for_turn(self):
        pass


class _FakeAgent:
    def __init__(self):
        self.session_id = "sess-1"
        self.model = "gpt-6-luna"
        self.provider = "openai-codex"
        self.base_url = "https://chatgpt.com/backend-api/codex"
        self.api_key = "sk-x"
        self.api_mode = "chat_completions"
        self.platform = "cli"
        self.quiet_mode = True
        self.max_iterations = 90
        self.tools = []
        self.valid_tool_names = set()
        self._skip_mcp_refresh = True
        self.compression_enabled = False
        self.context_compressor = types.SimpleNamespace(protect_first_n=2, protect_last_n=2)
        self._cached_system_prompt = "SYSTEM"
        self._memory_store = None
        self._memory_manager = None
        self._memory_nudge_interval = 0
        self._turns_since_memory = 0
        self._user_turn_count = 0
        self._todo_store = _FakeTodoStore()
        self._tool_guardrails = _FakeGuardrails()
        self._compression_warning = None
        self._interrupt_requested = False
        self._memory_write_origin = "assistant_tool"
        self._stream_context_scrubber = None
        self._stream_think_scrubber = None
        self.reasoning_config = {"enabled": True, "effort": "medium"}

    def _ensure_db_session(self):
        pass

    def _restore_primary_runtime(self):
        pass

    def _cleanup_dead_connections(self):
        return False

    def _emit_status(self, _msg):
        pass

    def _replay_compression_warning(self):
        pass

    def _hydrate_todo_store(self, *_a, **_k):
        pass

    def _safe_print(self, *_a, **_k):
        pass

    def _persist_session(self, messages, _history=None):
        pass


def _build(agent, user_message="hello"):
    return build_turn_context(
        agent=agent, user_message=user_message, system_message=None, conversation_history=None,
        task_id=None, stream_callback=None, persist_user_message=None,
        restore_or_build_system_prompt=lambda *a, **k: None, install_safe_stdio=lambda: None,
        sanitize_surrogates=lambda s: s, summarize_user_message_for_log=lambda s: s,
        set_session_context=lambda _sid: None, set_current_write_origin=lambda _o: None,
        ra=lambda: types.SimpleNamespace(_set_interrupt=lambda *a, **k: None),
    )


def _role(role="lawyer", context="ROLE-CTX"):
    return types.SimpleNamespace(
        profile_context_used=bool(context), context_text=context, selected_role=role, canonical_role=role,
        fallback_reason=None, operation_category="", critical_approval_required=False)


@pytest.fixture(autouse=True)
def _stub_runtime_main():
    with patch("agent.auxiliary_client.set_runtime_main", lambda *a, **k: None):
        yield


@pytest.fixture(autouse=True)
def _roles_on(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config_readonly",
                        lambda *a, **k: {"role_routing": {"strategy": "llm"}})


@pytest.fixture
def switches(monkeypatch):
    calls = []

    def fake_switch(agent, model, provider, *a, **k):
        calls.append((model, provider))
        agent.model = model
    monkeypatch.setattr("agent.local_roles.switch_model", fake_switch)
    return calls


def test_role_context_reaches_the_model_not_the_transcript(monkeypatch, switches):
    monkeypatch.setattr("hermes_cli.profile_context.build_role_context_for_task", lambda task: _role())
    with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
        ctx = _build(_FakeAgent())
    msg = ctx.messages[ctx.current_turn_user_idx]
    assert msg["content"] == "hello"
    assert msg["api_content"] == "hello\n\nROLE-CTX"


def test_role_context_comes_before_plugin_context(monkeypatch, switches):
    monkeypatch.setattr("hermes_cli.profile_context.build_role_context_for_task", lambda task: _role())
    with patch("hermes_cli.plugins.invoke_hook", return_value=[{"context": "PLUGIN-CTX"}]):
        ctx = _build(_FakeAgent())
    assert ctx.messages[ctx.current_turn_user_idx]["api_content"] == "hello\n\nROLE-CTX\n\nPLUGIN-CTX"


def test_the_turn_moves_to_its_roles_model_and_effort(monkeypatch, switches):
    monkeypatch.setattr("hermes_cli.profile_context.build_role_context_for_task",
                        lambda task: _role("engineer", ""))
    agent = _FakeAgent()
    with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
        _build(agent, "rebase the branch")
    assert switches == [("gpt-5.6-terra", "openai-codex")]
    assert agent.reasoning_config == {"enabled": True, "effort": "high"}
    assert agent._model_selection["effective_role"] == "engineer"


def test_a_broken_role_build_never_breaks_the_turn(monkeypatch, switches):
    def boom(task):
        raise RuntimeError("router down")
    monkeypatch.setattr("hermes_cli.profile_context.build_role_context_for_task", boom)
    with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
        ctx = _build(_FakeAgent())
    assert "api_content" not in ctx.messages[ctx.current_turn_user_idx]


def test_the_prologue_calls_the_role_setup_before_plugins():
    import pathlib

    src = pathlib.Path("agent/turn_context.py").read_text()
    assert src.index("apply_turn_role(") < src.index("plugin_user_context = _collect_pre_llm_call_context(")


def test_a_child_inherits_the_parents_pre_floor_effort():
    from hermes_cli.role_reasoning import apply_reasoning_floor
    from tools.delegate_tool_config import _child_reasoning

    parent = types.SimpleNamespace(reasoning_config={"enabled": True, "effort": "medium"},
                                   _reasoning_session_override=None)
    apply_reasoning_floor(parent, "high")
    assert parent.reasoning_config == {"enabled": True, "effort": "high"}
    assert _child_reasoning(parent, {}, None) == {"enabled": True, "effort": "medium"}


def test_a_fallback_swap_keeps_the_roles_floor(monkeypatch):
    from agent.chat_completion_helpers import _reresolve_fallback_reasoning_config
    from hermes_cli.role_reasoning import apply_reasoning_floor

    agent = types.SimpleNamespace(model="fallback/model", reasoning_config={"enabled": True, "effort": "medium"},
                                  _reasoning_session_override=None)
    apply_reasoning_floor(agent, "high")
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    monkeypatch.setattr("hermes_constants.resolve_reasoning_config",
                        lambda cfg, model: {"enabled": True, "effort": "medium"})
    _reresolve_fallback_reasoning_config(agent)
    assert agent.reasoning_config == {"enabled": True, "effort": "high"}


def test_without_role_routing_the_turn_is_upstreams(monkeypatch, switches):
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda *a, **k: {})
    monkeypatch.setattr("hermes_cli.profile_context.build_role_context_for_task", lambda task: _role("engineer"))
    agent = _FakeAgent()
    with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
        ctx = _build(agent, "rebase the branch")
    assert "api_content" not in ctx.messages[ctx.current_turn_user_idx]
    assert switches == [] and agent.reasoning_config == {"enabled": True, "effort": "medium"}
