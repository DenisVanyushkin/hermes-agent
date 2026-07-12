"""Router infra-failure degradation tests.

When the LLM pipeline router itself dies on an infrastructure error (auth 401,
network, timeout), the autonomous fail-closed guard must NOT swallow the turn
with the canned "phrase it as an explicit engineering task" template. Instead
the gateway degrades to a no-tools conversational turn with an honest
disclosure prefix. Contract/validation router failures keep the old
fail-closed behavior.

Regression source: 2026-07-12 codex token_invalidated incident — a plain
diagnostic question got a canned refusal with api_calls=0.
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
from hermes_cli.pipeline_router import classify_router_failure
from hermes_cli.pipeline_state import ExecutionReport, OrchestratorObserveReport, PipelineState
from hermes_cli.pipeline_session import PipelineSession, PipelineSessionStatus

AUTH_401_REASON = (
    "AuthenticationError: Error code: 401 - {'error': {'message': 'Your authentication "
    "token has been invalidated. Please try signing in again.', 'type': "
    "'invalid_request_error', 'code': 'token_invalidated', 'param': None}, 'status': 401}"
)
CONTRACT_REASON = "invalid_router_contract: status must be exactly one of: selected, ..."


# ---------------------------------------------------------------------------
# classify_router_failure unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "reason",
    [
        AUTH_401_REASON,
        "Codex auxiliary Responses stream exceeded 8.0s total timeout",
        "APIConnectionError: Connection error.",
        "TimeoutError: Codex stream produced no SSE events for 12s after first byte",
        "HTTP 503: Service temporarily unavailable",
        "RateLimitError: Error code: 429 - rate limit reached",
    ],
)
def test_classify_router_failure_infra(reason):
    assert classify_router_failure(reason) == "infra"


@pytest.mark.parametrize(
    "reason",
    [
        CONTRACT_REASON,
        "invalid_confidence: confidence must be a float in [0, 1]",
        "",
        None,
    ],
)
def test_classify_router_failure_contract(reason):
    assert classify_router_failure(reason) == "contract"


# ---------------------------------------------------------------------------
# Gateway harness (mirrors tests/gateway/test_session_model_override_routing.py)
# ---------------------------------------------------------------------------

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


def _routing_failed_report(router_status="routing_failed"):
    return OrchestratorObserveReport(
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
            created_at="2026-07-12T00:00:00+00:00",
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
            router_status=router_status,
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


def _router_stub(reason):
    return types.SimpleNamespace(status="routing_failed", routing_failure_reason=reason)


def _patch_gateway(monkeypatch, *, router_reason, agent_cls):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = agent_cls
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
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: _router_stub(router_reason),
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(
        orchestrator, "observe_gateway_turn", lambda **_kwargs: _routing_failed_report()
    )


class _CapturingAgent:
    last_init = None
    run_calls = 0

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(self, user_message: str, conversation_history=None, task_id=None, **kwargs):
        type(self).run_calls += 1
        return {"final_response": "ok-from-agent", "messages": [], "api_calls": 1}


def _run(runner):
    source = SessionSource(
        platform=Platform.LOCAL, chat_id="cli", chat_name="CLI", chat_type="dm", user_id="user-1"
    )
    return asyncio.run(
        runner._run_agent(
            message="В чем здесь была проблема?",
            context_prompt="",
            history=[],
            source=source,
            session_id="session-auto-failed",
            session_key="agent:main:local:dm",
        )
    )


# ---------------------------------------------------------------------------
# Integration: infra failure degrades, contract failure stays fail-closed
# ---------------------------------------------------------------------------

def test_infra_router_failure_degrades_to_no_tools_conversation(monkeypatch):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    _patch_gateway(monkeypatch, router_reason=AUTH_401_REASON, agent_cls=_CapturingAgent)

    runner = _make_runner()
    result = _run(runner)

    assert _CapturingAgent.run_calls == 1
    assert "ok-from-agent" in result["final_response"]
    assert "I could not reliably select" not in result["final_response"]
    # Honest disclosure prefix about the degraded turn
    assert "Pipeline router" in result["final_response"]
    assert result["final_response"].index("Pipeline router") < result["final_response"].index("ok-from-agent")
    # Tools must be hard-disabled for the degraded turn
    assert _CapturingAgent.last_init["enabled_toolsets"] == []


def test_contract_router_failure_keeps_fail_closed_template(monkeypatch):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)

    runner = _make_runner()
    result = _run(runner)

    assert _CapturingAgent.run_calls == 0
    assert result["api_calls"] == 0
    assert "final_verdict: safe_default_fallback_used" in result["final_response"]


def test_missing_failure_reason_keeps_fail_closed_template(monkeypatch):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    _patch_gateway(monkeypatch, router_reason=None, agent_cls=_CapturingAgent)

    runner = _make_runner()
    result = _run(runner)

    assert _CapturingAgent.run_calls == 0
    assert "final_verdict: safe_default_fallback_used" in result["final_response"]


# ---------------------------------------------------------------------------
# Unit: notice helper
# ---------------------------------------------------------------------------

def test_notice_none_when_router_selected():
    runner = _make_runner()
    notice = runner._pipeline_router_infra_degraded_notice(
        _routing_failed_report(router_status="selected"),
        orchestrator_mode="autonomous",
        router_decision=_router_stub(AUTH_401_REASON),
    )
    assert notice is None


def test_notice_present_for_infra_failure():
    runner = _make_runner()
    notice = runner._pipeline_router_infra_degraded_notice(
        _routing_failed_report(),
        orchestrator_mode="autonomous",
        router_decision=_router_stub(AUTH_401_REASON),
    )
    assert notice is not None
    assert "Pipeline router" in notice
    assert "401" in notice
