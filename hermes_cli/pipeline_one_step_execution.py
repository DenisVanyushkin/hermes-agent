"""Controlled one-step execution helper behind the explicit execution fuse."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hermes_cli.pipeline_evaluation import PipelineEvaluationRequest, evaluate_pipeline_step
from hermes_cli.pipeline_execution_fuse import (
    PipelineExecutionFuseResult,
    evaluate_pipeline_execution_fuse,
    evaluate_pipeline_reviewer_execution_fuse,
)
from hermes_cli.pipeline_report import build_pipeline_execution_report
from hermes_cli.pipeline_reviewer_packet import MACHINE_CAPTURED_TEST_STATUSES
from hermes_cli.pipeline_session import PipelineSession
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
    runner_request = _build_runner_request_from_runtime_plan(
        session=session,
        planned_step=step,
        runtime_plan=runtime_plan,
        execution_mode=fuse.execution_mode,
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
            runtime_factory_plan=runtime_plan.to_safe_dict(),
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


def execute_controlled_reviewer_one_step(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: SubagentRunner,
    prior_result: ControlledOneStepExecutionResult,
    user_message: str,
) -> ControlledOneStepExecutionResult:
    pipeline_spec = loaded_specs.pipeline_specs[session.pipeline_id]
    prior_snapshot = prior_result.state_snapshot
    fuse = evaluate_pipeline_reviewer_execution_fuse(
        config=config,
        session=session,
        state_snapshot=prior_snapshot,
    )
    if not fuse.actual_invocation_allowed:
        return ControlledOneStepExecutionResult(
            fuse=fuse,
            state_snapshot=prior_snapshot,
            execution_report=build_pipeline_execution_report(
                session=session,
                state_snapshot=prior_snapshot,
                preflight_result={"allowed": False, "reason_code": fuse.blocked_reason},
            ),
        )

    step = prior_snapshot.planned_steps[1]
    runtime_plan = runtime_factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id=step.subagent_id,
            pipeline_session_id=session.pipeline_session_id,
            invocation_id=f"{session.pipeline_session_id}:{step.step_kind}:one_step",
        )
    )
    runner_request = _build_runner_request_from_runtime_plan(
        session=session,
        planned_step=step,
        runtime_plan=runtime_plan,
        execution_mode=fuse.execution_mode,
    )
    invocation_request = SubagentInvocationRequest(
        subagent_id=step.subagent_id,
        pipeline_session_id=session.pipeline_session_id,
        invocation_id=f"{session.pipeline_session_id}:{step.step_kind}:one_step",
        input_messages=[{"role": "user", "content": user_message}],
        metadata={"execution_scope": fuse.execution_scope, "engineer_result_present": True},
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
            runtime_factory_plan=runtime_plan.to_safe_dict(),
            subagent_spec=loaded_specs.subagent_specs.get(step.subagent_id, {}),
            all_subagent_specs=getattr(loaded_specs, "subagent_specs", {}),
        )
    )
    updated_step = replace(
        step,
        execution_status="executed_one_step",
        planning_mode=fuse.execution_mode,
        runtime_factory_plan=runtime_plan.to_safe_dict(),
        runner_request=_runner_request_payload(runner_request, runtime_plan),
        runner_result=adapted_runner_result.to_safe_dict(),
        evaluation_result=evaluation.to_safe_dict(),
    )
    updated_steps = list(prior_snapshot.planned_steps)
    updated_steps[1] = updated_step
    updated_runtime_plans = list(prior_snapshot.runtime_factory_plans)
    if len(updated_runtime_plans) > 1:
        updated_runtime_plans[1] = runtime_plan.to_safe_dict()

    reviewer_complete = evaluation.status.value == "candidate_complete"
    completion_allowed = reviewer_complete and not evaluation.blockers
    final_snapshot = replace(
        prior_snapshot,
        state="reviewer_one_step_execution_complete",
        completion_reason="reviewer_one_step_executed",
        execution_mode=fuse.execution_mode,
        executed=True,
        completion_allowed=completion_allowed,
        completion_blocked_reason=None if completion_allowed else _reviewer_completion_blocked_reason(evaluation),
        final_verdict=(
            "controlled_reviewer_one_step_candidate_complete"
            if completion_allowed
            else "controlled_reviewer_one_step_blocked"
        ),
        planned_steps=updated_steps,
        runtime_factory_plans=updated_runtime_plans,
    )
    return ControlledOneStepExecutionResult(
        fuse=fuse,
        state_snapshot=final_snapshot,
        execution_report=build_pipeline_execution_report(
            session=session,
            state_snapshot=final_snapshot,
            preflight_result={"allowed": True, "reason_code": "reviewer_fuse_allowed"},
        ),
    )


def _adapt_runner_result(
    *,
    invocation_result: Any,
    runner_request: SubagentRunnerRequest,
    runtime_plan: Any,
) -> SubagentRunnerResult:
    structured_output = _structured_output_from_invocation(invocation_result.raw_metadata if invocation_result.ok else None)
    raw_tool_calls = _raw_tool_calls(invocation_result.raw_metadata)
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
        runtime_mode=_runtime_mode_from_invocation(invocation_result.raw_metadata, runtime_plan),
        real_provider_allowed=_real_provider_allowed_from_invocation(invocation_result.raw_metadata, runtime_plan),
        provider_policy_status=_provider_policy_status_from_invocation(invocation_result.raw_metadata, runtime_plan),
        usage_summary=SubagentUsageSummary(
            input_tokens=token_usage.get("input_tokens"),
            output_tokens=token_usage.get("output_tokens"),
            reasoning_tokens=token_usage.get("reasoning_tokens"),
            total_tokens=token_usage.get("total_tokens"),
        ),
        cache_summary=SubagentCacheSummary(),
        tool_call_summaries=[
            SubagentToolCallSummary(
                tool_name=str(tool_call.get("tool_name") or intent.get("name") or "unknown"),
                call_count=1,
                status=_tool_call_status(tool_call),
                result_payload=_tool_result_payload(tool_call),
            )
            for intent, tool_call in _paired_tool_calls(invocation_result.tool_intents, raw_tool_calls)
        ],
        elapsed_ms=invocation_result.record.elapsed_ms,
        artifacts_created=[],
        structured_output=structured_output,
        schema_validation_status=structured_output.validation_status if structured_output is not None else "not_applicable",
        raw_output_redacted=True,
    )


def _paired_tool_calls(
    tool_intents: list[dict[str, Any]] | None,
    raw_tool_calls: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    intents = list(tool_intents or [])
    if not raw_tool_calls:
        return [(intent, {}) for intent in intents]
    if len(raw_tool_calls) >= len(intents):
        padded_intents = intents + [{} for _ in range(len(raw_tool_calls) - len(intents))]
        return list(zip(padded_intents, raw_tool_calls))
    pairs = list(zip(intents[: len(raw_tool_calls)], raw_tool_calls))
    pairs.extend((intent, {}) for intent in intents[len(raw_tool_calls) :])
    return pairs


def _raw_tool_calls(raw_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(raw_metadata, dict):
        return []
    value = raw_metadata.get("tool_calls")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _tool_call_status(tool_call: dict[str, Any]) -> str:
    status = str(tool_call.get("status") or "").strip()
    return status or "planned"


def _tool_result_payload(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    if str(tool_call.get("tool_name") or "") != "pytest":
        return None
    payload = tool_call.get("result")
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "").strip()
    if status not in MACHINE_CAPTURED_TEST_STATUSES:
        return None
    return dict(payload)


def _structured_output_from_invocation(raw_metadata: dict[str, Any] | None) -> StructuredOutputEnvelope | None:
    if not isinstance(raw_metadata, dict):
        return None
    if "structured_output" in raw_metadata:
        return validate_structured_output_envelope(raw_metadata.get("structured_output"))
    if raw_metadata.get("structured_output_missing_reason") != "engineer_max_iterations_without_structured_output":
        return None
    output_text = raw_metadata.get("diagnostic_output_text")
    if not isinstance(output_text, str) or not output_text.strip():
        return None
    return StructuredOutputEnvelope(
        schema_version=None,
        subagent_id=None,
        role=None,
        status="blocked",
        summary=output_text.strip(),
        validation_status="missing_structured_output",
        validation_errors=[
            {
                "field": "payload",
                "message": "engineer_max_iterations_without_structured_output",
            }
        ],
    )


def _bridge_metadata(raw_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_metadata, dict):
        return {}
    metadata = raw_metadata.get("bridge_metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _runtime_mode_from_invocation(raw_metadata: dict[str, Any] | None, runtime_plan: Any) -> str:
    bridge_metadata = _bridge_metadata(raw_metadata)
    value = bridge_metadata.get("runtime_mode")
    if isinstance(value, str) and value.strip():
        return value
    runtime_mode = getattr(runtime_plan, "runtime_mode", None)
    if isinstance(runtime_mode, str) and runtime_mode.strip() and runtime_mode != "fake":
        return runtime_mode
    if getattr(runtime_plan, "actual_runtime_status", None) == "ready_to_construct":
        return "bridge_executor"
    return "fake"


def _real_provider_allowed_from_invocation(raw_metadata: dict[str, Any] | None, runtime_plan: Any) -> bool:
    bridge_metadata = _bridge_metadata(raw_metadata)
    if "real_provider_allowed" in bridge_metadata:
        return bool(bridge_metadata.get("real_provider_allowed"))
    if bool(getattr(runtime_plan, "real_provider_allowed", False)):
        return True
    return bool(
        getattr(runtime_plan, "actual_runtime_status", None) == "ready_to_construct"
        and getattr(runtime_plan, "constructor_provider", None)
        and getattr(runtime_plan, "constructor_model", None)
    )


def _provider_policy_status_from_invocation(raw_metadata: dict[str, Any] | None, runtime_plan: Any) -> str:
    bridge_metadata = _bridge_metadata(raw_metadata)
    value = bridge_metadata.get("provider_policy_status")
    if isinstance(value, str) and value.strip():
        return value
    provider_policy_status = getattr(runtime_plan, "provider_policy_status", None)
    if isinstance(provider_policy_status, str) and provider_policy_status.strip() and provider_policy_status != "not_requested":
        return provider_policy_status
    if getattr(runtime_plan, "actual_runtime_status", None) == "ready_to_construct":
        return "ready_to_construct"
    return "not_requested"


def _runner_request_payload(request: SubagentRunnerRequest, runtime_plan: Any) -> dict[str, Any]:
    payload = request.to_safe_dict()
    payload["status"] = "invoked_one_step"
    payload["actual_provider"] = runtime_plan.constructor_provider
    payload["actual_model"] = runtime_plan.constructor_model
    payload["actual_model_class"] = runtime_plan.selection.selected_model_class if runtime_plan.selection else None
    return payload


def _build_runner_request_from_runtime_plan(
    *,
    session: PipelineSession,
    planned_step: Any,
    runtime_plan: Any,
    execution_mode: str,
) -> SubagentRunnerRequest:
    return SubagentRunnerRequest(
        pipeline_session_id=session.pipeline_session_id,
        trace_id=session.trace_id,
        pipeline_id=session.pipeline_id,
        step_id=planned_step.step_kind,
        subagent_id=runtime_plan.subagent_id,
        role_id=planned_step.step_kind,
        runtime_factory_plan_id=f"{runtime_plan.pipeline_session_id}:{planned_step.step_kind}:{runtime_plan.subagent_id}",
        runtime_factory_status=runtime_plan.actual_runtime_status,
        execution_mode=execution_mode,
        prompt_input_hash=session.user_message_hash,
        actual_provider=runtime_plan.constructor_provider,
        actual_model=runtime_plan.constructor_model,
        actual_model_class=runtime_plan.selection.selected_model_class if runtime_plan.selection else None,
    )


def _reviewer_completion_blocked_reason(evaluation: Any) -> str:
    if getattr(evaluation, "status", None) is not None and evaluation.status.value == "invalid_structured_output":
        return "reviewer_invalid_structured_output"
    if getattr(evaluation, "blockers", None):
        return "review_blockers_present"
    completion = getattr(evaluation, "completion", None)
    blocked_reason = getattr(completion, "blocked_reason", None)
    if blocked_reason:
        return str(blocked_reason)
    return "reviewer_not_complete"
