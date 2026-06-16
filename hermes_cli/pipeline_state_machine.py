"""Metadata-only state-machine boundary for observe-mode pipeline planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes_cli.pipeline_session import (
    ENGINEERING_PIPELINE_ID,
    PipelineSession,
    PipelineSessionStatus,
    PipelineStepPlan,
)
from hermes_cli.runtime_factory import build_runtime_factory_plan
from hermes_cli.subagent_runner import (
    build_not_invoked_runner_result,
    build_subagent_runner_request,
)


@dataclass(frozen=True)
class PipelineStateSnapshot:
    pipeline_session_id: str
    pipeline_id: str
    status: str
    state: str
    transition_path: list[str]
    completion_reason: str
    execution_mode: str
    executed: bool
    completion_allowed: bool
    completion_blocked_reason: str | None
    final_verdict: str
    router_status: str
    reviewer_condition: str | None
    selected_subagent_ids: list[str] = field(default_factory=list)
    planned_steps: list[PipelineStepPlan] = field(default_factory=list)
    runtime_factory_plans: list[dict[str, Any]] = field(default_factory=list)
    loop_policy: dict[str, Any] = field(default_factory=dict)


def build_pipeline_state_snapshot(
    *,
    session: PipelineSession,
    pipeline_spec: dict[str, Any],
    loaded_specs: Any | None = None,
) -> PipelineStateSnapshot:
    loop_policy = dict(pipeline_spec.get("loop_policy") or {})
    enriched_steps, runtime_factory_plans = _build_step_contracts(
        session=session,
        pipeline_spec=pipeline_spec,
        planned_steps=list(session.planned_steps),
        loaded_specs=loaded_specs,
    )
    if session.pipeline_id == ENGINEERING_PIPELINE_ID:
        transition_path = [
            "task_received",
            "engineering_review_pipeline_selected",
            "engineer_step_planned",
            "reviewer_step_planned_if_code_changes",
            "preflight_blocked_execution",
        ]
        final_verdict = "observe_engineering_preflight_blocked"
    else:
        transition_path = [
            "task_received",
            "default_pipeline_selected",
            "response_planned",
            "preflight_blocked_execution",
        ]
        final_verdict = "observe_default_preflight_blocked"

    return PipelineStateSnapshot(
        pipeline_session_id=session.pipeline_session_id,
        pipeline_id=session.pipeline_id,
        status=PipelineSessionStatus.PLANNED.value,
        state="preflight_blocked_execution",
        transition_path=transition_path,
        completion_reason="plan_only",
        execution_mode="observe_plan_only",
        executed=False,
        completion_allowed=True,
        completion_blocked_reason="execution_disabled",
        final_verdict=final_verdict,
        router_status=session.router_status,
        reviewer_condition=session.reviewer_condition,
        selected_subagent_ids=list(session.selected_subagent_ids),
        planned_steps=enriched_steps,
        runtime_factory_plans=runtime_factory_plans,
        loop_policy=loop_policy,
    )


def _build_step_contracts(
    *,
    session: PipelineSession,
    pipeline_spec: dict[str, Any],
    planned_steps: list[PipelineStepPlan],
    loaded_specs: Any | None,
) -> tuple[list[PipelineStepPlan], list[dict[str, Any]]]:
    if loaded_specs is None:
        from hermes_cli.pipeline_specs import load_pipeline_specs

        loaded_specs = load_pipeline_specs()

    subagent_specs = getattr(loaded_specs, "subagent_specs", {})
    runtime_factory_plans: list[dict[str, Any]] = []
    enriched_steps: list[PipelineStepPlan] = []
    for step in planned_steps:
        runtime_factory_plan = build_runtime_factory_plan(
            session=session,
            planned_step=step,
            subagent_spec=subagent_specs.get(step.subagent_id) if isinstance(subagent_specs, dict) else None,
            config=pipeline_spec,
        )
        runtime_payload = runtime_factory_plan.to_safe_dict()
        runtime_factory_plans.append(runtime_payload)

        if session.pipeline_id != ENGINEERING_PIPELINE_ID:
            enriched_steps.append(step)
            continue

        runner_request = build_subagent_runner_request(
            session=session,
            planned_step=step,
            runtime_factory_plan=runtime_factory_plan,
        )
        runner_result = build_not_invoked_runner_result(
            request=runner_request,
            runtime_factory_plan=runtime_factory_plan,
        )
        enriched_steps.append(
            PipelineStepPlan(
                step_kind=step.step_kind,
                subagent_id=step.subagent_id,
                condition=step.condition,
                execution_status=step.execution_status,
                planning_mode=step.planning_mode,
                runtime_factory_plan=runtime_payload,
                runner_request=runner_request.to_safe_dict(),
                runner_result=runner_result.to_safe_dict(),
            )
        )

    return enriched_steps, runtime_factory_plans
