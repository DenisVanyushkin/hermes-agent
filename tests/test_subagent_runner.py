from __future__ import annotations

from dataclasses import replace
import importlib
import shutil
import sys
from pathlib import Path

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _build_runtime_result(tmp_path: Path, subagent_id: str):
    from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeFactory

    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    factory = RuntimeFactory(repo_root=repo_root)
    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id=subagent_id,
            pipeline_session_id=f"pipe-{subagent_id}",
            invocation_id=f"invoke-{subagent_id}",
        )
    )
    return repo_root, result


def _engineering_session():
    decision = RouterDecision(
        pipeline_session_id="pipe-runner-contract",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.95,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-runner-contract",
            user_message="implement code",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )


def _runtime_factory_plan(tmp_path: Path, *, step_kind: str, subagent_id: str):
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == step_kind)

    from hermes_cli.runtime_factory import build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs[subagent_id],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )
    return session, step, plan


def _valid_envelope_payload(**overrides):
    payload = {
        "schema_version": "v1",
        "subagent_id": "hermes_code_reviewer",
        "role": "reviewer",
        "status": "needs_review",
        "summary": "Review completed with one blocker.",
        "findings": [{"code": "bug", "summary": "Edge case broken"}],
        "blockers": ["Edge case broken"],
        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
        "confidence": 0.78,
        "requires_review": True,
        "next_action": "engineer_fix_blockers",
    }
    payload.update(overrides)
    return payload


def test_runs_general_operator_with_fake_executor(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    runner = SubagentRunner(
        executor=lambda request, runtime_plan: {
            "output_text": f"handled {request.subagent_id}",
            "completion_reason": "completed",
            "stop_reason": "end_turn",
            "token_usage": {"input_tokens": 11, "output_tokens": 7},
            "raw_metadata": {"trace_id": "trace-general"},
        }
    )

    result = runner.run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-general-1",
            input_messages=[{"role": "user", "content": "Book a haircut"}],
        ),
    )

    assert result.ok is True
    assert result.execution_status == "completed"
    assert result.record.constructor_provider == "openai-codex"
    assert result.record.constructor_model == "gpt-5.4-mini"
    assert result.record.selected_model_class == "general"
    assert result.output_text == "handled general_operator"
    assert result.token_usage["input_tokens"] == 11
    assert result.record.safe_output_summary == "handled general_operator"


def test_runs_engineer_subagent_with_fake_executor(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "hermes_engineer_core")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    runner = SubagentRunner(
        executor=lambda _request, _runtime_plan: {
            "output_text": "patch prepared",
            "completion_reason": "completed",
            "stop_reason": "stop",
            "token_usage": {"input_tokens": 20, "output_tokens": 8},
        }
    )

    result = runner.run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-engineer-1",
            input_messages=[{"role": "user", "content": "Implement E1"}],
        ),
    )

    assert result.ok is True
    assert result.record.constructor_provider == "openrouter"
    assert result.record.constructor_model == "xiaomi/mimo-v2.5-pro"
    assert result.record.selected_model_class == "base_coding"


def test_records_tool_intents_without_executing_tools(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "hermes_engineer_core")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    executed = {"count": 0}

    def fake_executor(_request, _runtime_plan):
        executed["count"] += 1
        return {
            "output_text": "Need to patch files",
            "completion_reason": "tool_gate_required",
            "tool_intents": [
                {"name": "patch", "arguments": {"path": "foo.py"}},
                {"name": "git_commit", "arguments": {"message": "nope"}},
            ],
        }

    result = SubagentRunner(executor=fake_executor).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-tools-1",
            input_messages=[{"role": "user", "content": "Patch foo.py"}],
        ),
    )

    assert executed["count"] == 1
    assert result.ok is True
    assert result.tool_intents_count == 2
    assert result.requires_tool_gate is True
    assert result.record.tool_intents_count == 2
    assert result.record.requires_tool_gate is True
    assert result.tool_intents[0]["name"] == "patch"


def test_safe_dict_summarizes_nested_tool_intents_without_leaking_payloads(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "hermes_engineer_core")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(
        executor=lambda *_args, **_kwargs: {
            "output_text": "Need gated tool use",
            "completion_reason": "tool_gate_required",
            "tool_intents": [
                {
                    "name": "patch",
                    "intent_type": "file_write",
                    "arguments": {
                        "path": "foo.py",
                        "api_key": "secret-value",
                        "client": object(),
                        "prompt_text": "leak me",
                        "env": {"TOKEN": "secret"},
                        "nested": [{"password": "bad"}, {"ok": "still hidden"}],
                    },
                }
            ],
        }
    ).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-tools-safe",
            input_messages=[{"role": "user", "content": "Patch foo.py"}],
        ),
    )

    payload = result.to_safe_dict()
    log_payload = result.as_log_payload()

    assert result.requires_tool_gate is True
    assert payload["tool_intents"][0]["name"] == "patch"
    assert payload["tool_intents"][0]["argument_keys"] == ["api_key", "client", "env", "nested", "path", "prompt_text"]
    assert payload["tool_intents"][0]["argument_count"] == 6
    assert payload["tool_intents"][0]["redacted_arguments"] is True
    assert "arguments" not in payload["tool_intents"][0]
    assert "secret-value" not in str(payload)
    assert "leak me" not in str(payload)
    assert "TOKEN" not in str(payload)
    assert "password" not in str(payload)
    assert "<object object" not in str(payload)
    assert payload == log_payload


def test_blocked_runtime_plan_fails_closed(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")
    blocked_runtime = replace(runtime_result, actual_runtime_status="blocked")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(executor=lambda *_args, **_kwargs: {"output_text": "unexpected"}).run(
        blocked_runtime,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-blocked-1",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "runtime_plan_not_ready"
    assert result.execution_status == "rejected"


def test_missing_executor_fails_closed(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(executor=None).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-no-executor",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "missing_executor"
    assert result.execution_status == "failed"


def test_executor_exception_becomes_structured_failure(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    def boom(_request, _runtime_plan):
        raise RuntimeError("executor exploded")

    result = SubagentRunner(executor=boom).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-executor-exc",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "executor_exception"
    assert "exploded" in (result.error_message or "")


def test_malformed_executor_result_becomes_structured_failure(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(executor=lambda *_args, **_kwargs: object()).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-malformed",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "malformed_executor_result"


def test_malformed_executor_result_rejects_non_list_tool_intents(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(
        executor=lambda *_args, **_kwargs: {"tool_intents": {"name": "patch"}}
    ).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-malformed-tool-intents",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "malformed_executor_result"
    assert result.execution_status == "failed"


def test_malformed_executor_result_rejects_non_mapping_tool_intent_item(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(
        executor=lambda *_args, **_kwargs: {"tool_intents": ["patch"]}
    ).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-malformed-tool-intent-item",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "malformed_executor_result"


def test_malformed_executor_result_rejects_non_string_output_text(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "general_operator")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(
        executor=lambda *_args, **_kwargs: {"output_text": {"unexpected": "shape"}}
    ).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-malformed-output-text",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "malformed_executor_result"


def test_subagent_id_mismatch_is_rejected(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "hermes_engineer_core")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(executor=lambda *_args, **_kwargs: {"output_text": "unexpected"}).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="general_operator",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-mismatch",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "subagent_id_mismatch"


def test_safe_dict_excludes_prompt_text_secrets_and_raw_clients(tmp_path: Path) -> None:
    _, runtime_result = _build_runtime_result(tmp_path, "hermes_engineer_core")

    from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner

    result = SubagentRunner(
        executor=lambda *_args, **_kwargs: {
            "output_text": "x" * 400,
            "completion_reason": "completed",
            "raw_metadata": {
                "trace_id": "trace-1",
                "api_key": "secret-value",
                "prompt_text": "do not leak",
                "client": object(),
                "env": {"TOKEN": "secret"},
            },
        }
    ).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-safe",
            input_messages=[{"role": "user", "content": "Hello"}],
        ),
    )

    payload = result.to_safe_dict()

    assert payload["record"]["prompt_artifact"]["path"] == runtime_result.prompt.path
    assert "prompt_text" not in str(payload)
    assert "secret-value" not in str(payload)
    assert "TOKEN" not in str(payload)
    assert "client" not in payload.get("raw_metadata", {})
    assert len(payload["record"]["safe_output_summary"]) < 260


def test_importing_subagent_runner_stays_import_light() -> None:
    before = set(sys.modules)
    for name in (
        "hermes_cli.subagent_runner",
        "gateway.run",
        "tools.tool_executor",
        "agent.conversation_loop",
        "run_agent",
        "slack_sdk",
    ):
        sys.modules.pop(name, None)

    module = importlib.import_module("hermes_cli.subagent_runner")

    assert hasattr(module, "SubagentRunner")
    imported = set(sys.modules) - before
    assert "gateway.run" not in imported
    assert "tools.tool_executor" not in imported
    assert "agent.conversation_loop" not in imported
    assert "run_agent" not in imported
    assert "slack_sdk" not in imported


def test_build_runner_request_from_runtime_factory_plan(tmp_path: Path) -> None:
    session, step, plan = _runtime_factory_plan(
        tmp_path,
        step_kind="engineer",
        subagent_id="hermes_engineer_core",
    )

    from hermes_cli.subagent_runner import build_subagent_runner_request

    request = build_subagent_runner_request(
        session=session,
        planned_step=step,
        runtime_factory_plan=plan,
    )

    assert request.pipeline_session_id == session.pipeline_session_id
    assert request.trace_id == session.trace_id
    assert request.pipeline_id == "engineering_review_pipeline"
    assert request.step_id == "engineer"
    assert request.subagent_id == "hermes_engineer_core"
    assert request.role_id == "engineer"
    assert request.runtime_factory_plan_id == "pipe-runner-contract:engineer:hermes_engineer_core"
    assert request.runtime_factory_status == "plan_only"
    assert request.actual_provider is None
    assert request.actual_model is None
    assert request.prompt_input_hash == session.user_message_hash


def test_not_invoked_runner_result_for_observe_mode(tmp_path: Path) -> None:
    session, step, plan = _runtime_factory_plan(
        tmp_path,
        step_kind="reviewer",
        subagent_id="hermes_code_reviewer",
    )

    from hermes_cli.subagent_runner import (
        SubagentRunnerStatus,
        build_not_invoked_runner_result,
        build_subagent_runner_request,
    )

    request = build_subagent_runner_request(
        session=session,
        planned_step=step,
        runtime_factory_plan=plan,
    )
    result = build_not_invoked_runner_result(
        request=request,
        runtime_factory_plan=plan,
        reason="observe_mode_plan_only",
    )

    assert result.status == SubagentRunnerStatus.NOT_INVOKED
    assert result.failure_reason == "observe_mode_plan_only"
    assert result.schema_validation_status == "not_applicable"
    assert result.structured_output is None
    assert result.raw_output_redacted is True
    assert result.actual_provider is None
    assert result.actual_model is None
    assert result.tool_call_summaries == []


def test_structured_output_envelope_validates_known_good_payload() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    envelope = validate_structured_output_envelope(_valid_envelope_payload())

    assert envelope.validation_status == "valid"
    assert envelope.status == "needs_review"
    assert envelope.findings[0]["code"] == "bug"


def test_structured_output_envelope_fails_closed_on_missing_required_fields() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    envelope = validate_structured_output_envelope(
        {
            "schema_version": "v1",
            "subagent_id": "hermes_engineer_core",
            "role": "engineer",
            "summary": "missing status",
            "blockers": [],
            "artifacts": [],
            "confidence": 0.5,
            "requires_review": False,
            "next_action": "none",
        }
    )

    assert envelope.validation_status == "invalid_structured_output"
    assert envelope.validation_errors
    assert any(error["field"] == "status" for error in envelope.validation_errors)


def test_invalid_output_returns_invalid_structured_output() -> None:
    from hermes_cli.subagent_runner import (
        StructuredOutputEnvelope,
        validate_structured_output_envelope,
    )

    envelope = validate_structured_output_envelope("not-a-dict")

    assert isinstance(envelope, StructuredOutputEnvelope)
    assert envelope.validation_status == "invalid_structured_output"
    assert envelope.validation_errors[0]["field"] == "payload"


def test_structured_output_envelope_rejects_invalid_status_type() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    for invalid_status in ([], 123):
        envelope = validate_structured_output_envelope(_valid_envelope_payload(status=invalid_status))
        assert envelope.validation_status == "invalid_structured_output"
        assert any(error["field"] == "status" for error in envelope.validation_errors)


def test_structured_output_envelope_rejects_invalid_required_string_types() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    invalid_cases = {
        "schema_version": 1,
        "subagent_id": {"id": "hermes_code_reviewer"},
        "role": ["reviewer"],
        "summary": {"text": "Review completed"},
        "next_action": False,
    }

    for field_name, invalid_value in invalid_cases.items():
        envelope = validate_structured_output_envelope(_valid_envelope_payload(**{field_name: invalid_value}))
        assert envelope.validation_status == "invalid_structured_output"
        assert any(error["field"] == field_name for error in envelope.validation_errors)


def test_structured_output_envelope_rejects_blank_required_strings() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    for field_name in ("schema_version", "subagent_id", "role", "status", "summary", "next_action"):
        envelope = validate_structured_output_envelope(_valid_envelope_payload(**{field_name: "   "}))
        assert envelope.validation_status == "invalid_structured_output"
        assert any(error["field"] == field_name for error in envelope.validation_errors)


def test_structured_output_envelope_rejects_bool_confidence() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    envelope = validate_structured_output_envelope(_valid_envelope_payload(confidence=True))

    assert envelope.validation_status == "invalid_structured_output"
    assert any(error["field"] == "confidence" for error in envelope.validation_errors)


def test_structured_output_envelope_rejects_out_of_range_confidence() -> None:
    from hermes_cli.subagent_runner import validate_structured_output_envelope

    for confidence in (-0.01, 1.01):
        envelope = validate_structured_output_envelope(_valid_envelope_payload(confidence=confidence))
        assert envelope.validation_status == "invalid_structured_output"
        assert any(error["field"] == "confidence" for error in envelope.validation_errors)
