"""Bounded engineer-reviewer rework loop harness behind an explicit loop fuse."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hermes_cli.pipeline_control_channel import resolve_loop_limit_policy
from hermes_cli.pipeline_evaluation import PipelineEvaluationRequest, evaluate_pipeline_step
from hermes_cli.pipeline_execution_fuse import (
    ENGINEER_SUBAGENT_ID,
    REVIEWER_SUBAGENT_ID,
    PipelineExecutionFuseResult,
    evaluate_pipeline_execution_fuse,
    evaluate_pipeline_reviewer_execution_fuse,
)
from hermes_cli.pipeline_one_step_execution import (
    ControlledOneStepExecutionResult,
    _adapt_runner_result,
    _build_runner_request_from_runtime_plan,
)
from hermes_cli.pipeline_git_delta import GitMaterialChangeResult, GitSnapshot, capture_git_snapshot, compare_git_snapshots
from hermes_cli.pipeline_report import build_pipeline_execution_report
from hermes_cli.pipeline_reviewer_packet import build_reviewer_packet
from hermes_cli.pipeline_session import PipelineSession
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot
from hermes_cli.runtime_factory import RuntimeBuildRequest
from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner


SAFE_FALLBACK_MAX_REVIEW_ITERATIONS = 1
REVIEWER_APPROVAL_STATUS = "candidate_complete"


@dataclass(frozen=True)
class ReworkLoopIterationRecord:
    iteration_index: int
    engineer_message: str
    reviewer_message: str
    engineer_runner_status: str
    reviewer_runner_status: str
    engineer_evaluation_status: str
    reviewer_evaluation_status: str
    reviewer_blockers: list[str]
    loop_limit_snapshot: dict[str, Any]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "iteration_index": self.iteration_index,
            "engineer_message": self.engineer_message,
            "reviewer_message": self.reviewer_message,
            "engineer_runner_status": self.engineer_runner_status,
            "reviewer_runner_status": self.reviewer_runner_status,
            "engineer_evaluation_status": self.engineer_evaluation_status,
            "reviewer_evaluation_status": self.reviewer_evaluation_status,
            "reviewer_blockers": list(self.reviewer_blockers),
            "loop_limit_snapshot": dict(self.loop_limit_snapshot),
        }


@dataclass(frozen=True)
class PipelineReworkLoopResult:
    fuse: PipelineExecutionFuseResult
    state_snapshot: Any
    execution_report: Any
    iteration_history: list[ReworkLoopIterationRecord]
    review_iterations_completed: int
    max_review_iterations: int
    policy_source: str
    original_task: str
    appended_rework_context: list[str]
    completion_allowed: bool
    candidate_complete: bool
    user_action_required: bool
    blocked_reason: str | None
    git_gate: dict[str, Any]
    reviewer_packet: dict[str, Any]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "fuse": self.fuse.to_safe_dict(),
            "iteration_history": [item.to_safe_dict() for item in self.iteration_history],
            "review_iterations_completed": self.review_iterations_completed,
            "max_review_iterations": self.max_review_iterations,
            "policy_source": self.policy_source,
            "original_task": self.original_task,
            "appended_rework_context": list(self.appended_rework_context),
            "completion_allowed": self.completion_allowed,
            "candidate_complete": self.candidate_complete,
            "user_action_required": self.user_action_required,
            "blocked_reason": self.blocked_reason,
            "git_gate": dict(self.git_gate),
            "reviewer_packet": dict(self.reviewer_packet),
        }


def execute_bounded_rework_loop(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: SubagentRunner,
    user_message: str,
    repo_path: str | None = None,
    test_summary: Any = None,
    allow_completion_after_review: bool = False,
) -> PipelineReworkLoopResult:
    pipeline_spec = loaded_specs.pipeline_specs[session.pipeline_id]
    initial_snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=pipeline_spec,
        loaded_specs=loaded_specs,
    )
    fuse = evaluate_pipeline_rework_loop_fuse(
        config=config,
        session=session,
        state_snapshot=initial_snapshot,
        pipeline_spec=pipeline_spec,
    )
    if not fuse.actual_invocation_allowed:
        return _blocked_loop_result(
            fuse=fuse,
            session=session,
            snapshot=initial_snapshot,
            original_task=user_message,
            appended_rework_context=[],
            iteration_history=[],
            review_iterations_completed=0,
            max_review_iterations=_coerce_positive_int(getattr(fuse, "max_review_iterations", None), SAFE_FALLBACK_MAX_REVIEW_ITERATIONS),
            policy_source=getattr(fuse, "loop_policy_source", "default"),
            blocked_reason=fuse.blocked_reason,
            user_action_required=False,
            git_gate=_disabled_git_gate(),
            reviewer_packet=_disabled_reviewer_packet(),
        )

    appended_rework_context: list[str] = []
    iteration_history: list[ReworkLoopIterationRecord] = []
    current_snapshot = initial_snapshot
    review_iterations_completed = 0
    max_review_iterations = _coerce_positive_int(getattr(fuse, "max_review_iterations", None), SAFE_FALLBACK_MAX_REVIEW_ITERATIONS)
    policy_source = getattr(fuse, "loop_policy_source", "default")
    baseline_snapshot = capture_git_snapshot(repo_path) if repo_path else None
    current_git_gate = _disabled_git_gate() if not repo_path else _git_gate_from_snapshots(
        baseline_snapshot=baseline_snapshot,
        post_snapshot=None,
        git_result=None,
    )
    current_reviewer_packet = _disabled_reviewer_packet() if not repo_path else _absent_reviewer_packet()

    while True:
        loop_snapshot = {
            "review_iterations_completed": review_iterations_completed,
            "max_review_iterations": max_review_iterations,
            "policy_source": policy_source,
        }
        engineer_message = _compose_engineer_message(
            original_task=user_message,
            appended_rework_context=appended_rework_context,
        )
        engineer_result = _execute_step(
            session=session,
            loaded_specs=loaded_specs,
            runtime_factory=runtime_factory,
            runner=runner,
            pipeline_spec=pipeline_spec,
            current_snapshot=current_snapshot,
            step_index=0,
            step_kind="engineer",
            user_message=engineer_message,
            metadata={
                "execution_scope": fuse.execution_scope,
                "loop_allowed": True,
                "review_iterations_completed": review_iterations_completed,
            },
        )
        current_snapshot = engineer_result.state_snapshot
        engineer_output = _step_structured_output(current_snapshot, 0)
        post_snapshot: GitSnapshot | None = None
        git_result: GitMaterialChangeResult | None = None
        if baseline_snapshot is not None:
            post_snapshot = capture_git_snapshot(repo_path)
            git_result = compare_git_snapshots(baseline_snapshot, post_snapshot)
            current_git_gate = _git_gate_from_snapshots(
                baseline_snapshot=baseline_snapshot,
                post_snapshot=post_snapshot,
                git_result=git_result,
            )
            current_reviewer_packet = _reviewer_packet_metadata(
                packet=build_reviewer_packet(
                    pipeline_id=session.pipeline_id,
                    session_id=session.session_id,
                    task_summary=user_message,
                    engineer_output=engineer_output,
                    baseline_snapshot=baseline_snapshot,
                    post_snapshot=post_snapshot,
                    git_result=git_result,
                    test_summary=test_summary,
                )
            )
        engineer_fail_closed_reason = _engineer_fail_closed_reason(current_snapshot)
        if engineer_fail_closed_reason is not None:
            return _blocked_loop_result(
                fuse=fuse,
                session=session,
                snapshot=current_snapshot,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                blocked_reason=engineer_fail_closed_reason,
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )
        if git_result is not None and _git_result_blocks_completion(git_result):
            return _blocked_loop_result(
                fuse=fuse,
                session=session,
                snapshot=current_snapshot,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                blocked_reason=git_result.blocked_reason or git_result.status,
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )
        if git_result is not None and not git_result.review_required and not git_result.material_changes_present:
            final_snapshot = replace(
                current_snapshot,
                state="rework_loop_candidate_complete",
                completion_reason="rework_loop_candidate_complete",
                executed=True,
                completion_allowed=True,
                completion_blocked_reason=None,
                final_verdict="controlled_rework_loop_candidate_complete",
            )
            return PipelineReworkLoopResult(
                fuse=fuse,
                state_snapshot=final_snapshot,
                execution_report=build_pipeline_execution_report(
                    session=session,
                    state_snapshot=final_snapshot,
                    preflight_result={"allowed": True, "reason_code": "rework_loop_fuse_allowed"},
                ),
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                completion_allowed=True,
                candidate_complete=True,
                user_action_required=False,
                blocked_reason=None,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )

        reviewer_fuse = evaluate_pipeline_reviewer_execution_fuse(
            config=config,
            session=session,
            state_snapshot=current_snapshot,
        )
        if not reviewer_fuse.actual_invocation_allowed:
            return _blocked_loop_result(
                fuse=fuse,
                session=session,
                snapshot=current_snapshot,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                blocked_reason=reviewer_fuse.blocked_reason,
                user_action_required=False,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )

        reviewer_message = _compose_reviewer_message(
            original_task=user_message,
            engineer_message=engineer_message,
            appended_rework_context=appended_rework_context,
        )
        reviewer_result = _execute_step(
            session=session,
            loaded_specs=loaded_specs,
            runtime_factory=runtime_factory,
            runner=runner,
            pipeline_spec=pipeline_spec,
            current_snapshot=current_snapshot,
            step_index=1,
            step_kind="reviewer",
            user_message=reviewer_message,
            metadata={
                "execution_scope": reviewer_fuse.execution_scope,
                "engineer_result_present": True,
                "loop_allowed": True,
                "review_iterations_completed": review_iterations_completed,
            },
        )
        current_snapshot = reviewer_result.state_snapshot
        reviewer_step = reviewer_result.state_snapshot.planned_steps[1]
        reviewer_eval = getattr(reviewer_step, "evaluation_result", None) or {}
        reviewer_blockers = list(reviewer_eval.get("blockers") or [])
        reviewer_status = str(reviewer_eval.get("status") or "not_evaluated")
        engineer_step = engineer_result.state_snapshot.planned_steps[0]

        review_iterations_completed += 1
        iteration_history.append(
            ReworkLoopIterationRecord(
                iteration_index=review_iterations_completed,
                engineer_message=engineer_message,
                reviewer_message=reviewer_message,
                engineer_runner_status=_step_runner_status(engineer_step),
                reviewer_runner_status=_step_runner_status(reviewer_step),
                engineer_evaluation_status=_step_evaluation_status(engineer_step),
                reviewer_evaluation_status=reviewer_status,
                reviewer_blockers=reviewer_blockers,
                loop_limit_snapshot=dict(loop_snapshot),
            )
        )

        reviewer_fail_closed_reason = _reviewer_fail_closed_reason(
            reviewer_status=reviewer_status,
            reviewer_blockers=reviewer_blockers,
        )
        if reviewer_fail_closed_reason is not None:
            final_snapshot = replace(
                current_snapshot,
                state="rework_loop_reviewer_fail_closed",
                completion_reason=reviewer_fail_closed_reason,
                executed=True,
                completion_allowed=False,
                completion_blocked_reason=reviewer_fail_closed_reason,
                final_verdict="controlled_rework_loop_reviewer_fail_closed",
            )
            return PipelineReworkLoopResult(
                fuse=fuse,
                state_snapshot=final_snapshot,
                execution_report=build_pipeline_execution_report(
                    session=session,
                    state_snapshot=final_snapshot,
                    preflight_result={"allowed": True, "reason_code": "rework_loop_fuse_allowed"},
                ),
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                completion_allowed=False,
                candidate_complete=False,
                user_action_required=True,
                blocked_reason=reviewer_fail_closed_reason,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )

        # Approval requires positive reviewer candidate_complete verdict; absence of blockers is not sufficient.
        if reviewer_status == REVIEWER_APPROVAL_STATUS and not reviewer_blockers:
            completion_allowed = allow_completion_after_review and not (git_result and _git_result_blocks_completion(git_result))
            blocked_reason = None if completion_allowed else "loop_harness_not_live_final"
            final_snapshot = replace(
                current_snapshot,
                state="rework_loop_candidate_complete",
                completion_reason="rework_loop_candidate_complete",
                executed=True,
                completion_allowed=completion_allowed,
                completion_blocked_reason=blocked_reason,
                final_verdict="controlled_rework_loop_candidate_complete",
            )
            return PipelineReworkLoopResult(
                fuse=fuse,
                state_snapshot=final_snapshot,
                execution_report=build_pipeline_execution_report(
                    session=session,
                    state_snapshot=final_snapshot,
                    preflight_result={"allowed": True, "reason_code": "rework_loop_fuse_allowed"},
                ),
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                completion_allowed=completion_allowed,
                candidate_complete=True,
                user_action_required=False,
                blocked_reason=blocked_reason,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )

        if review_iterations_completed >= max_review_iterations:
            final_snapshot = replace(
                current_snapshot,
                state="rework_loop_limit_blocked",
                completion_reason="review_loop_limit_exceeded",
                executed=True,
                completion_allowed=False,
                completion_blocked_reason="review_loop_limit_exceeded",
                final_verdict="controlled_rework_loop_limit_blocked",
            )
            return PipelineReworkLoopResult(
                fuse=fuse,
                state_snapshot=final_snapshot,
                execution_report=build_pipeline_execution_report(
                    session=session,
                    state_snapshot=final_snapshot,
                    preflight_result={"allowed": True, "reason_code": "rework_loop_fuse_allowed"},
                ),
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                completion_allowed=False,
                candidate_complete=False,
                user_action_required=True,
                blocked_reason="review_loop_limit_exceeded",
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
            )

        appended_rework_context.append(_format_blocker_context(review_iterations_completed, reviewer_blockers))


def evaluate_pipeline_rework_loop_fuse(
    *,
    config: dict[str, Any] | None,
    session: Any,
    state_snapshot: Any,
    pipeline_spec: dict[str, Any] | None,
) -> PipelineExecutionFuseResult:
    engineer_fuse = evaluate_pipeline_execution_fuse(
        config=config,
        session=session,
        state_snapshot=state_snapshot,
    )
    if not engineer_fuse.actual_invocation_allowed:
        return engineer_fuse

    reviewer_fuse = evaluate_pipeline_reviewer_execution_fuse(
        config=config,
        session=session,
        state_snapshot=_reviewer_prereq_satisfied_snapshot(state_snapshot),
    )
    if not reviewer_fuse.actual_invocation_allowed:
        return reviewer_fuse

    requirements_met = list(engineer_fuse.requirements_met)
    requirements_met.extend(item for item in reviewer_fuse.requirements_met if item not in requirements_met)
    requirements_failed: list[str] = []

    if not bool(config and config.get("pipelines", {}).get("execution", {}).get("allow_actual_rework_loop", False)):
        requirements_failed.append("allow_actual_rework_loop")
        return _loop_blocked_result(
            base=reviewer_fuse,
            blocked_reason="rework_loop_fuse_disabled",
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("allow_actual_rework_loop")

    allowed_subagents = list(config.get("pipelines", {}).get("execution", {}).get("allowed_subagents", []) or [])
    if ENGINEER_SUBAGENT_ID not in allowed_subagents or REVIEWER_SUBAGENT_ID not in allowed_subagents:
        requirements_failed.append("required_loop_subagents_allowed")
        return _loop_blocked_result(
            base=reviewer_fuse,
            blocked_reason="unsupported_subagent",
            requirements_met=requirements_met,
            requirements_failed=requirements_failed,
        )
    requirements_met.append("required_loop_subagents_allowed")

    loop_policy = resolve_loop_limit_policy(pipeline_spec)
    max_review_iterations = loop_policy.max_review_iterations or SAFE_FALLBACK_MAX_REVIEW_ITERATIONS
    return replace(
        reviewer_fuse,
        actual_invocation_allowed=True,
        blocked_reason=None,
        execution_scope="bounded_rework_loop_only",
        reviewer_allowed=True,
        loop_allowed=True,
        requirements_met=requirements_met,
        requirements_failed=requirements_failed,
        max_review_iterations=max_review_iterations,
        loop_policy_source=loop_policy.policy_source,
    )


def _execute_step(
    *,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: SubagentRunner,
    pipeline_spec: dict[str, Any],
    current_snapshot: Any,
    step_index: int,
    step_kind: str,
    user_message: str,
    metadata: dict[str, Any],
) -> ControlledOneStepExecutionResult:
    step = current_snapshot.planned_steps[step_index]
    runtime_plan = runtime_factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id=step.subagent_id,
            pipeline_session_id=session.pipeline_session_id,
            invocation_id=f"{session.pipeline_session_id}:{step_kind}:loop:{step_index}",
        )
    )
    runner_request = _build_runner_request_from_runtime_plan(
        session=session,
        planned_step=step,
        runtime_plan=runtime_plan,
        execution_mode="controlled_one_step",
    )
    invocation_request = SubagentInvocationRequest(
        subagent_id=step.subagent_id,
        pipeline_session_id=session.pipeline_session_id,
        invocation_id=f"{session.pipeline_session_id}:{step_kind}:loop:{step_index}",
        input_messages=[{"role": "user", "content": user_message}],
        metadata=metadata,
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
            execution_mode="controlled_one_step",
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
        planning_mode="controlled_one_step",
        runtime_factory_plan=runtime_plan.to_safe_dict(),
        runner_request=runner_request.to_safe_dict(),
        runner_result=adapted_runner_result.to_safe_dict(),
        evaluation_result=evaluation.to_safe_dict(),
    )
    updated_steps = list(current_snapshot.planned_steps)
    updated_steps[step_index] = updated_step
    updated_runtime_plans = list(current_snapshot.runtime_factory_plans)
    if len(updated_runtime_plans) > step_index:
        updated_runtime_plans[step_index] = runtime_plan.to_safe_dict()
    next_snapshot = replace(
        current_snapshot,
        executed=True,
        execution_mode="controlled_one_step",
        state=f"{step_kind}_loop_step_complete",
        completion_reason=f"{step_kind}_loop_step_executed",
        completion_allowed=False,
        completion_blocked_reason="loop_harness_not_live_final",
        final_verdict=f"{step_kind}_loop_step_executed",
        planned_steps=updated_steps,
        runtime_factory_plans=updated_runtime_plans,
    )
    return ControlledOneStepExecutionResult(
        fuse=PipelineExecutionFuseResult(
            execution_mode="controlled_one_step",
            actual_invocation_allowed=True,
            blocked_reason=None,
            selected_pipeline_id=session.pipeline_id,
            selected_step_kind=step_kind,
            selected_subagent_id=step.subagent_id,
        ),
        state_snapshot=next_snapshot,
        execution_report=build_pipeline_execution_report(
            session=session,
            state_snapshot=next_snapshot,
            preflight_result={"allowed": True, "reason_code": "rework_loop_step_executed"},
        ),
    )


def _loop_blocked_result(
    *,
    base: PipelineExecutionFuseResult,
    blocked_reason: str,
    requirements_met: list[str],
    requirements_failed: list[str],
) -> PipelineExecutionFuseResult:
    return replace(
        base,
        actual_invocation_allowed=False,
        blocked_reason=blocked_reason,
        execution_scope="bounded_rework_loop_only",
        reviewer_allowed=True,
        loop_allowed=False,
        model_escalation_allowed=False,
        requirements_met=requirements_met,
        requirements_failed=requirements_failed,
    )


def _reviewer_prereq_satisfied_snapshot(state_snapshot: Any) -> Any:
    steps = list(state_snapshot.planned_steps)
    engineer_step = steps[0]
    steps[0] = replace(
        engineer_step,
        runner_result={
            "status": "succeeded",
            "structured_output": {"validation_status": "valid"},
        },
        evaluation_result={"status": "candidate_complete", "completion": {"candidate_complete": True}},
    )
    return replace(state_snapshot, planned_steps=steps)


def _blocked_loop_result(
    *,
    fuse: PipelineExecutionFuseResult,
    session: PipelineSession,
    snapshot: Any,
    original_task: str,
    appended_rework_context: list[str],
    iteration_history: list[ReworkLoopIterationRecord],
    review_iterations_completed: int,
    max_review_iterations: int,
    policy_source: str,
    blocked_reason: str | None,
    user_action_required: bool,
    git_gate: dict[str, Any],
    reviewer_packet: dict[str, Any],
) -> PipelineReworkLoopResult:
    return PipelineReworkLoopResult(
        fuse=fuse,
        state_snapshot=snapshot,
        execution_report=build_pipeline_execution_report(
            session=session,
            state_snapshot=snapshot,
            preflight_result={"allowed": False, "reason_code": blocked_reason},
        ),
        iteration_history=iteration_history,
        review_iterations_completed=review_iterations_completed,
        max_review_iterations=max_review_iterations,
        policy_source=policy_source,
        original_task=original_task,
        appended_rework_context=appended_rework_context,
        completion_allowed=False,
        candidate_complete=False,
        user_action_required=user_action_required,
        blocked_reason=blocked_reason,
        git_gate=git_gate,
        reviewer_packet=reviewer_packet,
    )


def _disabled_git_gate() -> dict[str, Any]:
    return {
        "status": "disabled",
        "enabled": False,
        "baseline_capture_status": "not_configured",
        "post_capture_status": "not_configured",
        "material_change_status": "not_configured",
        "material_changes_present": False,
        "review_required": False,
        "completion_blocked_reason": None,
        "changed_files": [],
        "head_changed": False,
        "baseline_dirty": False,
    }


def _git_gate_from_snapshots(
    *,
    baseline_snapshot: GitSnapshot | None,
    post_snapshot: GitSnapshot | None,
    git_result: GitMaterialChangeResult | None,
) -> dict[str, Any]:
    if baseline_snapshot is None:
        return _disabled_git_gate()
    return {
        "status": "enabled",
        "enabled": True,
        "baseline_capture_status": baseline_snapshot.capture_status,
        "post_capture_status": post_snapshot.capture_status if post_snapshot is not None else "not_captured",
        "material_change_status": git_result.status if git_result is not None else "not_evaluated",
        "material_changes_present": bool(git_result.material_changes_present) if git_result is not None else False,
        "review_required": bool(git_result.review_required) if git_result is not None else False,
        "completion_blocked_reason": git_result.blocked_reason if git_result is not None else None,
        "changed_files": list(git_result.changed_files) if git_result is not None else [],
        "head_changed": bool(git_result.head_changed) if git_result is not None else False,
        "baseline_dirty": bool(git_result.baseline_dirty if git_result is not None else baseline_snapshot.is_dirty),
    }


def _disabled_reviewer_packet() -> dict[str, Any]:
    return {
        "present": False,
        "packet_status": "disabled",
        "review_required": False,
        "user_action_required": False,
        "blocked_reason": None,
        "safe_packet": None,
    }


def _absent_reviewer_packet() -> dict[str, Any]:
    return {
        "present": False,
        "packet_status": "not_built",
        "review_required": False,
        "user_action_required": False,
        "blocked_reason": None,
        "safe_packet": None,
    }


def _reviewer_packet_metadata(*, packet: Any) -> dict[str, Any]:
    safe_packet = packet.to_safe_dict()
    return {
        "present": True,
        "packet_status": safe_packet.get("packet_status"),
        "review_required": bool(safe_packet.get("review_required")),
        "user_action_required": bool(safe_packet.get("user_action_required")),
        "blocked_reason": safe_packet.get("blocked_reason"),
        "safe_packet": safe_packet,
    }


def _compose_engineer_message(*, original_task: str, appended_rework_context: list[str]) -> str:
    if not appended_rework_context:
        return original_task
    return "\n\n".join([original_task, *appended_rework_context])


def _compose_reviewer_message(
    *,
    original_task: str,
    engineer_message: str,
    appended_rework_context: list[str],
) -> str:
    parts = [original_task, "Engineer candidate follows.", engineer_message]
    if appended_rework_context:
        parts.extend(appended_rework_context)
    return "\n\n".join(parts)


def _format_blocker_context(iteration_index: int, blockers: list[str]) -> str:
    return f"Reviewer blockers after iteration {iteration_index}: " + "; ".join(blockers)


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default


def _step_runner_status(step: Any) -> str:
    runner_result = getattr(step, "runner_result", None) or {}
    return str(runner_result.get("status") or "not_invoked")


def _step_evaluation_status(step: Any) -> str:
    evaluation_result = getattr(step, "evaluation_result", None) or {}
    return str(evaluation_result.get("status") or "not_evaluated")


def _step_structured_output(state_snapshot: Any, step_index: int) -> dict[str, Any]:
    planned_steps = list(getattr(state_snapshot, "planned_steps", []) or [])
    if len(planned_steps) <= step_index:
        return {}
    runner_result = getattr(planned_steps[step_index], "runner_result", None) or {}
    structured_output = runner_result.get("structured_output")
    return structured_output if isinstance(structured_output, dict) else {}


def _git_result_blocks_completion(git_result: GitMaterialChangeResult) -> bool:
    return git_result.status in {
        "baseline_invalid",
        "post_snapshot_invalid",
        "git_unavailable",
    } or bool(git_result.baseline_dirty)


def _engineer_fail_closed_reason(state_snapshot: Any) -> str | None:
    planned_steps = list(getattr(state_snapshot, "planned_steps", []) or [])
    if not planned_steps:
        return "engineer_result_missing"
    engineer_step = planned_steps[0]
    runner_status = _step_runner_status(engineer_step)
    evaluation_status = _step_evaluation_status(engineer_step)
    if runner_status == "not_invoked":
        return "engineer_result_missing"
    if runner_status != "succeeded":
        return "engineer_result_failed"
    if evaluation_status != REVIEWER_APPROVAL_STATUS:
        return "engineer_result_invalid"
    return None


def _reviewer_fail_closed_reason(*, reviewer_status: str, reviewer_blockers: list[str]) -> str | None:
    if reviewer_status == REVIEWER_APPROVAL_STATUS:
        return None if not reviewer_blockers else "reviewer_verdict_blocked"
    if reviewer_blockers:
        return None
    if reviewer_status == "invalid_structured_output":
        return "reviewer_result_invalid"
    if reviewer_status in {"blocked", "needs_review", "needs_escalation"}:
        return "reviewer_verdict_blocked"
    if reviewer_status in {"not_evaluated", "", "None"}:
        return "reviewer_unavailable"
    return "reviewer_result_invalid"
