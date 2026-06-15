from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

from hermes_cli.pipeline_gate import PipelineGateDecision, PipelineGateMode
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_specs import load_pipeline_specs


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _build_loaded_specs(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    return repo_root, load_pipeline_specs(repo_root=repo_root)


def _gate_decision(*, allowed: bool, mode: PipelineGateMode = PipelineGateMode.EXECUTE) -> PipelineGateDecision:
    return PipelineGateDecision(
        allowed=allowed,
        mode=mode,
        pipeline_id="engineering_review_pipeline",
        pipeline_session_id="pipe-h1",
        reason_code="allowed" if allowed else "observe_only",
        reason="allowed for tests" if allowed else "execution denied",
        requirements_met=["router_selected"] if allowed else [],
        requirements_failed=[] if allowed else ["execute_mode_required"],
        risk_level="medium" if allowed else "high",
        safe_to_log_payload={"mode": mode.value, "allowed": allowed},
    )


def _router_decision() -> RouterDecision:
    return RouterDecision(
        pipeline_session_id="pipe-h1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.94,
        reasoning_summary="engineering request",
        fallback_safe=False,
    )


def _execution_request(tmp_path: Path):
    from hermes_cli.pipeline_executor import PipelineExecutionRequest

    repo_root, loaded_specs = _build_loaded_specs(tmp_path)
    request = PipelineExecutionRequest(
        loaded_specs=loaded_specs,
        pipeline_session_id="pipe-h1",
        task_summary="Implement H1 with SECRET_TOKEN=abc123",
        repo_path=str(repo_root),
        mode="execute",
    )
    return repo_root, request


def _assert_no_secret_text(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for needle in (
        "SECRET_TOKEN",
        "abc123",
        "raw prompt text",
        "tool_args",
        "danger",
        "Implement H1",
    ):
        assert needle not in serialized


def test_pipeline_handoff_import_is_lightweight():
    for name in (
        "hermes_cli.pipeline_handoff",
        "gateway.run",
        "agent.conversation_loop",
        "agent.tool_executor",
    ):
        sys.modules.pop(name, None)

    handoff = importlib.import_module("hermes_cli.pipeline_handoff")

    assert handoff.PipelineHandoffMode.TEST_EXECUTE.value == "test_execute"
    assert "gateway.run" not in sys.modules
    assert "agent.conversation_loop" not in sys.modules
    assert "agent.tool_executor" not in sys.modules


def test_pipeline_handoff_missing_gate_denies_without_execution(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)
    runner = handoff.PipelineHandoffCoordinator()

    result = runner.run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=None,
            execution_request=execution_request,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.DENIED
    assert result.would_execute is False
    assert result.executed is False
    assert result.pipeline_executor_result is None
    assert result.gate_allowed is False
    assert result.gate_reason_code == "missing_gate_decision"


def test_pipeline_handoff_denied_gate_returns_structured_noop(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=False, mode=PipelineGateMode.OBSERVE),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.OBSERVE_ONLY,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.DENIED
    assert result.handoff_reason == "execution denied"
    assert result.would_execute is False
    assert result.executed is False
    assert result.pipeline_executor_result is None
    assert result.gate_allowed is False
    assert result.gate_reason_code == "observe_only"


def test_pipeline_handoff_allowed_but_missing_test_flag_blocks(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=True),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=False,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.BLOCKED
    assert result.would_execute is True
    assert result.executed is False
    assert result.gate_allowed is True
    assert result.gate_reason_code == "allowed"
    assert result.handoff_reason == "test_execution_not_enabled"


def test_pipeline_handoff_allowed_but_missing_fake_executors_blocks(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=True),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=True,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.BLOCKED
    assert result.would_execute is True
    assert result.executed is False
    assert result.handoff_reason == "missing_fake_executors"


def test_pipeline_handoff_pipeline_id_mismatch_fails_closed(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)
    mismatched_gate = PipelineGateDecision(
        allowed=True,
        mode=PipelineGateMode.EXECUTE,
        pipeline_id="default_conversation_pipeline",
        pipeline_session_id="pipe-h1",
        reason_code="allowed",
        reason="allowed for tests",
    )

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=mismatched_gate,
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=True,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.FAILED
    assert result.executed is False
    assert result.handoff_reason == "pipeline_id_mismatch"


def test_pipeline_handoff_session_id_mismatch_fails_closed(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)
    mismatched_gate = PipelineGateDecision(
        allowed=True,
        mode=PipelineGateMode.EXECUTE,
        pipeline_id="engineering_review_pipeline",
        pipeline_session_id="pipe-other",
        reason_code="allowed",
        reason="allowed for tests",
    )

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=mismatched_gate,
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=True,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.FAILED
    assert result.executed is False
    assert result.handoff_reason == "pipeline_session_id_mismatch"


def test_pipeline_handoff_non_execute_gate_mode_fails_closed(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=True, mode=PipelineGateMode.OBSERVE),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=True,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.FAILED
    assert result.executed is False
    assert result.handoff_reason == "gate_execute_mode_required"


def test_pipeline_handoff_non_test_execute_mode_blocks(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=True),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.PLAN_ONLY,
            allow_test_execution=True,
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.BLOCKED
    assert result.executed is False
    assert result.would_execute is False
    assert result.handoff_reason == "test_execute_mode_required"


def test_pipeline_handoff_test_execute_only_returns_ready_for_activation(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=True),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=True,
            engineer_executor=lambda *_: {"unexpected": True},
            reviewer_executor=lambda *_: {"unexpected": True},
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.READY
    assert result.would_execute is True
    assert result.executed is False
    assert result.execution_mode == "test_execute"
    assert result.handoff_reason == "activation_required"
    assert result.safe_summary == "Pipeline handoff is ready for guarded test-only activation."


def test_pipeline_handoff_ready_payload_redacts_task_text(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=True),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.TEST_EXECUTE,
            allow_test_execution=True,
            engineer_executor=lambda *_: {"should_not_run": True},
            reviewer_executor=lambda *_: {"should_not_run": True},
        )
    )

    assert result.handoff_status == handoff.PipelineHandoffStatus.READY
    payload = result.to_safe_dict()
    _assert_no_secret_text(payload)


def test_pipeline_handoff_safe_dict_redacts_task_text_and_executor_output(tmp_path: Path):
    handoff = importlib.import_module("hermes_cli.pipeline_handoff")
    _, execution_request = _execution_request(tmp_path)

    result = handoff.PipelineHandoffCoordinator().run(
        handoff.PipelineHandoffRequest(
            pipeline_id="engineering_review_pipeline",
            pipeline_session_id="pipe-h1",
            router_decision=_router_decision(),
            gate_decision=_gate_decision(allowed=False, mode=PipelineGateMode.OBSERVE),
            execution_request=execution_request,
            mode=handoff.PipelineHandoffMode.OBSERVE_ONLY,
        )
    )

    payload = result.to_safe_dict()

    assert payload["pipeline_id"] == "engineering_review_pipeline"
    assert payload["pipeline_session_id"] == "pipe-h1"
    assert payload["gate_allowed"] is False
    assert payload["handoff_status"] == "denied"
    assert payload["execution_mode"] == "observe_only"
    assert payload["would_execute"] is False
    assert payload["executed"] is False
    assert payload["pipeline_executor_result"] is None
    _assert_no_secret_text(payload)
