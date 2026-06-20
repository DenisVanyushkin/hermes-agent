from __future__ import annotations

import importlib
import sys

from hermes_cli.pipeline_router import RouterDecision


def _selected_decision(*, confidence: float = 0.93, pipeline_id: str = "engineering_review_pipeline") -> RouterDecision:
    return RouterDecision(
        pipeline_session_id="pipe-g1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id=pipeline_id,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=confidence,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )


def _plan_payload(
    *,
    status: str = "planned",
    completion_reason: str | None = "plan_only",
    runtime_plan_failed: bool = False,
    plan_error: dict[str, object] | None = None,
    reviewer_condition: str = "code_changes_require_review",
    include_constructor_metadata: bool = True,
) -> dict[str, object]:
    engineer_step = {
        "step_kind": "engineer",
        "subagent_id": "hermes_engineer_core",
        "condition": None,
        "prompt_artifact": {"sha256": "eng", "preview": "hidden"},
        "tool_permission_plan_summary": {"tools": ["apply_patch"]},
        "metadata_summary": {"planned_execution": True},
    }
    reviewer_step = {
        "step_kind": "reviewer",
        "subagent_id": "hermes_code_reviewer",
        "condition": reviewer_condition,
        "prompt_artifact": {"sha256": "rev", "preview": "hidden"},
        "tool_permission_plan_summary": {"tools": ["review"]},
        "metadata_summary": {"planned_execution": True},
    }
    if include_constructor_metadata:
        engineer_step.update({"constructor_provider": "openrouter", "constructor_model": "xiaomi/mimo-v2.5-pro"})
        reviewer_step.update({"constructor_provider": "openai-codex", "constructor_model": "gpt-5.5"})
    return {
        "pipeline_plan_status": status,
        "pipeline_plan_completion_reason": completion_reason,
        "planned_steps_count": 2,
        "planned_subagent_ids": ["hermes_engineer_core", "hermes_code_reviewer"],
        "reviewer_planned": True,
        "reviewer_condition": reviewer_condition,
        "runtime_plan_failed": runtime_plan_failed,
        "pipeline_plan_error": plan_error,
        "pipeline_plan": {
            "status": status,
            "completion_reason": completion_reason,
            "step_records": [engineer_step, reviewer_step],
        },
    }


def _execution_config(mode: str = "disabled") -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": mode,
                "allow_pipelines": ["engineering_review_pipeline"],
                "min_router_confidence": 0.90,
            },
        }
    }


def test_pipeline_gate_import_is_lightweight():
    for name in (
        "hermes_cli.pipeline_gate",
        "hermes_cli.pipeline_executor",
        "hermes_cli.runtime_factory",
        "hermes_cli.subagent_runner",
    ):
        sys.modules.pop(name, None)

    gate = importlib.import_module("hermes_cli.pipeline_gate")

    assert gate.PipelineGateMode.DISABLED.value == "disabled"
    assert "hermes_cli.pipeline_executor" not in sys.modules


def test_pipeline_gate_default_missing_config_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=None,
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.mode == gate.PipelineGateMode.DISABLED
    assert decision.reason_code == "gate_disabled"
    assert decision.to_safe_dict()["blocked"] is True
    assert decision.to_safe_dict()["executed"] is False


def test_pipeline_gate_observe_mode_denies_execution():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("observe"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(include_constructor_metadata=False),
        )
    )

    payload = decision.to_safe_dict()
    assert decision.allowed is False
    assert decision.mode == gate.PipelineGateMode.OBSERVE
    assert decision.reason_code == "observe_only"
    assert payload["would_execute"] is False
    assert payload["executed"] is False
    assert decision.reason.startswith("Observe mode")
    assert "runtime_constructor_verified" not in payload["required_checks_summary"]["failed"]


def test_pipeline_gate_controlled_manual_mode_is_valid_and_allows_preflight():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("controlled_manual"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
            platform_allowed=True,
            destructive_task=False,
        )
    )

    payload = decision.to_safe_dict()
    assert decision.allowed is True
    assert decision.mode.value == "controlled_manual"
    assert decision.reason_code == "allowed"
    assert payload["would_execute"] is True
    assert payload["executed"] is False
    assert "runtime_constructor_verified" in payload["required_checks_summary"]["passed"]


def test_pipeline_gate_invalid_mode_fails_closed():
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    config = _execution_config("execute")
    config["pipelines"]["execution"]["mode"] = {"bad": "value"}

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=config,
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "missing_required_config"


def test_pipeline_gate_malformed_allowlist_fails_closed():
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    config = _execution_config("execute")
    config["pipelines"]["execution"]["allow_pipelines"] = "engineering_review_pipeline"

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=config,
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "missing_required_config"


def test_pipeline_gate_unsupported_pipeline_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(pipeline_id="default_conversation_pipeline"),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "unsupported_pipeline"


def test_pipeline_gate_router_not_selected_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    router_decision = _selected_decision()
    router_decision = RouterDecision(
        pipeline_session_id=router_decision.pipeline_session_id,
        router_subagent_id=router_decision.router_subagent_id,
        status="needs_clarification",
        selected_pipeline_id=None,
        fallback_pipeline_id=router_decision.fallback_pipeline_id,
        confidence=0.45,
        reasoning_summary="ambiguous",
        fallback_safe=False,
    )

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=router_decision,
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "router_not_selected"


def test_pipeline_gate_runtime_constructor_must_be_verified():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(include_constructor_metadata=False),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "runtime_constructor_unverified"


def test_pipeline_gate_destructive_task_requires_approval():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
            destructive_task=True,
            explicit_approval=False,
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "destructive_task_requires_approval"


def test_pipeline_gate_execute_mode_returns_allowed_preflight():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
            platform_allowed=True,
            destructive_task=False,
        )
    )

    payload = decision.to_safe_dict()
    assert decision.allowed is True
    assert payload["would_execute"] is True
    assert payload["executed"] is False
    assert payload["selected_pipeline_id"] == "engineering_review_pipeline"
    assert payload["planned_steps_count"] == 2
    assert "runtime_constructor_verified" in payload["required_checks_summary"]["passed"]
