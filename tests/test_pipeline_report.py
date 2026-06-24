from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.pipeline_report import (
    PipelineReportStatus,
    _build_subagent_run_reports,
    _coerce_subagent_run_reports,
    build_pipeline_execution_report,
)
from hermes_cli.pipeline_report_artifacts import (
    persist_controlled_execution_report_artifacts,
    sanitize_report_artifact_metadata,
    sanitize_report_run_id,
)
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


def test_coerce_subagent_run_reports_preserves_effective_fallback_fields() -> None:
    runs = _coerce_subagent_run_reports([
        {
            "step_id": "engineer",
            "subagent_id": "hermes_engineer_core",
            "role_id": "engineer",
            "status": "succeeded",
            "actual_provider": "openrouter",
            "actual_model": "xiaomi/mimo-v2.5-pro",
            "initial_provider": "openrouter",
            "initial_model": "xiaomi/mimo-v2.5-pro",
            "effective_provider": "openai-codex",
            "effective_model": "gpt-5.4",
            "fallback_attempted": True,
            "fallback_activated": True,
            "fallback_provider": "openai-codex",
            "fallback_model": "gpt-5.4",
            "fallback_base_url": "https://chatgpt.com/backend-api/codex",
            "fallback_api_mode": "responses",
            "fallback_error": "provider_402",
            "fallback_result": "activated",
            "providers_used_effective": ["openrouter", "openai-codex"],
        }
    ])

    payload = runs[0].to_safe_dict()

    assert payload["actual_provider"] == "openrouter"
    assert payload["actual_model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["initial_provider"] == "openrouter"
    assert payload["initial_model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["effective_provider"] == "openai-codex"
    assert payload["effective_model"] == "gpt-5.4"
    assert payload["fallback_attempted"] is True
    assert payload["fallback_activated"] is True
    assert payload["fallback_provider"] == "openai-codex"
    assert payload["fallback_model"] == "gpt-5.4"
    assert payload["fallback_base_url"] == "https://chatgpt.com/backend-api/codex"
    assert payload["fallback_api_mode"] == "responses"
    assert payload["fallback_error"] == "provider_402"
    assert payload["fallback_result"] == "activated"
    assert payload["providers_used_effective"] == ["openrouter", "openai-codex"]


def test_build_subagent_run_reports_preserves_effective_fallback_fields_from_runner_result() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    engineer_step = snapshot.planned_steps[0]
    engineer_runner = {
        "status": "succeeded",
        "runtime_mode": "autonomous",
        "actual_provider": "openrouter",
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "initial_provider": "openrouter",
        "initial_model": "xiaomi/mimo-v2.5-pro",
        "effective_provider": "openai-codex",
        "effective_model": "gpt-5.4",
        "fallback_attempted": True,
        "fallback_activated": True,
        "fallback_provider": "openai-codex",
        "fallback_model": "gpt-5.4",
        "fallback_base_url": "https://chatgpt.com/backend-api/codex",
        "fallback_api_mode": "responses",
        "fallback_error": "provider_402",
        "fallback_result": "activated",
        "providers_used_effective": ["openrouter", "openai-codex"],
        "raw_output_redacted": True,
    }
    snapshot.planned_steps[0] = engineer_step.__class__(**{**engineer_step.__dict__, "runner_result": engineer_runner})

    runs = _build_subagent_run_reports(snapshot.planned_steps)
    payload = runs[0].to_safe_dict()

    assert payload["effective_provider"] == "openai-codex"
    assert payload["effective_model"] == "gpt-5.4"
    assert payload["fallback_attempted"] is True
    assert payload["fallback_activated"] is True
    assert payload["fallback_provider"] == "openai-codex"
    assert payload["fallback_model"] == "gpt-5.4"
    assert payload["providers_used_effective"] == ["openrouter", "openai-codex"]


def test_report_builder_usage_preserves_providers_used_effective_distinct_from_providers_used() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        subagent_runs_override=[
            {
                "step_id": "engineer",
                "subagent_id": "hermes_engineer_core",
                "role_id": "engineer",
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "effective_provider": "openai-codex",
                "effective_model": "gpt-5.4",
                "fallback_attempted": True,
                "fallback_activated": True,
                "fallback_provider": "openai-codex",
                "fallback_model": "gpt-5.4",
                "providers_used_effective": ["openrouter", "openai-codex"],
                "raw_output_redacted": True,
            }
        ],
    )
    payload = report.to_safe_dict()

    assert payload["usage"]["providers_used"] == ["openrouter"]
    assert payload["usage"]["providers_used_effective"] == ["openrouter", "openai-codex"]
    assert payload["usage_summary"]["providers_used"] == ["openrouter"]
    assert payload["usage_summary"]["providers_used_effective"] == ["openrouter", "openai-codex"]


def test_constructor_provider_stays_backward_compatible_without_inventing_fallback_metadata() -> None:
    runs = _coerce_subagent_run_reports([
        {
            "step_id": "engineer",
            "subagent_id": "hermes_engineer_core",
            "role_id": "engineer",
            "status": "succeeded",
            "actual_provider": "openrouter",
            "actual_model": "xiaomi/mimo-v2.5-pro",
        }
    ])

    payload = runs[0].to_safe_dict()

    assert payload["actual_provider"] == "openrouter"
    assert payload["actual_model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["effective_provider"] is None
    assert payload["effective_model"] is None
    assert payload["fallback_attempted"] is False
    assert payload["fallback_activated"] is False
    assert payload["fallback_provider"] is None
    assert payload["fallback_model"] is None
    assert payload["providers_used_effective"] == []


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
        mutation_summary={
            "enabled": True,
            "workspace": "repo",
            "attempted_count": 1,
            "applied_count": 1,
            "denied_count": 0,
            "results": [{"operation": "write_text", "path": "new.txt", "status": "applied", "content_sha256": "abc", "bytes_written": 3}],
        },
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
    assert payload["mutation_summary"]["applied_count"] == 1
    assert "Added unit test" not in json.dumps(payload["mutation_summary"], sort_keys=True)


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



def test_report_builder_successful_completion_clears_all_blocked_and_placeholder_reasons() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "completion_allowed": True,
        "completion_blocked_reason": "stale_blocked_reason",
        "final_verdict": "completed",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        git_gate={
            "status": "enabled",
            "enabled": True,
            "changed_files": ["new.txt"],
            "completion_blocked_reason": "stale_git_gate_reason",
        },
    )
    payload = report.to_safe_dict()

    assert report.completion.completion_allowed is True
    assert report.completion.blocked_reason is None
    assert payload["completion"]["blocked_reason"] is None


def test_report_builder_prefers_runtime_block_reason_after_execution_starts() -> None:
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
        "completion_blocked_reason": "execution_disabled",
        "final_verdict": "engineer_invalid_after_test_denied",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        blocked_reason_override="test_command_denied",
    )
    payload = report.to_safe_dict()

    assert report.executed is True
    assert report.completion.blocked_reason == "test_command_denied"
    assert payload["completion"]["blocked_reason"] == "test_command_denied"
    assert payload["final_response"]["placeholder_reason"] == "test_command_denied"
    assert payload["safety"]["execution_enabled"] is True
    assert "execution_disabled" not in payload["safety"]["policy_notes"]
    assert payload["review"]["blocked_reason"] == "test_command_denied"
    assert payload["git_gate"]["completion_blocked_reason"] == "test_command_denied"


def test_report_builder_preserves_honest_blocked_final_response_text() -> None:
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
        "completion_blocked_reason": "execution_disabled",
        "final_verdict": "engineer_invalid_after_test_denied",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        final_response_text="Autonomous execution did not complete successfully.",
        blocked_reason_override="test_command_denied",
    )
    payload = report.to_safe_dict()

    assert payload["final_response"]["text"] == "Autonomous execution did not complete successfully."
    assert payload["final_response"]["placeholder_reason"] == "test_command_denied"


def test_report_builder_blocked_completion_preserves_blocked_reason() -> None:
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
        "completion_blocked_reason": "review_blocked",
        "final_verdict": "review_blocked",
    })

    report = build_pipeline_execution_report(session=session, state_snapshot=snapshot)
    payload = report.to_safe_dict()

    assert report.completion.completion_allowed is False
    assert report.completion.blocked_reason == "review_blocked"
    assert payload["review"]["blocked_reason"] == "review_blocked"
    assert payload["final_response"]["placeholder_reason"] == "review_blocked"
    assert payload["git_gate"]["completion_blocked_reason"] == "review_blocked"


def test_report_builder_surfaces_reviewer_rework_reason_and_test_evidence() -> None:
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
        "completion_blocked_reason": "reviewer_requested_rework_missing_test_evidence",
        "final_verdict": "controlled_rework_requested",
    })

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        final_response_text="Reviewer requested rework because test evidence is missing.",
        tests={
            "status": "not_requested",
            "command": "venv/bin/pytest -q tests/test_smoke_square.py",
            "summary": "Requested focused pytest command was not captured.",
        },
        review_overrides={
            "status": "rework_required",
            "reviewer_approved": False,
            "final_review_decision": "changes_requested",
            "decision_category": "rework_required",
            "decision_reason": "missing_test_evidence",
            "rework_attempted": False,
            "rework_exhausted": False,
        },
    )

    payload = report.to_safe_dict()

    assert payload["review"]["status"] == "rework_required"
    assert payload["review"]["decision_category"] == "rework_required"
    assert payload["review"]["decision_reason"] == "missing_test_evidence"
    assert payload["review"]["final_review_decision"] == "changes_requested"
    assert payload["review"]["rework_attempted"] is False
    assert payload["review"]["rework_exhausted"] is False
    assert payload["tests"]["status"] == "not_requested"
    assert payload["tests"]["command"] == "venv/bin/pytest -q tests/test_smoke_square.py"
    assert "missing" in payload["final_response"]["text"].lower()


def test_report_builder_execution_disabled_preserves_disabled_placeholder_semantics() -> None:
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
    payload = report.to_safe_dict()

    assert report.executed is False
    assert report.completion.completion_allowed is False
    assert report.completion.blocked_reason == "execution_disabled"
    assert payload["final_response"]["placeholder_reason"] == "execution_disabled"
    assert payload["git_gate"]["completion_blocked_reason"] == "execution_disabled"
    assert payload["safety"]["execution_enabled"] is False
    assert payload["safety"]["controlled_execution"] is False


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


def test_report_subagent_runs_include_runtime_mode_and_real_provider_policy_metadata() -> None:
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
                "runtime_mode": "real_provider",
                "real_provider_allowed": True,
                "provider_policy_status": "allowed",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"cache_hit": False, "cache_write": False},
            }
        ],
    )

    payload = report.to_safe_dict()

    assert payload["subagent_runs"][0]["runtime_mode"] == "real_provider"
    assert payload["subagent_runs"][0]["real_provider_allowed"] is True
    assert payload["subagent_runs"][0]["provider_policy_status"] == "allowed"


def test_report_completion_allowed_clears_blocked_reason_and_placeholder() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    engineer_step = snapshot.planned_steps[0]
    engineer_eval = dict(engineer_step.evaluation_result or {})
    engineer_eval["completion"] = {
        "candidate_complete": True,
        "blocked_reason": "execution_disabled",
    }
    snapshot.planned_steps[0] = engineer_step.__class__(**{**engineer_step.__dict__, "evaluation_result": engineer_eval})
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "execution_mode": "controlled_runtime_loop",
        "completion_allowed": True,
        "completion_blocked_reason": None,
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
                "runtime_mode": "real_provider",
                "real_provider_allowed": True,
                "provider_policy_status": "allowed",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
            {
                "step_id": "reviewer",
                "subagent_id": "hermes_code_reviewer",
                "role_id": "reviewer",
                "status": "succeeded",
                "runtime_mode": "real_provider",
                "real_provider_allowed": True,
                "provider_policy_status": "allowed",
                "actual_provider": "openai-codex",
                "actual_model": "gpt-5.5",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            },
        ],
    )

    payload = report.to_safe_dict()

    assert payload["completion"]["completion_allowed"] is True
    assert payload["completion"]["blocked_reason"] is None
    assert payload["final_response"]["status"] == "completion_allowed"
    assert payload["final_response"]["placeholder_reason"] is None
    assert payload["review"]["blocked_reason"] is None
    assert payload["review"]["status"] == "not_required"


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


def test_report_preserves_structured_test_summary() -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )

    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        tests={
            "status": "failed",
            "blocked_reason": "test_command_failed",
            "results": [
                {
                    "command": ["venv/bin/pytest", "-q", "tests/test_example.py"],
                    "status": "failed",
                    "cwd": "repo",
                }
            ],
        },
    )

    payload = report.to_safe_dict()

    assert payload["tests"]["status"] == "failed"
    assert payload["tests"]["blocked_reason"] == "test_command_failed"
    assert payload["tests"]["results"][0]["cwd"] == "repo"


def test_persist_controlled_execution_report_artifacts_writes_workspace_and_durable_reports(tmp_path: Path) -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "completion_allowed": True,
        "completion_blocked_reason": None,
        "final_verdict": "completed",
    })
    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        final_response_text="Controlled pipeline validation completed.",
        review_overrides={"reviewer_approved": True},
    )
    workspace = tmp_path / "controlled-workspace"
    workspace.mkdir()
    durable_root = tmp_path / "durable"

    metadata = persist_controlled_execution_report_artifacts(
        session=session,
        state_snapshot=snapshot,
        controller_payload={
            "status": "completed",
            "actual_execution_invoked": True,
            "execution_mode": "controlled_manual",
            "helper_result_status": "completed",
            "workspace_basename": workspace.name,
        },
        pipeline_execution_report_payload=report.to_safe_dict(),
        router_decision=RouterDecision(
            pipeline_session_id=session.pipeline_session_id,
            router_subagent_id="hermes_pipeline_router",
            status="selected",
            selected_pipeline_id="engineering_review_pipeline",
            fallback_pipeline_id="default_conversation_pipeline",
            confidence=0.99,
            reasoning_summary="controlled_manual_trigger_override",
            selected_provider="openai-codex",
            selected_model="gpt-5.4-mini",
        ),
        workspace_path=workspace,
        durable_root=durable_root,
    )

    workspace_report = workspace / "controlled_execution_report.json"
    durable_report = durable_root / session.pipeline_session_id / "controlled_execution_report.json"

    assert metadata["run_id"] == session.pipeline_session_id
    assert metadata["workspace_report_path"] == str(workspace_report)
    assert metadata["durable_report_path"] == str(durable_report)
    assert metadata["workspace_report_written"] is True
    assert metadata["durable_report_written"] is True
    assert workspace_report.exists()
    assert durable_report.exists()

    payload = json.loads(workspace_report.read_text(encoding="utf-8"))
    assert payload["run_id"] == session.pipeline_session_id
    assert payload["routing"]["selected_pipeline_id"] == "engineering_review_pipeline"
    assert payload["execution"]["actual_execution_invoked"] is True
    assert payload["execution"]["executed_subagent_count"] == 0
    assert payload["review"]["reviewer_invoked"] is False
    assert payload["subagent_runs"] == []
    assert payload["artifacts"]["workspace_report_path"] == str(workspace_report)
    assert payload["artifacts"]["durable_report_path"] == str(durable_report)
    assert payload["workspace"]["path"] == str(workspace)
    assert payload["workspace"]["basename"] == workspace.name
    assert payload["routing"]["router_provider"] == "openai-codex"
    assert payload["routing"]["router_model"] == "gpt-5.4-mini"
    assert payload["pipeline_execution_report"]["completion"]["completion_allowed"] is True
    encoded = json.dumps(payload, sort_keys=True)
    assert "SECRET_TOKEN=abc123" not in encoded
    assert "raw_metadata" not in encoded
    assert "output_text" not in encoded
    assert payload["execution"]["api_calls"] is None
    assert payload["execution"]["api_calls_known"] is False


def test_persist_controlled_execution_report_artifacts_writes_partial_failure_payload(tmp_path: Path) -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    report = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
        preflight_result={"allowed": False, "reason_code": "observe_only"},
    )
    workspace = tmp_path / "controlled-workspace"
    workspace.mkdir()

    metadata = persist_controlled_execution_report_artifacts(
        session=session,
        state_snapshot=snapshot,
        controller_payload={
            "status": "execution_failed",
            "blocked_reason": "controller_helper_failed",
            "actual_execution_invoked": True,
            "execution_mode": "controlled_manual",
            "helper_result_status": "controller_helper_failed",
            "helper_error": "RuntimeError",
            "workspace_basename": workspace.name,
        },
        pipeline_execution_report_payload=report.to_safe_dict(),
        workspace_path=workspace,
        durable_root=None,
    )

    payload = json.loads((workspace / "controlled_execution_report.json").read_text(encoding="utf-8"))

    assert metadata["durable_report_path"] is None
    assert payload["status"] == "execution_failed"
    assert payload["first_failed_point"] == "controller_helper_failed"
    assert payload["error"]["class"] == "RuntimeError"
    assert payload["error"]["summary"] == "controller_helper_failed"
    assert payload["execution"]["actual_execution_invoked"] is True
    assert payload["pipeline_execution_report"]["status"] == "not_executed"


def test_persist_controlled_execution_report_artifacts_sanitizes_durable_run_id(tmp_path: Path) -> None:
    loaded = load_pipeline_specs()
    base_session = _session_for("engineering_review_pipeline", status="selected")
    session = base_session.__class__(**{**base_session.__dict__, "pipeline_session_id": "../unsafe/run-id"})
    snapshot = build_pipeline_state_snapshot(
        session=base_session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    durable_root = tmp_path / "durable"

    metadata = persist_controlled_execution_report_artifacts(
        session=session,
        state_snapshot=snapshot,
        controller_payload={"status": "completed", "actual_execution_invoked": True, "execution_mode": "controlled_manual"},
        pipeline_execution_report_payload=build_pipeline_execution_report(
            session=base_session,
            state_snapshot=snapshot,
        ).to_safe_dict(),
        durable_root=durable_root,
    )

    assert metadata["durable_report_path"] is not None
    assert "../" not in metadata["durable_report_path"]
    assert "/../" not in metadata["durable_report_path"]
    assert Path(metadata["durable_report_path"]).parent.name != "run-id"
    assert Path(metadata["durable_report_path"]).exists()


def test_persist_controlled_execution_report_artifacts_tolerates_write_failures(tmp_path: Path, monkeypatch, caplog) -> None:
    loaded = load_pipeline_specs()
    session = _session_for("engineering_review_pipeline", status="selected")
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    report_payload = build_pipeline_execution_report(
        session=session,
        state_snapshot=snapshot,
    ).to_safe_dict()

    def _boom(self: Path, content: str, encoding: str) -> int:
        raise PermissionError("no write")

    monkeypatch.setattr(Path, "write_text", _boom)

    with caplog.at_level("WARNING"):
        metadata = persist_controlled_execution_report_artifacts(
            session=session,
            state_snapshot=snapshot,
            controller_payload={"status": "completed", "actual_execution_invoked": True, "execution_mode": "controlled_manual"},
            pipeline_execution_report_payload=report_payload,
            workspace_path=tmp_path / "workspace",
            durable_root=tmp_path / "durable",
        )

    assert metadata["workspace_report_written"] is False
    assert metadata["durable_report_written"] is False
    assert metadata["workspace_report_path"] is None
    assert metadata["durable_report_path"] is None
    assert "controlled execution report workspace write failed" in caplog.text
    assert "controlled execution report durable write failed" in caplog.text


def test_sanitize_report_artifact_metadata_excludes_absolute_paths() -> None:
    payload = sanitize_report_artifact_metadata(
        {
            "run_id": "pipe-report-1",
            "workspace_report_path": "/tmp/hermes-gateway-controlled-runs/pipe-report-1/controlled_execution_report.json",
            "durable_report_path": "/home/hermes/.hermes/controlled-runs/pipe-report-1/controlled_execution_report.json",
            "workspace_report_written": True,
            "durable_report_written": True,
            "workspace_basename": "pipe-report-1",
            "report_workspace_filename": "controlled_execution_report.json",
            "durable_report_available": True,
        }
    )

    encoded = json.dumps(payload, sort_keys=True)
    assert payload["report_artifact_written"] is True
    assert payload["report_run_id"] == "pipe-report-1"
    assert payload["report_workspace_filename"] == "controlled_execution_report.json"
    assert payload["durable_report_available"] is True
    assert "/tmp/hermes-gateway-controlled-runs" not in encoded
    assert "/home/hermes/.hermes/controlled-runs" not in encoded


def test_sanitize_report_run_id_accepts_safe_ids_and_rewrites_unsafe_ids() -> None:
    assert sanitize_report_run_id("pipe-report-1") == "pipe-report-1"
    assert sanitize_report_run_id("../pipe-report-1") != "../pipe-report-1"
