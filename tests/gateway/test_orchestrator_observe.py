from __future__ import annotations

import importlib
import json
import logging
from dataclasses import asdict

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
    assert report.execution_report.pipeline_session_id == report.session.pipeline_session_id
    log_message = next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message)
    assert '"effective_pipeline_id": "default_conversation_pipeline"' in log_message


def test_gateway_orchestrator_observe_emits_one_canonical_preflight_payload(caplog):
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
    assert report.state.completion_allowed is True
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert "pipeline_preflight" in payload
    assert "pipeline_gate" not in payload
    assert "pipeline_handoff" not in payload
    assert "pipeline_activation" not in payload
    assert "pipeline_readiness" not in payload
    assert "handoff_status" not in json.dumps(payload, sort_keys=True)
    assert payload["pipeline_preflight"]["allowed"] is False
    assert payload["pipeline_preflight"]["blocked"] is True
    assert payload["pipeline_preflight"]["executed"] is False
    assert payload["pipeline_preflight"]["would_execute"] is False
    assert payload["pipeline_preflight"]["selected_pipeline_id"] == "engineering_review_pipeline"


def test_gateway_orchestrator_observe_contains_preflight_exception(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-gate-fail",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )

    def _boom(_request):
        raise RuntimeError("gate exploded with config details")

    monkeypatch.setattr(orchestrator, "evaluate_pipeline_gate", _boom)

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="Implement Slice G1 with SECRET_TOKEN=abc123",
            session_id="sess-gate-fail",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_preflight"]["allowed"] is False
    assert payload["pipeline_preflight"]["reason_code"] in {"unknown", "missing_required_config"}
    assert "SECRET_TOKEN=abc123" not in json.dumps(payload, sort_keys=True)


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
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload_text = next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1]
    payload = json.loads(payload_text)
    assert payload["pipeline_plan_status"] == "planned"
    assert payload["pipeline_plan_mode"] == "observe_plan_only"
    assert payload["planned_steps_count"] == 2
    assert payload["engineer_step_present"] is True
    assert payload["reviewer_step_present"] is True
    assert payload["pipeline_preflight"]["planned_steps_count"] == 2
    assert payload["pipeline_preflight"]["executed"] is False
    assert payload["pipeline_plan"]["step_records"][0]["step_kind"] == "engineer"
    assert payload["pipeline_plan"]["step_records"][1]["step_kind"] == "reviewer"
    assert "actual_provider" not in payload_text
    assert "actual_model" not in payload_text
    assert "selected_provider" not in payload_text
    assert "selected_model" not in payload_text
    assert "constructor_provider" not in payload_text
    assert "constructor_model" not in payload_text
    assert "runtime_bridge_allowed" not in payload_text
    assert "runtime_bridge_enabled" not in payload_text
    assert "SECRET_TOKEN=abc123" not in json.dumps(payload, sort_keys=True)


def test_gateway_orchestrator_observe_does_not_load_runtime_bridge_components(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    def _boom():
        raise AssertionError("observe path must not load runtime bridge components")

    monkeypatch.setattr(orchestrator, "_load_runtime_factory_class", _boom, raising=False)
    monkeypatch.setattr(orchestrator, "_load_pipeline_planning_components", _boom, raising=False)
    monkeypatch.setattr(orchestrator, "_load_subagent_runner_class", _boom, raising=False)
    monkeypatch.setattr(orchestrator, "_load_pipeline_execution_request_class", _boom, raising=False)

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
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_plan_status"] == "planned"
    assert payload["pipeline_plan_mode"] == "observe_plan_only"
    assert payload["runtime_plan_failed"] is False
    assert payload["pipeline_preflight"]["executed"] is False
    assert payload["completion_allowed"] is True


def test_gateway_orchestrator_observe_uses_pipeline_session_and_state_machine_boundary(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    session_module = importlib.import_module("hermes_cli.pipeline_session")

    decision = RouterDecision(
        pipeline_session_id="router-boundary",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.94,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )

    captured: dict[str, object] = {}
    original_create = orchestrator.create_pipeline_session
    original_build = orchestrator.build_pipeline_state_snapshot

    def _capturing_create(**kwargs):
        session = original_create(**kwargs)
        captured["session"] = session
        return session

    def _capturing_build(*, session, pipeline_spec):
        snapshot = original_build(session=session, pipeline_spec=pipeline_spec)
        captured["snapshot"] = snapshot
        return snapshot

    monkeypatch.setattr(orchestrator, "create_pipeline_session", _capturing_create)
    monkeypatch.setattr(orchestrator, "build_pipeline_state_snapshot", _capturing_build)

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}},
            user_message="Implement pipeline boundary",
            session_id="sess-boundary",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    session = captured["session"]
    snapshot = captured["snapshot"]
    assert isinstance(session, session_module.PipelineSession)
    assert session.pipeline_id == "engineering_review_pipeline"
    assert session.selected_subagent_ids == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert snapshot.state == "preflight_blocked_execution"
    assert snapshot.reviewer_condition == "code_changes_require_review"
    assert [step.step_kind for step in snapshot.planned_steps] == ["engineer", "reviewer"]
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_plan"]["transition_path"][-1] == "preflight_blocked_execution"
    assert "actual_provider" not in json.dumps(asdict(session), sort_keys=True)


def test_gateway_orchestrator_observe_reports_preflight_as_dev_fuse(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-preflight-fuse",
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
            user_message="Implement architecture-aligned cleanup",
            session_id="sess-preflight-fuse",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_preflight"]["reason_code"] in {"observe_only", "gate_disabled"}


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
