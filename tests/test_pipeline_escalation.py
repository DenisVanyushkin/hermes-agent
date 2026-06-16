from __future__ import annotations

from hermes_cli.pipeline_control_channel import LoopCounterSnapshot
from hermes_cli.pipeline_escalation import (
    EscalationContext,
    plan_disagreement_resolution,
    plan_model_escalation,
)


def _context(pipeline_spec: dict[str, object] | None = None) -> EscalationContext:
    return EscalationContext(
        pipeline_session_id="pipe-1",
        trace_id="trace-1",
        pipeline_id="engineering_review_pipeline",
        step_id="engineer",
        subagent_id="hermes_engineer_core",
        pipeline_spec=pipeline_spec or _engineering_pipeline_spec(),
        current_runtime={
            "provider": "openrouter",
            "model": "xiaomi/mimo-v2.5-pro",
            "model_class": "base_coding",
            "status": "plan_only",
        },
        all_subagent_specs={
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
        },
    )


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
                }
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


def test_loop_limit_exceeded_requires_user_escalation_and_keeps_counters() -> None:
    decision, plan = plan_model_escalation(
        context=_context(),
        trigger_reason="low_confidence",
        source="evaluation",
        counters=LoopCounterSnapshot(model_escalations=1),
    )

    assert decision.allowed is False
    assert decision.user_approval_required is True
    assert decision.counters_reset is False
    assert decision.counters_persist is True
    assert plan.blocked_reason == "loop_limit_exceeded"


def test_disagreement_within_limit_plans_controlled_resolution_metadata() -> None:
    decision, plan = plan_disagreement_resolution(
        context=_context(),
        source_subagent_id="hermes_engineer_core",
        counterparty_subagent_id="hermes_code_reviewer",
        trigger_reason="engineer_reviewer_disagreement",
        summary="Engineer disputes reviewer feedback once.",
        counters=LoopCounterSnapshot(disagreement_rounds=0),
    )

    assert decision.allowed is True
    assert decision.planned is True
    assert plan.status == "planned"
    assert plan.authoritative_subagent_id == "hermes_code_reviewer"


def test_disagreement_over_limit_blocks_and_requires_user_escalation() -> None:
    decision, plan = plan_disagreement_resolution(
        context=_context(),
        source_subagent_id="hermes_engineer_core",
        counterparty_subagent_id="hermes_code_reviewer",
        trigger_reason="engineer_reviewer_disagreement",
        summary="Disagreement exceeded allowed rounds.",
        counters=LoopCounterSnapshot(disagreement_rounds=1),
    )

    assert decision.allowed is False
    assert decision.requires_user_escalation is True
    assert plan.blocked_reason == "disagreement_round_limit_exceeded"

