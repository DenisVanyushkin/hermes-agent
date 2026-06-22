"""Regression tests for session-scoped model/provider overrides in gateway agents.

These cover the bug where `/model ...` stored a session override, but fresh
agent constructions still resolved model/provider from global config/runtime.
That let helper agents (and cache-miss main agents) route GPT-5.4 to the wrong
provider, e.g. Nous instead of OpenAI Codex.
"""

import asyncio
import importlib
import sys
import threading
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource
from hermes_cli.pipeline_state import ExecutionReport, OrchestratorObserveReport, PipelineState
from hermes_cli.pipeline_session import PipelineSession, PipelineSessionStatus


class _CapturingAgent:
    """Fake agent that records init kwargs for assertions."""

    last_init = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(self, user_message: str, conversation_history=None, task_id=None):
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
        }


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner.session_store = None
    runner.config = None
    runner._voice_mode = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._service_tier = None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._background_tasks = set()
    runner._session_db = None
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._pending_approvals = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    return runner


def _codex_override():
    return {
        "model": "gpt-5.4",
        "provider": "openai-codex",
        "api_key": "***",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_mode": "codex_responses",
    }


def _explode_runtime_resolution():
    raise AssertionError(
        "global runtime resolution should not run when a complete session override exists"
    )


def test_run_agent_prefers_session_override_over_global_runtime(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _explode_runtime_resolution)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    _CapturingAgent.last_init = None
    runner = _make_runner()

    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="cli",
        chat_name="CLI",
        chat_type="dm",
        user_id="user-1",
    )
    session_key = "agent:main:local:dm"
    runner._session_model_overrides[session_key] = _codex_override()
    runner._session_reasoning_overrides[session_key] = {"enabled": True, "effort": "high"}

    result = asyncio.run(
        runner._run_agent(
            message="ping",
            context_prompt="",
            history=[],
            source=source,
            session_id="session-1",
            session_key=session_key,
        )
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init is not None
    assert _CapturingAgent.last_init["model"] == "gpt-5.4"
    assert _CapturingAgent.last_init["provider"] == "openai-codex"
    assert _CapturingAgent.last_init["api_mode"] == "codex_responses"
    assert _CapturingAgent.last_init["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert _CapturingAgent.last_init["api_key"] == "***"
    assert _CapturingAgent.last_init["reasoning_config"] == {"enabled": True, "effort": "high"}


def test_pipeline_observe_hook_runs_before_run_conversation_without_changing_result(monkeypatch):
    events: list[tuple[str, object]] = []

    class _OrderingAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []
            self.model = kwargs.get("model")
            self.provider = kwargs.get("provider")

        def run_conversation(self, user_message: str, conversation_history=None, task_id=None, **kwargs):
            events.append(
                (
                    "run_conversation",
                    {
                        "user_message": user_message,
                        "conversation_history": conversation_history,
                        "task_id": task_id,
                        "kwargs": kwargs,
                    },
                )
            )
            return {
                "final_response": "ok",
                "messages": [],
                "api_calls": 1,
            }

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _OrderingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"pipelines": {"router": {"mode": "observe"}}})
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "***",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "credential_pool": None,
            "max_tokens": None,
        },
    )

    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")

    def _fake_observe(**kwargs):
        events.append(("observe", kwargs))
        return None

    monkeypatch.setattr(pipeline_observe, "observe_pipeline_router_decision", _fake_observe)

    runner = _make_runner()
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="cli",
        chat_name="CLI",
        chat_type="dm",
        user_id="user-1",
    )

    result = asyncio.run(
        runner._run_agent(
            message="ping",
            context_prompt="",
            history=[],
            source=source,
            session_id="session-observe-1",
            session_key="agent:main:local:dm",
        )
    )

    assert result["final_response"] == "ok"
    assert [event for event, _ in events] == ["observe", "run_conversation"]
    run_payload = events[1][1]
    assert events[0][1]["logger"] is gateway_run.logger
    assert run_payload["user_message"] == "ping"
    assert run_payload["conversation_history"] == []
    assert run_payload["task_id"] == "session-observe-1"


def test_autonomous_routing_failed_blocks_normal_agent_fallback(monkeypatch):
    events: list[str] = []

    class _FailIfCalledAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []

        def run_conversation(self, user_message: str, conversation_history=None, task_id=None, **kwargs):
            events.append("run_conversation")
            raise AssertionError("normal AIAgent fallback must not run after autonomous routing failure")

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _FailIfCalledAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "pipelines": {
                "enabled": True,
                "router": {"mode": "autonomous"},
                "orchestrator": {"mode": "autonomous"},
                "execution": {"mode": "autonomous"},
            }
        },
    )
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "***",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "credential_pool": None,
            "max_tokens": None,
        },
    )

    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: gateway_run.dataclasses.make_dataclass("RouterStub", [("status", str)])("routing_failed"),
    )

    report = OrchestratorObserveReport(
        session=PipelineSession(
            pipeline_session_id="pipe-failed",
            trace_id="pipe-failed",
            pipeline_id="default_conversation_pipeline",
            router_status="routing_failed",
            router_confidence=0.0,
            platform="local",
            session_key="agent:main:local:dm",
            session_id="session-auto-failed",
            chat_id="cli",
            thread_id=None,
            user_id="user-1",
            created_at="2026-06-22T00:00:00+00:00",
            user_message_hash="hash",
            mode="autonomous",
            current_state="safe_default_fallback",
            status=PipelineSessionStatus.CREATED,
            planned_steps=[],
            selected_subagent_ids=["general_operator"],
            reviewer_condition=None,
        ),
        state=PipelineState(
            pipeline_session_id="pipe-failed",
            pipeline_id="default_conversation_pipeline",
            state="safe_default_fallback",
            mode="autonomous",
            router_status="routing_failed",
            selected_pipeline_id=None,
            fallback_pipeline_id="default_conversation_pipeline",
            completion_allowed=False,
            completion_blocked_reason="autonomous_not_selected",
            final_verdict="safe_default_fallback_used",
        ),
        execution_report=ExecutionReport(
            pipeline_session_id="pipe-failed",
            pipeline_id="default_conversation_pipeline",
            router_status="routing_failed",
            selected_pipeline_id=None,
            fallback_pipeline_id="default_conversation_pipeline",
            completion_allowed=False,
            completion_reason="safe_default_fallback_used",
            executed=False,
            would_execute=False,
            execution_mode="autonomous",
            runtime_status="not_executed",
        ),
        pipeline_execution_controller=type(
            "ControllerStub",
            (),
            {"actual_execution_invoked": False, "blocked_reason": "autonomous_not_selected"},
        )(),
    )
    monkeypatch.setattr(orchestrator, "observe_gateway_turn", lambda **_kwargs: report)

    runner = _make_runner()
    source = SessionSource(platform=Platform.LOCAL, chat_id="cli", chat_name="CLI", chat_type="dm", user_id="user-1")
    result = asyncio.run(
        runner._run_agent(
            message="Create tests/autonomous_runtime_smoke_marker.py and write a marker",
            context_prompt="",
            history=[],
            source=source,
            session_id="session-auto-failed",
            session_key="agent:main:local:dm",
        )
    )

    assert events == []
    assert result["api_calls"] == 0
    assert result["tools"] == []
    assert "I could not reliably select the autonomous engineering pipeline" in result["final_response"]
    assert "effective_pipeline: default_conversation_pipeline" in result["final_response"]
    assert "final_verdict: safe_default_fallback_used" in result["final_response"]
    assert "tools_enabled: false" in result["final_response"]
    assert "controller_invoked: false" in result["final_response"]
    assert "mutation: none" in result["final_response"]


@pytest.mark.asyncio
async def test_pipeline_observe_hook_runs_before_proxy_return_without_changing_result(monkeypatch):
    events: list[tuple[str, object]] = []

    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")

    def _fake_observe(**kwargs):
        events.append(("observe", kwargs))
        return None

    async def _fake_proxy(**kwargs):
        events.append(("proxy", kwargs))
        return {
            "final_response": "proxied",
            "messages": [],
            "api_calls": 0,
        }

    monkeypatch.setattr(pipeline_observe, "observe_pipeline_router_decision", _fake_observe)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"pipelines": {"router": {"mode": "observe"}}})

    runner = _make_runner()
    runner._get_proxy_url = lambda: "http://proxy.example"
    runner._run_agent_via_proxy = _fake_proxy

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="67890",
        chat_name="Alerts",
        chat_type="group",
        user_id="user-1",
        thread_id="42",
    )

    history = [{"role": "assistant", "content": "prior"}]
    result = await runner._run_agent(
        message="ping",
        context_prompt="ctx",
        history=history,
        source=source,
        session_id="session-proxy-1",
        session_key="agent:main:telegram:group:67890:42",
    )

    assert result["final_response"] == "proxied"
    assert [event for event, _ in events] == ["observe", "proxy"]
    proxy_payload = events[1][1]
    assert events[0][1]["logger"] is gateway_run.logger
    assert proxy_payload["message"] == "ping"
    assert proxy_payload["context_prompt"] == "ctx"
    assert proxy_payload["history"] == history
    assert proxy_payload["session_id"] == "session-proxy-1"


def test_pipeline_observe_disabled_skips_hook_for_local_and_proxy_paths(monkeypatch):
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    load_calls: list[dict] = []

    monkeypatch.setattr(
        pipeline_observe,
        "load_pipeline_specs",
        lambda **kwargs: load_calls.append(kwargs),
    )
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"pipelines": {"router": {"mode": "disabled"}}})
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "***",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "credential_pool": None,
            "max_tokens": None,
        },
    )

    class _NoopAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []

        def run_conversation(self, user_message: str, conversation_history=None, task_id=None):
            return {"final_response": "ok", "messages": [], "api_calls": 1}

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _NoopAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    runner_local = _make_runner()
    source_local = SessionSource(platform=Platform.LOCAL, chat_id="cli", chat_type="dm", user_id="u1")
    local_result = asyncio.run(
        runner_local._run_agent(
            message="local ping",
            context_prompt="",
            history=[],
            source=source_local,
            session_id="session-local-disabled",
            session_key="agent:main:local:dm",
        )
    )

    runner_proxy = _make_runner()
    runner_proxy._get_proxy_url = lambda: "http://proxy.example"

    async def _fake_proxy(**kwargs):
        return {"final_response": "proxied", "messages": [], "api_calls": 0}

    runner_proxy._run_agent_via_proxy = _fake_proxy
    source_proxy = SessionSource(platform=Platform.TELEGRAM, chat_id="67890", chat_type="group", user_id="u2")
    proxy_result = asyncio.run(
        runner_proxy._run_agent(
            message="proxy ping",
            context_prompt="",
            history=[],
            source=source_proxy,
            session_id="session-proxy-disabled",
            session_key="agent:main:telegram:group:67890",
        )
    )

    assert local_result["final_response"] == "ok"
    assert proxy_result["final_response"] == "proxied"
    assert load_calls == []


def test_pipeline_orchestrator_disabled_skips_hook_for_local_and_proxy_paths(monkeypatch):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    observe_calls: list[dict] = []

    monkeypatch.setattr(
        orchestrator,
        "observe_gateway_turn",
        lambda **kwargs: observe_calls.append(kwargs),
    )
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"pipelines": {"enabled": False, "router": {"mode": "observe"}, "orchestrator": {"mode": "disabled"}}},
    )
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "***",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "credential_pool": None,
            "max_tokens": None,
        },
    )

    class _NoopAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []

        def run_conversation(self, user_message: str, conversation_history=None, task_id=None):
            return {"final_response": "ok", "messages": [], "api_calls": 1}

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _NoopAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    runner_local = _make_runner()
    source_local = SessionSource(platform=Platform.LOCAL, chat_id="cli", chat_type="dm", user_id="u1")
    local_result = asyncio.run(
        runner_local._run_agent(
            message="local ping",
            context_prompt="",
            history=[],
            source=source_local,
            session_id="session-local-orchestrator-disabled",
            session_key="agent:main:local:dm",
        )
    )

    runner_proxy = _make_runner()
    runner_proxy._get_proxy_url = lambda: "http://proxy.example"

    async def _fake_proxy(**kwargs):
        return {"final_response": "proxied", "messages": [], "api_calls": 0}

    runner_proxy._run_agent_via_proxy = _fake_proxy
    source_proxy = SessionSource(platform=Platform.TELEGRAM, chat_id="67890", chat_type="group", user_id="u2")
    proxy_result = asyncio.run(
        runner_proxy._run_agent(
            message="proxy ping",
            context_prompt="",
            history=[],
            source=source_proxy,
            session_id="session-proxy-orchestrator-disabled",
            session_key="agent:main:telegram:group:67890",
        )
    )

    assert local_result["final_response"] == "ok"
    assert proxy_result["final_response"] == "proxied"
    assert observe_calls == []


@pytest.mark.asyncio
async def test_pipeline_observe_failure_is_swallowed_for_proxy_and_local_paths(monkeypatch):
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")

    def _boom(**kwargs):
        raise RuntimeError("observe failed")

    monkeypatch.setattr(pipeline_observe, "observe_pipeline_router_decision", _boom)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"pipelines": {"router": {"mode": "observe"}}})
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "api_key": "***",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "credential_pool": None,
            "max_tokens": None,
        },
    )

    class _NoopAgent:
        def __init__(self, *args, **kwargs):
            self.tools = []

        def run_conversation(self, user_message: str, conversation_history=None, task_id=None):
            return {"final_response": "ok", "messages": [], "api_calls": 1}

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _NoopAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    runner_local = _make_runner()
    source_local = SessionSource(platform=Platform.LOCAL, chat_id="cli", chat_type="dm", user_id="u1")
    local_result = await runner_local._run_agent(
        message="local ping",
        context_prompt="",
        history=[],
        source=source_local,
        session_id="session-local-failure",
        session_key="agent:main:local:dm",
    )

    runner_proxy = _make_runner()
    runner_proxy._get_proxy_url = lambda: "http://proxy.example"

    async def _fake_proxy(**kwargs):
        return {"final_response": "proxied", "messages": [], "api_calls": 0}

    runner_proxy._run_agent_via_proxy = _fake_proxy
    source_proxy = SessionSource(platform=Platform.TELEGRAM, chat_id="67890", chat_type="group", user_id="u2")
    proxy_result = await runner_proxy._run_agent(
        message="proxy ping",
        context_prompt="",
        history=[],
        source=source_proxy,
        session_id="session-proxy-failure",
        session_key="agent:main:telegram:group:67890",
    )

    assert local_result["final_response"] == "ok"
    assert proxy_result["final_response"] == "proxied"


@pytest.mark.asyncio
async def test_background_task_prefers_session_override_over_global_runtime(monkeypatch):
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", _explode_runtime_resolution)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    _CapturingAgent.last_init = None
    runner = _make_runner()

    adapter = AsyncMock()
    adapter.send = AsyncMock()
    adapter.extract_media = MagicMock(return_value=([], "ok"))
    adapter.extract_images = MagicMock(return_value=([], "ok"))
    runner.adapters[Platform.TELEGRAM] = adapter

    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="12345",
        chat_id="67890",
        user_name="testuser",
    )
    session_key = runner._session_key_for_source(source)
    runner._session_model_overrides[session_key] = _codex_override()
    runner._session_reasoning_overrides[session_key] = {"enabled": True, "effort": "high"}

    await runner._run_background_task("say hello", source, "bg_test")

    assert _CapturingAgent.last_init is not None
    assert _CapturingAgent.last_init["model"] == "gpt-5.4"
    assert _CapturingAgent.last_init["provider"] == "openai-codex"
    assert _CapturingAgent.last_init["api_mode"] == "codex_responses"
    assert _CapturingAgent.last_init["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert _CapturingAgent.last_init["api_key"] == "***"
    assert _CapturingAgent.last_init["reasoning_config"] == {"enabled": True, "effort": "high"}

def test_gateway_auth_fallback_uses_fallback_model_from_config(tmp_path, monkeypatch):
    """Regression: fallback provider must not inherit the primary model.

    If primary openai-codex auth fails and fallback_providers selects
    OpenRouter/minimax, the gateway must instantiate AIAgent with the fallback
    model, not the primary config model (e.g. gpt-5.5). Otherwise OpenRouter
    receives an unintended GPT request.
    """
    config = tmp_path / "config.yaml"
    config.write_text(
        """
model:
  default: gpt-5.5
  provider: openai-codex
fallback_providers:
  - provider: openrouter
    model: minimax/minimax-m2.7
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    def fake_resolve_runtime_provider(*, requested=None, explicit_base_url=None, explicit_api_key=None):
        if requested in {None, "", "openai-codex"}:
            from hermes_cli.auth import AuthError
            raise AuthError("No Codex credentials stored. Run `hermes auth` to authenticate.")
        assert requested == "openrouter"
        return {
            "api_key": "sk-openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        }

    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", fake_resolve_runtime_provider)

    runner = _make_runner()
    model, runtime_kwargs = runner._resolve_session_agent_runtime(
        session_key="agent:main:telegram:group:-1003715515980:63",
        user_config={
            "model": {"default": "gpt-5.5", "provider": "openai-codex"},
            "fallback_providers": [{"provider": "openrouter", "model": "minimax/minimax-m2.7"}],
        },
    )

    assert model == "minimax/minimax-m2.7"
    assert runtime_kwargs["provider"] == "openrouter"
    assert runtime_kwargs["api_key"] == "sk-openrouter"


def test_gateway_auth_fallback_resolves_key_env_for_custom_provider(tmp_path, monkeypatch):
    """Auth-failure fallback should honor key_env/api_key_env custom-endpoint hints."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
fallback_providers:
  - provider: custom
    model: fallback-model
    base_url: https://fallback.example/v1
    key_env: MY_FALLBACK_KEY
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setenv("MY_FALLBACK_KEY", "env-secret")

    def fake_resolve_runtime_provider(*, requested=None, explicit_base_url=None, explicit_api_key=None):
        assert requested == "custom"
        assert explicit_base_url == "https://fallback.example/v1"
        assert explicit_api_key == "env-secret"
        return {
            "api_key": explicit_api_key,
            "base_url": explicit_base_url,
            "provider": "custom",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        }

    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", fake_resolve_runtime_provider)

    runtime_kwargs = gateway_run._try_resolve_fallback_provider()

    assert runtime_kwargs is not None
    assert runtime_kwargs["provider"] == "custom"
    assert runtime_kwargs["api_key"] == "env-secret"
    assert runtime_kwargs["base_url"] == "https://fallback.example/v1"
    assert runtime_kwargs["model"] == "fallback-model"
