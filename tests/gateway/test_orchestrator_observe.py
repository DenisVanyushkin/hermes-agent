from __future__ import annotations

import importlib
import logging
import json

from hermes_cli.pipeline_router import RouterDecision


def test_gateway_orchestrator_observe_logs_default_pipeline_report(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-1",
        router_subagent_id="hermes_pipeline_router",
        status="no_specialized_pipeline",
        selected_pipeline_id=None,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.82,
        reasoning_summary="default request",
        fallback_safe=True,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="hello from telegram",
            session_id="sess-1",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            chat_id="chat-1",
            user_id="user-1",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    assert report.state.pipeline_id == "default_conversation_pipeline"
    assert report.state.selected_pipeline_id is None
    assert report.state.router_status == "no_specialized_pipeline"
    assert report.state.completion_allowed is True
    assert report.execution_report.pipeline_id == "default_conversation_pipeline"
    assert report.execution_report.selected_pipeline_id is None
    assert report.execution_report.pipeline_session_id == report.session.pipeline_session_id
    assert report.session.user_message_hash != "hello from telegram"
    log_message = next(
        record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message
    )
    assert '"event": "pipeline_orchestrator_observe_report"' in log_message
    assert '"effective_pipeline_id": "default_conversation_pipeline"' in log_message


def test_gateway_orchestrator_observe_records_selected_pipeline_without_enforcement(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-2",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="Implement Slice C1",
            session_id="sess-2",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    assert report.state.pipeline_id == "engineering_review_pipeline"
    assert report.state.selected_pipeline_id == "engineering_review_pipeline"
    assert report.state.completion_allowed is True
    assert report.execution_report.pipeline_id == "engineering_review_pipeline"
    assert report.execution_report.completion_allowed is True
    assert report.execution_report.actual_provider == "unavailable"
    assert report.execution_report.actual_model == "unavailable"


def test_gateway_orchestrator_observe_engineering_pipeline_adds_plan_only_report(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-eng-plan",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.94,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="Implement F3 with prompt text and SECRET_TOKEN=abc123",
            session_id="sess-eng-plan",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            router_decision=decision,
            selected_provider="openai-codex",
            selected_model="gpt-5.4-mini",
            actual_provider="openai-codex",
            actual_model="gpt-5.4-mini",
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    log_message = next(
        record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message
    )
    payload = json.loads(log_message.split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_plan_status"] == "planned"
    assert payload["pipeline_plan_completion_reason"] == "plan_only"
    assert payload["planned_steps_count"] == 2
    assert payload["planned_subagent_ids"] == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert payload["reviewer_planned"] is True
    assert payload["reviewer_condition"] == "code_changes_require_review"
    assert payload["runtime_plan_failed"] is False
    assert payload["pipeline_plan_elapsed_ms"] >= 0
    assert payload["pipeline_plan"]["status"] == "planned"
    assert payload["pipeline_plan"]["completion_reason"] == "plan_only"
    assert [step["step_kind"] for step in payload["pipeline_plan"]["step_records"]] == ["engineer", "reviewer"]
    assert payload["pipeline_plan"]["step_records"][0]["constructor_provider"] == "openrouter"
    assert payload["pipeline_plan"]["step_records"][0]["constructor_model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["pipeline_plan"]["step_records"][1]["constructor_provider"] == "openai-codex"
    assert payload["pipeline_plan"]["step_records"][1]["constructor_model"] == "gpt-5.5"
    assert payload["pipeline_plan"]["step_records"][1]["condition"] == "code_changes_require_review"
    assert "SECRET_TOKEN=abc123" not in log_message
    assert "prompt text" not in log_message
    assert "output_text" not in log_message


def test_gateway_orchestrator_observe_non_engineering_routes_skip_pipeline_plan(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-default",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="default_conversation_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.85,
        reasoning_summary="general request",
        fallback_safe=True,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="general request",
            session_id="sess-default",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    log_message = next(
        record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message
    )
    payload = json.loads(log_message.split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_plan_status"] == "not_applicable"
    assert payload["planned_steps_count"] == 0
    assert payload["planned_subagent_ids"] == []
    assert payload["runtime_plan_failed"] is False
    assert payload["pipeline_plan"] is None


def test_gateway_orchestrator_observe_reports_plan_failure_without_raising(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    real_factory_cls = orchestrator._load_runtime_factory_class()

    class BrokenFactory(real_factory_cls):
        def build(self, request):
            raise RuntimeError("runtime planning exploded")

    monkeypatch.setattr(orchestrator, "_load_runtime_factory_class", lambda: BrokenFactory)

    decision = RouterDecision(
        pipeline_session_id="router-plan-failed",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.91,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="Implement F3",
            session_id="sess-plan-failed",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    log_message = next(
        record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message
    )
    payload = json.loads(log_message.split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_plan_status"] == "failed"
    assert payload["pipeline_plan_completion_reason"] == "planning_failed"
    assert payload["runtime_plan_failed"] is True
    assert payload["planned_steps_count"] == 0
    assert payload["pipeline_plan"] is None
    assert payload["pipeline_plan_error"]["error_type"] == "RuntimeError"
    assert "runtime planning exploded" in payload["pipeline_plan_error"]["message"]


def test_gateway_orchestrator_observe_without_router_decision_uses_safe_placeholder(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="plain request",
            session_id="sess-3",
            session_key="agent:main:local:dm",
            platform="local",
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    assert report.state.router_status == "unavailable"
    assert report.state.pipeline_id == "default_conversation_pipeline"
    assert report.state.selected_pipeline_id == "default_conversation_pipeline"
    assert report.execution_report.completion_reason == "observe_only_default_path"
    assert report.session.pipeline_session_id == report.state.pipeline_session_id
    assert report.state.pipeline_session_id == report.execution_report.pipeline_session_id


def test_gateway_orchestrator_disabled_mode_skips_logging(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": False, "orchestrator": {"mode": "disabled"}}},
            user_message="plain request",
            session_id="sess-4",
            logger=logging.getLogger("gateway.test"),
        )

    assert report is None
    assert not [record for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message]
