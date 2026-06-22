from __future__ import annotations

import importlib
import json
import logging
from dataclasses import asdict

from hermes_cli.pipeline_router import RouterDecision


def test_gateway_autonomous_builds_plan_before_controller_and_uses_execution_report(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    controller_module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    captured = {}
    executed_report = {
        "status": "executed",
        "controller": {"executed": True, "execution_mode": "autonomous"},
        "completion": {"final_verdict": "approved", "blocked_reason": None},
        "final_response": {"text": "autonomous result"},
        "subagent_runs": [{"subagent_id": "hermes_engineer_core"}, {"subagent_id": "hermes_code_reviewer"}],
    }

    monkeypatch.setattr(orchestrator, "build_autonomous_helper_context", lambda **_kwargs: {"runtime_factory": object(), "runner": object(), "user_message": "task"})

    def _controller(**kwargs):
        captured["snapshot"] = kwargs["state_snapshot"]
        return controller_module.PipelineExecutionControllerResult(
            status="executed",
            execution_allowed=True,
            blocked_reason=None,
            selected_pipeline_id="engineering_review_pipeline",
            would_call="bounded_rework_loop",
            actual_execution_invoked=True,
            execution_mode="autonomous",
            resolved_helper_name="bounded_rework_loop",
            helper_result_status="executed",
            helper_result={"status": "executed", "report": executed_report},
            final_response_text="autonomous result",
        )

    monkeypatch.setattr(orchestrator, "evaluate_pipeline_execution_controller", _controller)
    decision = RouterDecision(
        pipeline_session_id="router-autonomous",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.99,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )
    config = {
        "pipelines": {
            "enabled": True,
            "router": {"mode": "autonomous"},
            "orchestrator": {"mode": "autonomous"},
            "execution": {
                "mode": "autonomous",
                "enable_gateway_execution_controller": True,
                "allow_real_provider_execution": True,
                "allow_pipelines": ["engineering_review_pipeline"],
                "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
            },
        }
    }
    with caplog.at_level(logging.INFO, logger="gateway.autonomous.test"):
        report = orchestrator.observe_gateway_turn(
            config=config,
            user_message="implement through autonomous pipeline",
            session_id="sess-autonomous",
            platform="telegram",
            chat_id="chat-autonomous",
            router_decision=decision,
            logger=logging.getLogger("gateway.autonomous.test"),
        )

    assert report is not None
    assert captured["snapshot"].status == "planned"
    assert captured["snapshot"].execution_mode == "autonomous"
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_preflight"]["reason_code"] == "allowed"
    assert payload["pipeline_plan_mode"] == "autonomous"
    assert payload["pipeline_execution_report"] == {**executed_report, "execution_mode": "autonomous"}
    assert "observe_plan_only" not in json.dumps(payload["pipeline_execution_report"])


def test_gateway_autonomous_prefers_helper_report_over_stale_snapshot(monkeypatch, caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    controller_module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    executed_report = {
        "status": "blocked",
        "executed": True,
        "execution_mode": "autonomous",
        "controller": {"executed": True, "execution_mode": "autonomous"},
        "completion": {"final_verdict": "blocked", "blocked_reason": "invalid_engineer_output"},
        "review": {"reviewer_invoked": False, "blocked_reason": "invalid_engineer_output"},
        "subagent_runs": [
            {
                "subagent_id": "hermes_engineer_core",
                "runtime_mode": "bridge_executor",
                "real_provider_allowed": True,
                "provider_policy_status": "ready_to_construct",
            }
        ],
    }

    monkeypatch.setattr(orchestrator, "build_autonomous_helper_context", lambda **_kwargs: {"runtime_factory": object(), "runner": object(), "user_message": "task"})
    monkeypatch.setattr(
        orchestrator,
        "evaluate_pipeline_execution_controller",
        lambda **_kwargs: controller_module.PipelineExecutionControllerResult(
            status="executed",
            execution_allowed=True,
            blocked_reason=None,
            selected_pipeline_id="engineering_review_pipeline",
            would_call="bounded_rework_loop",
            actual_execution_invoked=True,
            execution_mode="autonomous",
            subagent_execution_invoked=True,
            real_provider_bridge_invoked=True,
            resolved_helper_name="bounded_rework_loop",
            helper_result_status="executed",
            helper_result={"status": "executed", "subagent_runs": executed_report["subagent_runs"], "report": executed_report},
            final_response_text=None,
        ),
    )
    decision = RouterDecision(
        pipeline_session_id="router-autonomous-truthful",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.99,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )
    config = {
        "pipelines": {
            "enabled": True,
            "router": {"mode": "autonomous"},
            "orchestrator": {"mode": "autonomous"},
            "execution": {
                "mode": "autonomous",
                "enable_gateway_execution_controller": True,
                "allow_real_provider_execution": True,
                "allow_pipelines": ["engineering_review_pipeline"],
                "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
            },
        }
    }

    with caplog.at_level(logging.INFO, logger="gateway.autonomous.truthful"):
        report = orchestrator.observe_gateway_turn(
            config=config,
            user_message="implement through autonomous pipeline",
            session_id="sess-autonomous-truthful",
            platform="telegram",
            chat_id="chat-autonomous",
            router_decision=decision,
            logger=logging.getLogger("gateway.autonomous.truthful"),
        )

    assert report is not None
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["pipeline_execution_report"]["status"] == "blocked"
    assert payload["pipeline_execution_report"]["executed"] is True
    assert payload["pipeline_execution_report"]["completion"]["blocked_reason"] == "invalid_engineer_output"
    assert payload["pipeline_execution_report"]["subagent_runs"][0]["runtime_mode"] == "bridge_executor"


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
                        "mode": "observe",
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
    assert payload["pipeline_execution_controller"]["status"] == "blocked"
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "observe_only"
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
                        "mode": "observe",
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
    assert payload["pipeline_execution_controller"]["status"] == "blocked"
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "observe_only"
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
                    "execution": {"mode": "observe"},
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
    assert payload["pipeline_execution_controller"]["status"] == "blocked"
    assert payload["pipeline_execution_controller"]["execution_allowed"] is False
    assert payload["pipeline_execution_controller"]["blocked_reason"] == "observe_only"
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

    def _capturing_build(*, session, pipeline_spec, execution_mode="observe_plan_only"):
        snapshot = original_build(session=session, pipeline_spec=pipeline_spec, execution_mode=execution_mode)
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


def test_gateway_orchestrator_autonomous_routing_failed_is_terminal(caplog):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")

    decision = RouterDecision(
        pipeline_session_id="router-autonomous-routing-failed",
        router_subagent_id="hermes_pipeline_router",
        status="routing_failed",
        selected_pipeline_id=None,
        fallback_pipeline_id=None,
        confidence=0.0,
        reasoning_summary="Router timed out before selecting a pipeline.",
        fallback_safe=False,
        routing_failure_reason="TimeoutError: Codex auxiliary Responses stream exceeded 10.0s total timeout",
    )

    with caplog.at_level(logging.INFO, logger="gateway.test"):
        report = orchestrator.observe_gateway_turn(
            config={
                "pipelines": {
                    "enabled": True,
                    "router": {"mode": "autonomous"},
                    "orchestrator": {"mode": "autonomous"},
                    "execution": {"mode": "autonomous", "enable_gateway_execution_controller": True},
                }
            },
            user_message="Create tests/autonomous_runtime_smoke_marker.py and write a marker",
            session_id="sess-routing-failed",
            platform="telegram",
            router_decision=decision,
            logger=logging.getLogger("gateway.test"),
        )

    assert report is not None
    assert report.session.pipeline_id == "default_conversation_pipeline"
    assert report.state.pipeline_id == "default_conversation_pipeline"
    assert report.state.router_status == "routing_failed"
    assert report.state.final_verdict == "safe_default_fallback_used"
    assert report.pipeline_execution_controller.actual_execution_invoked is False
    assert report.pipeline_execution_controller.blocked_reason == "autonomous_not_selected"
    payload = json.loads(next(record.message for record in caplog.records if "pipeline_orchestrator_observe_report" in record.message).split("pipeline_orchestrator_observe ", 1)[1])
    assert payload["selected_pipeline_id"] is None
    assert payload["effective_pipeline_id"] == "default_conversation_pipeline"
    assert payload["pipeline_preflight"]["reason_code"] == "safe_default_fallback_used"
    assert payload["pipeline_preflight"]["tools_enabled"] is False
    assert payload["pipeline_preflight"]["safe_default_fallback"] is True
    assert payload["pipeline_execution_controller"]["actual_execution_invoked"] is False
    assert payload["pipeline_execution_controller"]["helper_result"] is None
    assert payload["pipeline_execution_report"]["status"] == "not_executed"
    assert payload["pipeline_execution_report"]["summary"]["pipeline_id"] == "default_conversation_pipeline"
    assert payload["pipeline_execution_report"]["summary"]["route_status"] == "safe_default_fallback_used"
    assert payload["pipeline_execution_report"]["completion"]["blocked_reason"] == "autonomous_not_selected"
    assert payload["pipeline_execution_report"]["completion"]["final_verdict"] == "safe_default_fallback_used"
    assert payload["pipeline_execution_report"]["safety"]["executed"] is False
    assert "tools_enabled=false" in payload["pipeline_execution_report"]["safety"]["policy_notes"]
    assert payload["pipeline_execution_report"]["usage"]["tool_calls"] == 0
