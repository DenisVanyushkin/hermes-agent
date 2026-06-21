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
from hermes_cli.pipeline_gate import PipelineGateDecision, PipelineGateMode
from hermes_cli.pipeline_controlled_dry_run import format_controlled_manual_summary
from hermes_cli.pipeline_router import RouterDecision


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
    pipeline_id: str = "engineering_review_pipeline",
    completion_allowed: bool = True,
    blocked_reason: str | None = None,
    report_artifacts: dict[str, object] | None = None,
):
    return SimpleNamespace(
        state=SimpleNamespace(
            pipeline_id=pipeline_id,
            selected_pipeline_id=pipeline_id if pipeline_id == "engineering_review_pipeline" else None,
            router_status="selected" if pipeline_id != "default_conversation_pipeline" else "no_specialized_pipeline",
        ),
        execution_report=SimpleNamespace(executed=actual_execution_invoked, execution_mode=execution_mode),
        pipeline_execution_controller=SimpleNamespace(
            actual_execution_invoked=actual_execution_invoked,
            execution_mode=execution_mode,
            final_response_text=final_response_text,
            completion_allowed=completion_allowed,
            blocked_reason=blocked_reason,
            report_artifacts=report_artifacts,
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
    assert result["model"] == "gpt-5.4"
    assert result["requested_model"] == "gpt-5.4"
    assert result["last_prompt_tokens"] == 0
    assert result["context_length"] == 0
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_controlled_manual_intercepts_safe_final_response_with_report_reference(monkeypatch, tmp_path):
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
            final_response_text="safe controlled reply",
            report_artifacts={
                "run_id": "pipe-report-1",
                "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-1/controlled_execution_report.json",
                "durable_report_path": "/home/hermes/.hermes/controlled-runs/pipe-report-1/controlled_execution_report.json",
            },
        ),
    )

    assert len(observe_calls) == 1
    assert "safe controlled reply" in result["final_response"]
    assert "report_run_id: pipe-report-1" in result["final_response"]
    assert "controlled_execution_report.json" in result["final_response"]
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_controlled_manual_rewrites_blank_report_path_to_durable_path(monkeypatch, tmp_path):
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
            final_response_text=(
                "status: completion_allowed\n"
                "report_run_id: pipe-report-4\n"
                "report_path:\n"
                "workspace: /tmp/hermes-gateway-controlled-runs/pipe-report-4"
            ),
            report_artifacts={
                "run_id": "pipe-report-4",
                "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-4/controlled_execution_report.json",
                "durable_report_path": "/home/hermes/.hermes/controlled-runs/pipe-report-4/controlled_execution_report.json",
            },
        ),
    )

    assert len(observe_calls) == 1
    assert "report_path: /home/hermes/.hermes/controlled-runs/pipe-report-4/controlled_execution_report.json" in result["final_response"]
    assert "report_path:\n" not in result["final_response"]
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_controlled_manual_rewrites_placeholder_report_path_values_when_real_artifact_exists(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    for placeholder in ("~", "unavailable"):
        result, observe_calls = await _run_once(
            monkeypatch,
            tmp_path,
            config=config,
            report=_controlled_report(
                actual_execution_invoked=True,
                final_response_text=(
                    "status: completion_allowed\n"
                    "report_run_id: pipe-report-5\n"
                    f"report_path: {placeholder}\n"
                    "workspace: /tmp/hermes-gateway-controlled-runs/pipe-report-5"
                ),
                report_artifacts={
                    "run_id": "pipe-report-5",
                    "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-5/controlled_execution_report.json",
                    "durable_report_path": "/home/hermes/.hermes/controlled-runs/pipe-report-5/controlled_execution_report.json",
                },
            ),
        )

        assert len(observe_calls) == 1
        assert "report_path: /home/hermes/.hermes/controlled-runs/pipe-report-5/controlled_execution_report.json" in result["final_response"]
        assert f"report_path: {placeholder}" not in result["final_response"]
        assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_controlled_manual_preserves_single_correct_report_path_without_duplication(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "controlled_manual"},
            "execution": {"mode": "controlled_manual"},
        }
    }

    durable_report_path = "/home/hermes/.hermes/controlled-runs/pipe-report-6/controlled_execution_report.json"
    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(
            actual_execution_invoked=True,
            final_response_text=(
                "status: completion_allowed\n"
                "report_run_id: pipe-report-6\n"
                f"report_path: {durable_report_path}\n"
                "workspace: /tmp/hermes-gateway-controlled-runs/pipe-report-6"
            ),
            report_artifacts={
                "run_id": "pipe-report-6",
                "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-6/controlled_execution_report.json",
                "durable_report_path": durable_report_path,
            },
        ),
    )

    assert len(observe_calls) == 1
    assert result["final_response"].count(f"report_path: {durable_report_path}") == 1
    assert _CapturingAgent.run_calls == []


def test_controlled_manual_summary_uses_report_as_source_of_truth_for_not_executed_case() -> None:
    summary = format_controlled_manual_summary(
        {
            "status": "completed",
            "completion_allowed": True,
            "mutation_summary": {"applied_count": 1, "denied_count": 0},
            "test_summary": {"status": "passed", "results": [{"status": "passed"}]},
            "report_artifacts": {
                "run_id": "357f97b7884143c488d05f4f1f0d40ae",
                "durable_report_path": "/home/hermes/.hermes/controlled-runs/357f97b7884143c488d05f4f1f0d40ae/controlled_execution_report.json",
                "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/357f97b7884143c488d05f4f1f0d40ae/controlled_execution_report.json",
            },
            "report": {
                "status": "not_executed",
                "routing": {
                    "selected_pipeline_id": "engineering_review_pipeline",
                    "router_status": "selected",
                },
                "controller": {
                    "executed": False,
                    "execution_mode": "observe_plan_only",
                },
                "completion": {
                    "final_verdict": "observe_engineering_preflight_blocked",
                    "blocked_reason": "execution_disabled",
                },
                "tests": {"status": "unavailable", "summary": None},
                "usage_summary": {"providers_used": [], "models_used": []},
                "models": [
                    {
                        "role_id": "engineer",
                        "provider": "openrouter",
                        "model": "xiaomi/mimo-v2.5-pro",
                        "runtime_status": "plan_only",
                    },
                    {
                        "role_id": "reviewer",
                        "provider": "openai-codex",
                        "model": "gpt-5.5",
                        "runtime_status": "plan_only",
                    },
                ],
                "changed_files": [],
            },
        }
    )

    assert summary is not None
    assert "status: not_executed" in summary
    assert "execution_mode: observe_plan_only" in summary
    assert "final_verdict: observe_engineering_preflight_blocked" in summary
    assert "blocked_reason: execution_disabled" in summary
    assert "report_execution_invoked: False" in summary
    assert "mutation: none" in summary
    assert "tests: not_run" in summary
    assert "models_used: none" in summary
    assert "planned_model: engineer: openrouter / xiaomi/mimo-v2.5-pro, plan_only" in summary
    assert "report_run_id: 357f97b7884143c488d05f4f1f0d40ae" in summary
    assert "report_path: /home/hermes/.hermes/controlled-runs/357f97b7884143c488d05f4f1f0d40ae/controlled_execution_report.json" in summary
    assert "workspace: /tmp/hermes-gateway-controlled-runs/357f97b7884143c488d05f4f1f0d40ae" in summary
    assert "status: completed" not in summary
    assert "applied_count=1" not in summary
    assert "tests: passed" not in summary
    assert "~" not in summary


def test_controlled_manual_summary_uses_report_values_for_executed_case() -> None:
    summary = format_controlled_manual_summary(
        {
            "status": "completed",
            "report_artifacts": {
                "run_id": "pipe-report-1",
                "durable_report_path": "/home/hermes/.hermes/controlled-runs/pipe-report-1/controlled_execution_report.json",
                "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-1/controlled_execution_report.json",
            },
            "report": {
                "status": "completed",
                "routing": {"selected_pipeline_id": "engineering_review_pipeline"},
                "controller": {"executed": True, "execution_mode": "controlled_manual"},
                "completion": {"final_verdict": "completed", "blocked_reason": None},
                "tests": {"status": "passed", "summary": "focused"},
                "usage_summary": {
                    "providers_used": ["openrouter", "openai-codex"],
                    "models_used": ["xiaomi/mimo-v2.5-pro", "gpt-5.5"],
                },
                "review": {"reviewer_invoked": True},
                "changed_files": ["tests/test_generated_example.py"],
            },
        }
    )

    assert summary is not None
    assert "status: completed" in summary
    assert "report_execution_invoked: True" in summary
    assert "mutation: changed_files=1" in summary
    assert "tests: passed (focused)" in summary
    assert "models_used: openrouter / xiaomi/mimo-v2.5-pro, openai-codex / gpt-5.5" in summary
    assert "reviewer_invoked: True" in summary


def test_controlled_manual_summary_drops_placeholder_values_without_dropping_tilde_text() -> None:
    summary = format_controlled_manual_summary(
        {
            "status": "blocked",
            "report_artifacts": {
                "run_id": "~",
                "durable_report_path": "~",
                "workspace_report_path": "~",
            },
            "report": {
                "status": "not_executed",
                "routing": {"selected_pipeline_id": "engineering_review_pipeline"},
                "controller": {"executed": False, "execution_mode": "observe_plan_only"},
                "completion": {
                    "final_verdict": "observe_engineering_preflight_blocked",
                    "blocked_reason": "awaiting_user_input ~ keep note",
                },
                "tests": {"status": "unavailable"},
                "usage_summary": {"providers_used": [], "models_used": []},
            },
        }
    )

    assert summary is not None
    assert "blocked_reason: awaiting_user_input ~ keep note" in summary
    assert "report_run_id:" not in summary
    assert "report_path: unavailable" in summary
    assert "workspace:" not in summary


def test_controlled_manual_summary_uses_workspace_report_path_when_durable_missing() -> None:
    summary = format_controlled_manual_summary(
        {
            "status": "completed",
            "report_artifacts": {
                "run_id": "pipe-report-2",
                "durable_report_path": "",
                "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-2/controlled_execution_report.json",
            },
            "report": {
                "status": "completed",
                "routing": {"selected_pipeline_id": "engineering_review_pipeline"},
                "controller": {"executed": True, "execution_mode": "controlled_manual"},
                "completion": {"final_verdict": "completed", "blocked_reason": None},
                "tests": {"status": "passed", "summary": "focused"},
                "usage_summary": {"providers_used": [], "models_used": []},
                "review": {"reviewer_invoked": True},
                "changed_files": ["tests/test_generated_example.py"],
            },
        }
    )

    assert summary is not None
    assert "report_path: /tmp/hermes-gateway-controlled-runs/pipe-report-2/controlled_execution_report.json" in summary


def test_controlled_manual_summary_renders_unavailable_when_artifact_paths_missing() -> None:
    summary = format_controlled_manual_summary(
        {
            "status": "completed",
            "report_artifacts": {
                "run_id": "pipe-report-3",
                "durable_report_path": "",
                "workspace_report_path": "",
            },
            "report": {
                "status": "completed",
                "routing": {"selected_pipeline_id": "engineering_review_pipeline"},
                "controller": {"executed": True, "execution_mode": "controlled_manual"},
                "completion": {"final_verdict": "completed", "blocked_reason": None},
                "tests": {"status": "passed", "summary": "focused"},
                "usage_summary": {"providers_used": [], "models_used": []},
                "review": {"reviewer_invoked": True},
                "changed_files": ["tests/test_generated_example.py"],
            },
        }
    )

    assert summary is not None
    assert "report_path: unavailable" in summary


def test_controlled_manual_summary_renders_unknown_for_provider_model_length_mismatch() -> None:
    summary = format_controlled_manual_summary(
        {
            "status": "completed",
            "report": {
                "status": "completed",
                "changed_files": ["a.py"],
                "routing": {"selected_pipeline_id": "engineering_review_pipeline"},
                "controller": {"executed": True, "execution_mode": "controlled_runtime_loop"},
                "completion": {"final_verdict": "executed", "blocked_reason": None},
                "tests": {"status": "passed", "summary": "ok"},
                "usage_summary": {
                    "providers_used": ["openrouter"],
                    "models_used": ["xiaomi/mimo-v2.5-pro", "gpt-5.5"],
                },
                "review": {"reviewer_invoked": False},
            },
        }
    )

    assert summary is not None
    assert "models_used: openrouter / xiaomi/mimo-v2.5-pro, unknown / gpt-5.5" in summary


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
async def test_controlled_manual_without_actual_invocation_returns_static_block_without_agent(monkeypatch, tmp_path):
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
            actual_execution_invoked=False,
            final_response_text="safe controlled reply",
            blocked_reason="controlled_manual_trigger_missing",
        ),
    )

    assert len(observe_calls) == 1
    assert result["final_response"].startswith("Controlled pipeline required.")
    assert "pipeline: engineering_review_pipeline" in result["final_response"]
    assert "blocked_reason: controlled_manual_trigger_missing" in result["final_response"]
    assert "execution_invoked: false" in result["final_response"]
    assert len(_CapturingAgent.run_calls) == 0


@pytest.mark.asyncio
async def test_autonomous_production_context_blocks_without_real_provider_enablement(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "autonomous"},
            "execution": {
                "mode": "autonomous",
                "enable_gateway_execution_controller": True,
                "allow_actual_subagent_invocation": True,
                "allow_actual_reviewer_invocation": True,
                "allow_actual_rework_loop": True,
                "allow_pipelines": ["engineering_review_pipeline"],
                "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
            },
        }
    }
    captured_contexts = []
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    original_evaluate = orchestrator.evaluate_pipeline_execution_controller

    def _capturing_evaluate(**kwargs):
        captured_contexts.append(kwargs.get("helper_execution_context"))
        return original_evaluate(**kwargs)

    monkeypatch.setattr(orchestrator, "evaluate_pipeline_execution_controller", _capturing_evaluate)
    monkeypatch.setattr(
        orchestrator,
        "_evaluate_pipeline_gate_safely",
        lambda **_kwargs: PipelineGateDecision(
            allowed=True,
            mode=PipelineGateMode.AUTONOMOUS,
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-autonomous-test",
            selected_pipeline_id="engineering_review_pipeline",
            planned_steps_count=2,
            reason_code="allowed",
            reason="forced test preflight",
            would_execute=True,
            executed=False,
            requirements_met=["test_override"],
            requirements_failed=[],
            risk_level="medium",
            safe_to_log_payload={"mode": "autonomous"},
        ),
    )

    report = orchestrator.observe_gateway_turn(
        config=config,
        user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run",
        session_id="session-1",
        session_key="agent:main:telegram:dm:12345",
        platform="telegram",
        chat_id="12345",
        user_id="user-1",
        router_decision=RouterDecision(
            pipeline_session_id="pipe-autonomous-test",
            router_subagent_id="hermes_pipeline_router",
            status="selected",
            selected_pipeline_id="engineering_review_pipeline",
            fallback_pipeline_id="default_conversation_pipeline",
            confidence=0.99,
            reasoning_summary="autonomous test",
            fallback_safe=False,
        ),
    )

    assert report is not None
    assert len(captured_contexts) == 1
    context = captured_contexts[0]
    assert isinstance(context, dict)
    controlled_context = context["controlled_runtime_context"]
    assert controlled_context["real_executor_ready"] is False
    assert controlled_context["blocked_reason"] == "real_subagent_executor_missing"
    assert "real_provider_client_factory" not in controlled_context
    assert "invocation_client" not in controlled_context
    assert controlled_context["allow_mutations"] is False
    assert controlled_context["allow_test_commands"] is False
    controller_result = report.pipeline_execution_controller
    assert controller_result.status == "blocked"
    assert controller_result.blocked_reason == "real_subagent_executor_missing"
    assert controller_result.helper_result is not None
    assert controller_result.helper_result["completion_allowed"] is False
    assert controller_result.helper_result["report"]["review"]["reviewer_invoked"] is False
    assert "completion_allowed" not in (controller_result.final_response_text or "")
    assert "runtime_mode=real_provider" not in (controller_result.final_response_text or "")
    assert "reviewer_invoked=True" not in (controller_result.final_response_text or "")
    assert not (tmp_path / "tests" / "test_generated_example.py").exists()
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_engineering_observe_with_disabled_execution_returns_plan_only_without_agent(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "observe"},
            "execution": {"mode": "disabled"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(
            actual_execution_invoked=False,
            final_response_text=None,
            execution_mode="disabled",
            pipeline_id="engineering_review_pipeline",
        ),
    )

    assert len(observe_calls) == 1
    assert "Engineering pipeline was selected" in result["final_response"]
    assert "I did not run terminal commands" in result["final_response"]
    assert result["api_calls"] == 0
    assert result["tools"] == []
    assert _CapturingAgent.run_calls == []


@pytest.mark.asyncio
async def test_default_observe_with_disabled_execution_still_uses_normal_agent(monkeypatch, tmp_path):
    config = {
        "pipelines": {
            "enabled": True,
            "orchestrator": {"mode": "observe"},
            "execution": {"mode": "disabled"},
        }
    }

    result, observe_calls = await _run_once(
        monkeypatch,
        tmp_path,
        config=config,
        report=_controlled_report(
            actual_execution_invoked=False,
            final_response_text=None,
            execution_mode="disabled",
            pipeline_id="default_conversation_pipeline",
        ),
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
