"""Bounded engineer-reviewer rework loop harness behind an explicit loop fuse."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from pathlib import Path
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
from hermes_cli.pipeline_mutations import MutationDenied, apply_controlled_mutations
from hermes_cli.pipeline_report import (
    _mapping_list,
    _mapping_value,
    _normalized_cache_payload,
    _normalized_usage_payload,
    _runner_result_is_reportable,
    build_pipeline_execution_report,
)
from hermes_cli.pipeline_reviewer_packet import build_reviewer_packet
from hermes_cli.pipeline_reviewer_packet import MACHINE_CAPTURED_TEST_STATUSES
from hermes_cli.pipeline_session import PipelineSession
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot
from hermes_cli.pipeline_test_runner import preserve_explicit_pytest_command, run_controlled_tests
from hermes_cli.runtime_factory import RuntimeBuildRequest, build_controlled_runtime, build_runtime_factory_plan
from hermes_cli.subagent_runner import (
    ControlledRuntimeRunner,
    SubagentInvocationRequest,
    SubagentRunner,
    validate_structured_output_envelope,
)


SAFE_FALLBACK_MAX_REVIEW_ITERATIONS = 1
REVIEWER_APPROVAL_STATUS = "candidate_complete"
ESCALATED_REVIEWER_SUBAGENT_ID = "hermes_code_reviewer_escalated"
ESCALATED_REVIEWER_ROLE_ID = "reviewer"
ESCALATED_REVIEWER_DECISION_APPROVED = "approved"
ESCALATED_REVIEWER_DECISION_BLOCKER_MAINTAINED = "blocker_maintained"
ESCALATED_REVIEWER_DECISION_UNABLE = "unable_to_arbitrate"
ESCALATED_REVIEWER_ALLOWED_DECISIONS = {
    ESCALATED_REVIEWER_DECISION_APPROVED,
    ESCALATED_REVIEWER_DECISION_BLOCKER_MAINTAINED,
    ESCALATED_REVIEWER_DECISION_UNABLE,
}
MAX_SAFE_EVIDENCE_ITEMS = 5
MAX_SAFE_EVIDENCE_LABEL_CHARS = 64
_DIFF_MARKERS = ("diff --git", "@@", "+++", "---")
_SENSITIVE_PARTS = ("api_key", "token", "password", "secret", "credential")


@dataclass(frozen=True)
class ControlledRuntimeContext:
    controlled_runner: ControlledRuntimeRunner
    invocation_client: Any = None
    executor_bridge: Any = None
    allow_model_escalation: bool = False
    allow_real_provider_execution: bool = False
    request_real_provider_execution: bool = False
    allowed_real_providers: tuple[str, ...] = ()
    allowed_real_models: tuple[str, ...] = ()
    allowed_real_providers_by_role: dict[str, tuple[str, ...]] | None = None
    allowed_real_models_by_role: dict[str, tuple[str, ...]] | None = None
    allowed_real_providers_by_subagent: dict[str, tuple[str, ...]] | None = None
    allowed_real_models_by_subagent: dict[str, tuple[str, ...]] | None = None
    real_provider_client_factory: Any = None
    allow_mutations: bool = False
    mutation_workspace: str | None = None
    allow_test_commands: bool = False
    test_workspace: str | None = None


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
            "engineer_message_hash": _stable_text_hash(self.engineer_message),
            "reviewer_message_hash": _stable_text_hash(self.reviewer_message),
            "engineer_runner_status": self.engineer_runner_status,
            "reviewer_runner_status": self.reviewer_runner_status,
            "engineer_evaluation_status": self.engineer_evaluation_status,
            "reviewer_evaluation_status": self.reviewer_evaluation_status,
            "reviewer_blockers": list(self.reviewer_blockers),
            "loop_limit_snapshot": dict(self.loop_limit_snapshot),
        }


class ExecutorBridgeResolutionError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
    appended_rework_context: list[dict[str, Any]]
    completion_allowed: bool
    candidate_complete: bool
    user_action_required: bool
    blocked_reason: str | None
    git_gate: dict[str, Any]
    reviewer_packet: dict[str, Any]
    subagent_runs: list[dict[str, Any]]
    usage_summary: dict[str, Any]
    mutation_summary: dict[str, Any] | None = None
    test_summary: dict[str, Any] | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        report_payload = _safe_execution_report_payload(self.execution_report)
        return {
            "fuse": self.fuse.to_safe_dict(),
            "iteration_history": [item.to_safe_dict() for item in self.iteration_history],
            "review_iterations_completed": self.review_iterations_completed,
            "max_review_iterations": self.max_review_iterations,
            "policy_source": self.policy_source,
            "original_task": "[redacted]",
            "original_task_hash": _stable_text_hash(self.original_task),
            "appended_rework_context": ["[redacted]" for _ in self.appended_rework_context],
            "appended_rework_context_hashes": [_stable_text_hash(_serialize_rework_context(item)) for item in self.appended_rework_context],
            "completion_allowed": self.completion_allowed,
            "candidate_complete": self.candidate_complete,
            "user_action_required": self.user_action_required,
            "blocked_reason": self.blocked_reason,
            "git_gate": dict(self.git_gate),
            "reviewer_packet": dict(self.reviewer_packet),
            "subagent_runs": [dict(item) for item in self.subagent_runs],
            "usage_summary": dict(self.usage_summary),
            "mutation_summary": dict(self.mutation_summary or {}),
            "test_summary": _tests_payload(self.test_summary),
            "report": report_payload,
            "execution_report": report_payload,
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
    controlled_runtime_context: ControlledRuntimeContext | dict[str, Any] | None = None,
) -> PipelineReworkLoopResult:
    runtime_context = _normalize_controlled_runtime_context(controlled_runtime_context)
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
            subagent_runs=[],
            usage_summary=_usage_summary_from_subagent_runs([]),
        )

    appended_rework_context: list[dict[str, Any]] = []
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
    accumulated_subagent_runs: list[dict[str, Any]] = []
    current_mutation_summary = _mutation_summary_disabled(runtime_context)
    current_test_summary = dict(test_summary) if isinstance(test_summary, dict) else _test_summary_disabled(runtime_context)
    peer_messages: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    model_escalations: list[dict[str, Any]] = []
    pending_reviewer_blockers: list[str] = []
    peer_round_used = False
    decisive_subagent = REVIEWER_SUBAGENT_ID
    review_overrides: dict[str, Any] = {}
    loop_policy = resolve_loop_limit_policy(pipeline_spec)

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
        try:
            engineer_result = _execute_step(
                config=config,
                session=session,
                loaded_specs=loaded_specs,
                runtime_factory=runtime_factory,
                runner=runner,
                controlled_runtime_context=runtime_context,
                pipeline_spec=pipeline_spec,
                current_snapshot=current_snapshot,
                step_index=0,
                step_kind="engineer",
                user_message=engineer_message,
                metadata={
                    "execution_backend": "controlled_runtime_runner" if runtime_context is not None else "legacy_runner",
                    "execution_scope": fuse.execution_scope,
                    "loop_allowed": True,
                    "review_iterations_completed": review_iterations_completed,
                },
            )
        except ExecutorBridgeResolutionError as exc:
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
                blocked_reason=exc.reason,
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )
        current_snapshot = engineer_result.state_snapshot
        _append_step_run(accumulated_subagent_runs, current_snapshot, 0)
        engineer_output = _step_structured_output(current_snapshot, 0)
        try:
            current_mutation_summary = _apply_step_mutations(
                step_kind="engineer",
                step_subagent_id=ENGINEER_SUBAGENT_ID,
                structured_output=engineer_output,
                runtime_context=runtime_context,
            )
        except MutationDenied as exc:
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
                blocked_reason="mutation_denied",
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=_mutation_summary_from_denied_exception(exc, runtime_context),
                test_summary=current_test_summary,
            )
        engineer_test_summary = _apply_step_tests(
            step_kind="engineer",
            step_subagent_id=ENGINEER_SUBAGENT_ID,
            structured_output=engineer_output,
            runner_result=getattr(current_snapshot.planned_steps[0], "runner_result", None),
            runtime_context=runtime_context,
        )
        if int(engineer_test_summary.get("requested_count") or 0) > 0 or engineer_test_summary.get("blocked_reason") is not None:
            current_test_summary = engineer_test_summary
        current_test_summary = _preserve_requested_test_summary(
            current=current_test_summary,
            original_task=user_message,
            engineer_output=engineer_output,
            runner_result=getattr(current_snapshot.planned_steps[0], "runner_result", None),
            runtime_context=runtime_context,
            test_workspace=repo_path or (runtime_context.test_workspace if runtime_context is not None else None),
        )
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
                    test_summary=current_test_summary,
                    engineer_evaluation_status=_step_evaluation_status(current_snapshot.planned_steps[0]),
                )
            )
        if current_test_summary.get("blocked_reason") is not None:
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
                blocked_reason=str(current_test_summary.get("blocked_reason")),
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )
        engineer_fail_closed_reason = _engineer_fail_closed_reason(
            current_snapshot,
            material_changes_present=bool(git_result.material_changes_present) if git_result is not None else False,
        )
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
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
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
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )
        active_reviewer_blockers = _active_reviewer_blockers(
            current_snapshot=current_snapshot,
            pending_reviewer_blockers=pending_reviewer_blockers,
        )
        if active_reviewer_blockers and _engineer_requests_disagreement(engineer_output):
            if peer_round_used or loop_policy.max_peer_discussion_rounds <= 0:
                disagreement = _build_disagreement_record(
                    status="reviewer_maintained_blocker",
                    reviewer_blockers=active_reviewer_blockers,
                    peer_round_limit_status="max_peer_discussion_rounds_reached",
                    decisive_subagent=decisive_subagent,
                )
                disagreements.append(disagreement)
                escalation_result = _execute_model_escalation_if_allowed(
                    config=config,
                    session=session,
                    loaded_specs=loaded_specs,
                    runtime_context=runtime_context,
                    accumulated_subagent_runs=accumulated_subagent_runs,
                    current_snapshot=current_snapshot,
                    original_task=user_message,
                    active_reviewer_blockers=active_reviewer_blockers,
                    trigger="reviewer_maintains_blocker_after_max_peer_round",
                    reason="reviewer maintained blocker after allowed peer disagreement round",
                )
                if escalation_result is not None:
                    escalation_result = _apply_completion_gate_to_escalation_result(
                        escalation_result=escalation_result,
                        allow_completion_after_review=allow_completion_after_review,
                    )
                    disagreements[-1]["status"] = escalation_result["disagreement_status"]
                    disagreements[-1]["resolution"] = escalation_result["disagreement_resolution"]
                    disagreements[-1]["decisive_subagent"] = escalation_result["decisive_subagent"]
                    model_escalations.append(dict(escalation_result["model_escalation"]))
                    review_overrides = dict(escalation_result["review_overrides"])
                    decisive_subagent = escalation_result["decisive_subagent"]
                    return _finalize_loop_result(
                        fuse=fuse,
                        session=session,
                        snapshot=escalation_result["snapshot"],
                        preflight_allowed=True,
                        preflight_reason_code="rework_loop_fuse_allowed",
                        iteration_history=iteration_history,
                        review_iterations_completed=review_iterations_completed,
                        max_review_iterations=max_review_iterations,
                        policy_source=policy_source,
                        original_task=user_message,
                        appended_rework_context=appended_rework_context,
                        completion_allowed=allow_completion_after_review and bool(escalation_result["completion_allowed"]),
                        candidate_complete=bool(escalation_result["candidate_complete"]),
                        user_action_required=bool(escalation_result["user_action_required"]),
                        blocked_reason=escalation_result["blocked_reason"],
                        git_gate=current_git_gate,
                        reviewer_packet=current_reviewer_packet,
                        subagent_runs=accumulated_subagent_runs,
                        peer_messages=peer_messages,
                        disagreements=disagreements,
                        decisive_subagent=decisive_subagent,
                        model_escalations=model_escalations,
                        tests=_tests_payload(current_test_summary),
                        mutation_summary=current_mutation_summary,
                        review_overrides=review_overrides,
                        test_summary=current_test_summary,
                    )

                model_escalations.append(
                    _build_model_escalation_record(
                        status="block_and_escalate_to_user",
                        trigger="reviewer_maintains_blocker_after_max_peer_round",
                        reason="reviewer maintained blocker after allowed peer disagreement round",
                        from_subagent=REVIEWER_SUBAGENT_ID,
                    )
                )
                final_snapshot = replace(
                    current_snapshot,
                    state="rework_loop_disagreement_blocked",
                    completion_reason="reviewer_decisive_after_disagreement",
                    executed=True,
                    completion_allowed=False,
                    completion_blocked_reason="reviewer_decisive_after_disagreement",
                    final_verdict="reviewer_decisive_after_disagreement",
                )
                review_overrides = {
                    "reviewer_approved": False,
                    "escalation_invoked": False,
                    "escalation_approved": False,
                    "final_review_decision": "blocker_maintained",
                    "status": "blocked_after_disagreement",
                }
                return _finalize_loop_result(
                    fuse=fuse,
                    session=session,
                    snapshot=final_snapshot,
                    preflight_allowed=True,
                    preflight_reason_code="rework_loop_fuse_allowed",
                    iteration_history=iteration_history,
                    review_iterations_completed=review_iterations_completed,
                    max_review_iterations=max_review_iterations,
                    policy_source=policy_source,
                    original_task=user_message,
                    appended_rework_context=appended_rework_context,
                    completion_allowed=False,
                    candidate_complete=False,
                    user_action_required=True,
                    blocked_reason="reviewer_decisive_after_disagreement",
                    git_gate=current_git_gate,
                    reviewer_packet=current_reviewer_packet,
                    subagent_runs=accumulated_subagent_runs,
                    peer_messages=peer_messages,
                    disagreements=disagreements,
                    decisive_subagent=decisive_subagent,
                    model_escalations=model_escalations,
                    tests=_tests_payload(current_test_summary),
                    mutation_summary=current_mutation_summary,
                    review_overrides=review_overrides,
                    test_summary=current_test_summary,
                )

            peer_round_used = True
            disagreement = _build_disagreement_record(
                status="peer_discussion_requested",
                reviewer_blockers=active_reviewer_blockers,
                peer_round_limit_status=None,
                decisive_subagent=decisive_subagent,
            )
            peer_message = _build_peer_message(
                session=session,
                engineer_output=engineer_output,
                reviewer_blockers=active_reviewer_blockers,
                related_verdict_id=f"review-{review_iterations_completed}",
            )
            disagreements.append(disagreement)
            peer_messages.append(peer_message)
            reviewer_fuse = evaluate_pipeline_reviewer_execution_fuse(
                config=config,
                session=session,
                state_snapshot=_reviewer_prereq_satisfied_snapshot(current_snapshot),
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
                    subagent_runs=accumulated_subagent_runs,
                    usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                    mutation_summary=current_mutation_summary,
                    test_summary=current_test_summary,
                )
            reviewer_message = _compose_peer_reviewer_message(
                original_task=user_message,
                peer_message=peer_message,
            )
            reviewer_result = _execute_step(
                config=config,
                session=session,
                loaded_specs=loaded_specs,
                runtime_factory=runtime_factory,
                runner=runner,
                controlled_runtime_context=runtime_context,
                pipeline_spec=pipeline_spec,
                current_snapshot=current_snapshot,
                step_index=1,
                step_kind="reviewer",
                user_message=reviewer_message,
                metadata={
                    "execution_backend": "controlled_runtime_runner" if runtime_context is not None else "legacy_runner",
                    "execution_scope": reviewer_fuse.execution_scope,
                    "engineer_result_present": True,
                    "loop_allowed": True,
                    "review_iterations_completed": review_iterations_completed,
                    "peer_discussion": True,
                    "reviewer_packet": current_reviewer_packet,
                },
            )
            current_snapshot = reviewer_result.state_snapshot
            _append_step_run(accumulated_subagent_runs, current_snapshot, 1)
            try:
                current_mutation_summary = _merge_mutation_summaries(
                    current_mutation_summary,
                    _apply_step_mutations(
                        step_kind="reviewer",
                        step_subagent_id=REVIEWER_SUBAGENT_ID,
                        structured_output=_step_structured_output(current_snapshot, 1),
                        runtime_context=runtime_context,
                    ),
                )
            except MutationDenied as exc:
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
                    blocked_reason="mutation_denied",
                    user_action_required=True,
                    git_gate=current_git_gate,
                    reviewer_packet=current_reviewer_packet,
                    subagent_runs=accumulated_subagent_runs,
                    usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                    mutation_summary=_mutation_summary_from_denied_exception(exc, runtime_context),
                    test_summary=current_test_summary,
                )
            reviewer_test_summary = _apply_step_tests(
                step_kind="reviewer",
                step_subagent_id=REVIEWER_SUBAGENT_ID,
                structured_output=_step_structured_output(current_snapshot, 1),
                runner_result=getattr(current_snapshot.planned_steps[1], "runner_result", None),
                runtime_context=runtime_context,
            )
            if int(reviewer_test_summary.get("requested_count") or 0) > 0 or reviewer_test_summary.get("blocked_reason") is not None:
                current_test_summary = reviewer_test_summary
            if current_test_summary.get("blocked_reason") is not None:
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
                    blocked_reason=str(current_test_summary.get("blocked_reason")),
                    user_action_required=True,
                    git_gate=current_git_gate,
                    reviewer_packet=current_reviewer_packet,
                    subagent_runs=accumulated_subagent_runs,
                    usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                    mutation_summary=current_mutation_summary,
                    test_summary=current_test_summary,
                )
            reviewer_step = reviewer_result.state_snapshot.planned_steps[1]
            reviewer_eval = getattr(reviewer_step, "evaluation_result", None) or {}
            reviewer_blockers = list(reviewer_eval.get("blockers") or [])
            reviewer_status = str(reviewer_eval.get("status") or "not_evaluated")
            current_reviewer_packet = _with_reviewer_findings(
                current_reviewer_packet,
                _extract_reviewer_findings(_step_structured_output(current_snapshot, 1)),
            )
            if reviewer_status == REVIEWER_APPROVAL_STATUS and not reviewer_blockers:
                disagreements[-1]["status"] = "resolved"
                disagreements[-1]["resolution"] = "reviewer_revised_to_approved"
                model_escalations.append(
                    _build_model_escalation_record(
                        status="not_required",
                        trigger="disagreement_resolved_by_reviewer",
                        reason="reviewer revised to approved after peer disagreement round",
                        from_subagent=REVIEWER_SUBAGENT_ID,
                    )
                )
                final_snapshot = replace(
                    current_snapshot,
                    state="peer_discussion_completed",
                    completion_reason="rework_loop_candidate_complete",
                    executed=True,
                    completion_allowed=allow_completion_after_review,
                    completion_blocked_reason=None if allow_completion_after_review else "loop_harness_not_live_final",
                    final_verdict="controlled_rework_loop_candidate_complete",
                )
                review_overrides = {
                    "reviewer_approved": True,
                    "escalation_invoked": False,
                    "escalation_approved": False,
                    "final_review_decision": "approved",
                    "status": "approved_after_disagreement",
                }
                return _finalize_loop_result(
                    fuse=fuse,
                    session=session,
                    snapshot=final_snapshot,
                    preflight_allowed=True,
                    preflight_reason_code="rework_loop_fuse_allowed",
                    iteration_history=iteration_history,
                    review_iterations_completed=review_iterations_completed,
                    max_review_iterations=max_review_iterations,
                    policy_source=policy_source,
                    original_task=user_message,
                    appended_rework_context=appended_rework_context,
                    completion_allowed=allow_completion_after_review,
                    candidate_complete=True,
                    user_action_required=False,
                    blocked_reason=None if allow_completion_after_review else "loop_harness_not_live_final",
                    git_gate=current_git_gate,
                    reviewer_packet=current_reviewer_packet,
                    subagent_runs=accumulated_subagent_runs,
                    peer_messages=peer_messages,
                    disagreements=disagreements,
                    decisive_subagent=decisive_subagent,
                    model_escalations=model_escalations,
                    tests=_tests_payload(current_test_summary),
                    mutation_summary=current_mutation_summary,
                    review_overrides=review_overrides,
                    test_summary=current_test_summary,
                )

            disagreements[-1]["status"] = "reviewer_maintained_blocker"
            disagreements[-1]["resolution"] = "reviewer_blocker_authoritative"
            disagreements[-1]["peer_round_limit_status"] = "max_peer_discussion_rounds_reached"
            escalation_result = _execute_model_escalation_if_allowed(
                config=config,
                session=session,
                loaded_specs=loaded_specs,
                runtime_context=runtime_context,
                accumulated_subagent_runs=accumulated_subagent_runs,
                current_snapshot=current_snapshot,
                original_task=user_message,
                active_reviewer_blockers=reviewer_blockers,
                trigger="reviewer_maintains_blocker_after_peer_round",
                reason="reviewer maintained blocker after allowed peer disagreement round",
            )
            if escalation_result is not None:
                escalation_result = _apply_completion_gate_to_escalation_result(
                    escalation_result=escalation_result,
                    allow_completion_after_review=allow_completion_after_review,
                )
                disagreements[-1]["status"] = escalation_result["disagreement_status"]
                disagreements[-1]["resolution"] = escalation_result["disagreement_resolution"]
                disagreements[-1]["decisive_subagent"] = escalation_result["decisive_subagent"]
                model_escalations.append(dict(escalation_result["model_escalation"]))
                review_overrides = dict(escalation_result["review_overrides"])
                decisive_subagent = escalation_result["decisive_subagent"]
                return _finalize_loop_result(
                    fuse=fuse,
                    session=session,
                    snapshot=escalation_result["snapshot"],
                    preflight_allowed=True,
                    preflight_reason_code="rework_loop_fuse_allowed",
                    iteration_history=iteration_history,
                    review_iterations_completed=review_iterations_completed,
                    max_review_iterations=max_review_iterations,
                    policy_source=policy_source,
                    original_task=user_message,
                    appended_rework_context=appended_rework_context,
                    completion_allowed=allow_completion_after_review and bool(escalation_result["completion_allowed"]),
                    candidate_complete=bool(escalation_result["candidate_complete"]),
                    user_action_required=bool(escalation_result["user_action_required"]),
                    blocked_reason=escalation_result["blocked_reason"],
                    git_gate=current_git_gate,
                    reviewer_packet=current_reviewer_packet,
                    subagent_runs=accumulated_subagent_runs,
                    peer_messages=peer_messages,
                    disagreements=disagreements,
                    decisive_subagent=decisive_subagent,
                    model_escalations=model_escalations,
                    tests=_tests_payload(current_test_summary),
                    mutation_summary=current_mutation_summary,
                    review_overrides=review_overrides,
                    test_summary=current_test_summary,
                )

            model_escalations.append(
                _build_model_escalation_record(
                    status="block_and_escalate_to_user",
                    trigger="reviewer_maintains_blocker_after_peer_round",
                    reason="reviewer maintained blocker after allowed peer disagreement round",
                    from_subagent=REVIEWER_SUBAGENT_ID,
                )
            )
            final_snapshot = replace(
                current_snapshot,
                state="disagreement_unresolved",
                completion_reason="reviewer_decisive_after_disagreement",
                executed=True,
                completion_allowed=False,
                completion_blocked_reason="reviewer_decisive_after_disagreement",
                final_verdict="reviewer_decisive_after_disagreement",
            )
            review_overrides = {
                "reviewer_approved": False,
                "escalation_invoked": False,
                "escalation_approved": False,
                "final_review_decision": "blocker_maintained",
                "status": "blocked_after_disagreement",
            }
            return _finalize_loop_result(
                fuse=fuse,
                session=session,
                snapshot=final_snapshot,
                preflight_allowed=True,
                preflight_reason_code="rework_loop_fuse_allowed",
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                completion_allowed=False,
                candidate_complete=False,
                user_action_required=True,
                blocked_reason="reviewer_decisive_after_disagreement",
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                peer_messages=peer_messages,
                disagreements=disagreements,
                decisive_subagent=decisive_subagent,
                model_escalations=model_escalations,
                tests=_tests_payload(current_test_summary),
                mutation_summary=current_mutation_summary,
                review_overrides=review_overrides,
                test_summary=current_test_summary,
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
            return _finalize_loop_result(
                fuse=fuse,
                session=session,
                snapshot=final_snapshot,
                preflight_allowed=True,
                preflight_reason_code="rework_loop_fuse_allowed",
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
                subagent_runs=accumulated_subagent_runs,
                peer_messages=peer_messages,
                disagreements=disagreements,
                decisive_subagent=decisive_subagent,
                model_escalations=model_escalations,
                tests=_tests_payload(current_test_summary),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )

        reviewer_fuse = evaluate_pipeline_reviewer_execution_fuse(
            config=config,
            session=session,
            state_snapshot=current_snapshot,
            material_changes_present=bool(git_result.material_changes_present) if git_result is not None else False,
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
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )

        reviewer_message = _compose_reviewer_message(
            original_task=user_message,
            engineer_message=engineer_message,
            appended_rework_context=appended_rework_context,
        )
        try:
            reviewer_result = _execute_step(
                config=config,
                session=session,
                loaded_specs=loaded_specs,
                runtime_factory=runtime_factory,
                runner=runner,
                controlled_runtime_context=runtime_context,
                pipeline_spec=pipeline_spec,
                current_snapshot=current_snapshot,
                step_index=1,
                step_kind="reviewer",
                user_message=reviewer_message,
                metadata={
                    "execution_backend": "controlled_runtime_runner" if runtime_context is not None else "legacy_runner",
                    "execution_scope": reviewer_fuse.execution_scope,
                    "engineer_result_present": True,
                    "loop_allowed": True,
                    "review_iterations_completed": review_iterations_completed,
                    "reviewer_packet": current_reviewer_packet,
                },
            )
        except ExecutorBridgeResolutionError as exc:
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
                blocked_reason=exc.reason,
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )
        current_snapshot = reviewer_result.state_snapshot
        _append_step_run(accumulated_subagent_runs, current_snapshot, 1)
        try:
            current_mutation_summary = _merge_mutation_summaries(
                current_mutation_summary,
                _apply_step_mutations(
                    step_kind="reviewer",
                    step_subagent_id=REVIEWER_SUBAGENT_ID,
                    structured_output=_step_structured_output(current_snapshot, 1),
                    runtime_context=runtime_context,
                ),
            )
        except MutationDenied as exc:
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
                blocked_reason="mutation_denied",
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=_mutation_summary_from_denied_exception(exc, runtime_context),
                test_summary=current_test_summary,
            )
        reviewer_test_summary = _apply_step_tests(
            step_kind="reviewer",
            step_subagent_id=REVIEWER_SUBAGENT_ID,
            structured_output=_step_structured_output(current_snapshot, 1),
            runner_result=getattr(current_snapshot.planned_steps[1], "runner_result", None),
            runtime_context=runtime_context,
        )
        if int(reviewer_test_summary.get("requested_count") or 0) > 0 or reviewer_test_summary.get("blocked_reason") is not None:
            current_test_summary = reviewer_test_summary
        if current_test_summary.get("blocked_reason") is not None:
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
                blocked_reason=str(current_test_summary.get("blocked_reason")),
                user_action_required=True,
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                usage_summary=_usage_summary_from_subagent_runs(accumulated_subagent_runs),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )
        reviewer_step = reviewer_result.state_snapshot.planned_steps[1]
        reviewer_eval = getattr(reviewer_step, "evaluation_result", None) or {}
        reviewer_blockers = list(reviewer_eval.get("blockers") or [])
        reviewer_status = str(reviewer_eval.get("status") or "not_evaluated")
        reviewer_structured_output = _step_structured_output(current_snapshot, 1)
        current_reviewer_packet = _with_reviewer_findings(
            current_reviewer_packet,
            _extract_reviewer_findings(reviewer_structured_output),
        )
        current_reviewer_packet = _with_synthesized_reviewer_findings(
            current_reviewer_packet,
            reviewer_status=reviewer_status,
            reviewer_blockers=reviewer_blockers,
            test_summary=current_test_summary,
        )
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
            reviewer_packet=current_reviewer_packet,
            test_summary=current_test_summary,
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
            return _finalize_loop_result(
                fuse=fuse,
                session=session,
                snapshot=final_snapshot,
                preflight_allowed=True,
                preflight_reason_code="rework_loop_fuse_allowed",
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
                subagent_runs=accumulated_subagent_runs,
                peer_messages=peer_messages,
                disagreements=disagreements,
                decisive_subagent=decisive_subagent,
                model_escalations=model_escalations,
                tests=_tests_payload(current_test_summary),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
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
            return _finalize_loop_result(
                fuse=fuse,
                session=session,
                snapshot=final_snapshot,
                preflight_allowed=True,
                preflight_reason_code="rework_loop_fuse_allowed",
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
                subagent_runs=accumulated_subagent_runs,
                peer_messages=peer_messages,
                disagreements=disagreements,
                decisive_subagent=decisive_subagent,
                model_escalations=model_escalations,
                tests=_tests_payload(current_test_summary),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )

        appended_rework_context.append(
            _build_rework_context(
                iteration_index=review_iterations_completed,
                reviewer_eval=reviewer_eval,
                reviewer_structured_output=_step_structured_output(current_snapshot, 1),
                reviewer_packet=current_reviewer_packet,
                test_summary=current_test_summary,
            )
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
            return _finalize_loop_result(
                fuse=fuse,
                session=session,
                snapshot=final_snapshot,
                preflight_allowed=True,
                preflight_reason_code="rework_loop_fuse_allowed",
                iteration_history=iteration_history,
                review_iterations_completed=review_iterations_completed,
                max_review_iterations=max_review_iterations,
                policy_source=policy_source,
                original_task=user_message,
                appended_rework_context=appended_rework_context,
                completion_allowed=False,
                candidate_complete=False,
                user_action_required=True,
                blocked_reason=_rework_exhausted_reason(
                    reviewer_status=reviewer_status,
                    reviewer_blockers=reviewer_blockers,
                    reviewer_packet=current_reviewer_packet,
                    test_summary=current_test_summary,
                ),
                git_gate=current_git_gate,
                reviewer_packet=current_reviewer_packet,
                subagent_runs=accumulated_subagent_runs,
                peer_messages=peer_messages,
                disagreements=disagreements,
                decisive_subagent=decisive_subagent,
                model_escalations=model_escalations,
                tests=_tests_payload(current_test_summary),
                mutation_summary=current_mutation_summary,
                test_summary=current_test_summary,
            )

        pending_reviewer_blockers = list(reviewer_blockers)


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
    config: dict[str, Any] | None,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_factory: Any,
    runner: SubagentRunner,
    controlled_runtime_context: ControlledRuntimeContext | None,
    pipeline_spec: dict[str, Any],
    current_snapshot: Any,
    step_index: int,
    step_kind: str,
    user_message: str,
    metadata: dict[str, Any],
) -> ControlledOneStepExecutionResult:
    step = current_snapshot.planned_steps[step_index]
    configured_execution_mode = str(
        (((config or {}).get("pipelines") or {}).get("execution") or {}).get("mode") or "disabled"
    ).strip().lower()
    executor_bridge = _resolve_executor_bridge(
        controlled_runtime_context.executor_bridge if controlled_runtime_context is not None else None,
        step.subagent_id,
    )
    if executor_bridge is not None:
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
            execution_mode=configured_execution_mode,
        )
        invocation_request = SubagentInvocationRequest(
            subagent_id=step.subagent_id,
            pipeline_session_id=session.pipeline_session_id,
            invocation_id=f"{session.pipeline_session_id}:{step_kind}:loop:{step_index}",
            input_messages=[{"role": "user", "content": user_message}],
            metadata=metadata,
        )
        invocation_result = SubagentRunner(executor_bridge).run(runtime_plan, invocation_request)
        adapted_runner_result = _adapt_runner_result(
            invocation_result=invocation_result,
            runner_request=runner_request,
            runtime_plan=runtime_plan,
        )
        runner_request_payload = runner_request.to_safe_dict()
        execution_mode = configured_execution_mode
    elif controlled_runtime_context is not None:
        runtime_plan = build_runtime_factory_plan(
            session=session,
            planned_step=step,
            subagent_spec=loaded_specs.subagent_specs.get(step.subagent_id),
            config=config,
        )
        runtime = build_controlled_runtime(
            plan=runtime_plan,
            invocation_client=controlled_runtime_context.invocation_client,
            request_real_provider_execution=controlled_runtime_context.request_real_provider_execution,
            allow_real_provider_execution=controlled_runtime_context.allow_real_provider_execution,
            allowed_real_providers=controlled_runtime_context.allowed_real_providers,
            allowed_real_models=controlled_runtime_context.allowed_real_models,
            allowed_real_providers_by_role=controlled_runtime_context.allowed_real_providers_by_role,
            allowed_real_models_by_role=controlled_runtime_context.allowed_real_models_by_role,
            allowed_real_providers_by_subagent=controlled_runtime_context.allowed_real_providers_by_subagent,
            allowed_real_models_by_subagent=controlled_runtime_context.allowed_real_models_by_subagent,
            real_provider_client_factory=controlled_runtime_context.real_provider_client_factory,
        )
        controlled_result = controlled_runtime_context.controlled_runner.run(
            runtime,
            input_messages=[{"role": "user", "content": user_message}],
            request_metadata=metadata,
        )
        structured_output = validate_structured_output_envelope(controlled_result.structured_output)
        adapted_runner_result = replace(
            controlled_result,
            structured_output=structured_output,
            schema_validation_status=structured_output.validation_status,
        )
        runner_request_payload = {
            "pipeline_session_id": runtime.pipeline_session_id,
            "trace_id": runtime.trace_id,
            "pipeline_id": runtime.pipeline_id,
            "step_id": step.step_kind,
            "subagent_id": runtime.subagent_id,
            "role_id": runtime.role_id,
            "runtime_factory_plan_id": f"{runtime.pipeline_session_id}:{runtime.role_id}:{runtime.subagent_id}",
            "runtime_factory_status": runtime.runtime_status,
            "execution_mode": "controlled_runtime_loop",
            "prompt_input_hash": session.user_message_hash,
            "actual_provider": runtime.provider,
            "actual_model": runtime.model,
            "actual_model_class": runtime.model_class,
            "status": adapted_runner_result.status.value,
        }
        execution_mode = "controlled_runtime_loop"
    else:
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
        runner_request_payload = runner_request.to_safe_dict()
        execution_mode = "controlled_one_step"
    evaluation = evaluate_pipeline_step(
        PipelineEvaluationRequest(
            pipeline_session_id=session.pipeline_session_id,
            trace_id=session.trace_id,
            pipeline_id=session.pipeline_id,
            step_id=step.step_kind,
            subagent_id=step.subagent_id,
            execution_mode=execution_mode,
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
        planning_mode=execution_mode,
        runtime_factory_plan=runtime_plan.to_safe_dict(),
        runner_request=runner_request_payload,
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
        execution_mode=execution_mode,
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
            execution_mode=execution_mode,
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


def _resolve_executor_bridge(executor_bridge: Any, subagent_id: str) -> Any:
    if executor_bridge is None:
        return None
    if callable(executor_bridge):
        return executor_bridge
    if isinstance(executor_bridge, dict):
        selected = executor_bridge.get(subagent_id)
        if selected is None:
            raise ExecutorBridgeResolutionError(f"executor_bridge_missing:{subagent_id}")
        if not callable(selected):
            raise ExecutorBridgeResolutionError(f"executor_bridge_invalid:{subagent_id}")
        return selected
    raise ExecutorBridgeResolutionError("executor_bridge_invalid_configuration")


def _execute_model_escalation_if_allowed(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_context: ControlledRuntimeContext | None,
    accumulated_subagent_runs: list[dict[str, Any]],
    current_snapshot: Any,
    original_task: str,
    active_reviewer_blockers: list[str],
    trigger: str,
    reason: str,
) -> dict[str, Any] | None:
    if runtime_context is None or not runtime_context.allow_model_escalation:
        return None

    escalation_run = _run_escalated_reviewer(
        config=config,
        session=session,
        loaded_specs=loaded_specs,
        runtime_context=runtime_context,
        current_snapshot=current_snapshot,
        original_task=original_task,
        active_reviewer_blockers=active_reviewer_blockers,
        trigger=trigger,
        reason=reason,
    )
    accumulated_subagent_runs.append(dict(escalation_run["subagent_run"]))

    model_escalation = _build_model_escalation_record(
        status="executed",
        trigger=trigger,
        reason=reason,
        from_subagent=REVIEWER_SUBAGENT_ID,
        to_subagent=ESCALATED_REVIEWER_SUBAGENT_ID,
        decisive_subagent=ESCALATED_REVIEWER_SUBAGENT_ID,
        verdict=escalation_run["verdict"],
        run=escalation_run["subagent_run"],
        blocked_reason=escalation_run["blocked_reason"],
    )
    return {
        "snapshot": escalation_run["snapshot"],
        "completion_allowed": escalation_run["completion_allowed"],
        "candidate_complete": escalation_run["candidate_complete"],
        "user_action_required": escalation_run["user_action_required"],
        "blocked_reason": escalation_run["blocked_reason"],
        "model_escalation": model_escalation,
        "decisive_subagent": ESCALATED_REVIEWER_SUBAGENT_ID,
        "disagreement_status": escalation_run["disagreement_status"],
        "disagreement_resolution": escalation_run["disagreement_resolution"],
        "review_overrides": escalation_run["review_overrides"],
    }


def _run_escalated_reviewer(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
    loaded_specs: Any,
    runtime_context: ControlledRuntimeContext,
    current_snapshot: Any,
    original_task: str,
    active_reviewer_blockers: list[str],
    trigger: str,
    reason: str,
) -> dict[str, Any]:
    reviewer_step = list(current_snapshot.planned_steps)[1]
    reviewer_spec = dict(loaded_specs.subagent_specs.get(REVIEWER_SUBAGENT_ID) or {})
    escalated_spec = _build_escalated_reviewer_spec(reviewer_spec)
    runtime_plan = build_runtime_factory_plan(
        session=session,
        planned_step=replace(reviewer_step, subagent_id=ESCALATED_REVIEWER_SUBAGENT_ID),
        subagent_spec=escalated_spec,
        config=config,
    )
    runtime = build_controlled_runtime(
        plan=runtime_plan,
        invocation_client=runtime_context.invocation_client,
        request_real_provider_execution=runtime_context.request_real_provider_execution,
        allow_real_provider_execution=runtime_context.allow_real_provider_execution,
        allowed_real_providers=runtime_context.allowed_real_providers,
        allowed_real_models=runtime_context.allowed_real_models,
        allowed_real_providers_by_role=runtime_context.allowed_real_providers_by_role,
        allowed_real_models_by_role=runtime_context.allowed_real_models_by_role,
        allowed_real_providers_by_subagent=runtime_context.allowed_real_providers_by_subagent,
        allowed_real_models_by_subagent=runtime_context.allowed_real_models_by_subagent,
        real_provider_client_factory=runtime_context.real_provider_client_factory,
    )
    controlled_result = runtime_context.controlled_runner.run(
        runtime,
        input_messages=[{"role": "user", "content": _compose_escalation_message(original_task=original_task, reviewer_blockers=active_reviewer_blockers, reason=reason)}],
        request_metadata={
            "execution_backend": "controlled_runtime_runner",
            "execution_scope": "controlled_model_escalation_only",
            "loop_allowed": True,
            "model_escalation_allowed": True,
            "model_escalation_trigger": trigger,
            "model_escalation_reason": reason,
        },
    )
    raw_structured_output = controlled_result.structured_output
    structured_output = validate_structured_output_envelope(raw_structured_output)
    verdict = _validate_escalation_verdict(raw_structured_output, structured_output)
    subagent_run = _subagent_run_from_result(
        step_id="reviewer_escalation",
        subagent_id=ESCALATED_REVIEWER_SUBAGENT_ID,
        role_id=ESCALATED_REVIEWER_ROLE_ID,
        runner_result=replace(
            controlled_result,
            structured_output=structured_output,
            schema_validation_status=structured_output.validation_status,
        ).to_safe_dict(),
    )
    if verdict["status"] == "invalid":
        snapshot = replace(
            current_snapshot,
            state="model_escalation_invalid",
            completion_reason="invalid_escalation_output",
            executed=True,
            completion_allowed=False,
            completion_blocked_reason="invalid_escalation_output",
            final_verdict="controlled_model_escalation_invalid",
        )
        return {
            "snapshot": snapshot,
            "completion_allowed": False,
            "candidate_complete": False,
            "user_action_required": True,
            "blocked_reason": "invalid_escalation_output",
            "verdict": "unable",
            "disagreement_status": "escalation_failed_closed",
            "disagreement_resolution": "invalid_escalation_output",
            "review_overrides": {
                "reviewer_approved": False,
                "escalation_invoked": True,
                "escalation_approved": False,
                "final_review_decision": "unable_to_arbitrate",
                "status": "blocked_after_escalation",
            },
            "subagent_run": subagent_run,
        }

    if verdict["decision"] == ESCALATED_REVIEWER_DECISION_APPROVED:
        snapshot = replace(
            current_snapshot,
            state="model_escalation_approved",
            completion_reason="rework_loop_candidate_complete",
            executed=True,
            completion_allowed=True,
            completion_blocked_reason=None,
            final_verdict="controlled_model_escalation_approved",
        )
        return {
            "snapshot": snapshot,
            "completion_allowed": True,
            "candidate_complete": True,
            "user_action_required": False,
            "blocked_reason": None,
            "verdict": ESCALATED_REVIEWER_DECISION_APPROVED,
            "disagreement_status": "resolved_by_escalation",
            "disagreement_resolution": "escalated_reviewer_approved",
            "review_overrides": {
                "reviewer_approved": False,
                "escalation_invoked": True,
                "escalation_approved": True,
                "final_review_decision": "approved",
                "status": "approved_after_escalation",
            },
            "subagent_run": subagent_run,
        }

    if verdict["decision"] == ESCALATED_REVIEWER_DECISION_BLOCKER_MAINTAINED:
        snapshot = replace(
            current_snapshot,
            state="model_escalation_blocked",
            completion_reason="escalation_maintained_blocker",
            executed=True,
            completion_allowed=False,
            completion_blocked_reason="escalation_maintained_blocker",
            final_verdict="controlled_model_escalation_blocked",
        )
        return {
            "snapshot": snapshot,
            "completion_allowed": False,
            "candidate_complete": False,
            "user_action_required": True,
            "blocked_reason": "escalation_maintained_blocker",
            "verdict": ESCALATED_REVIEWER_DECISION_BLOCKER_MAINTAINED,
            "disagreement_status": "resolved_by_escalation",
            "disagreement_resolution": "escalated_reviewer_maintained_blocker",
            "review_overrides": {
                "reviewer_approved": False,
                "escalation_invoked": True,
                "escalation_approved": False,
                "final_review_decision": "blocker_maintained",
                "status": "blocked_after_escalation",
            },
            "subagent_run": subagent_run,
        }

    snapshot = replace(
        current_snapshot,
        state="model_escalation_unable",
        completion_reason="escalation_unable_to_arbitrate",
        executed=True,
        completion_allowed=False,
        completion_blocked_reason="escalation_unable_to_arbitrate",
        final_verdict="controlled_model_escalation_unable",
    )
    return {
        "snapshot": snapshot,
        "completion_allowed": False,
        "candidate_complete": False,
        "user_action_required": True,
        "blocked_reason": "escalation_unable_to_arbitrate",
        "verdict": "unable",
        "disagreement_status": "escalation_failed_closed",
        "disagreement_resolution": "escalated_reviewer_unable_to_arbitrate",
        "review_overrides": {
            "reviewer_approved": False,
            "escalation_invoked": True,
            "escalation_approved": False,
            "final_review_decision": "unable_to_arbitrate",
            "status": "blocked_after_escalation",
        },
        "subagent_run": subagent_run,
    }


def _apply_completion_gate_to_escalation_result(
    *,
    escalation_result: dict[str, Any],
    allow_completion_after_review: bool,
) -> dict[str, Any]:
    if not escalation_result.get("completion_allowed"):
        return escalation_result
    if allow_completion_after_review:
        return escalation_result
    gated_snapshot = replace(
        escalation_result["snapshot"],
        completion_allowed=False,
        completion_blocked_reason="loop_harness_not_live_final",
    )
    gated_result = dict(escalation_result)
    gated_result["snapshot"] = gated_snapshot
    gated_result["completion_allowed"] = False
    gated_result["blocked_reason"] = "loop_harness_not_live_final"
    gated_result["user_action_required"] = False
    return gated_result


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
    appended_rework_context: list[dict[str, Any]],
    iteration_history: list[ReworkLoopIterationRecord],
    review_iterations_completed: int,
    max_review_iterations: int,
    policy_source: str,
    blocked_reason: str | None,
    user_action_required: bool,
    git_gate: dict[str, Any],
    reviewer_packet: dict[str, Any],
    subagent_runs: list[dict[str, Any]],
    usage_summary: dict[str, Any],
    mutation_summary: dict[str, Any] | None = None,
    test_summary: dict[str, Any] | None = None,
) -> PipelineReworkLoopResult:
    return PipelineReworkLoopResult(
        fuse=fuse,
        state_snapshot=snapshot,
        execution_report=build_pipeline_execution_report(
            session=session,
            state_snapshot=snapshot,
            preflight_result={"allowed": False, "reason_code": blocked_reason},
            final_response_text=_blocked_final_response_text(
                blocked_reason=blocked_reason,
                test_summary=test_summary,
                reviewer_packet=reviewer_packet,
            ),
            reviewer_packet=reviewer_packet,
            git_gate=git_gate,
            tests=_tests_payload(test_summary),
            mutation_summary=mutation_summary or {},
            blocked_reason_override=blocked_reason,
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
        subagent_runs=subagent_runs,
        usage_summary=usage_summary,
        mutation_summary=mutation_summary or {},
        test_summary=test_summary,
    )


def _blocked_final_response_text(
    *,
    blocked_reason: str | None,
    test_summary: dict[str, Any] | None,
    reviewer_packet: dict[str, Any],
) -> str | None:
    git_gate_reasons = {
        "baseline_dirty",
        "baseline_invalid",
        "post_snapshot_invalid",
        "repo_path_mismatch",
        "git_diff_failed",
        "git_unavailable",
    }
    if blocked_reason not in {
        "test_command_denied",
        "invalid_engineer_output",
        "engineer_result_invalid",
        "engineer_reported_blocked",
        "missing_structured_output",
        "max_iterations_plain_text_output",
        "reviewer_result_invalid",
        "reviewer_verdict_blocked",
        "reviewer_unavailable",
        "terminal_blocked",
        "review_loop_limit_exceeded",
        "rework_exhausted_after_ordinary_reviewer_findings",
        "rework_exhausted_after_missing_test_evidence",
        *git_gate_reasons,
    }:
        return None
    lines = [
        "Autonomous execution did not complete successfully.",
        "",
        "What happened:",
    ]
    if blocked_reason == "test_command_denied":
        denied_results = list((test_summary or {}).get("results") or [])
        denied_result = denied_results[0] if denied_results else {}
        denied_command = _safe_test_text(denied_result.get("denied_command_raw_sanitized"))
        validator_reason = _safe_test_text(denied_result.get("validator_reason"))
        malformed_payload = (
            denied_command == "targets=[denied]"
            or (validator_reason is not None and validator_reason.startswith("structured_pytest_payload_"))
            or (denied_command is not None and denied_command.lstrip().startswith("{"))
        )
        lines.extend(
            [
                "- The requested file changes were prepared in the autonomous workspace.",
                (
                    "- A malformed test payload was blocked by the controlled test validator."
                    if malformed_payload
                    else "- Pytest was requested but blocked by the controlled test validator."
                ),
                "- Reviewer was not invoked because engineer output was invalid after the blocked test step.",
                "",
                "No verified passing test result is available.",
            ]
        )
        if denied_command:
            lines.append(f"Denied payload: {denied_command}")
        if validator_reason and validator_reason != "test_command_denied":
            lines.append(f"Validator rule: {validator_reason}")
    elif blocked_reason == "missing_structured_output":
        lines.extend(
            [
                "- The autonomous bridge reached execution, but the engineer result did not contain the required structured output packet.",
                "- Reviewer was not invoked because the engineer result failed the structured-output contract.",
                "",
                "No verified passing test result is available.",
            ]
        )
    elif blocked_reason == "max_iterations_plain_text_output":
        lines.extend(
            [
                "- The engineer hit the autonomous iteration cap and returned plain-text output instead of the required structured output packet.",
                "- Reviewer was not invoked because the engineer result failed the structured-output contract.",
                "",
                "No verified passing test result is available.",
            ]
        )
    elif blocked_reason in {"reviewer_result_invalid", "reviewer_verdict_blocked", "reviewer_unavailable"}:
        lines.append(
            {
                "reviewer_result_invalid": "- The reviewer could not produce a valid review packet after controlled execution reached the review boundary.",
                "reviewer_verdict_blocked": "- The reviewer blocked completion for a terminal review reason.",
                "reviewer_unavailable": "- The reviewer did not complete a usable review after the patch reached the review boundary.",
            }[blocked_reason]
        )
        lines.append("- Controlled completion remains blocked at the reviewer boundary.")
        findings = list(reviewer_packet.get("review_findings") or [])
        if findings:
            lines.extend(["", "Reviewer findings:"])
            for item in findings:
                summary = _safe_test_text(item.get("summary")) if isinstance(item, dict) else _safe_test_text(item)
                if summary:
                    lines.append(f"- {summary}")
    elif blocked_reason == "terminal_blocked":
        lines.extend(
            [
                "- The controlled pipeline issued a terminal safety block.",
                "- The run stopped instead of routing the issue into another rework attempt.",
            ]
        )
        findings = list(reviewer_packet.get("review_findings") or [])
        if findings:
            lines.extend(["", "Safety findings:"])
            for item in findings:
                summary = _safe_test_text(item.get("summary")) if isinstance(item, dict) else _safe_test_text(item)
                if summary:
                    lines.append(f"- {summary}")
    elif blocked_reason in {
        "review_loop_limit_exceeded",
        "rework_exhausted_after_ordinary_reviewer_findings",
        "rework_exhausted_after_missing_test_evidence",
    }:
        lines.extend(
            [
                "- Reviewer requested implementation rework.",
                "- Rework attempts were exhausted before the reviewer issues were resolved.",
            ]
        )
        if blocked_reason == "rework_exhausted_after_missing_test_evidence":
            lines.append("- Missing test evidence remained unresolved at the review boundary.")
        findings = list(reviewer_packet.get("review_findings") or [])
        if findings:
            lines.extend(["", "Reviewer findings:"])
            for item in findings:
                summary = _safe_test_text(item.get("summary")) if isinstance(item, dict) else _safe_test_text(item)
                if summary:
                    lines.append(f"- {summary}")
    elif blocked_reason in git_gate_reasons:
        lines.append(
            {
                "baseline_dirty": "- The workspace baseline was not clean before controlled completion could be trusted.",
                "baseline_invalid": "- The initial git baseline snapshot was invalid or unavailable.",
                "post_snapshot_invalid": "- The post-run git snapshot was invalid or unavailable.",
                "repo_path_mismatch": "- Git snapshot comparison failed because the baseline and post-run repositories did not match.",
                "git_diff_failed": "- Material diff could not be trusted because the git diff between baseline and post-run state failed.",
                "git_unavailable": "- Material diff could not be trusted because required git data was unavailable.",
            }[blocked_reason]
        )
        lines.append("- The repository baseline must be clean and comparable before automatic completion can proceed.")
    elif blocked_reason == "engineer_reported_blocked":
        lines.extend(
            [
                "- The engineer produced a valid, schema-conformant result and self-reported it could not proceed further.",
                "- Reviewer was not invoked because the engineer requested review instead of declaring completion.",
            ]
        )
    else:
        lines.extend(
            [
                "- Engineer output did not satisfy the controlled execution contract.",
                "- Reviewer was not invoked because engineer output was invalid.",
            ]
        )
    # reviewer_packet is the safe metadata wrapper built by _reviewer_packet_metadata
    # ({present, packet_status, ..., safe_packet: {...}}); engineer_summary and
    # blocked_reason_detail live inside safe_packet, not at the top level. Tests that
    # hand-build a flat reviewer_packet dict (no "safe_packet" key) still work because
    # we fall back to the dict itself.
    safe_packet = reviewer_packet.get("safe_packet")
    if not isinstance(safe_packet, dict):
        safe_packet = reviewer_packet
    engineer_summary = _safe_test_text(safe_packet.get("engineer_summary"))
    evidence_preserving_reasons = {
        "max_iterations_plain_text_output",
        "invalid_engineer_output",
        "engineer_result_invalid",
        "engineer_reported_blocked",
        "missing_structured_output",
    }
    if blocked_reason in evidence_preserving_reasons and engineer_summary:
        lines.extend(["", "Preserved diagnostic summary:", engineer_summary])
    engineer_sanitized_output = safe_packet.get("engineer_sanitized_output")
    if not isinstance(engineer_sanitized_output, dict):
        engineer_sanitized_output = {}
    engineer_blockers = [
        _safe_test_text(item) for item in list(engineer_sanitized_output.get("blockers") or [])
    ]
    engineer_blockers = [item for item in engineer_blockers if item]
    if blocked_reason in evidence_preserving_reasons and engineer_blockers:
        lines.extend(["", "Engineer-reported blockers:"])
        for item in engineer_blockers:
            lines.append(f"- {item}")
    engineer_next_action = _safe_test_text(engineer_sanitized_output.get("next_action"))
    if blocked_reason in evidence_preserving_reasons and engineer_next_action:
        lines.append(f"Suggested next action: {engineer_next_action}")
    changed_files = [
        str(item).strip() for item in list((safe_packet.get("git") or {}).get("changed_files") or []) if str(item).strip()
    ]
    if blocked_reason in evidence_preserving_reasons and changed_files:
        lines.extend(["", "Changed files:"])
        for path in changed_files:
            lines.append(f"- {path}")
    test_status = _safe_test_text((test_summary or {}).get("status"))
    test_command = _safe_test_text((test_summary or {}).get("command"))
    if test_status:
        lines.append(f"Test status: {test_status}")
    if test_command:
        lines.append(f"Test command: {test_command}")
    safe_summary = _safe_test_text((test_summary or {}).get("blocked_reason"))
    if safe_summary and safe_summary not in {"test_command_denied", "engineer_result_invalid"}:
        lines.append(f"Blocked reason: {safe_summary}")
    elif blocked_reason in git_gate_reasons | {"terminal_blocked"}:
        lines.append(f"Blocked reason: {blocked_reason}")
    detail = _safe_test_text(safe_packet.get("blocked_reason_detail"))
    raw_detail = str(safe_packet.get("blocked_reason_detail") or "").strip()
    if detail and detail not in {"missing_structured_output", "invalid_engineer_structured_output"}:
        lines.append(f"Blocked reason detail: {detail}")
    elif blocked_reason == "terminal_blocked" and raw_detail and re.fullmatch(r"[a-z0-9_:-]+", raw_detail):
        lines.append(f"Blocked reason detail: {raw_detail}")
    packet_status = str(reviewer_packet.get("packet_status") or "").strip()
    if packet_status and packet_status != "disabled":
        lines.append(f"Reviewer status: {packet_status}")
    return "\n".join(lines)


def _completion_allowed_final_response_text(
    *,
    git_gate: dict[str, Any] | None,
    test_summary: dict[str, Any] | None,
    reviewer_packet: dict[str, Any] | None,
) -> str:
    lines = [
        "Controlled engineering execution completed and stopped at the commit gate.",
        "",
    ]

    changed_files = [str(item).strip() for item in list((git_gate or {}).get("changed_files") or []) if str(item).strip()]
    if changed_files:
        lines.append("Changed files:")
        for path in changed_files:
            lines.append(f"- {path}")
        lines.append("")

    lines.append("Tests:")
    test_status = _safe_test_text((test_summary or {}).get("status")) or "unavailable"
    lines.append(f"- status: {test_status}")

    executed_command = _safe_test_text((test_summary or {}).get("executed_command"))
    requested_command = _safe_test_text((test_summary or {}).get("requested_command"))
    command = _safe_test_text((test_summary or {}).get("command"))
    command_relation = _safe_test_text((test_summary or {}).get("command_relation"))
    summary = _safe_test_text((test_summary or {}).get("summary"))

    if executed_command:
        lines.append(f"- command: {executed_command}")
    elif command:
        lines.append(f"- command: {command}")
    if requested_command and requested_command != executed_command:
        lines.append(f"- requested command: {requested_command}")
    if command_relation:
        lines.append(f"- command relation: {command_relation}")
    if summary:
        lines.append(f"- summary: {summary}")

    lines.extend(
        [
            "",
            "Reviewer:",
            "- approved: yes",
            "- decision: candidate_complete",
            "",
            "No commit or push was performed. Waiting for user approval before commit.",
        ]
    )

    packet_status = str((reviewer_packet or {}).get("packet_status") or "").strip()
    if packet_status and packet_status not in {"", "disabled", "not_built"}:
        lines.insert(-2, f"- status: {packet_status}")

    return "\n".join(lines)


def _finalize_loop_result(
    *,
    fuse: PipelineExecutionFuseResult,
    session: PipelineSession,
    snapshot: Any,
    preflight_allowed: bool,
    preflight_reason_code: str | None,
    iteration_history: list[ReworkLoopIterationRecord],
    review_iterations_completed: int,
    max_review_iterations: int,
    policy_source: str,
    original_task: str,
    appended_rework_context: list[dict[str, Any]],
    completion_allowed: bool,
    candidate_complete: bool,
    user_action_required: bool,
    blocked_reason: str | None,
    git_gate: dict[str, Any],
    reviewer_packet: dict[str, Any],
    subagent_runs: list[dict[str, Any]],
    peer_messages: list[dict[str, Any]],
    disagreements: list[dict[str, Any]],
    decisive_subagent: str | None,
    model_escalations: list[dict[str, Any]],
    tests: dict[str, Any],
    mutation_summary: dict[str, Any] | None = None,
    review_overrides: dict[str, Any] | None = None,
    test_summary: dict[str, Any] | None = None,
) -> PipelineReworkLoopResult:
    final_response_text = (
        _completion_allowed_final_response_text(
            git_gate=git_gate,
            test_summary=test_summary,
            reviewer_packet=reviewer_packet,
        )
        if completion_allowed and candidate_complete and blocked_reason is None
        else _blocked_final_response_text(
            blocked_reason=blocked_reason,
            test_summary=test_summary,
            reviewer_packet=reviewer_packet,
        )
    )
    return PipelineReworkLoopResult(
        fuse=fuse,
        state_snapshot=snapshot,
        execution_report=build_pipeline_execution_report(
            session=session,
            state_snapshot=snapshot,
            preflight_result={"allowed": preflight_allowed, "reason_code": preflight_reason_code},
            final_response_text=final_response_text,
            peer_messages=peer_messages,
            disagreements=disagreements,
            decisive_subagent=decisive_subagent,
            model_escalations=model_escalations,
            reviewer_packet=reviewer_packet,
            git_gate=git_gate,
            changed_files=list(git_gate.get("changed_files") or []),
            tests=tests,
            review_overrides=review_overrides or {},
            subagent_runs_override=subagent_runs,
            mutation_summary=mutation_summary or {},
        ),
        iteration_history=iteration_history,
        review_iterations_completed=review_iterations_completed,
        max_review_iterations=max_review_iterations,
        policy_source=policy_source,
        original_task=original_task,
        appended_rework_context=appended_rework_context,
        completion_allowed=completion_allowed,
        candidate_complete=candidate_complete,
        user_action_required=user_action_required,
        blocked_reason=blocked_reason,
        git_gate=git_gate,
        reviewer_packet=reviewer_packet,
        subagent_runs=_collect_subagent_runs(snapshot),
        usage_summary=_usage_summary_from_snapshot(snapshot),
        mutation_summary=mutation_summary or {},
        test_summary=test_summary,
    )


def _normalize_controlled_runtime_context(
    value: ControlledRuntimeContext | dict[str, Any] | None,
) -> ControlledRuntimeContext | None:
    if value is None:
        return None
    if isinstance(value, ControlledRuntimeContext):
        return value
    if not isinstance(value, dict):
        raise ValueError("controlled_runtime_context must be a mapping")
    if value.get("invocation_client") is None and value.get("executor_bridge") is None:
        raise ValueError("controlled_runtime_context requires invocation_client or executor_bridge")
    controlled_runner = value.get("controlled_runner")
    if not isinstance(controlled_runner, ControlledRuntimeRunner):
        controlled_runner = ControlledRuntimeRunner()
    return ControlledRuntimeContext(
        invocation_client=value.get("invocation_client"),
        executor_bridge=value.get("executor_bridge"),
        controlled_runner=controlled_runner,
        allow_model_escalation=bool(value.get("allow_model_escalation", False)),
        allow_real_provider_execution=bool(value.get("allow_real_provider_execution", False)),
        request_real_provider_execution=bool(value.get("request_real_provider_execution", False)),
        allowed_real_providers=tuple(str(item) for item in list(value.get("allowed_real_providers") or ()) if item is not None),
        allowed_real_models=tuple(str(item) for item in list(value.get("allowed_real_models") or ()) if item is not None),
        allowed_real_providers_by_role=_normalize_identity_policy(value.get("allowed_real_providers_by_role")),
        allowed_real_models_by_role=_normalize_identity_policy(value.get("allowed_real_models_by_role")),
        allowed_real_providers_by_subagent=_normalize_identity_policy(value.get("allowed_real_providers_by_subagent")),
        allowed_real_models_by_subagent=_normalize_identity_policy(value.get("allowed_real_models_by_subagent")),
        real_provider_client_factory=value.get("real_provider_client_factory"),
        allow_mutations=bool(value.get("allow_mutations", False)),
        mutation_workspace=str(value.get("mutation_workspace")) if value.get("mutation_workspace") is not None else None,
        allow_test_commands=bool(value.get("allow_test_commands", False)),
        test_workspace=str(value.get("test_workspace")) if value.get("test_workspace") is not None else None,
    )


def _normalize_identity_policy(value: Any) -> dict[str, tuple[str, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, tuple[str, ...]] = {}
    for key, items in value.items():
        normalized[str(key)] = tuple(str(item) for item in list(items or ()) if item is not None)
    return normalized


def _apply_step_mutations(
    *,
    step_kind: str,
    step_subagent_id: str,
    structured_output: dict[str, Any],
    runtime_context: ControlledRuntimeContext | None,
) -> dict[str, Any]:
    mutations = list(structured_output.get("mutations") or [])
    if not mutations:
        return _mutation_summary_disabled(runtime_context)
    first = dict(mutations[0] or {})
    path = str(first.get("path") or "<unknown>")
    operation = str(first.get("operation") or "write_text")
    if step_kind != "engineer" or step_subagent_id != ENGINEER_SUBAGENT_ID:
        raise MutationDenied("role_not_permitted", operation, path)
    if runtime_context is None:
        raise MutationDenied("mutation_gate_disabled", operation, path)
    summary = apply_controlled_mutations(
        allow_mutations=runtime_context.allow_mutations,
        mutation_workspace=runtime_context.mutation_workspace,
        mutations_payload=mutations,
    ).to_safe_dict()
    if int(summary.get("denied_count") or 0) > 0:
        first_denied = next((item for item in summary.get("results") or [] if item.get("status") == "denied"), None)
        if isinstance(first_denied, dict):
            raise MutationDenied(
                str(first_denied.get("reason") or "mutation_denied"),
                str(first_denied.get("operation") or operation),
                str(first_denied.get("path") or path),
            )
    return summary


def _mutation_summary_disabled(runtime_context: ControlledRuntimeContext | None) -> dict[str, Any]:
    workspace = Path(runtime_context.mutation_workspace).name if runtime_context and runtime_context.mutation_workspace else None
    enabled = bool(runtime_context.allow_mutations) if runtime_context is not None else False
    return {
        "enabled": enabled,
        "workspace": workspace,
        "attempted_count": 0,
        "applied_count": 0,
        "denied_count": 0,
        "results": [],
    }


def _mutation_summary_from_denied_exception(exc: MutationDenied, runtime_context: ControlledRuntimeContext | None) -> dict[str, Any]:
    summary = _mutation_summary_disabled(runtime_context)
    summary["attempted_count"] = 1
    summary["denied_count"] = 1
    summary["results"] = [exc.to_result().to_safe_dict()]
    return summary


def _merge_mutation_summaries(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(left.get("enabled") or right.get("enabled")),
        "workspace": left.get("workspace") or right.get("workspace"),
        "attempted_count": int(left.get("attempted_count") or 0) + int(right.get("attempted_count") or 0),
        "applied_count": int(left.get("applied_count") or 0) + int(right.get("applied_count") or 0),
        "denied_count": int(left.get("denied_count") or 0) + int(right.get("denied_count") or 0),
        "results": list(left.get("results") or []) + list(right.get("results") or []),
    }


def _apply_step_tests(
    *,
    step_kind: str,
    step_subagent_id: str,
    structured_output: dict[str, Any],
    runner_result: Any,
    runtime_context: ControlledRuntimeContext | None,
) -> dict[str, Any]:
    executed_summary = _machine_captured_test_summary(runner_result)
    if executed_summary is not None:
        return executed_summary
    tests = list(structured_output.get("tests") or [])
    if not tests:
        return _test_summary_disabled(runtime_context)
    return run_controlled_tests(
        allow_test_commands=bool(runtime_context.allow_test_commands) if runtime_context is not None else False,
        test_workspace=runtime_context.test_workspace if runtime_context is not None else None,
        tests_payload=tests,
        step_kind=step_kind,
        step_subagent_id=step_subagent_id,
    ).to_safe_dict()


def _preserve_requested_test_summary(
    *,
    current: dict[str, Any],
    original_task: str,
    engineer_output: dict[str, Any],
    runner_result: Any,
    runtime_context: ControlledRuntimeContext | None,
    test_workspace: str | None,
) -> dict[str, Any]:
    for source, command in _candidate_test_commands(
        original_task=original_task,
        engineer_output=engineer_output,
        runner_result=runner_result,
    ):
        if str(current.get("status") or "") != "not_requested":
            updated = dict(current)
            updated.setdefault("requested_command", command)
            if updated.get("executed_command") is None and updated.get("command") is not None:
                updated["executed_command"] = updated.get("command")
            return updated
        if not test_workspace:
            return current
        try:
            return preserve_explicit_pytest_command(
                raw_command=command,
                test_workspace=test_workspace,
                source=source,
            )
        except ValueError:
            continue
    return current


def _machine_captured_test_summary(runner_result: Any) -> dict[str, Any] | None:
    if not isinstance(runner_result, dict):
        return None
    for tool_call in _mapping_list(runner_result.get("tool_call_summaries")):
        if str(tool_call.get("tool_name") or "") != "pytest":
            continue
        payload = tool_call.get("result_payload")
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "").strip()
        if status not in MACHINE_CAPTURED_TEST_STATUSES:
            continue
        results = _mapping_list(payload.get("results"))
        first_result = results[0] if results else {}
        command_tokens = first_result.get("command")
        command = " ".join(str(token) for token in command_tokens) if isinstance(command_tokens, list) else None
        normalized = dict(payload)
        normalized["command"] = command
        normalized["executed_command"] = command
        normalized["exit_code"] = first_result.get("exit_code")
        normalized["source"] = str(payload.get("source") or "allowed_tool")
        normalized["results"] = results
        return normalized
    return None


def _candidate_test_commands(
    *,
    original_task: str,
    engineer_output: dict[str, Any],
    runner_result: Any,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(source: str, text: Any) -> None:
        for command in _extract_explicit_pytest_commands(text):
            if command in seen:
                continue
            seen.add(command)
            candidates.append((source, command))

    for item in list(engineer_output.get("tests") or []):
        _add("engineer_output", item)
    if isinstance(runner_result, dict):
        _add("engineer_output", runner_result.get("output_text"))
        for tool_call in _mapping_list(runner_result.get("tool_call_summaries")):
            if str(tool_call.get("tool_name") or "") == "pytest":
                _add("prompt_via_pytest_intent", original_task)
                break
    _add("prompt", original_task)
    return candidates


def _extract_explicit_pytest_commands(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        lines = [line.strip() for line in value.splitlines()]
        if len(lines) == 1:
            lines.append(value.strip())
    else:
        lines = [str(value).strip()]
    commands: list[str] = []
    prefixes = ("venv/bin/pytest ", ".venv/bin/pytest ", "pytest ", "python -m pytest ", "python3 -m pytest ")
    for line in lines:
        if not line:
            continue
        normalized = line.strip("`").strip()
        if normalized in {"venv/bin/pytest", ".venv/bin/pytest", "pytest"}:
            continue
        if any(normalized.startswith(prefix) for prefix in prefixes):
            commands.append(normalized)
    return commands


def _test_summary_disabled(runtime_context: ControlledRuntimeContext | None) -> dict[str, Any]:
    workspace = Path(runtime_context.test_workspace).name if runtime_context and runtime_context.test_workspace else None
    enabled = bool(runtime_context.allow_test_commands) if runtime_context is not None else False
    return {
        "enabled": enabled,
        "workspace": workspace,
        "status": "not_requested",
        "requested_count": 0,
        "executed_count": 0,
        "passed_count": 0,
        "failed_count": 0,
        "denied_count": 0,
        "timeout_count": 0,
        "blocked_reason": None,
        "summary": None,
        "results": [],
    }


def _collect_subagent_runs(snapshot: Any) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for step in list(getattr(snapshot, "planned_steps", []) or []):
        runner_result = getattr(step, "runner_result", None)
        if not isinstance(runner_result, dict) or not runner_result:
            continue
        runner_payload = dict(runner_result or {})
        runs.append(
            {
                "step_id": step.step_kind,
                "subagent_id": step.subagent_id,
                "role_id": step.step_kind,
                "status": runner_payload.get("status"),
                "runtime_mode": runner_payload.get("runtime_mode"),
                "real_provider_allowed": bool(runner_payload.get("real_provider_allowed", False)),
                "provider_policy_status": runner_payload.get("provider_policy_status"),
                "actual_provider": runner_payload.get("actual_provider"),
                "actual_model": runner_payload.get("actual_model"),
                "initial_provider": runner_payload.get("initial_provider"),
                "initial_model": runner_payload.get("initial_model"),
                "effective_provider": runner_payload.get("effective_provider"),
                "effective_model": runner_payload.get("effective_model"),
                "fallback_attempted": bool(runner_payload.get("fallback_attempted", False)),
                "fallback_activated": bool(runner_payload.get("fallback_activated", False)),
                "fallback_provider": runner_payload.get("fallback_provider"),
                "fallback_model": runner_payload.get("fallback_model"),
                "fallback_base_url": runner_payload.get("fallback_base_url"),
                "fallback_api_mode": runner_payload.get("fallback_api_mode"),
                "fallback_error": runner_payload.get("fallback_error"),
                "fallback_result": runner_payload.get("fallback_result"),
                "providers_used_effective": list(runner_payload.get("providers_used_effective") or []),
                "input_hash": runner_payload.get("input_hash"),
                "prompt_hash": runner_payload.get("prompt_hash"),
                "response_output_hash": runner_payload.get("response_output_hash"),
                "token_usage": _normalized_usage_payload(runner_payload.get("usage_summary")),
                "cache": _normalized_cache_payload(runner_payload.get("cache_summary")),
                "tool_call_summaries": _mapping_list(runner_payload.get("tool_call_summaries")),
                "elapsed_ms": runner_payload.get("elapsed_ms"),
                "failure_reason": runner_payload.get("failure_reason"),
                "error_type": runner_payload.get("error_type"),
                "raw_output_redacted": bool(runner_payload.get("raw_output_redacted", True)),
            }
        )
    return runs


def _append_step_run(accumulator: list[dict[str, Any]], snapshot: Any, step_index: int) -> None:
    planned_steps = list(getattr(snapshot, "planned_steps", []) or [])
    if len(planned_steps) <= step_index:
        return
    step = planned_steps[step_index]
    runner_result = getattr(step, "runner_result", None)
    if not isinstance(runner_result, dict) or not runner_result:
        return
    runner_payload = dict(runner_result or {})
    accumulator.append(
        {
            "step_id": step.step_kind,
            "subagent_id": step.subagent_id,
            "role_id": step.step_kind,
            "status": runner_payload.get("status"),
            "runtime_mode": runner_payload.get("runtime_mode"),
            "real_provider_allowed": bool(runner_payload.get("real_provider_allowed", False)),
            "provider_policy_status": runner_payload.get("provider_policy_status"),
            "actual_provider": runner_payload.get("actual_provider"),
            "actual_model": runner_payload.get("actual_model"),
            "initial_provider": runner_payload.get("initial_provider"),
            "initial_model": runner_payload.get("initial_model"),
            "effective_provider": runner_payload.get("effective_provider"),
            "effective_model": runner_payload.get("effective_model"),
            "fallback_attempted": bool(runner_payload.get("fallback_attempted", False)),
            "fallback_activated": bool(runner_payload.get("fallback_activated", False)),
            "fallback_provider": runner_payload.get("fallback_provider"),
            "fallback_model": runner_payload.get("fallback_model"),
            "fallback_base_url": runner_payload.get("fallback_base_url"),
            "fallback_api_mode": runner_payload.get("fallback_api_mode"),
            "fallback_error": runner_payload.get("fallback_error"),
            "fallback_result": runner_payload.get("fallback_result"),
            "providers_used_effective": list(runner_payload.get("providers_used_effective") or []),
            "input_hash": runner_payload.get("input_hash"),
            "prompt_hash": runner_payload.get("prompt_hash"),
            "response_output_hash": runner_payload.get("response_output_hash"),
            "token_usage": _normalized_usage_payload(runner_payload.get("usage_summary")),
            "cache": _normalized_cache_payload(runner_payload.get("cache_summary")),
            "tool_call_summaries": _mapping_list(runner_payload.get("tool_call_summaries")),
            "elapsed_ms": runner_payload.get("elapsed_ms"),
            "failure_reason": runner_payload.get("failure_reason"),
            "error_type": runner_payload.get("error_type"),
            "raw_output_redacted": bool(runner_payload.get("raw_output_redacted", True)),
        }
    )


def _usage_summary_from_snapshot(snapshot: Any) -> dict[str, Any]:
    return _usage_summary_from_subagent_runs(
        _collect_subagent_runs(snapshot),
        planned_subagent_count=len(list(getattr(snapshot, "planned_steps", []) or [])),
    )


def _usage_summary_from_subagent_runs(
    subagent_runs: list[dict[str, Any]],
    planned_subagent_count: int | None = None,
) -> dict[str, Any]:
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    token_sources: list[str] = []
    cache_sources: list[str] = []
    models_used: list[str] = []
    providers_used: list[str] = []
    providers_used_effective: list[str] = []
    planned_count = max(int(planned_subagent_count if planned_subagent_count is not None else len(subagent_runs)), 0)
    executed_subagent_count = 0
    subagent_run_instance_count = 0
    for run in subagent_runs:
        subagent_run_instance_count += 1
        if not _runner_result_is_reportable(run):
            continue
        executed_subagent_count += 1
        usage = _normalized_usage_payload(run.get("token_usage"))
        cache = _normalized_cache_payload(run.get("cache"))
        total_input_tokens += int(usage.get("input_tokens") or 0)
        total_output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        token_source = usage.get("source")
        cache_source = cache.get("source")
        model = run.get("actual_model")
        provider = run.get("actual_provider")
        effective_provider = run.get("effective_provider")
        effective_chain = [str(item) for item in list(run.get("providers_used_effective") or []) if str(item)]
        if token_source and token_source not in token_sources:
            token_sources.append(str(token_source))
        if cache_source and cache_source not in cache_sources:
            cache_sources.append(str(cache_source))
        if model and model not in models_used:
            models_used.append(str(model))
        if provider and provider not in providers_used:
            providers_used.append(str(provider))
        for candidate in effective_chain or ([str(effective_provider)] if effective_provider else []):
            if candidate and candidate not in providers_used_effective:
                providers_used_effective.append(candidate)
    return {
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "token_sources": token_sources,
        "cache_sources": cache_sources,
        "planned_subagent_count": planned_count,
        "executed_subagent_count": executed_subagent_count,
        "subagent_run_instance_count": subagent_run_instance_count,
        "execution_round_count": _execution_round_count(
            planned_subagent_count=planned_count,
            subagent_run_instance_count=subagent_run_instance_count,
        ),
        "subagent_count": subagent_run_instance_count,
        "models_used": models_used,
        "providers_used": providers_used,
        "providers_used_effective": providers_used_effective,
    }


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
    task_summary = safe_packet.get("task_summary")
    if task_summary:
        safe_packet["task_summary"] = "[redacted]"
        safe_packet["task_summary_hash"] = _stable_text_hash(str(task_summary))
    return {
        "present": True,
        "packet_status": safe_packet.get("packet_status"),
        "review_required": bool(safe_packet.get("review_required")),
        "user_action_required": bool(safe_packet.get("user_action_required")),
        "blocked_reason": safe_packet.get("blocked_reason"),
        "safe_packet": safe_packet,
    }


def _with_reviewer_findings(current: dict[str, Any], findings: list[str]) -> dict[str, Any]:
    if not findings:
        return current
    updated = dict(current)
    updated["review_findings"] = list(findings)
    return updated


def _with_synthesized_reviewer_findings(
    current: dict[str, Any],
    *,
    reviewer_status: str,
    reviewer_blockers: list[str],
    test_summary: dict[str, Any],
) -> dict[str, Any]:
    if list(current.get("review_findings") or []):
        return current
    synthesized = _synthesized_reviewer_findings(
        reviewer_status=reviewer_status,
        reviewer_blockers=reviewer_blockers,
        test_summary=test_summary,
    )
    if not synthesized:
        return current
    updated = dict(current)
    updated["review_findings"] = synthesized
    updated["review_findings_synthesized"] = True
    return updated


def _extract_reviewer_findings(reviewer_structured_output: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for item in list(reviewer_structured_output.get("findings") or []):
        if not isinstance(item, dict):
            continue
        summary = _safe_test_text(item.get("summary"))
        if summary:
            findings.append(summary)
    return findings


_ORDINARY_REVIEWER_BLOCK_STATUSES = {"blocked", "needs_review", "needs_escalation"}
_ORDINARY_TEST_EVIDENCE_GAP_STATUSES = {"requested_not_executed", "not_requested", "invalid", "unavailable", "missing"}


def _ordinary_reviewer_rework_reason(
    *,
    reviewer_status: str,
    reviewer_blockers: list[str],
    reviewer_packet: dict[str, Any],
    test_summary: dict[str, Any],
) -> str | None:
    if reviewer_status not in _ORDINARY_REVIEWER_BLOCK_STATUSES:
        return None
    if _catastrophic_reviewer_reason(reviewer_blockers) is not None:
        return None
    findings = list(reviewer_packet.get("review_findings") or [])
    findings_synthesized = bool(reviewer_packet.get("review_findings_synthesized"))
    if reviewer_blockers or findings:
        if any("missing_test_evidence" in str(item or "").lower() for item in reviewer_blockers):
            return "missing_test_evidence"
        if findings_synthesized and not reviewer_blockers:
            test_status = str(test_summary.get("status") or "").strip().lower()
            if test_status in _ORDINARY_TEST_EVIDENCE_GAP_STATUSES:
                if test_status == "requested_not_executed":
                    return "requested_test_not_executed"
                return "missing_test_evidence"
        return "ordinary_reviewer_findings"
    if not bool(reviewer_packet.get("present")):
        return None
    test_status = str(test_summary.get("status") or "").strip().lower()
    if test_status in _ORDINARY_TEST_EVIDENCE_GAP_STATUSES:
        if test_status == "requested_not_executed":
            return "requested_test_not_executed"
        return "missing_test_evidence"
    return None


def _synthesized_reviewer_findings(
    *,
    reviewer_status: str,
    reviewer_blockers: list[str],
    test_summary: dict[str, Any],
) -> list[str]:
    ordinary_reason = _ordinary_reviewer_rework_reason(
        reviewer_status=reviewer_status,
        reviewer_blockers=reviewer_blockers,
        reviewer_packet={},
        test_summary=test_summary,
    )
    if ordinary_reason not in {"requested_test_not_executed", "missing_test_evidence"}:
        return []
    test_command = _safe_test_text(test_summary.get("command"))
    if ordinary_reason == "requested_test_not_executed" and test_command:
        return [f"Run {test_command} and attach the result."]
    if ordinary_reason in {"requested_test_not_executed", "missing_test_evidence"}:
        return ["Provide passing test evidence through the controlled pytest path before completion."]
    return []


_CATASTROPHIC_REVIEW_CODES = {
    "credential_exfiltration_risk",
    "credential_exposure",
    "destructive_operation",
    "unrelated_repo_mutation",
    "uninspectable_diff",
    "severely_broken_code",
    "safety_policy_violation",
    "unsafe_bypass",
}

_CATASTROPHIC_REVIEW_TEXT = (
    "credential exfiltration",
    "credential exposure",
    "secret leak",
    "destructive operation",
    "rm -rf",
    "unrelated mutation",
    "cannot inspect diff",
    "uninspectable diff",
    "unsafe bypass",
)


def _compose_engineer_message(*, original_task: str, appended_rework_context: list[dict[str, Any]]) -> str:
    if not appended_rework_context:
        return original_task
    return "\n\n".join([original_task, "Normalized reviewer feedback:", *[_serialize_rework_context(item) for item in appended_rework_context]])


def _compose_reviewer_message(
    *,
    original_task: str,
    engineer_message: str,
    appended_rework_context: list[dict[str, Any]],
) -> str:
    parts = [original_task, "Engineer candidate follows.", engineer_message]
    if appended_rework_context:
        parts.extend(_serialize_rework_context(item) for item in appended_rework_context)
    return "\n\n".join(parts)


def _compose_peer_reviewer_message(*, original_task: str, peer_message: dict[str, Any]) -> str:
    summary = _mapping_value((peer_message.get("content") or {}), "summary") or "Engineer submitted a disagreement summary."
    arguments = list((peer_message.get("content") or {}).get("arguments") or [])
    evidence = list((peer_message.get("content") or {}).get("evidence") or [])
    parts = [
        original_task,
        "Peer discussion follow-up.",
        f"Summary: {summary}",
    ]
    if arguments:
        parts.append("Arguments: " + "; ".join(str(item) for item in arguments))
    if evidence:
        parts.append("Evidence: " + "; ".join(str(item) for item in evidence))
    return "\n\n".join(parts)


def _compose_escalation_message(*, original_task: str, reviewer_blockers: list[str], reason: str) -> str:
    return "\n\n".join(
        [
            original_task,
            "Escalated reviewer arbitration requested.",
            f"Escalation reason: {reason}",
            "Reviewer blockers: " + "; ".join(reviewer_blockers or ["none"]),
        ]
    )


def _safe_execution_report_payload(execution_report: Any) -> dict[str, Any] | None:
    if hasattr(execution_report, "to_safe_dict"):
        payload = execution_report.to_safe_dict()
        return dict(payload) if isinstance(payload, dict) else None
    if isinstance(execution_report, dict):
        return dict(execution_report)
    return None


def _serialize_rework_context(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _build_rework_context(
    *,
    iteration_index: int,
    reviewer_eval: dict[str, Any],
    reviewer_structured_output: dict[str, Any],
    reviewer_packet: dict[str, Any],
    test_summary: dict[str, Any],
) -> dict[str, Any]:
    blocking_findings: list[str] = []
    non_blocking_findings: list[str] = []
    for item in list(reviewer_structured_output.get("findings") or []):
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        severity = str(item.get("severity") or "").lower()
        if severity in {"high", "critical", "blocker"}:
            blocking_findings.append(summary)
        else:
            non_blocking_findings.append(summary)
    safe_packet = dict(reviewer_packet.get("safe_packet") or {})
    git_payload = dict(safe_packet.get("git") or {})
    normalized_rework_reason = _ordinary_reviewer_rework_reason(
        reviewer_status=str(reviewer_eval.get("status") or "not_evaluated"),
        reviewer_blockers=[str(item) for item in list(reviewer_eval.get("blockers") or []) if item],
        reviewer_packet=reviewer_packet,
        test_summary=test_summary,
    )
    return {
        "review_iteration": iteration_index,
        "reviewer_verdict": str(reviewer_eval.get("status") or "not_evaluated"),
        "reviewer_blockers": [str(item) for item in list(reviewer_eval.get("blockers") or []) if item],
        "blocking_findings": blocking_findings,
        "non_blocking_findings": non_blocking_findings,
        "reviewer_packet_summary": {
            "packet_status": safe_packet.get("packet_status"),
            "changed_files": list(git_payload.get("changed_files") or []),
            "material_change_status": git_payload.get("material_change_status"),
            "review_required": bool(safe_packet.get("review_required")),
        },
        "test_status": str(test_summary.get("status") or "unknown"),
        "test_command": _safe_test_text(test_summary.get("command")),
        "normalized_rework_reason": normalized_rework_reason,
    }


def _engineer_requests_disagreement(engineer_output: dict[str, Any]) -> bool:
    return str(engineer_output.get("status") or "") == "disagree_with_reviewer" and str(
        engineer_output.get("next_action") or ""
    ) == "disagreement"


def _build_peer_message(
    *,
    session: PipelineSession,
    engineer_output: dict[str, Any],
    reviewer_blockers: list[str],
    related_verdict_id: str,
) -> dict[str, Any]:
    evidence: list[str] = []
    for item in list(engineer_output.get("evidence") or [])[:MAX_SAFE_EVIDENCE_ITEMS]:
        if not item:
            continue
        safe_item = _safe_path_evidence(item)
        if safe_item:
            evidence.append(safe_item)
    return {
        "message_id": f"peer-{session.pipeline_session_id}-{len(evidence)}-{len(reviewer_blockers)}",
        "pipeline_session_id": session.pipeline_session_id,
        "from_subagent": ENGINEER_SUBAGENT_ID,
        "to_subagent": REVIEWER_SUBAGENT_ID,
        "type": "disagreement",
        "related_verdict_id": related_verdict_id,
        "content": {
            "summary": str(engineer_output.get("summary") or "Engineer disagrees with reviewer blocker."),
            "arguments": [str(item) for item in list(engineer_output.get("reviewer_objections") or reviewer_blockers)],
            "evidence": evidence,
        },
        "requires_response": True,
    }


def _build_disagreement_record(
    *,
    status: str,
    reviewer_blockers: list[str],
    peer_round_limit_status: str | None,
    decisive_subagent: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "reviewer_blockers": list(reviewer_blockers),
        "peer_round_limit_status": peer_round_limit_status,
        "decisive_subagent": decisive_subagent,
    }


def _build_model_escalation_record(
    *,
    status: str,
    trigger: str,
    reason: str,
    from_subagent: str,
    to_subagent: str = ESCALATED_REVIEWER_SUBAGENT_ID,
    decisive_subagent: str | None = None,
    verdict: str | None = None,
    run: dict[str, Any] | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "escalation_id": f"escalation:{_stable_text_hash('|'.join([trigger, status, from_subagent]))[:12]}",
        "condition": "disagreement_unresolved_after_allowed_peer_discussion",
        "trigger": trigger,
        "status": status,
        "from_subagent": from_subagent,
        "to_subagent": to_subagent,
        "decisive_subagent": decisive_subagent or REVIEWER_SUBAGENT_ID,
        "target_subagent": to_subagent,
        "escalation_model_class": "senior_reasoning",
        "reason": reason,
        "verdict": verdict,
        "blocked_reason": blocked_reason,
        "redaction_markers": {
            "reason_redacted": False,
            "raw_output_redacted": True,
        },
    }
    if run is not None:
        payload["run_id"] = f"{run.get('step_id')}:{run.get('subagent_id')}"
        payload["actual_provider"] = run.get("actual_provider")
        payload["actual_model"] = run.get("actual_model")
        payload["usage"] = dict(run.get("token_usage") or {})
    return payload


def _build_escalated_reviewer_spec(reviewer_spec: dict[str, Any]) -> dict[str, Any]:
    if not reviewer_spec:
        return {}
    escalated_spec = dict(reviewer_spec)
    escalated_spec["id"] = ESCALATED_REVIEWER_SUBAGENT_ID
    escalated_spec["display_name"] = "Hermes Code Reviewer Escalated"
    escalated_spec["purpose"] = "Controlled escalated reviewer/arbitrator for disagreement resolution."
    models = dict(reviewer_spec.get("models") or {})
    default_model = dict(models.get("default") or {})
    default_model["class"] = "senior_reasoning"
    allowed_models = [dict(item) for item in list(models.get("allowed") or [])]
    if not allowed_models and default_model:
        allowed_models = [dict(default_model)]
    for item in allowed_models:
        item["class"] = "senior_reasoning"
    models["default"] = default_model
    models["allowed"] = allowed_models
    models["escalation"] = {"allowed": False, "rules": []}
    escalated_spec["models"] = models
    return escalated_spec


def _validate_escalation_verdict(raw_payload: Any, envelope: Any) -> dict[str, Any]:
    if not isinstance(raw_payload, dict):
        return {"status": "invalid", "decision": None}
    if getattr(envelope, "validation_status", None) != "valid":
        return {"status": "invalid", "decision": None}
    decision = str(raw_payload.get("decision") or "")
    if decision not in ESCALATED_REVIEWER_ALLOWED_DECISIONS:
        return {"status": "invalid", "decision": None}
    status = str(raw_payload.get("status") or "")
    blockers = list(raw_payload.get("blockers") or [])
    if decision == ESCALATED_REVIEWER_DECISION_APPROVED:
        if status != "succeeded" or blockers:
            return {"status": "invalid", "decision": None}
    elif decision == ESCALATED_REVIEWER_DECISION_BLOCKER_MAINTAINED:
        if status not in {"blocked", "needs_review"} or not blockers:
            return {"status": "invalid", "decision": None}
    else:
        if status not in {"blocked", "needs_review"} or not blockers:
            return {"status": "invalid", "decision": None}
    return {"status": "valid", "decision": decision}


def _subagent_run_from_result(*, step_id: str, subagent_id: str, role_id: str, runner_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "subagent_id": subagent_id,
        "role_id": role_id,
        "status": runner_result.get("status"),
        "runtime_mode": runner_result.get("runtime_mode"),
        "real_provider_allowed": bool(runner_result.get("real_provider_allowed", False)),
        "provider_policy_status": runner_result.get("provider_policy_status"),
        "actual_provider": runner_result.get("actual_provider"),
        "actual_model": runner_result.get("actual_model"),
        "initial_provider": runner_result.get("initial_provider"),
        "initial_model": runner_result.get("initial_model"),
        "effective_provider": runner_result.get("effective_provider"),
        "effective_model": runner_result.get("effective_model"),
        "fallback_attempted": bool(runner_result.get("fallback_attempted", False)),
        "fallback_activated": bool(runner_result.get("fallback_activated", False)),
        "fallback_provider": runner_result.get("fallback_provider"),
        "fallback_model": runner_result.get("fallback_model"),
        "fallback_base_url": runner_result.get("fallback_base_url"),
        "fallback_api_mode": runner_result.get("fallback_api_mode"),
        "fallback_error": runner_result.get("fallback_error"),
        "fallback_result": runner_result.get("fallback_result"),
        "providers_used_effective": list(runner_result.get("providers_used_effective") or []),
        "input_hash": runner_result.get("input_hash"),
        "prompt_hash": runner_result.get("prompt_hash"),
        "response_output_hash": runner_result.get("response_output_hash"),
        "token_usage": _normalized_usage_payload(runner_result.get("usage_summary")),
        "cache": _normalized_cache_payload(runner_result.get("cache_summary")),
        "tool_call_summaries": _mapping_list(runner_result.get("tool_call_summaries")),
        "elapsed_ms": runner_result.get("elapsed_ms"),
        "failure_reason": runner_result.get("failure_reason"),
        "error_type": runner_result.get("error_type"),
        "raw_output_redacted": bool(runner_result.get("raw_output_redacted", True)),
    }


def _tests_payload(test_summary: Any) -> dict[str, Any]:
    if isinstance(test_summary, dict):
        payload = {
            "status": str(test_summary.get("status") or "available"),
            "source": str(test_summary.get("source") or "provided"),
            "summary": _safe_test_text(test_summary.get("summary")),
        }
        if test_summary.get("blocked_reason") is not None:
            payload["blocked_reason"] = _safe_test_text(test_summary.get("blocked_reason"))
        if isinstance(test_summary.get("results"), list):
            payload["results"] = [
                {
                    **({key: value for key, value in dict(item).items() if key not in {"stdout_excerpt", "stderr_excerpt"}}),
                    **({"stdout_excerpt": _safe_test_text(item.get("stdout_excerpt"))} if isinstance(item, dict) and item.get("stdout_excerpt") is not None else {}),
                    **({"stderr_excerpt": _safe_test_text(item.get("stderr_excerpt"))} if isinstance(item, dict) and item.get("stderr_excerpt") is not None else {}),
                }
                for item in list(test_summary.get("results") or [])
                if isinstance(item, dict)
            ]
        return payload
    return {
        "status": "unavailable",
        "source": "unavailable",
        "summary": None,
    }


def _safe_test_text(value: Any) -> str | None:
    if value is None:
        return None
    lines: list[str] = []
    for raw_line in str(value).splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if any(marker in line for marker in _DIFF_MARKERS):
            continue
        if any(part in lower for part in _SENSITIVE_PARTS):
            continue
        if line:
            lines.append(line)
    cleaned = " ".join(lines).strip()
    return cleaned or None


def _safe_path_evidence(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _looks_like_path(text):
        return _sanitize_path_like_evidence(text)
    return _redacted_evidence_label(text)


def _looks_like_path(text: str) -> bool:
    return "/" in text or "\\" in text or text.startswith(".")


def _sanitize_path_like_evidence(text: str) -> str:
    normalized = text.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return _redacted_evidence_label(text)
    if any(part == ".." for part in parts):
        return _redacted_evidence_label(text)
    leaf = parts[-1]
    if not _is_safe_filename(leaf):
        return _redacted_evidence_label(text)
    return leaf[:MAX_SAFE_EVIDENCE_LABEL_CHARS]


def _is_safe_filename(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return all(ch in allowed for ch in value)


def _redacted_evidence_label(text: str) -> str:
    return f"[redacted:{_stable_text_hash(text)[:12]}]"


def _execution_round_count(*, planned_subagent_count: int, subagent_run_instance_count: int) -> int:
    if planned_subagent_count <= 0 or subagent_run_instance_count <= 0:
        return 0
    quotient, remainder = divmod(subagent_run_instance_count, planned_subagent_count)
    return quotient + (1 if remainder else 0)


def _active_reviewer_blockers(*, current_snapshot: Any, pending_reviewer_blockers: list[str]) -> list[str]:
    if pending_reviewer_blockers:
        return list(pending_reviewer_blockers)
    planned_steps = list(getattr(current_snapshot, "planned_steps", []) or [])
    if len(planned_steps) <= 1:
        return []
    evaluation_result = getattr(planned_steps[1], "evaluation_result", None) or {}
    return [str(item) for item in list(evaluation_result.get("blockers") or []) if item]


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


def _engineer_fail_closed_reason(state_snapshot: Any, *, material_changes_present: bool = False) -> str | None:
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
    structured_output = _step_structured_output(state_snapshot, 0)
    if not structured_output:
        if material_changes_present:
            return None
        return "missing_structured_output"
    if structured_output.get("validation_status") == "missing_structured_output":
        if material_changes_present:
            return None
        for item in list(structured_output.get("validation_errors") or []):
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or "").strip()
            if message == "engineer_max_iterations_without_structured_output":
                return "max_iterations_plain_text_output"
        return "missing_structured_output"
    if _engineer_requests_disagreement(structured_output):
        return None
    if evaluation_status != REVIEWER_APPROVAL_STATUS:
        if material_changes_present:
            return None
        # A schema-valid envelope that self-reports it cannot proceed (blocked /
        # needs_review / needs_escalation) is not the same failure as a malformed
        # or unparsable envelope. Conflating the two collapsed legitimate
        # engineer-reported blockers into the generic "invalid_engineer_output"
        # contract violation and discarded the engineer's evidence in the final
        # response (see 2026-07-02 09:44-09:47 Slack incident).
        if structured_output.get("validation_status") == "valid":
            return "engineer_reported_blocked"
        return "invalid_engineer_output"
    return None


def _reviewer_fail_closed_reason(
    *,
    reviewer_status: str,
    reviewer_blockers: list[str],
    reviewer_packet: dict[str, Any],
    test_summary: dict[str, Any],
) -> str | None:
    if reviewer_status == REVIEWER_APPROVAL_STATUS:
        return None if not reviewer_blockers else "reviewer_verdict_blocked"
    catastrophic_reason = _catastrophic_reviewer_reason(reviewer_blockers)
    if catastrophic_reason is not None:
        return catastrophic_reason
    ordinary_rework_reason = _ordinary_reviewer_rework_reason(
        reviewer_status=reviewer_status,
        reviewer_blockers=reviewer_blockers,
        reviewer_packet=reviewer_packet,
        test_summary=test_summary,
    )
    if ordinary_rework_reason is not None:
        return None
    if reviewer_blockers:
        return None
    if reviewer_status == "invalid_structured_output":
        return "reviewer_result_invalid"
    if reviewer_status in {"blocked", "needs_review", "needs_escalation"}:
        return "reviewer_verdict_blocked"
    if reviewer_status in {"not_evaluated", "", "None"}:
        return "reviewer_unavailable"
    return "reviewer_result_invalid"


def _catastrophic_reviewer_reason(reviewer_blockers: list[str]) -> str | None:
    for raw_item in reviewer_blockers:
        blocker = str(raw_item or "").strip()
        if not blocker:
            continue
        normalized = blocker.lower()
        if normalized in _CATASTROPHIC_REVIEW_CODES:
            return "terminal_blocked"
        if any(marker in normalized for marker in _CATASTROPHIC_REVIEW_TEXT):
            return "terminal_blocked"
    return None


def _rework_exhausted_reason(
    *,
    reviewer_status: str,
    reviewer_blockers: list[str],
    reviewer_packet: dict[str, Any],
    test_summary: dict[str, Any],
) -> str:
    ordinary_reason = _ordinary_reviewer_rework_reason(
        reviewer_status=reviewer_status,
        reviewer_blockers=reviewer_blockers,
        reviewer_packet=reviewer_packet,
        test_summary=test_summary,
    )
    if ordinary_reason in {"requested_test_not_executed", "missing_test_evidence"}:
        return "rework_exhausted_after_missing_test_evidence"
    if ordinary_reason == "ordinary_reviewer_findings":
        return "rework_exhausted_after_ordinary_reviewer_findings"
    return "review_loop_limit_exceeded"


def _stable_text_hash(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
