from __future__ import annotations

from dataclasses import replace
import importlib
import shutil
import sys
from pathlib import Path

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
