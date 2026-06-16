from __future__ import annotations

from hermes_cli.pipeline_evaluation import (
    PipelineEvaluationRequest,
    PipelineEvaluationStatus,
    evaluate_pipeline_step,
)
from hermes_cli.subagent_runner import (
    StructuredOutputEnvelope,
    SubagentRunnerResult,
    SubagentRunnerStatus,
    validate_structured_output_envelope,
)


def _runner_result(
    *,
    status: SubagentRunnerStatus,
    structured_output: StructuredOutputEnvelope | None = None,
    failure_reason: str | None = None,
) -> SubagentRunnerResult:
    return SubagentRunnerResult(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        step_id="engineer",
        subagent_id="hermes_engineer_core",
        role_id="engineer",
        runtime_factory_plan_id="pipe-1:engineer:hermes_engineer_core",
        runtime_factory_status="plan_only",
        status=status,
        failure_reason=failure_reason,
        structured_output=structured_output,
        schema_validation_status=(
            structured_output.validation_status if structured_output is not None else "not_applicable"
        ),
    )


def _request(
    runner_result: SubagentRunnerResult,
    *,
    structured_output: StructuredOutputEnvelope | None = None,
    execution_mode: str = "observe_plan_only",
    pipeline_spec: dict[str, object] | None = None,
) -> PipelineEvaluationRequest:
    return PipelineEvaluationRequest(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        step_id="engineer",
        subagent_id="hermes_engineer_core",
        execution_mode=execution_mode,
        runner_result=runner_result,
        structured_output=structured_output,
        pipeline_spec=_engineering_pipeline_spec() if pipeline_spec is None else pipeline_spec,
        runtime_factory_plan={
            "provider": "openrouter",
            "model": "xiaomi/mimo-v2.5-pro",
            "model_class": "base_coding",
            "status": "plan_only",
        },
        subagent_spec={},
        all_subagent_specs=_subagent_specs(),
    )


def _envelope(**overrides: object) -> StructuredOutputEnvelope:
    payload = {
        "schema_version": "v1",
        "subagent_id": "hermes_engineer_core",
        "role": "engineer",
        "status": "succeeded",
        "summary": "Prepared a patch plan.",
        "findings": [{"code": "plan", "summary": "Patch prepared"}],
        "changes": [{"path": "hermes_cli/pipeline_evaluation.py", "kind": "modify"}],
        "blockers": [],
        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
        "confidence": 0.92,
        "requires_review": False,
        "next_action": "none",
    }
    payload.update(overrides)
    return validate_structured_output_envelope(payload)


def _engineering_pipeline_spec() -> dict[str, object]:
    return {
        "subagents": {
            "engineer": "hermes_engineer_core",
            "reviewer": "hermes_code_reviewer",
        },
        "loop_policy": {
            "max_review_iterations": 3,
            "max_disagreement_rounds_per_iteration": 1,
            "max_invalid_output_retries": 1,
            "max_model_escalations": 1,
            "max_tool_retries": 1,
        },
        "model_escalation_policy": {
            "enabled": True,
            "resets_loop_counters": False,
            "rules": [
                {
                    "condition": "low_confidence",
                    "target_subagent": "engineer",
                    "escalate_to_model_class": "senior_coding",
                },
                {
                    "condition": "invalid_structured_output",
                    "target_subagent": "reviewer",
                    "escalate_to_model_class": "senior_review",
                },
            ],
        },
        "disagreement_policy": {
            "enabled": True,
            "decisive_subagent": "hermes_code_reviewer",
            "escalation_allowed": True,
            "escalation_trigger": "engineer_reviewer_disagreement",
            "max_rounds": 1,
        },
    }


def _subagent_specs() -> dict[str, dict[str, object]]:
    return {
        "hermes_engineer_core": {
            "models": {
                "allowed": [
                    {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "class": "base_coding"},
                    {"provider": "openai-codex", "model": "gpt-5.5", "class": "senior_coding"},
                ]
            }
        },
        "hermes_code_reviewer": {
            "models": {
                "allowed": [
                    {"provider": "openai-codex", "model": "gpt-5.5", "class": "senior_review"}
                ]
            }
        },
    }


def test_not_invoked_runner_result_blocks_completion() -> None:
    result = evaluate_pipeline_step(
        _request(
            _runner_result(
                status=SubagentRunnerStatus.NOT_INVOKED,
                failure_reason="observe_mode_plan_only",
            )
        )
    )

    assert result.status is PipelineEvaluationStatus.NOT_EVALUATED
    assert result.failure_reason == "runner_not_invoked"
    assert result.completion.completion_allowed is False
    assert result.decision_path == ["runner_not_invoked"]
    assert result.control_channel is not None
    assert result.control_channel.decisions == []
    assert result.model_escalation is not None
    assert result.model_escalation.blocked_reason == "runner_not_invoked"


def test_invalid_structured_envelope_fails_closed() -> None:
    invalid = validate_structured_output_envelope({"status": "succeeded"})

    result = evaluate_pipeline_step(
        _request(
            _runner_result(
                status=SubagentRunnerStatus.SUCCEEDED,
                structured_output=invalid,
            ),
            structured_output=invalid,
        )
    )

    assert result.status is PipelineEvaluationStatus.INVALID_STRUCTURED_OUTPUT
    assert result.failure_reason == "invalid_structured_output"
    assert result.completion.completion_allowed is False
    assert result.validation_errors
    assert result.control_channel is not None
    assert result.control_channel.decisions[0].message.message_purpose == "clarification"
    assert result.model_escalation is not None
    assert result.model_escalation.allowed is True
    assert result.model_escalation.candidate_model["model"] == "gpt-5.5"


def test_valid_envelope_with_blockers_blocks_completion() -> None:
    envelope = _envelope(blockers=["failing test"], next_action="fix_blocker")

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.SUCCEEDED, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.status is PipelineEvaluationStatus.BLOCKED
    assert result.blockers == ["failing test"]
    assert result.next_action == "fix_blocker"
    assert result.completion.completion_allowed is False
    assert result.control_channel is not None
    assert result.control_channel.decisions[0].message.target_subagent_id == "hermes_code_reviewer"
    assert result.disagreement.disagreement_present is False
    assert result.disagreement_resolution is None


def test_requires_review_plans_review() -> None:
    envelope = _envelope(status="needs_review", requires_review=True, confidence=0.86, next_action="review")

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.NEEDS_REVIEW, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.status is PipelineEvaluationStatus.NEEDS_REVIEW
    assert result.review.review_required is True
    assert result.review.review_planned is True
    assert result.completion.completion_allowed is False
    assert result.control_channel is not None
    assert result.control_channel.decisions[0].allowed is True
    assert result.disagreement.disagreement_present is False
    assert result.disagreement_resolution is None


def test_successful_valid_envelope_becomes_candidate_complete() -> None:
    envelope = _envelope()

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.SUCCEEDED, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.status is PipelineEvaluationStatus.CANDIDATE_COMPLETE
    assert result.completion.completion_allowed is False
    assert result.completion.candidate_complete is True


def test_low_confidence_plans_escalation_and_blocks_completion() -> None:
    envelope = _envelope(confidence=0.41)

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.SUCCEEDED, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.status is PipelineEvaluationStatus.NEEDS_ESCALATION
    assert result.escalation.escalation_required is True
    assert result.escalation.escalation_planned is True
    assert result.completion.completion_allowed is False
    assert result.control_channel is not None
    assert result.control_channel.policy.resets_on_model_escalation is False
    assert result.model_escalation is not None
    assert result.model_escalation.current_model["model"] == "xiaomi/mimo-v2.5-pro"
    assert result.model_escalation.candidate_model["model"] == "gpt-5.5"
    assert result.model_escalation.counters_reset is False
    assert result.model_escalation.counters_persist is True


def test_default_pipeline_has_no_engineering_control_channel_metadata() -> None:
    envelope = _envelope()
    result = evaluate_pipeline_step(
        PipelineEvaluationRequest(
            pipeline_session_id="pipe-1",
            trace_id="trace-1",
            pipeline_id="default_conversation_pipeline",
            step_id="response",
            subagent_id="general_operator",
            execution_mode="observe_plan_only",
            runner_result=_runner_result(status=SubagentRunnerStatus.SUCCEEDED, structured_output=envelope),
            structured_output=envelope,
            pipeline_spec={},
            runtime_factory_plan={},
            subagent_spec={},
            all_subagent_specs={},
        )
    )

    assert result.control_channel is None
    assert result.model_escalation is None
    assert result.disagreement_resolution is None


def test_unknown_status_fails_closed() -> None:
    envelope = _envelope(status="failed")

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.FAILED, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.status is PipelineEvaluationStatus.BLOCKED
    assert result.failure_reason == "unsupported_envelope_status"
    assert result.completion.completion_allowed is False


def test_missing_policy_uses_conservative_defaults() -> None:
    envelope = _envelope(confidence=0.10)
    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.SUCCEEDED, structured_output=envelope),
            structured_output=envelope,
            pipeline_spec={},
        )
    )

    assert result.model_escalation is not None
    assert result.model_escalation.policy.policy_source == "default"
    assert result.model_escalation.allowed is False
    assert result.model_escalation.blocked_reason == "policy_disabled"


def test_reviewer_block_cannot_be_overridden_by_engineer() -> None:
    envelope = validate_structured_output_envelope(
        {
            "schema_version": "v1",
            "subagent_id": "hermes_code_reviewer",
            "role": "reviewer",
            "status": "blocked",
            "summary": "Reviewer found a blocker.",
            "findings": [{"code": "blocker", "summary": "Unsafe change"}],
            "changes": [{"path": "foo.py", "kind": "modify"}],
            "blockers": ["unsafe change"],
            "artifacts": [{"artifact_id": "rev-1", "kind": "review"}],
            "confidence": 0.91,
            "requires_review": True,
            "next_action": "rework",
        }
    )
    request = PipelineEvaluationRequest(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        step_id="reviewer",
        subagent_id="hermes_code_reviewer",
        execution_mode="observe_plan_only",
        runner_result=SubagentRunnerResult(
            pipeline_session_id="pipe-1",
            trace_id="trace-1",
            pipeline_id="engineering_review_pipeline",
            step_id="reviewer",
            subagent_id="hermes_code_reviewer",
            role_id="reviewer",
            runtime_factory_plan_id="pipe-1:reviewer:hermes_code_reviewer",
            runtime_factory_status="plan_only",
            status=SubagentRunnerStatus.BLOCKED,
            structured_output=envelope,
            schema_validation_status=envelope.validation_status,
        ),
        structured_output=envelope,
        pipeline_spec=_engineering_pipeline_spec(),
        runtime_factory_plan={
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "model_class": "senior_review",
            "status": "plan_only",
        },
        subagent_spec={},
        all_subagent_specs=_subagent_specs(),
    )
    result = evaluate_pipeline_step(request)

    assert result.disagreement.disagreement_present is False
    assert result.disagreement_resolution is None
    assert result.review.reason == "blockers_present"
    assert result.completion.blocked_reason == "blockers_present"


def test_configured_policy_from_engineering_spec_is_respected() -> None:
    envelope = _envelope(confidence=0.33)
    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.SUCCEEDED, structured_output=envelope),
            structured_output=envelope,
            pipeline_spec=_engineering_pipeline_spec(),
        )
    )

    assert result.model_escalation is not None
    assert result.model_escalation.policy.policy_source == "pipeline_spec"
    assert result.model_escalation.candidate_model["target_subagent_id"] == "hermes_engineer_core"


def test_explicit_disagreement_signal_creates_disagreement_resolution_metadata() -> None:
    envelope = _envelope(
        status="needs_review",
        requires_review=True,
        next_action="disagreement",
        summary="Engineer objects to reviewer blocker.",
    )

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.NEEDS_REVIEW, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.disagreement.disagreement_present is True
    assert result.disagreement.reason == "engineer_reviewer_disagreement"
    assert result.disagreement_resolution is not None
    assert result.disagreement_resolution.status == "planned"
    assert result.disagreement_resolution.authoritative_subagent_id == "hermes_code_reviewer"


def test_blockers_from_reviewer_do_not_create_disagreement_without_explicit_signal() -> None:
    envelope = validate_structured_output_envelope(
        {
            "schema_version": "v1",
            "subagent_id": "hermes_code_reviewer",
            "role": "reviewer",
            "status": "blocked",
            "summary": "Reviewer found a blocker.",
            "findings": [{"code": "blocker", "summary": "Unsafe change"}],
            "changes": [{"path": "foo.py", "kind": "modify"}],
            "blockers": ["unsafe change"],
            "artifacts": [{"artifact_id": "rev-1", "kind": "review"}],
            "confidence": 0.91,
            "requires_review": True,
            "next_action": "rework",
        }
    )
    request = PipelineEvaluationRequest(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        step_id="reviewer",
        subagent_id="hermes_code_reviewer",
        execution_mode="observe_plan_only",
        runner_result=SubagentRunnerResult(
            pipeline_session_id="pipe-1",
            trace_id="trace-1",
            pipeline_id="engineering_review_pipeline",
            step_id="reviewer",
            subagent_id="hermes_code_reviewer",
            role_id="reviewer",
            runtime_factory_plan_id="pipe-1:reviewer:hermes_code_reviewer",
            runtime_factory_status="plan_only",
            status=SubagentRunnerStatus.BLOCKED,
            structured_output=envelope,
            schema_validation_status=envelope.validation_status,
        ),
        structured_output=envelope,
        pipeline_spec=_engineering_pipeline_spec(),
        runtime_factory_plan={
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "model_class": "senior_review",
            "status": "plan_only",
        },
        subagent_spec={},
        all_subagent_specs=_subagent_specs(),
    )
    result = evaluate_pipeline_step(request)

    assert result.disagreement.disagreement_present is False
    assert result.disagreement_resolution is None


def test_blockers_from_non_reviewer_do_not_create_disagreement_without_explicit_signal() -> None:
    envelope = _envelope(blockers=["failing test"], next_action="fix_blocker")

    result = evaluate_pipeline_step(
        _request(
            _runner_result(status=SubagentRunnerStatus.BLOCKED, structured_output=envelope),
            structured_output=envelope,
        )
    )

    assert result.disagreement.disagreement_present is False
    assert result.disagreement_resolution is None
