from __future__ import annotations

from dataclasses import replace
import importlib
from pathlib import Path
import shutil
from types import SimpleNamespace

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeFactory
from hermes_cli.subagent_runner import SubagentRunner


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _loaded_specs(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    return repo_root, load_pipeline_specs(repo_root=repo_root)


def _session():
    decision = RouterDecision(
        pipeline_session_id="pipe-one-step-1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.97,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-one-step-1",
            user_message="Implement one step",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )


def _config(
    *,
    mode: str = "disabled",
    allow_actual_subagent_invocation: bool = False,
    allow_actual_reviewer_invocation: bool = False,
    allowed_subagents: list[str] | None = None,
) -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": mode,
                "allow_pipelines": ["engineering_review_pipeline"],
                "allowed_subagents": ["hermes_engineer_core"] if allowed_subagents is None else allowed_subagents,
                "allow_actual_subagent_invocation": allow_actual_subagent_invocation,
                "allow_actual_reviewer_invocation": allow_actual_reviewer_invocation,
                "min_router_confidence": 0.90,
            }
        }
    }


def _runtime_factory(repo_root: Path) -> RuntimeFactory:
    return RuntimeFactory(repo_root=repo_root)


def _valid_structured_output() -> dict[str, object]:
    return {
        "schema_version": "v1",
        "subagent_id": "hermes_engineer_core",
        "role": "engineer",
        "status": "succeeded",
        "summary": "Prepared a narrow patch.",
        "findings": [{"code": "patch", "summary": "One controlled patch prepared"}],
        "changes": [{"path": "hermes_cli/pipeline_execution_fuse.py", "kind": "modify"}],
        "blockers": [],
        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
        "confidence": 0.94,
        "requires_review": False,
        "next_action": "none",
    }


def test_disabled_mode_returns_not_invoked_and_does_not_call_runner(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    called = {"count": 0}

    runner = SubagentRunner(
        executor=lambda *_args, **_kwargs: called.__setitem__("count", called["count"] + 1)  # pragma: no cover
    )

    result = module.execute_controlled_one_step(
        config=_config(mode="disabled", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=runner,
        user_message="Implement one step",
    )

    assert called["count"] == 0
    assert result.fuse.actual_invocation_allowed is False
    assert result.state_snapshot.executed is False
    assert result.state_snapshot.planned_steps[0].runner_result["status"] == "not_invoked"
    assert result.execution_report.status.value == "not_executed"


def test_allowed_one_step_mode_calls_runner_exactly_once(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    called = {"count": 0}

    def _executor(_request, _runtime_plan):
        called["count"] += 1
        return {
            "output_text": "Prepared one controlled patch.",
            "completion_reason": "completed",
            "execution_status": "completed",
            "token_usage": {"input_tokens": 12, "output_tokens": 9, "total_tokens": 21},
            "raw_metadata": {"structured_output": _valid_structured_output()},
        }

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement one step",
    )

    assert called["count"] == 1
    assert result.fuse.actual_invocation_allowed is True
    assert result.state_snapshot.executed is True
    assert result.state_snapshot.planned_steps[0].runner_result["status"] == "succeeded"
    assert result.state_snapshot.planned_steps[1].runner_result["status"] == "not_invoked"


def test_runner_request_uses_same_runtime_plan_as_runner_invocation(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    seen: dict[str, object] = {}
    base_factory = _runtime_factory(repo_root)

    class DriftedRuntimeFactory:
        def build(self, request):
            plan = base_factory.build(request)
            return replace(
                plan,
                constructor_provider="canonical-test-provider",
                constructor_model="canonical-test-model",
            )

    def _executor(request, runtime_plan):
        seen["runtime_plan_provider"] = runtime_plan.constructor_provider
        seen["runtime_plan_model"] = runtime_plan.constructor_model
        return {
            "output_text": "Prepared one controlled patch.",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": _valid_structured_output()},
        }

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=DriftedRuntimeFactory(),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement one step",
    )

    assert seen["runtime_plan_provider"] == "canonical-test-provider"
    assert seen["runtime_plan_model"] == "canonical-test-model"
    assert result.state_snapshot.planned_steps[0].runner_request["actual_provider"] == "canonical-test-provider"
    assert result.state_snapshot.planned_steps[0].runner_request["actual_model"] == "canonical-test-model"


def test_allowed_one_step_result_is_validated_evaluated_and_reported(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_controlled_one_step(
        config=_config(mode="one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "Prepared one controlled patch.",
                "completion_reason": "completed",
                "execution_status": "completed",
                "token_usage": {"input_tokens": 12, "output_tokens": 9, "total_tokens": 21},
                "raw_metadata": {"structured_output": _valid_structured_output()},
            }
        ),
        user_message="Implement one step",
    )

    engineer_step = result.state_snapshot.planned_steps[0]
    assert engineer_step.runner_result["structured_output"]["validation_status"] == "valid"
    assert engineer_step.evaluation_result["status"] == "candidate_complete"
    assert result.execution_report.executed is True
    assert result.execution_report.summary.selected_subagents == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]


def test_one_step_adapter_preserves_bridge_runtime_metadata(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    adapted = module._adapt_runner_result(
        invocation_result=SimpleNamespace(
            ok=True,
            execution_status="completed",
            completion_reason="completed",
            token_usage={},
            tool_intents=[],
            raw_metadata={"structured_output": _valid_structured_output()},
            record=SimpleNamespace(elapsed_ms=12.0),
            error_code=None,
        ),
        runner_request=SimpleNamespace(
            pipeline_session_id="pipe-one-step-1",
            trace_id="trace-one-step-1",
            pipeline_id="engineering_review_pipeline",
            step_id="engineer",
            subagent_id="hermes_engineer_core",
            role_id="engineer",
            runtime_factory_plan_id="rfp-1",
            runtime_factory_status="ready_to_construct",
        ),
        runtime_plan=SimpleNamespace(
            constructor_provider="openrouter",
            constructor_model="xiaomi/mimo-v2.5-pro",
            selection=SimpleNamespace(selected_model_class="frontier"),
            actual_runtime_status="ready_to_construct",
            runtime_mode="real_provider",
            real_provider_allowed=True,
            provider_policy_status="ready_to_construct",
        ),
    )

    assert adapted.runtime_mode == "real_provider"
    assert adapted.real_provider_allowed is True
    assert adapted.provider_policy_status == "ready_to_construct"


def test_final_report_marks_only_one_step_executed_but_not_final_completion(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "Prepared one controlled patch.",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": _valid_structured_output()},
            }
        ),
        user_message="Implement one step",
    )

    assert result.execution_report.executed is True
    assert result.execution_report.completion.completion_allowed is False
    assert result.execution_report.completion.blocked_reason == "one_step_scope_not_final"
    assert result.execution_report.subagents[0].runner_status == "succeeded"
    assert result.execution_report.subagents[1].runner_status == "not_invoked"


def test_fake_runner_failure_becomes_structured_blocked_report(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "failed",
                "completion_reason": "failed",
                "execution_status": "failed",
            }
        ),
        user_message="Implement one step",
    )

    assert result.state_snapshot.executed is True
    assert result.state_snapshot.planned_steps[0].evaluation_result["status"] == "blocked"
    assert result.execution_report.status.value == "blocked"


def test_invalid_structured_output_fails_closed(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "bad envelope",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": {"status": "succeeded"}},
            }
        ),
        user_message="Implement one step",
    )

    assert result.state_snapshot.planned_steps[0].runner_result["structured_output"]["validation_status"] == "invalid_structured_output"
    assert result.state_snapshot.planned_steps[0].evaluation_result["status"] == "invalid_structured_output"
    assert result.execution_report.completion.blocked_reason == "one_step_scope_not_final"


def test_no_real_network_or_llm_calls_are_required(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "offline",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": _valid_structured_output()},
            }
        ),
        user_message="Implement one step",
    )

    assert result.execution_report.executed is True
    assert result.state_snapshot.planned_steps[0].runner_result["actual_provider"] == "openrouter"
    assert result.state_snapshot.planned_steps[0].runner_result["actual_model"] == "xiaomi/mimo-v2.5-pro"


def test_runtime_factory_not_ready_fails_closed_without_second_step(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    runtime_factory = _runtime_factory(repo_root)

    class BlockedRuntimeFactory:
        def build(self, request):
            return replace(runtime_factory.build(request), actual_runtime_status="blocked")

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=BlockedRuntimeFactory(),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: {"output_text": "unexpected"}),
        user_message="Implement one step",
    )

    assert result.state_snapshot.planned_steps[0].runner_result["status"] == "blocked"
    assert result.state_snapshot.planned_steps[1].runner_result["status"] == "not_invoked"
