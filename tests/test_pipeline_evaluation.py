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
        pipeline_spec=pipeline_spec or {},
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
