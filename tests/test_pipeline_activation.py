from __future__ import annotations

import importlib
import sys

from hermes_cli.pipeline_router import RouterDecision


def _router_decision(*, pipeline_id: str = "engineering_review_pipeline", session_id: str = "pipe-a1") -> RouterDecision:
    return RouterDecision(
        pipeline_session_id=session_id,
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id=pipeline_id,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.95,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )


def _preflight_decision(*, allowed: bool, pipeline_id: str = "engineering_review_pipeline", session_id: str = "pipe-a1"):
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    return gate.PipelineGateDecision(
        allowed=allowed,
        mode=gate.PipelineGateMode.EXECUTE if allowed else gate.PipelineGateMode.OBSERVE,
        pipeline_id=pipeline_id,
        pipeline_session_id=session_id,
        selected_pipeline_id=pipeline_id,
        planned_steps_count=2,
        reason_code="allowed" if allowed else "observe_only",
        reason="allowed for tests" if allowed else "execution denied",
        would_execute=allowed,
        executed=False,
        requirements_met=["router_selected"] if allowed else [],
        requirements_failed=[] if allowed else ["execute_mode_required"],
        risk_level="medium" if allowed else "high",
        safe_to_log_payload={},
    )


def test_pipeline_activation_import_is_lightweight():
    for name in (
        "hermes_cli.pipeline_activation",
        "gateway.run",
        "agent.conversation_loop",
        "agent.tool_executor",
    ):
        sys.modules.pop(name, None)

    activation = importlib.import_module("hermes_cli.pipeline_activation")

    assert activation.PipelineActivationStatus.BLOCKED.value == "blocked"
    assert "gateway.run" not in sys.modules


def test_pipeline_activation_blocked_preflight_stays_noop():
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            router_decision=_router_decision(),
            preflight_decision=_preflight_decision(allowed=False),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "observe_only"
    assert result.would_execute is False
    assert result.executed is False


def test_pipeline_activation_allowed_without_bridge_reports_unavailable():
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            router_decision=_router_decision(),
            preflight_decision=_preflight_decision(allowed=True),
            executor=None,
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.NOT_WIRED
    assert result.activation_reason == "activation_not_wired"
    assert result.would_execute is True
    assert result.executed is False


def test_pipeline_activation_pipeline_id_mismatch_blocks():
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            router_decision=_router_decision(pipeline_id="default_conversation_pipeline"),
            preflight_decision=_preflight_decision(allowed=True),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "pipeline_id_mismatch"


def test_pipeline_activation_pipeline_session_mismatch_blocks():
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            router_decision=_router_decision(session_id="pipe-other"),
            preflight_decision=_preflight_decision(allowed=True),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "pipeline_session_id_mismatch"


def test_pipeline_activation_never_calls_executor_even_when_supplied():
    activation = importlib.import_module("hermes_cli.pipeline_activation")
    called = False

    def _executor():
        nonlocal called
        called = True
        raise AssertionError("activation must not call executor in this slice")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            router_decision=_router_decision(),
            preflight_decision=_preflight_decision(allowed=True),
            executor=_executor,
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.NOT_WIRED
    assert result.activation_reason == "activation_not_wired"
    assert result.executed is False
    assert called is False


def test_pipeline_activation_never_reports_runtime_bridge_fields():
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            router_decision=_router_decision(),
            preflight_decision=_preflight_decision(allowed=True),
        )
    )

    payload = result.to_safe_dict()
    assert "runtime_bridge" not in repr(payload)
