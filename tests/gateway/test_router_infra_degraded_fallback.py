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
from hermes_cli.pipeline_router import RouterDecision
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


def _selected_engineering_blocked_report(
    blocked_reason="engineering_task_external_context_required",
):
    return OrchestratorObserveReport(
        session=PipelineSession(
            pipeline_session_id="pipe-context-acquisition",
            trace_id="pipe-context-acquisition",
            pipeline_id="engineering_review_pipeline",
            router_status="selected",
            router_confidence=0.92,
            platform="local",
            session_key="agent:main:local:dm",
            session_id="session-context-acquisition",
            chat_id="cli",
            thread_id=None,
            user_id="user-1",
            created_at="2026-08-14T00:00:00+00:00",
            user_message_hash="hash",
            mode="autonomous",
            current_state="blocked",
            status=PipelineSessionStatus.CREATED,
            planned_steps=[],
            selected_subagent_ids=["hermes_engineer_core"],
            reviewer_condition=None,
        ),
        state=PipelineState(
            pipeline_session_id="pipe-context-acquisition",
            pipeline_id="engineering_review_pipeline",
            state="blocked",
            mode="autonomous",
            router_status="selected",
            selected_pipeline_id="engineering_review_pipeline",
            fallback_pipeline_id="default_conversation_pipeline",
            completion_allowed=False,
            completion_blocked_reason=blocked_reason,
            final_verdict="autonomous_preflight_blocked",
        ),
        execution_report=ExecutionReport(
            pipeline_session_id="pipe-context-acquisition",
            pipeline_id="engineering_review_pipeline",
            router_status="selected",
            selected_pipeline_id="engineering_review_pipeline",
            fallback_pipeline_id="default_conversation_pipeline",
            completion_allowed=False,
            completion_reason=blocked_reason,
            executed=False,
            would_execute=False,
            execution_mode="autonomous",
            runtime_status="not_executed",
        ),
        pipeline_execution_controller=type(
            "ControllerStub",
            (),
            {
                "actual_execution_invoked": False,
                "subagent_execution_invoked": False,
                "real_provider_bridge_invoked": False,
                "blocked_reason": blocked_reason,
            },
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
    last_user_message = None
    run_calls = 0

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(self, user_message: str, conversation_history=None, task_id=None, **kwargs):
        type(self).run_calls += 1
        type(self).last_user_message = user_message
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


def test_internal_delegation_completion_bypasses_specialized_pipeline(monkeypatch):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)

    def _unexpected_pipeline_call(**_kwargs):
        raise AssertionError("internal completion entered the operator pipeline")

    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        _unexpected_pipeline_call,
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(orchestrator, "observe_gateway_turn", _unexpected_pipeline_call)

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
            message=(
                "[ASYNC DELEGATION BATCH COMPLETE — deleg_test]\n"
                "Changed /workspace/feature/example.py and committed the fix."
            ),
            context_prompt="",
            history=[],
            source=source,
            session_id="session-internal-completion",
            session_key="agent:main:local:dm",
            internal_event=True,
        )
    )

    assert _CapturingAgent.run_calls == 1
    assert result["final_response"] == "ok-from-agent"


def test_unresolved_cross_thread_engineering_task_fails_closed(
    monkeypatch,
):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    instruction = (
        "в дискуссии "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786449672479599 есть план реализации сервиса получения информации "
        "для генерации идей. Найди его и пусть инженер реализует этот план"
    )
    decision = RouterDecision(
        pipeline_session_id="pipe-context-acquisition",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id=None,
        confidence=0.92,
        reasoning_summary="External plan reference requires engineering execution.",
        fallback_safe=False,
    )
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: decision,
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(
        orchestrator,
        "observe_gateway_turn",
        lambda **_kwargs: _selected_engineering_blocked_report(),
    )

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
            message=instruction,
            raw_message=instruction,
            context_prompt="",
            history=[],
            source=source,
            session_id="session-context-acquisition",
            session_key="agent:main:local:dm",
        )
    )

    assert _CapturingAgent.run_calls == 0
    assert result["api_calls"] == 0
    assert result["tools"] == []
    assert "unrestricted agent" in result["final_response"].lower()


def test_not_engineering_task_uses_no_tools_llm_resolution(monkeypatch):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    instruction = "пусть специалист как-нибудь займётся этим"
    decision = RouterDecision(
        pipeline_session_id="pipe-not-engineering-resolution",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id=None,
        confidence=0.91,
        reasoning_summary="Ambiguous execution request.",
        fallback_safe=False,
    )
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: decision,
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(
        orchestrator,
        "observe_gateway_turn",
        lambda **_kwargs: _selected_engineering_blocked_report(
            "engineering_task_not_engineering_task"
        ),
    )

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
            message=instruction,
            raw_message=instruction,
            context_prompt="",
            history=[],
            source=source,
            session_id="session-not-engineering-resolution",
            session_key="agent:main:local:dm",
        )
    )

    assert result["final_response"] == "ok-from-agent"
    assert _CapturingAgent.run_calls == 1
    assert _CapturingAgent.last_init["enabled_toolsets"] == []
    prompt = _CapturingAgent.last_init["ephemeral_system_prompt"]
    assert "without tools" in prompt
    assert "delegate_task" not in prompt


def test_cross_thread_resolution_prefetches_authenticated_slack_context(monkeypatch):
    class _SlackAdapterStub:
        def __init__(self):
            self.calls = []

        def resolve_workspace_team_id(self, workspace_domain):
            assert workspace_domain == "vanyushkinhomelab"
            return "T0B32RP330D"

        async def _fetch_thread_context(self, **kwargs):
            self.calls.append(kwargs)
            return "[Thread context]\nFETCHED PLAN SENTINEL\n[End of thread context]"

    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    observed_contexts = []
    instruction = (
        "реализуй план из "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786449672479599"
    )
    decision = RouterDecision(
        pipeline_session_id="pipe-context-prefetch",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id=None,
        confidence=0.95,
        reasoning_summary="Implement the referenced plan.",
        fallback_safe=False,
    )
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: decision,
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(
        orchestrator,
        "observe_gateway_turn",
        lambda **kwargs: (
            observed_contexts.append(kwargs["engineering_task_context"])
            or _selected_engineering_blocked_report()
        ),
    )

    runner = _make_runner()
    adapter = _SlackAdapterStub()
    runner.adapters[Platform.SLACK] = adapter
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="cli",
        chat_name="CLI",
        chat_type="dm",
        user_id="user-1",
    )
    result = asyncio.run(
        runner._run_agent(
            message=instruction,
            raw_message=instruction,
            context_prompt="",
            history=[],
            source=source,
            session_id="session-context-prefetch",
            session_key="agent:main:local:dm",
        )
    )

    assert result["api_calls"] == 0
    assert adapter.calls == [
        {
            "channel_id": "C0B55FPG5B7",
            "thread_ts": "1786449672.479599",
            "current_ts": "",
            "team_id": "T0B32RP330D",
            "limit": 500,
            "force_refresh": True,
        }
    ]
    assert _CapturingAgent.run_calls == 0
    assert len(observed_contexts) == 1
    task_context = observed_contexts[0]
    assert task_context["resolution_status"] == "resolved"
    assert task_context["source_kind"] == "external_reference"
    assert "FETCHED PLAN SENTINEL" in task_context["task_text"]


def test_cross_thread_resolution_fails_closed_in_proxy_mode(monkeypatch):
    instruction = (
        "реализуй план из "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786449672479599"
    )
    decision = RouterDecision(
        pipeline_session_id="pipe-context-proxy",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id=None,
        confidence=0.95,
        reasoning_summary="Implement the referenced plan.",
        fallback_safe=False,
    )
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: decision,
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(
        orchestrator,
        "observe_gateway_turn",
        lambda **_kwargs: _selected_engineering_blocked_report(),
    )

    runner = _make_runner()
    runner._get_proxy_url = lambda: "http://proxy.example"
    runner._run_agent_via_proxy = AsyncMock(
        side_effect=AssertionError("unsafe proxy dispatch")
    )
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="cli",
        chat_name="CLI",
        chat_type="dm",
        user_id="user-1",
    )
    result = asyncio.run(
        runner._run_agent(
            message=instruction,
            raw_message=instruction,
            context_prompt="",
            history=[],
            source=source,
            session_id="session-context-proxy",
            session_key="agent:main:local:dm",
        )
    )

    runner._run_agent_via_proxy.assert_not_awaited()
    assert result["api_calls"] == 0
    assert result["tools"] == []
    assert "unrestricted agent" in result["final_response"].lower()


@pytest.mark.parametrize("proxy_url", [None, "http://proxy.example"])
def test_resolved_external_context_cannot_fall_through_to_generic_agent(
    monkeypatch, proxy_url
):
    _CapturingAgent.last_init = None
    _CapturingAgent.run_calls = 0
    class _SlackAdapterStub:
        def resolve_workspace_team_id(self, workspace_domain):
            assert workspace_domain == "vanyushkinhomelab"
            return "T0B32RP330D"

        async def _fetch_thread_context(self, **_kwargs):
            return "[Thread context]\nFETCHED PLAN SENTINEL"

    instruction = (
        "реализуй план из "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786449672479599"
    )
    decision = RouterDecision(
        pipeline_session_id="pipe-context-proxy-resolved",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id=None,
        confidence=0.95,
        reasoning_summary="Implement the referenced plan.",
        fallback_safe=False,
    )
    _patch_gateway(monkeypatch, router_reason=CONTRACT_REASON, agent_cls=_CapturingAgent)
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    monkeypatch.setattr(
        pipeline_observe,
        "observe_pipeline_router_decision",
        lambda **_kwargs: decision,
    )
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(orchestrator, "observe_gateway_turn", lambda **_kwargs: None)

    runner = _make_runner()
    runner.adapters[Platform.SLACK] = _SlackAdapterStub()
    runner._get_proxy_url = lambda: proxy_url
    runner._run_agent_via_proxy = AsyncMock(
        side_effect=AssertionError("unsafe proxy dispatch")
    )
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="cli",
        chat_name="CLI",
        chat_type="dm",
        user_id="user-1",
    )
    result = asyncio.run(
        runner._run_agent(
            message=instruction,
            raw_message=instruction,
            context_prompt="",
            history=[],
            source=source,
            session_id="session-context-proxy-resolved",
            session_key="agent:main:local:dm",
        )
    )

    runner._run_agent_via_proxy.assert_not_awaited()
    assert _CapturingAgent.run_calls == 0
    assert result["api_calls"] == 0
    assert result["tools"] == []
    assert "controlled" in result["final_response"].lower()


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
