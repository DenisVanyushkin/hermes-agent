from __future__ import annotations

from hermes_cli.pipeline_control_channel import (
    ControlledMessageChannelPlan,
    ControlledSubagentMessage,
    LoopCounterSnapshot,
    PipelineControlStatus,
    build_controlled_message_decision,
    evaluate_loop_limit,
    resolve_loop_limit_policy,
)
from hermes_cli.pipeline_specs import load_pipeline_specs


def _message(*, purpose: str = "review_feedback") -> ControlledSubagentMessage:
    return ControlledSubagentMessage(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        source_subagent_id="hermes_code_reviewer",
        target_subagent_id="hermes_engineer_core",
        message_purpose=purpose,
        payload_summary="One blocker requires rework.",
        related_step_id="reviewer",
        evaluation_id="eval-1",
    )


def test_loop_policy_loaded_from_engineering_pipeline_spec() -> None:
    loaded = load_pipeline_specs()

    policy = resolve_loop_limit_policy(loaded.pipeline_specs["engineering_review_pipeline"])

    assert policy.max_review_iterations == 3
    assert policy.max_peer_discussion_rounds == 1
    assert policy.max_disagreement_rounds == 1
    assert policy.max_invalid_output_retries == 1
    assert policy.max_tool_retries == 1
    assert policy.resets_on_model_escalation is False
    assert policy.policy_source == "pipeline_spec"


def test_safe_default_policy_fails_closed_when_missing() -> None:
    policy = resolve_loop_limit_policy({})

    assert policy.max_review_iterations == 1
    assert policy.max_peer_discussion_rounds == 0
    assert policy.max_invalid_output_retries == 0
    assert policy.policy_source == "safe_default"


def test_within_limit_reviewer_feedback_message_is_allowed_as_metadata() -> None:
    policy = resolve_loop_limit_policy(
        {"loop_policy": {"max_review_iterations": 3}, "model_escalation_policy": {"resets_loop_counters": False}}
    )

    decision = build_controlled_message_decision(
        message=_message(),
        policy=policy,
        counters=LoopCounterSnapshot(review_iterations=1),
        limit_name="max_review_iterations",
        runner_invoked=True,
    )

    assert decision.allowed is True
    assert decision.status is PipelineControlStatus.ALLOWED
    assert decision.loop_limit_decision is not None
    assert decision.loop_limit_decision.next_counter.review_iterations == 2


def test_loop_limit_exceeded_blocks_message_and_requires_user_escalation() -> None:
    policy = resolve_loop_limit_policy({"loop_policy": {"max_review_iterations": 2}})

    decision = build_controlled_message_decision(
        message=_message(),
        policy=policy,
        counters=LoopCounterSnapshot(review_iterations=2),
        limit_name="max_review_iterations",
        runner_invoked=True,
    )

    assert decision.allowed is False
    assert decision.status is PipelineControlStatus.LOOP_LIMIT_EXCEEDED
    assert decision.user_action_required is True
    assert decision.loop_limit_decision is not None
    assert decision.loop_limit_decision.reason == "max_review_iterations_exceeded"


def test_invalid_output_retry_respects_max_invalid_output_retries() -> None:
    policy = resolve_loop_limit_policy({"loop_policy": {"max_invalid_output_retries": 1}})

    allowed = evaluate_loop_limit(
        policy=policy,
        counters=LoopCounterSnapshot(invalid_output_retries=0),
        limit_name="max_invalid_output_retries",
    )
    blocked = evaluate_loop_limit(
        policy=policy,
        counters=LoopCounterSnapshot(invalid_output_retries=1),
        limit_name="max_invalid_output_retries",
    )

    assert allowed.allowed is True
    assert allowed.next_counter.invalid_output_retries == 1
    assert blocked.allowed is False
    assert blocked.status is PipelineControlStatus.LOOP_LIMIT_EXCEEDED


def test_disagreement_round_respects_max_disagreement_rounds() -> None:
    policy = resolve_loop_limit_policy({"loop_policy": {"max_disagreement_rounds": 1}})

    blocked = evaluate_loop_limit(
        policy=policy,
        counters=LoopCounterSnapshot(disagreement_rounds=1),
        limit_name="max_disagreement_rounds",
    )

    assert blocked.allowed is False
    assert blocked.reason == "max_disagreement_rounds_exceeded"


def test_model_escalation_does_not_reset_counters_unless_explicitly_allowed() -> None:
    policy = resolve_loop_limit_policy(
        {
            "loop_policy": {"max_review_iterations": 3},
            "model_escalation_policy": {"enabled": True, "resets_loop_counters": False},
        }
    )
    counters = LoopCounterSnapshot(review_iterations=2, model_escalations=1)

    decision = evaluate_loop_limit(
        policy=policy,
        counters=counters,
        limit_name="max_review_iterations",
    )

    assert policy.resets_on_model_escalation is False
    assert decision.allowed is True
    assert decision.next_counter.review_iterations == 3
    assert decision.next_counter.model_escalations == 1


def test_runner_not_invoked_does_not_produce_fake_message_exchange() -> None:
    policy = resolve_loop_limit_policy({"loop_policy": {"max_review_iterations": 3}})

    decision = build_controlled_message_decision(
        message=_message(),
        policy=policy,
        counters=LoopCounterSnapshot(review_iterations=0),
        limit_name="max_review_iterations",
        runner_invoked=False,
    )
    plan = ControlledMessageChannelPlan(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        policy=policy,
        counters=LoopCounterSnapshot(),
        decisions=[decision],
    )

    assert decision.allowed is False
    assert decision.status is PipelineControlStatus.RUNNER_NOT_INVOKED
    assert decision.blocked_reason == "runner_not_invoked"
    assert plan.execution_enabled is False
