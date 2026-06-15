from __future__ import annotations

import importlib
import logging

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
