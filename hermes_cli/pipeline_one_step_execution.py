"""Controlled one-step execution helper behind the explicit execution fuse."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hermes_cli.pipeline_evaluation import PipelineEvaluationRequest, evaluate_pipeline_step
from hermes_cli.pipeline_execution_fuse import (
    PipelineExecutionFuseResult,
    evaluate_pipeline_execution_fuse,
)
from hermes_cli.pipeline_report import build_pipeline_execution_report
from hermes_cli.pipeline_session import PipelineSession, PipelineStepPlan
from hermes_cli.pipeline_state_machine import PipelineStateSnapshot, build_pipeline_state_snapshot
from hermes_cli.runtime_factory import RuntimeBuildRequest
from hermes_cli.subagent_runner import (
    SubagentInvocationRequest,
    SubagentRunner,
    SubagentRunnerRequest,
    SubagentRunnerResult,
    SubagentRunnerStatus,
    StructuredOutputEnvelope,
    SubagentToolCallSummary,
    SubagentUsageSummary,
    SubagentCacheSummary,
    build_not_invoked_runner_result,
    build_subagent_runner_request,
    validate_structured_output_envelope,
)


@dataclass(frozen=True)
class ControlledOneStepExecutionResult:
    fuse: PipelineExecutionFuseResult
    state_snapshot: PipelineStateSnapshot
    execution_report: Any


def execute_controlled_one_step(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: SubagentRunner,
    user_message: str,
) -> ControlledOneStepExecutionResult:
    pipeline_spec = loaded_specs.pipeline_specs[session.pipeline_id]
    initial_snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=pipeline_spec,
        loaded_specs=loaded_specs,
    )
    fuse = evaluate_pipeline_execution_fuse(
        config=config,
        session=session,
        state_snapshot=initial_snapshot,
    )
    if not fuse.actual_invocation_allowed:
        return ControlledOneStepExecutionResult(
            fuse=fuse,
            state_snapshot=initial_snapshot,
            execution_report=build_pipeline_execution_report(
                session=session,
                state_snapshot=initial_snapshot,
                preflight_result={"allowed": False, "reason_code": fuse.blocked_reason},
            ),
        )

    step = initial_snapshot.planned_steps[0]
    runtime_plan = runtime_factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id=step.subagent_id,
            pipeline_session_id=session.pipeline_session_id,
            invocation_id=f"{session.pipeline_session_id}:{step.step_kind}:one_step",
        )
    )
    runner_request = build_subagent_runner_request(
        session=session,
        planned_step=step,
        runtime_factory_plan=replace(
            _metadata_runtime_plan(initial_snapshot, 0),
            execution_mode=fuse.execution_mode,
        ),
    )
    invocation_request = SubagentInvocationRequest(
        subagent_id=step.subagent_id,
        pipeline_session_id=session.pipeline_session_id,
        invocation_id=f"{session.pipeline_session_id}:{step.step_kind}:one_step",
        input_messages=[{"role": "user", "content": user_message}],
        metadata={"execution_scope": fuse.execution_scope},
    )
    invocation_result = runner.run(runtime_plan, invocation_request)
    adapted_runner_result = _adapt_runner_result(
        invocation_result=invocation_result,
        runner_request=runner_request,
        runtime_plan=runtime_plan,
    )
    evaluation = evaluate_pipeline_step(
        PipelineEvaluationRequest(
            pipeline_session_id=session.pipeline_session_id,
            trace_id=session.trace_id,
            pipeline_id=session.pipeline_id,
            step_id=step.step_kind,
            subagent_id=step.subagent_id,
            execution_mode=fuse.execution_mode,
            runner_result=adapted_runner_result,
            structured_output=adapted_runner_result.structured_output,
            pipeline_spec=pipeline_spec,
            runtime_factory_plan=initial_snapshot.runtime_factory_plans[0],
            subagent_spec=loaded_specs.subagent_specs.get(step.subagent_id, {}),
            all_subagent_specs=getattr(loaded_specs, "subagent_specs", {}),
        )
    )
    updated_step = replace(
        step,
        execution_status="executed_one_step",
        planning_mode=fuse.execution_mode,
        runner_request=_runner_request_payload(runner_request, runtime_plan),
        runner_result=adapted_runner_result.to_safe_dict(),
        evaluation_result=evaluation.to_safe_dict(),
    )
    updated_steps = list(initial_snapshot.planned_steps)
    updated_steps[0] = updated_step
    final_snapshot = replace(
        initial_snapshot,
        state="one_step_execution_complete",
        completion_reason="one_step_executed",
        execution_mode=fuse.execution_mode,
        executed=True,
        completion_allowed=False,
        completion_blocked_reason="one_step_scope_not_final",
        final_verdict="controlled_one_step_executed",
        planned_steps=updated_steps,
    )
    return ControlledOneStepExecutionResult(
        fuse=fuse,
        state_snapshot=final_snapshot,
        execution_report=build_pipeline_execution_report(
            session=session,
            state_snapshot=final_snapshot,
            preflight_result={"allowed": True, "reason_code": "fuse_allowed"},
        ),
    )


def _metadata_runtime_plan(snapshot: PipelineStateSnapshot, index: int):
    from hermes_cli.runtime_factory import RuntimeFactoryPlan, RuntimeFactoryStatus, RuntimeToolPolicy, RuntimeEnvironmentPolicy

    payload = snapshot.runtime_factory_plans[index]
    return RuntimeFactoryPlan(
        pipeline_session_id=payload["pipeline_session_id"],
        trace_id=payload["trace_id"],
        pipeline_id=payload["pipeline_id"],
        subagent_id=payload["subagent_id"],
        role_id=payload["role_id"],
        status=RuntimeFactoryStatus(payload["status"]),
        execution_mode=payload["execution_mode"],
        dry_run=bool(payload["dry_run"]),
        provider=payload["provider"],
        model=payload["model"],
        model_class=payload["model_class"],
        system_prompt_source_id=payload["system_prompt_source_id"],
        system_prompt_path=payload["system_prompt_path"],
        tool_set=list(payload["tool_set"]),
        tool_policy=RuntimeToolPolicy(**payload["tool_policy"]),
        environment_policy=RuntimeEnvironmentPolicy(**payload["environment_policy"]),
        context_window_policy=dict(payload["context_window_policy"]),
        prompt_cache_policy=dict(payload["prompt_cache_policy"]),
        logging_hooks_policy=dict(payload["logging_hooks_policy"]),
        token_accounting_policy=dict(payload["token_accounting_policy"]),
        safety_gates=dict(payload["safety_gates"]),
        errors=[],
    )


def _adapt_runner_result(
    *,
    invocation_result: Any,
    runner_request: SubagentRunnerRequest,
    runtime_plan: Any,
) -> SubagentRunnerResult:
    structured_output = _structured_output_from_invocation(invocation_result.raw_metadata if invocation_result.ok else None)
    if invocation_result.ok:
        if invocation_result.execution_status == "completed":
            status = SubagentRunnerStatus.SUCCEEDED
        elif invocation_result.execution_status == "failed":
            status = SubagentRunnerStatus.FAILED
        else:
            status = SubagentRunnerStatus.BLOCKED
    else:
        status = SubagentRunnerStatus.BLOCKED if invocation_result.execution_status == "rejected" else SubagentRunnerStatus.FAILED

    token_usage = invocation_result.token_usage or {}
    return SubagentRunnerResult(
        pipeline_session_id=runner_request.pipeline_session_id,
        trace_id=runner_request.trace_id,
        pipeline_id=runner_request.pipeline_id,
        step_id=runner_request.step_id,
        subagent_id=runner_request.subagent_id,
        role_id=runner_request.role_id,
        runtime_factory_plan_id=runner_request.runtime_factory_plan_id,
        runtime_factory_status=runner_request.runtime_factory_status,
        status=status,
        failure_reason=None if invocation_result.ok else (invocation_result.error_code or invocation_result.completion_reason),
        actual_provider=runtime_plan.constructor_provider,
        actual_model=runtime_plan.constructor_model,
        actual_model_class=runtime_plan.selection.selected_model_class if runtime_plan.selection else None,
        usage_summary=SubagentUsageSummary(
            input_tokens=token_usage.get("input_tokens"),
            output_tokens=token_usage.get("output_tokens"),
            reasoning_tokens=token_usage.get("reasoning_tokens"),
            total_tokens=token_usage.get("total_tokens"),
        ),
        cache_summary=SubagentCacheSummary(),
        tool_call_summaries=[
            SubagentToolCallSummary(
                tool_name=str(intent.get("name") or "unknown"),
                call_count=1,
                status="planned",
            )
            for intent in invocation_result.tool_intents
        ],
        elapsed_ms=invocation_result.record.elapsed_ms,
        artifacts_created=[],
        structured_output=structured_output,
        schema_validation_status=structured_output.validation_status if structured_output is not None else "not_applicable",
        raw_output_redacted=True,
    )


def _structured_output_from_invocation(raw_metadata: dict[str, Any] | None) -> StructuredOutputEnvelope | None:
    if not isinstance(raw_metadata, dict) or "structured_output" not in raw_metadata:
        return None
    return validate_structured_output_envelope(raw_metadata.get("structured_output"))


def _runner_request_payload(request: SubagentRunnerRequest, runtime_plan: Any) -> dict[str, Any]:
    payload = request.to_safe_dict()
    payload["status"] = "invoked_one_step"
    payload["actual_provider"] = runtime_plan.constructor_provider
    payload["actual_model"] = runtime_plan.constructor_model
    payload["actual_model_class"] = runtime_plan.selection.selected_model_class if runtime_plan.selection else None
    return payload
