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
    assert '"pipeline_execution_controller":' in log_message
    assert '"status": "disabled"' in log_message


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
    assert payload["pipeline_execution_report"]["status"] == "not_executed"
    assert payload["pipeline_execution_report"]["summary"]["pipeline_id"] == "engineering_review_pipeline"
    assert payload["pipeline_execution_report"]["summary"]["selected_subagents"] == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]
    assert payload["pipeline_execution_report"]["final_response"]["text"] is None
    assert payload["pipeline_execution_report"]["completion"]["blocked_reason"] == "execution_disabled"
    engineer_runtime_plan = payload["pipeline_plan"]["step_records"][0]["runtime_factory_plan"]
    reviewer_runtime_plan = payload["pipeline_plan"]["step_records"][1]["runtime_factory_plan"]
    assert engineer_runtime_plan["status"] == "plan_only"
    assert engineer_runtime_plan["provider"] == "openrouter"
    assert engineer_runtime_plan["model"] == "xiaomi/mimo-v2.5-pro"
    assert engineer_runtime_plan["tool_policy"]["write"] == ["patch", "write_file"]
    assert engineer_runtime_plan["environment_policy"]["can_mutate_files"] is True
    assert reviewer_runtime_plan["status"] == "plan_only"
    assert reviewer_runtime_plan["provider"] == "openai-codex"
    assert reviewer_runtime_plan["model"] == "gpt-5.5"
    assert reviewer_runtime_plan["environment_policy"]["can_mutate_files"] is False
    assert "actual_provider" not in payload_text
    assert "actual_model" not in payload_text
    assert "selected_provider" not in payload_text
    assert "selected_model" not in payload_text
    assert "constructor_provider" not in payload_text
    assert "constructor_model" not in payload_text
    assert "runtime_bridge_allowed" not in payload_text
    assert "runtime_bridge_enabled" not in payload_text
    assert "SECRET_TOKEN=abc123" not in json.dumps(payload, sort_keys=True)
    assert "prompt_input_hash" not in payload_text


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


def test_gateway_orchestrator_observe_does_not_wire_controlled_one_step_execution(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    def _boom(*_args, **_kwargs):
        raise AssertionError("observe path must remain metadata-only")

    monkeypatch.setattr(orchestrator, "execute_controlled_one_step", _boom, raising=False)

    decision = RouterDecision(
        pipeline_session_id="router-plan-only",
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
            user_message="Implement metadata only observe check",
            session_id="sess-plan-only",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_report"]["status"] == "not_executed"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_observe_with_controller_enabled_still_does_not_execute(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controller-not-wired",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "observe"},
                    "execution": {
                        "mode": "controlled_one_step",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="Controller should stay not wired on observe path",
            session_id="sess-controller-not-wired",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_report"]["status"] == "not_executed"
    assert payload["pipeline_execution_controller"]["status"] == "not_wired"
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "live_execution_not_wired"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_observe_does_not_resolve_registered_helper(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")

    def _boom(**_kwargs):
        raise AssertionError("observe path must not resolve registered helpers")

    monkeypatch.setattr(helpers, "resolve_pipeline_execution_helper", _boom)

    decision = RouterDecision(
        pipeline_session_id="router-controller-no-resolve",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "observe"},
                    "execution": {
                        "mode": "controlled_one_step",
                        "enable_gateway_execution_controller": True,
                    },
                }
            },
            user_message="Observe path must not resolve helpers",
            session_id="sess-controller-no-resolve",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_controller"]["status"] == "not_wired"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_observe_reports_execution_controller_disabled_by_default(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controller-default",
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
            user_message="Implement controller default behavior",
            session_id="sess-controller-default",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_controller"]["status"] == "disabled"
    assert payload["pipeline_execution_controller"]["execution_allowed"] is False
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "execution_mode_disabled"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_observe_reports_enabled_like_execution_as_would_execute(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controller-enabled-like",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "observe"},
                    "execution": {"mode": "controlled_one_step"},
                }
            },
            user_message="Implement controller enabled-like behavior",
            session_id="sess-controller-enabled-like",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_controller"]["status"] == "would_execute"
    assert payload["pipeline_execution_controller"]["execution_allowed"] is False
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "gateway_execution_not_enabled"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


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


def test_controlled_manual_router_mode_reaches_orchestrator_execution_gate(monkeypatch, caplog):
    pipeline_observe = importlib.import_module("hermes_cli.pipeline_observe")
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-gateway",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.94,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", lambda **kwargs: _FakeRouter())

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        router_decision = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"enabled": True, "router": {"mode": "controlled_manual"}}},
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run and return only the safe execution report summary.",
            session_id="sess-controlled-manual-router-gate",
            platform="telegram",
            logger=logging.getLogger("gateway.test"),
        )
        report = orchestrator.observe_gateway_turn(
            config={
                "pipelines": {
                    "enabled": True,
                    "router": {"mode": "controlled_manual"},
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {
                        "mode": "controlled_manual",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run and return only the safe execution report summary.",
            session_id="sess-controlled-manual-router-gate",
            platform="telegram",
            router_decision=router_decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert router_decision == decision
    assert report is not None
    observe_payload = json.loads(next(record.message for record in caplog.records if "pipeline_router_observe_decision" in record.message).split("pipeline_router_observe ", 1)[1])
    assert observe_payload["selected_pipeline_id"] == "engineering_review_pipeline"
    orchestrator_payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert orchestrator_payload["pipeline_execution_controller"]["actual_execution_invoked"] is True
    assert orchestrator_payload["pipeline_execution_controller"]["helper_result"]["provider_execution_mode"] == "fake_real_provider_client"
    assert not any("Invalid pipelines.router.mode" in record.message for record in caplog.records)


def test_gateway_orchestrator_controlled_manual_authorized_operator_without_trigger_executes(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-missing-trigger",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {
                        "mode": "controlled_manual",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="ordinary engineering request",
            session_id="sess-controlled-manual-missing-trigger",
            platform="telegram",
            chat_id="chat-123",
            user_id="user-1",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_controller"]["blocked_reason"] is None
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is True


def test_gateway_orchestrator_controlled_manual_unknown_context_without_trigger_stays_blocked(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-unknown-context",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {
                        "mode": "controlled_manual",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="ordinary engineering request",
            session_id=None,
            platform=None,
            chat_id=None,
            user_id=None,
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "controlled_manual_trigger_missing"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_controlled_manual_cron_without_trigger_stays_blocked(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-cron-context",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {
                        "mode": "controlled_manual",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="ordinary engineering request",
            session_id="cron-session-1",
            platform="cron",
            chat_id="cron-room",
            user_id="cron-user",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "controlled_manual_trigger_missing"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_controlled_manual_trigger_overrides_default_fallback(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-default-fallback",
        router_subagent_id="hermes_pipeline_router",
        status="no_specialized_pipeline",
        selected_pipeline_id=None,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.41,
        reasoning_summary="conversational fallback",
        fallback_safe=True,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {
                        "mode": "controlled_manual",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run and return only the safe execution report summary.",
            session_id="sess-controlled-manual-default-fallback",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["selected_pipeline_id"] == "engineering_review_pipeline"
    assert payload["effective_pipeline_id"] == "engineering_review_pipeline"
    assert payload["router_confidence"] == 0.99
    assert payload.get("router_reasoning_summary") == "controlled_manual_trigger_override"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is True
    final_response_text = payload["pipeline_execution_controller"]["final_response_text"]
    assert final_response_text.startswith("Controlled pipeline validation report.")
    assert "status: blocked" in final_response_text
    assert "report_execution_invoked: True" in final_response_text
    assert "mutation: none" in final_response_text
    assert "tests: passed" in final_response_text
    dumped = json.dumps(payload, sort_keys=True)
    assert "/tmp/hermes-gateway-controlled-runs" not in dumped
    assert "/home/hermes/.hermes/controlled-runs" not in dumped


def test_gateway_orchestrator_observe_trigger_does_not_override_default_fallback(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-observe-default-fallback",
        router_subagent_id="hermes_pipeline_router",
        status="no_specialized_pipeline",
        selected_pipeline_id=None,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.41,
        reasoning_summary="conversational fallback",
        fallback_safe=True,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "observe"},
                    "execution": {"mode": "controlled_manual", "enable_gateway_execution_controller": True},
                }
            },
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run and return only the safe execution report summary.",
            session_id="sess-observe-default-fallback",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["selected_pipeline_id"] is None
    assert payload["effective_pipeline_id"] == "default_conversation_pipeline"
    assert payload["router_confidence"] == 0.41
    assert payload.get("router_reasoning_summary") == "conversational fallback"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_controlled_manual_trigger_requires_controlled_execution_mode(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-observe-execution",
        router_subagent_id="hermes_pipeline_router",
        status="no_specialized_pipeline",
        selected_pipeline_id=None,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.41,
        reasoning_summary="conversational fallback",
        fallback_safe=True,
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {"mode": "observe", "enable_gateway_execution_controller": True},
                }
            },
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run and return only the safe execution report summary.",
            session_id="sess-controlled-manual-observe-execution",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["selected_pipeline_id"] is None
    assert payload["effective_pipeline_id"] == "default_conversation_pipeline"
    assert payload["router_confidence"] == 0.41
    assert payload.get("router_reasoning_summary") == "conversational fallback"
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False


def test_gateway_orchestrator_controlled_manual_executes_fake_only_dry_run(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-controlled-manual-exec",
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
            config={
                "pipelines": {
                    "enabled": True,
                    "orchestrator": {"mode": "controlled_manual"},
                    "execution": {
                        "mode": "controlled_manual",
                        "enable_gateway_execution_controller": True,
                        "allow_actual_subagent_invocation": True,
                        "allow_actual_reviewer_invocation": True,
                        "allow_actual_rework_loop": True,
                        "allow_pipelines": ["engineering_review_pipeline"],
                        "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                    },
                }
            },
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run and return only the safe execution report summary.",
            session_id="sess-controlled-manual-exec",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["execution_report"]["executed"] is True
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is True
    assert payload["pipeline_execution_controller"]["helper_result"]["provider_execution_mode"] == "fake_real_provider_client"
    final_response_text = payload["pipeline_execution_controller"]["final_response_text"]
    assert final_response_text.startswith("Controlled pipeline validation report.")
    assert "status: blocked" in final_response_text
    assert "execution_mode: controlled_runtime_loop" in final_response_text
    assert "report_execution_invoked: True" in final_response_text
    assert "tests: passed" in final_response_text
    assert "workspace: <redacted_absolute_path>/router-controlled-manual-exec" in final_response_text
    assert payload["pipeline_execution_report"]["status"] == "blocked"
    assert payload["pipeline_execution_report"]["controller"]["executed"] is True
    assert payload["pipeline_execution_report"]["tests"]["status"] == "passed"
    assert payload["pipeline_execution_report"]["mutation_summary"]["applied_count"] == 1
    dumped = json.dumps(payload, sort_keys=True)
    assert "/tmp/hermes-gateway-controlled-runs" not in dumped
    assert "/home/hermes/.hermes/controlled-runs" not in dumped
