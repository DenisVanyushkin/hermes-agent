from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_specs import load_pipeline_specs


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _execution_request(tmp_path: Path):
    from hermes_cli.pipeline_executor import PipelineExecutionRequest

    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    return PipelineExecutionRequest(
        loaded_specs=loaded_specs,
        pipeline_session_id="pipe-a1",
        task_summary="Implement I1 with SECRET_TOKEN=abc123",
        repo_path=str(repo_root),
        mode="execute",
    )


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


def _gate_decision(*, allowed: bool = True, mode: str = "execute", pipeline_id: str = "engineering_review_pipeline", session_id: str = "pipe-a1"):
    gate = importlib.import_module("hermes_cli.pipeline_gate")
    return gate.PipelineGateDecision(
        allowed=allowed,
        mode=gate.PipelineGateMode(mode),
        pipeline_id=pipeline_id,
        pipeline_session_id=session_id,
        reason_code="allowed" if allowed else "gate_denied",
        reason="allowed for tests" if allowed else "execution denied",
        requirements_met=["router_selected"] if allowed else [],
        requirements_failed=[] if allowed else ["gate_allowed"],
        risk_level="medium" if allowed else "high",
        safe_to_log_payload={},
    )


def _handoff_decision(*, would_execute: bool = True, executed: bool = False, pipeline_id: str = "engineering_review_pipeline", session_id: str = "pipe-a1"):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    return handoff.PipelineHandoffDecision(
        pipeline_id=pipeline_id,
        pipeline_session_id=session_id,
        gate_allowed=True,
        gate_reason_code="allowed",
        handoff_status=handoff.PipelineHandoffStatus.READY if would_execute else handoff.PipelineHandoffStatus.BLOCKED,
        handoff_reason="activation_required" if would_execute else "handoff_denied",
        execution_mode=handoff.PipelineHandoffMode.TEST_EXECUTE if would_execute else handoff.PipelineHandoffMode.OBSERVE_ONLY,
        would_execute=would_execute,
        executed=executed,
        safe_summary="safe",
    )


def _config(mode: str) -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": mode,
                "allow_pipelines": ["engineering_review_pipeline"],
            },
        }
    }


def _result_payload_without_secrets(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for needle in ("SECRET_TOKEN", "abc123", "tool_args", "raw prompt text", "Implement I1", "/tmp/secret"):
        assert needle not in serialized


def _approved_result():
    from hermes_cli.pipeline_executor import PipelineExecutionResult, PipelineExecutorStatus

    return PipelineExecutionResult(
        pipeline_id="engineering_review_pipeline",
        pipeline_session_id="pipe-a1",
        status=PipelineExecutorStatus.APPROVED,
        completion_reason="completed",
        iterations=[],
        step_records=[],
        reviewer_required=True,
        reviewer_ran=True,
        blocking_findings_count=0,
        final_approval_status="approved",
        elapsed_ms=1.0,
        safe_summary="Reviewer approved engineering changes.",
        error_code=None,
        error_message=None,
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
    assert "agent.conversation_loop" not in sys.modules
    assert "agent.tool_executor" not in sys.modules


def test_pipeline_activation_disabled_execution_mode_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("disabled"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.DISABLED
    assert result.executed is False
    assert result.activation_reason == "disabled_mode_blocks_activation"


def test_pipeline_activation_observe_mode_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("observe"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(allowed=False, mode="observe"),
            handoff_decision=_handoff_decision(would_execute=False),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.executed is False
    assert result.activation_reason == "observe_mode_blocks_activation"


def test_pipeline_activation_gate_denied_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(allowed=False),
            handoff_decision=_handoff_decision(),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "gate_denied"


def test_pipeline_activation_handoff_denied_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(would_execute=False),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "handoff_denied"


def test_pipeline_activation_handoff_pipeline_mismatch_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(pipeline_id="default_conversation_pipeline"),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "handoff_pipeline_id_mismatch"


def test_pipeline_activation_pipeline_id_mismatch_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="default_conversation_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "pipeline_id_mismatch"


def test_pipeline_activation_pipeline_session_id_mismatch_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-other",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "pipeline_session_id_mismatch"


def test_pipeline_activation_missing_executor_boundary_blocks(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
            allow_test_execution=True,
            platform_allowed=True,
            executor=None,
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "missing_executor_boundary"


def test_pipeline_activation_platform_allowed_false_blocks_before_executor(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")
    calls = {"count": 0}

    def _executor():
        calls["count"] += 1
        return _approved_result()

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
            executor=_executor,
            allow_test_execution=True,
            platform_allowed=False,
            destructive_task=False,
            explicit_approval=False,
        )
    )

    assert calls["count"] == 0
    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "unsafe_platform"
    assert result.would_execute is False
    assert result.executed is False
    assert "platform_allowed" in result.requirements_failed
    assert "platform_allowed" not in result.requirements_met


def test_pipeline_activation_platform_allowed_none_blocks_before_executor(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")
    calls = {"count": 0}

    def _executor():
        calls["count"] += 1
        return _approved_result()

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
            executor=_executor,
            allow_test_execution=True,
            platform_allowed=None,
            destructive_task=False,
            explicit_approval=False,
        )
    )

    assert calls["count"] == 0
    assert result.activation_status == activation.PipelineActivationStatus.BLOCKED
    assert result.activation_reason == "unsafe_platform"
    assert result.would_execute is False
    assert result.executed is False
    assert "platform_allowed" in result.requirements_failed
    assert "platform_allowed" not in result.requirements_met


def test_pipeline_activation_test_only_allowed_invokes_injected_fake_executor_once(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")
    calls = {"count": 0}

    def _executor():
        calls["count"] += 1
        return _approved_result()

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
            executor=_executor,
            allow_test_execution=True,
            platform_allowed=True,
            destructive_task=False,
            explicit_approval=False,
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.EXECUTED
    assert result.executed is True
    assert result.pipeline_executor_status == "approved"
    assert calls["count"] == 1


def test_pipeline_activation_failure_is_structured_and_safe(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    def _executor():
        raise RuntimeError("SECRET_TOKEN=abc123 raw prompt text tool_args={'danger': true}")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
            executor=_executor,
            allow_test_execution=True,
            platform_allowed=True,
        )
    )

    assert result.activation_status == activation.PipelineActivationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "activation_executor_failed"
    payload = result.to_safe_dict()
    _result_payload_without_secrets(payload)


def test_pipeline_activation_safe_payload_excludes_sensitive_content(tmp_path: Path):
    activation = importlib.import_module("hermes_cli.pipeline_activation")

    result = activation.PipelineActivationCoordinator().run(
        activation.PipelineActivationRequest(
            config=_config("execute"),
            router_decision=_router_decision(),
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-a1",
            gate_decision=_gate_decision(),
            handoff_decision=_handoff_decision(),
            executor=lambda: _approved_result(),
            allow_test_execution=True,
            platform_allowed=True,
        )
    )

    payload = result.to_safe_dict()
    _result_payload_without_secrets(payload)
