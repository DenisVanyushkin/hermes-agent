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
) -> dict[str, object]:
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
            "step_records": [
                {
                    "step_kind": "engineer",
                    "subagent_id": "hermes_engineer_core",
                    "condition": None,
                    "prompt_artifact": {"sha256": "eng", "preview": "hidden"},
                    "tool_permission_plan_summary": {"tools": ["apply_patch"]},
                    "metadata_summary": {"planned_execution": True},
                },
                {
                    "step_kind": "reviewer",
                    "subagent_id": "hermes_code_reviewer",
                    "condition": reviewer_condition,
                    "prompt_artifact": {"sha256": "rev", "preview": "hidden"},
                    "tool_permission_plan_summary": {"tools": ["review"]},
                    "metadata_summary": {"planned_execution": True},
                },
            ],
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
    assert "hermes_cli.runtime_factory" not in sys.modules
    assert "hermes_cli.subagent_runner" not in sys.modules


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
    assert "execution mode" in decision.reason.lower()


def test_pipeline_gate_observe_mode_denies_execution():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("observe"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.mode == gate.PipelineGateMode.OBSERVE
    assert decision.reason_code == "observe_only"


def test_pipeline_gate_plan_only_mode_denies_execution():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("plan_only"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.mode == gate.PipelineGateMode.PLAN_ONLY
    assert decision.reason_code == "plan_only"


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


def test_pipeline_gate_low_confidence_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(confidence=0.89),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "low_router_confidence"


def test_pipeline_gate_plan_not_ready_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(status="approved"),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "plan_not_ready"


def test_pipeline_gate_runtime_plan_failed_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(runtime_plan_failed=True),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "runtime_plan_failed"


def test_pipeline_gate_plan_error_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(plan_error={"error_type": "RuntimeError", "message": "boom"}),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "plan_error"


def test_pipeline_gate_missing_expected_steps_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    payload = _plan_payload()
    payload["planned_subagent_ids"] = ["hermes_engineer_core"]
    payload["pipeline_plan"]["step_records"] = payload["pipeline_plan"]["step_records"][:1]

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=payload,
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "missing_expected_steps"


def test_pipeline_gate_reviewer_not_conditional_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(reviewer_condition="always_run"),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "reviewer_not_conditional"


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


def test_pipeline_gate_unsafe_platform_denies():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
            platform="slack",
            platform_allowed=False,
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "unsafe_platform"


def test_pipeline_gate_missing_allowlist_denies_safely():
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    config = _execution_config("execute")
    del config["pipelines"]["execution"]["allow_pipelines"]

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=config,
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "missing_required_config"


def test_pipeline_gate_malformed_config_denies_safely():
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    config = {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": {"bad": "shape"},
                "allow_pipelines": "engineering_review_pipeline",
                "min_router_confidence": "not-a-number",
            },
        }
    }

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=config,
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
        )
    )

    assert decision.allowed is False
    assert decision.reason_code in {"missing_required_config", "unknown"}
    assert "SECRET" not in repr(decision.safe_to_log_payload)


def test_pipeline_gate_execute_happy_path_allows_policy_only():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
            platform="telegram",
            platform_allowed=True,
            destructive_task=False,
            explicit_approval=False,
        )
    )

    assert decision.allowed is True
    assert decision.mode == gate.PipelineGateMode.EXECUTE
    assert decision.reason_code == "allowed"
    assert decision.pipeline_id == "engineering_review_pipeline"
    assert decision.pipeline_session_id == "pipe-g1"


def test_pipeline_gate_safe_dict_excludes_sensitive_fields():
    gate = importlib.import_module("hermes_cli.pipeline_gate")

    decision = gate.evaluate_pipeline_gate(
        gate.PipelineGateRequest(
            config=_execution_config("execute"),
            router_decision=_selected_decision(),
            pipeline_plan_payload=_plan_payload(),
            user_message="SECRET_TOKEN=abc123 implement slice",
            prompt_text="system prompt with credentials",
            raw_executor_output={"output_text": "sensitive"},
            raw_tool_arguments={"command": "rm -rf /"},
            secrets={"token": "abc123"},
        )
    )

    safe = decision.to_safe_dict()

    assert "user_message" not in safe
    assert "prompt_text" not in safe
    assert "raw_executor_output" not in safe
    assert "raw_tool_arguments" not in safe
    assert "secrets" not in safe
    assert safe["safe_to_log_payload"]["user_message_length"] > 0
    assert safe["safe_to_log_payload"]["user_message_hash"]
