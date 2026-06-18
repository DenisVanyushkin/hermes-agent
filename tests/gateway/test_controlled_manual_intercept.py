from __future__ import annotations

import importlib
import logging
import sys
import threading
import types
from types import SimpleNamespace

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource


class _CapturingAgent:
    run_calls = []

    def __init__(self, *args, **kwargs):
        self.tools = []
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0, context_length=0)
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.model = "fake-model"

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        type(self).run_calls.append(
            {
                "user_message": user_message,
                "conversation_history": conversation_history,
                "task_id": task_id,
                "persist_user_message": persist_user_message,
            }
        )
        return {
            "final_response": "normal agent reply",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._pending_skills_reload_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner._session_run_generation = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(
        streaming=None,
        thread_sessions_per_user=False,
        group_sessions_per_user=False,
        stt_enabled=False,
    )
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda source: SimpleNamespace(session_id="session-1"),
        load_transcript=lambda session_id: [],
        _entries={},
        _save=lambda: None,
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._is_session_run_current = lambda session_key, run_generation: True
    runner._consume_pending_native_image_paths = lambda session_key: []
    return runner


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )


def _controlled_report(
    *,
    actual_execution_invoked: bool,
    final_response_text: str | None,
    execution_mode: str = "controlled_manual",
    completion_allowed: bool = True,
    blocked_reason: str | None = None,
):
    return SimpleNamespace(
        execution_report=SimpleNamespace(executed=actual_execution_invoked, execution_mode=execution_mode),
        pipeline_execution_controller=SimpleNamespace(
            actual_execution_invoked=actual_execution_invoked,
            execution_mode=execution_mode,
            final_response_text=final_response_text,
            completion_allowed=completion_allowed,
            blocked_reason=blocked_reason,
        ),
    )


async def _run_once(monkeypatch, tmp_path, *, config, report=None, observe_exc=None):
    _install_fake_agent(monkeypatch)
    _CapturingAgent.run_calls = []
    runner = _make_runner()

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: config)
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: config)
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "***",
        },
    )

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"})

    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    observe_calls = []

    def _fake_observe_gateway_turn(**kwargs):
        observe_calls.append(kwargs)
        if observe_exc is not None:
            raise observe_exc
        return report

    monkeypatch.setattr(orchestrator, "observe_gateway_turn", _fake_observe_gateway_turn)

    result = await runner._run_agent(
        message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run",
        context_prompt="",
        history=[],
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:telegram:dm:12345",
    )
    return result, observe_calls


@pytest.mark.asyncio
async def test_controlled_manual_intercepts_safe_final_response(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(actual_execution_invoked=True, final_response_text="safe controlled reply"),
    )

    assert len(observe_calls) == 1
    assert result["final_response"] == "safe controlled reply"
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_controlled_manual_blocked_execution_with_safe_final_response_intercepts(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(
            actual_execution_invoked=True,
            final_response_text="blocked but safe report",
            completion_allowed=False,
            blocked_reason="test_command_failed",
        ),
    )

    assert len(observe_calls) == 1
    assert result["final_response"] == "blocked but safe report"
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_controlled_manual_without_actual_invocation_falls_back(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(actual_execution_invoked=False, final_response_text="safe controlled reply"),
    )

    assert len(observe_calls) == 1
    assert result["final_response"] == "normal agent reply"
    assert len(_CapturingAgent.run_calls) == 1


@pytest.mark.asyncio
async def test_observe_mode_never_intercepts(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "observe"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(
            actual_execution_invoked=True,
            final_response_text="safe controlled reply",
            execution_mode="observe",
        ),
    )

    assert len(observe_calls) == 1
    assert result["final_response"] == "normal agent reply"
    assert len(_CapturingAgent.run_calls) == 1


@pytest.mark.asyncio
async def test_disabled_mode_skips_observe_hook(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": False,
            "orchestrator": {"mode": "disabled"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    result, observe_calls = await _run_once(monkeypatch, tmp_path, config=config, report=None)

    assert observe_calls == []
    assert result["final_response"] == "normal agent reply"
    assert len(_CapturingAgent.run_calls) == 1


@pytest.mark.asyncio
async def test_observe_exception_logs_and_falls_back(monkeypatch, tmp_path, caplog):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    with caplog.at_level(logging.WARNING, logger="gateway.run"):
        result, observe_calls = await _run_once(
            monkeypatch,
            tmp_path,
            config=config,
            observe_exc=RuntimeError("boom"),
        )

    assert len(observe_calls) == 1
    assert result["final_response"] == "normal agent reply"
    assert len(_CapturingAgent.run_calls) == 1
    assert any("pipeline orchestrator observe hook import/invocation failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_controlled_manual_without_final_response_text_falls_back(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(actual_execution_invoked=True, final_response_text=None),
    )

    assert len(observe_calls) == 1
    assert result["final_response"] == "normal agent reply"
    assert len(_CapturingAgent.run_calls) == 1
