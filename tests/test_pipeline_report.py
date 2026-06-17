from __future__ import annotations

import json

import pytest

from hermes_cli.pipeline_report import PipelineReportStatus, build_pipeline_execution_report
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot


def _session_for(pipeline_id: str | None, *, status: str):
    decision = RouterDecision(
        pipeline_session_id="pipe-report-1",
        router_subagent_id="hermes_pipeline_router",
        status=status,
        selected_pipeline_id=pipeline_id,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="route",
        fallback_safe=pipeline_id is None,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-report-1",
            session_key="agent:main:telegram:dm",
            user_message="Implement report with SECRET_TOKEN=abc123",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )


def test_report_builder_returns_not_executed_engineering_report() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": False, "reason_code": "gate_disabled"},
    )

    assert report.status is PipelineReportStatus.NOT_EXECUTED
    assert report.executed is False
    assert report.summary.pipeline_id == "engineering_review_pipeline"
    assert report.summary.pipeline_session_id == session.pipeline_session_id
    assert report.summary.trace_id == session.trace_id
    assert report.summary.selected_subagents == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert [item.subagent_id for item in report.subagents] == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert [item.provider for item in report.models] == ["openrouter", "openai-codex"]
    assert report.completion.completion_allowed is False
    assert report.completion.blocked_reason == "execution_disabled"
    assert report.final_response.status is PipelineReportStatus.NOT_EXECUTED
    assert report.final_response.text is None
    assert report.gate.preflight_reason_code == "gate_disabled"
    assert report.safety.policy_notes[:2] == ["execution_disabled", "gate_disabled"]


def test_report_builder_preserves_evaluation_and_policy_metadata() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    engineer_step = snapshot.planned_steps[0]
    engineer_eval = dict(engineer_step.evaluation_result or {})
    engineer_eval["control_channel"] = {
        "decisions": [
            {
                "status": "loop_limit_exceeded",
                "user_action_required": True,
                "loop_limit_decision": {
                    "status": "loop_limit_exceeded",
                    "user_action_required": True,
                },
            }
        ]
    }
    engineer_eval["model_escalation"] = {
        "user_approval_required": True,
        "candidate_model": {"provider": "openai-codex", "model": "gpt-5.5"},
    }
    engineer_eval["escalation"] = {"escalation_required": True}
    engineer_eval["disagreement"] = {"disagreement_present": True}
    snapshot.planned_steps[0] = engineer_step.__class__(**{**engineer_step.__dict__, "evaluation_result": engineer_eval})

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": False, "reason_code": "observe_only"},
    )

    assert report.gate.escalation_required is True
    assert report.gate.disagreement_present is True
    assert report.gate.control_statuses == ["loop_limit_exceeded"]
    assert report.gate.loop_limit_statuses == ["loop_limit_exceeded"]
    assert report.completion.user_action_required is True
    assert report.summary.user_action_required is True
    assert report.models[0].candidate_model == {"provider": "openai-codex", "model": "gpt-5.5"}


def test_report_builder_default_pipeline_has_no_fake_engineering_steps() -> None:
    loaded = load_pipeline_specs()
    session = _session_for(None, status="no_specialized_pipeline")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["default_conversation_pipeline"],
    )

    report = build_pipeline_execution_report(session=session, state_snapshot=snapshot)

    assert report.summary.pipeline_id == "default_conversation_pipeline"
    assert report.summary.selected_subagents == ["general_operator"]
    assert [item.subagent_id for item in report.subagents] == ["general_operator"]
    assert report.gate.evaluation_statuses == ["not_evaluated"]
    assert report.models[0].provider == "openai-codex"


def test_report_serialization_is_safe_and_deterministic() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": False, "reason_code": "gate_disabled"},
    )

    first = json.dumps(report.to_safe_dict(), sort_keys=True)
    second = json.dumps(report.to_safe_dict(), sort_keys=True)

    assert first == second
    assert "SECRET_TOKEN=abc123" not in first
    assert '"text": null' in first
    assert "prompt_input_hash" not in first
    assert "system_prompt_path" not in first


def test_report_builder_serializes_disagreement_metadata_sections() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "completion_allowed": False,
        "completion_blocked_reason": "reviewer_decisive_after_disagreement",
        "final_verdict": "reviewer_decisive_after_disagreement",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": True, "reason_code": None},
        final_response_text=None,
        peer_messages=[{"message_id": "peer-1", "type": "disagreement", "content": {"summary": "summarized"}}],
        disagreements=[{"status": "reviewer_maintained_blocker", "decisive_subagent": "hermes_code_reviewer"}],
        model_escalations=[{"status": "block_and_escalate_to_user", "target_subagent": "decisive_subagent_or_arbitrator"}],
        reviewer_packet={"status": "available", "present": True},
        git_gate={"status": "enabled", "enabled": True, "changed_files": ["new.txt"]},
        changed_files=["new.txt"],
        tests={"status": "passed", "source": "pytest", "summary": "focused"},
        review_overrides={"reviewer_approved": False, "status": "blocked_after_disagreement"},
        decisive_subagent="hermes_code_reviewer",
    )

    payload = report.to_safe_dict()

    assert payload["peer_messages"][0]["message_id"] == "peer-1"
    assert payload["disagreements"][0]["decisive_subagent"] == "hermes_code_reviewer"
    assert payload["model_escalations"][0]["status"] == "block_and_escalate_to_user"
    assert payload["reviewer_packet"]["status"] == "available"
    assert payload["git_gate"]["status"] == "enabled"
    assert payload["changed_files"] == ["new.txt"]
    assert payload["tests"]["status"] == "passed"
    assert payload["review"]["status"] == "blocked_after_disagreement"
    assert payload["review"]["escalation_invoked"] is False
    assert payload["review"]["final_review_decision"] == "blocker_maintained"
    assert payload["decisive_subagent"] == "hermes_code_reviewer"


def test_report_builder_fails_closed_on_missing_required_metadata() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    broken_session = session.__class__(**{**session.__dict__, "pipeline_session_id": ""})

    with pytest.raises(ValueError, match="missing required pipeline session metadata"):
        build_pipeline_execution_report(session=broken_session, state_snapshot=snapshot)



def test_report_builder_exposes_stable_contract_sections_and_usage_fallback() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    engineer_step = snapshot.planned_steps[0]
    snapshot.planned_steps[0] = engineer_step.__class__(**{
        **engineer_step.__dict__,
        "runner_result": {
            **dict(engineer_step.runner_result or {}),
            "status": "succeeded",
            "actual_provider": "openrouter",
            "actual_model": "qwen/qwen3-coder",
            "usage_summary": {
                "input_tokens": 11,
                "output_tokens": 4,
                "source": "reported",
            },
            "cache_summary": {
                "cache_hit": True,
                "cache_write": False,
                "source": "reported",
            },
            "tool_call_summaries": [
                {"tool_name": "apply_patch", "call_count": 2},
                "not-a-mapping",
            ],
        },
    })
    reviewer_step = snapshot.planned_steps[1]
    snapshot.planned_steps[1] = reviewer_step.__class__(**{
        **reviewer_step.__dict__,
        "runner_result": {
            **dict(reviewer_step.runner_result or {}),
            "status": "not_invoked",
            "failure_reason": "observe_mode_plan_only",
            "actual_provider": "openai-codex",
            "actual_model": "gpt-5.5",
        },
    })
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "execution_mode": "controlled_runtime",
        "completion_allowed": False,
        "completion_blocked_reason": "review_required",
        "final_verdict": "review_required",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": True, "reason_code": None},
        final_response_text="done",
    )

    payload = report.to_safe_dict()

    assert payload["schema_version"] == "pipeline_execution_report.v1"
    assert payload["pipeline"]["pipeline_id"] == "engineering_review_pipeline"
    assert payload["routing"]["selected_pipeline_id"] == "engineering_review_pipeline"
    assert payload["controller"]["execution_mode"] == "controlled_runtime"
    assert payload["helper"]["executed_subagent_count"] == 1
    assert payload["session"]["pipeline_session_id"] == session.pipeline_session_id
    assert payload["loop"]["status"] == "review_required"
    assert payload["usage_summary"]["total_tokens"] == 15
    assert payload["usage_summary"]["planned_subagent_count"] == 2
    assert payload["usage_summary"]["executed_subagent_count"] == 1
    assert payload["usage_summary"]["subagent_count"] == 1
    assert payload["usage_summary"]["providers_used"] == ["openrouter"]
    assert payload["git_gate"]["status"] == "unavailable"
    assert payload["review"]["blocked_reason"] == "review_required"
    assert payload["reviewer_packet"]["status"] == "unavailable"
    assert payload["peer_messages"] == []
    assert payload["disagreements"] == []
    assert payload["model_escalations"] == []
    assert payload["changed_files"] == []
    assert payload["tests"]["status"] == "unavailable"
    assert payload["completion"]["blocked_reason"] == "review_required"
    assert payload["safety"]["raw_task_redacted"] is True
    assert payload["safety"]["raw_outputs_redacted"] is True
    assert payload["safety"]["live_execution_enabled"] is False
    assert payload["subagent_runs"][0]["tool_call_summaries"] == [{"tool_name": "apply_patch", "call_count": 2}]
    assert payload["subagent_runs"][0]["actual_provider"] == "openrouter"



def test_report_usage_alias_and_multi_iteration_accounting_are_explicit() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "execution_mode": "controlled_runtime_loop",
        "completion_allowed": False,
        "completion_blocked_reason": "reviewer_decisive_after_disagreement",
        "final_verdict": "reviewer_decisive_after_disagreement",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": True, "reason_code": "rework_loop_fuse_allowed"},
        subagent_runs_override=[
            {
                "step_id": "engineer",
                "subagent_id": "hermes_engineer_core",
                "role_id": "engineer",
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"cache_hit": True, "cache_write": False},
            },
            {
                "step_id": "reviewer",
                "subagent_id": "hermes_code_reviewer",
                "role_id": "reviewer",
                "status": "succeeded",
                "actual_provider": "openai-codex",
                "actual_model": "gpt-5.5",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                "cache": {"cache_hit": False, "cache_write": False},
            },
            {
                "step_id": "engineer",
                "subagent_id": "hermes_engineer_core",
                "role_id": "engineer",
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"cache_hit": True, "cache_write": False},
            },
            {
                "step_id": "reviewer",
                "subagent_id": "hermes_code_reviewer",
                "role_id": "reviewer",
                "status": "succeeded",
                "actual_provider": "openai-codex",
                "actual_model": "gpt-5.5",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                "cache": {"cache_hit": False, "cache_write": False},
            },
        ],
    )

    payload = report.to_safe_dict()

    assert payload["usage_summary"] == payload["usage"]
    assert payload["usage_summary"]["planned_subagent_count"] == 2
    assert payload["usage_summary"]["executed_subagent_count"] == 4
    assert payload["usage_summary"]["subagent_run_instance_count"] == 4
    assert payload["usage_summary"]["execution_round_count"] == 2
    assert payload["usage_summary"]["subagent_count"] == 4
    assert payload["helper"]["subagent_run_instance_count"] == 4
    assert payload["helper"]["execution_round_count"] == 2
    assert payload["review"]["reviewer_invoked"] is True


def test_report_reviewer_invoked_stays_false_for_multiple_non_reviewer_runs() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "execution_mode": "controlled_runtime_loop",
        "completion_allowed": False,
        "completion_blocked_reason": "loop_harness_not_live_final",
        "final_verdict": "controlled_rework_loop_candidate_complete",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": True, "reason_code": "rework_loop_fuse_allowed"},
        subagent_runs_override=[
            {
                "step_id": "engineer",
                "subagent_id": "hermes_engineer_core",
                "role_id": "engineer",
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"cache_hit": True, "cache_write": False},
            },
            {
                "step_id": "qa",
                "subagent_id": "hermes_test_runner",
                "role_id": "qa",
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "qwen/qwen3-coder",
                "token_usage": {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9},
                "cache": {"cache_hit": False, "cache_write": False},
            },
        ],
    )

    payload = report.to_safe_dict()

    assert payload["usage_summary"]["executed_subagent_count"] == 2
    assert payload["review"]["reviewer_invoked"] is False
