from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import pytest
import run_agent
import shutil
import subprocess

from hermes_cli.pipeline_aiagent_executor import (
    AIAgentExecutorBridgeError,
    AIAgentReviewerExecutorBridge,
    AIAgentSubagentExecutorBridge,
)
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeFactory
from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentRunner
from hermes_cli.pipeline_rework_loop import execute_bounded_rework_loop

REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")
BRIDGE_MODULE = REPO_ROOT / "hermes_cli" / "pipeline_aiagent_executor.py"


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "bridge-git-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, text=True, capture_output=True)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, text=True, capture_output=True)
    return repo


def _engineering_session():
    decision = RouterDecision(
        pipeline_session_id="pipe-aiagent-bridge",
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
            session_id="sess-aiagent-bridge",
            user_message="implement code",
            created_at="2026-06-21T00:00:00+00:00",
        )
    )


def _build_runtime_result(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    factory = RuntimeFactory(repo_root=repo_root)
    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-aiagent-bridge",
            invocation_id="inv-aiagent-bridge",
        )
    )
    return repo_root, result


def _build_reviewer_runtime_result(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    factory = RuntimeFactory(repo_root=repo_root)
    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_code_reviewer",
            pipeline_session_id="pipe-aiagent-bridge",
            invocation_id="inv-aiagent-reviewer-bridge",
        )
    )
    return repo_root, result


class _FakeAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.tools = []
        self.valid_tool_names = set()
        self.enabled_toolsets = None
        self.disabled_toolsets = None
        fallback_model = kwargs.get("fallback_model")
        if isinstance(fallback_model, list):
            self._fallback_chain = list(fallback_model)
        elif isinstance(fallback_model, dict) and fallback_model.get("provider") and fallback_model.get("model"):
            self._fallback_chain = [dict(fallback_model)]
        else:
            self._fallback_chain = []

    def run_conversation(self, _message: str):
        return {"final_response": "unused"}


class _DispatchingFakeAgent(_FakeAgent):
    def __init__(self, *, tool_name: str, tool_args: dict[str, object], **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.tool_args = dict(tool_args)

    def run_conversation(self, _message: str):
        tool_result = run_agent.handle_function_call(self.tool_name, self.tool_args)
        return {
            "final_response": "tool call completed",
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_engineer_core",
                    "role": "engineer",
                    "status": "succeeded",
                    "summary": "Prepared patch.",
                    "findings": [],
                    "changes": [],
                    "blockers": [],
                    "artifacts": [],
                    "confidence": 0.9,
                    "requires_review": False,
                    "next_action": "none",
                },
                "tool_result": tool_result,
            },
        }


def test_bridge_constructs_aiagent_from_runtime_kwargs(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    captured: dict[str, object] = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeAgent(**kwargs)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _bridge, _agent, _request, _runtime: {
            "output_text": "ok",
            "raw_metadata": {"structured_output": {"schema_version": "v1", "subagent_id": "hermes_engineer_core", "role": "engineer", "status": "succeeded", "summary": "Prepared patch.", "findings": [], "changes": [], "blockers": [], "artifacts": [], "confidence": 0.9, "requires_review": False, "next_action": "none"}},
        },
    )

    result = bridge(
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-1",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
        runtime_result,
    )

    assert captured["provider"] == "openrouter"
    assert captured["model"] == "xiaomi/mimo-v2.5-pro"
    assert captured["fallback_model"] == {
        "provider": "openai-codex",
        "model": "gpt-5.4",
    }
    assert captured["api_mode"] == runtime_result.constructor_api_mode
    assert captured["quiet_mode"] is True
    assert captured["enabled_toolsets"] == []
    assert captured["disabled_toolsets"] == ["terminal", "browser", "web", "code_execution", "computer_use", "messaging"]
    assert result["output_text"] == "ok"


def test_bridge_production_code_does_not_hardcode_current_engineer_fallback() -> None:
    source = BRIDGE_MODULE.read_text(encoding="utf-8")

    assert "_ENGINEER_REQUIRED_FALLBACK" not in source
    assert '"provider": "openai-codex"' not in source
    assert '"model": "gpt-5.4"' not in source


def test_bridge_rejects_engineer_agent_when_fallback_chain_is_missing(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
    )

    missing_fallback_result = replace(runtime_result, fallback_policy=None)

    with pytest.raises(AIAgentExecutorBridgeError, match="missing_engineer_fallback_policy"):
        bridge._build_agent(missing_fallback_result)


def test_bridge_accepts_config_derived_engineer_fallback_without_python_changes(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    alt_policy = replace(
        runtime_result.fallback_policy,
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    alt_runtime_result = replace(runtime_result, fallback_policy=alt_policy)
    captured: dict[str, object] = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeAgent(**kwargs)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _bridge, _agent, _request, _runtime: {"output_text": "ok"},
    )

    result = bridge(
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=alt_runtime_result.pipeline_session_id,
            invocation_id="inv-alt-fallback",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
        alt_runtime_result,
    )

    assert captured["fallback_model"] == {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
    }
    assert result["output_text"] == "ok"


def test_bridge_rejects_engineer_agent_when_fallback_chain_is_missing_after_construction(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    class _MissingFallbackAgent(_FakeAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._fallback_chain = []

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_MissingFallbackAgent,
    )

    with pytest.raises(AIAgentExecutorBridgeError, match="invalid_engineer_fallback_chain"):
        bridge._build_agent(runtime_result)


def test_bridge_rejects_engineer_agent_when_global_fallback_chain_leaks_in(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    class _WrongFallbackAgent(_FakeAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._fallback_chain = [{"provider": "openrouter", "model": "google/gemma-4-31b-it:free"}]

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_WrongFallbackAgent,
    )

    with pytest.raises(AIAgentExecutorBridgeError, match="invalid_engineer_fallback_chain"):
        bridge._build_agent(runtime_result)


def test_bridge_workspace_write_and_git_delta_succeed(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    def _runner(bridge, _agent, _request, _runtime):
        bridge.execute_tool("write_file", {"path": "notes.txt", "content": "hello\n"})
        diff_payload = json.loads(bridge.execute_tool("git_diff", {}))
        return {
            "output_text": "ok",
            "tool_calls": [{"tool_name": "write_file"}, {"tool_name": "git_diff"}],
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_engineer_core",
                    "role": "engineer",
                    "status": "succeeded",
                    "summary": "Prepared patch.",
                    "findings": [],
                    "changes": [{"path": "notes.txt", "kind": "modify"}],
                    "blockers": [],
                    "artifacts": [],
                    "confidence": 0.9,
                    "requires_review": False,
                    "next_action": "none",
                },
                "diff": diff_payload,
            },
        }

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=_runner,
    )
    result = SubagentRunner(executor=bridge).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-2",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
    )

    assert result.ok is True
    assert (git_repo / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert result.tool_intents_count == 2
    assert [item["name"] for item in result.tool_intents] == ["write_file", "git_diff"]
    assert "notes.txt" in subprocess.run(["git", "-C", str(git_repo), "status", "--short", "--untracked-files=all"], check=True, text=True, capture_output=True).stdout


def test_normalize_result_preserves_raw_metadata_structured_output(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    payload = {
        "output_text": "ok",
        "raw_metadata": {"structured_output": {"schema_version": "1", "subagent_id": "hermes_engineer_core", "role": "engineer", "status": "completed", "summary": "done"}},
    }
    normalized = bridge._normalize_result(payload)
    assert normalized["raw_metadata"]["structured_output"]["subagent_id"] == "hermes_engineer_core"
    assert normalized["raw_metadata"]["structured_output_source"] == "raw_metadata.structured_output"


def test_normalize_result_copies_top_level_structured_output(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    normalized = bridge._normalize_result(
        {
            "output_text": "ok",
            "structured_output": {"schema_version": "1", "subagent_id": "hermes_engineer_core", "role": "engineer", "status": "completed", "summary": "done"},
        }
    )
    assert normalized["raw_metadata"]["structured_output_source"] == "structured_output"
    assert normalized["raw_metadata"]["structured_output"]["role"] == "engineer"


def test_normalize_result_extracts_structured_output_from_final_response_mapping(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    normalized = bridge._normalize_result(
        {
            "final_response": {
                "schema_version": "1",
                "subagent_id": "hermes_engineer_core",
                "role": "engineer",
                "status": "completed",
                "summary": "done",
                "blockers": [],
            }
        }
    )
    assert normalized["raw_metadata"]["structured_output_source"] == "final_response"
    assert normalized["raw_metadata"]["structured_output"]["summary"] == "done"
    assert normalized["output_text"] is None


def test_normalize_result_extracts_parseable_json_from_output_text(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    normalized = bridge._normalize_result(
        json.dumps(
            {
                "schema_version": "1",
                "subagent_id": "hermes_engineer_core",
                "role": "engineer",
                "status": "completed",
                "summary": "done",
                "blockers": [],
                "artifacts": [],
                "confidence": 0.8,
                "requires_review": True,
                "next_action": "review",
                "changes": [],
            }
        )
    )
    assert normalized["raw_metadata"]["structured_output_source"] == "output_text_json"
    assert normalized["raw_metadata"]["structured_output"]["next_action"] == "review"


def test_normalize_result_records_parse_failure_without_structured_output(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    normalized = bridge._normalize_result("not json")
    assert normalized["raw_metadata"]["structured_output_missing"] is True
    assert normalized["raw_metadata"]["structured_output_source"] == "none"
    assert normalized["raw_metadata"]["structured_output_parse_error"].startswith("json_decode_error:")


def test_engineer_prompt_and_config_describe_structured_output_envelope(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    prompt_text = (repo_root / "prompts/subagents/hermes_engineer_core.md").read_text(encoding="utf-8")
    config_text = (repo_root / "config/subagents/hermes_engineer_core.yaml").read_text(encoding="utf-8")
    for required_field in (
        "schema_version",
        "subagent_id",
        "role",
        "status",
        "summary",
        "blockers",
        "artifacts",
        "confidence",
        "requires_review",
        "next_action",
    ):
        assert required_field in prompt_text
        assert required_field in config_text


def test_bridge_rejects_absolute_and_traversal_paths(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    for tool_args, reason in [
        ({"path": "/tmp/escape.txt", "content": "nope"}, "absolute_path_denied"),
        ({"path": "../escape.txt", "content": "nope"}, "path_outside_workspace"),
    ]:
        try:
            bridge.execute_tool("write_file", tool_args)
        except AIAgentExecutorBridgeError as exc:
            assert str(exc) == reason
        else:
            raise AssertionError("expected path guard failure")

    result = SubagentRunner(executor=bridge).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-3",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
    )
    assert result.ok is True


def test_bridge_rejects_symlink_escape(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (git_repo / "escape").symlink_to(outside)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    try:
        bridge.execute_tool("read_file", {"path": "escape"})
    except AIAgentExecutorBridgeError as exc:
        assert str(exc) == "symlink_target_denied"
    else:
        raise AssertionError("expected symlink guard failure")


def test_bridge_pytest_is_constrained_and_terminal_missing(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    payload = json.loads(bridge.execute_tool("pytest", {"command": "python -m pytest -q tests/test_ok.py"}))
    assert payload["status"] == "passed"
    assert all(tool["function"]["name"] != "terminal" for tool in bridge._tool_definitions())


def test_bridge_pytest_accepts_structured_payload_and_hides_executable_choice(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    payload = json.loads(bridge.execute_tool("pytest", {"targets": ["tests/test_ok.py"], "quiet": True, "maxfail": 1}))

    assert payload["status"] == "passed"
    pytest_tool = next(tool for tool in bridge._tool_definitions() if tool["function"]["name"] == "pytest")
    assert "command" not in pytest_tool["function"]["parameters"]["properties"]


def test_bridge_pytest_accepts_bare_pytest_safe_form(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    payload = json.loads(bridge.execute_tool("pytest", {"command": "pytest -q tests/test_ok.py"}))

    assert payload["status"] == "passed"


def test_bridge_pytest_denial_captures_forensics(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    try:
        bridge.execute_tool("pytest", {"command": "pytest -q tests/../escape.py"})
    except AIAgentExecutorBridgeError as exc:
        assert str(exc) == "test_command_denied"
    else:
        raise AssertionError("expected constrained pytest denial")


def test_bridge_can_patch_existing_file(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    (git_repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    bridge.execute_tool("patch", {"path": "tracked.txt", "old": "before", "new": "after"})
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "after\n"


def test_bridge_context_manager_routes_tool_dispatch(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    with bridge.patched_tool_dispatch():
        payload = json.loads(run_agent.handle_function_call("write_file", {"path": "via-dispatch.txt", "content": "ok\n"}))
    assert payload["applied_count"] == 1
    assert (git_repo / "via-dispatch.txt").exists()


def test_default_runner_intercepts_global_write_file_dispatch(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=lambda **kwargs: _DispatchingFakeAgent(
            tool_name="write_file",
            tool_args={"path": "default-runner.txt", "content": "ok\n"},
            **kwargs,
        ),
    )

    result = SubagentRunner(executor=bridge).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-default-runner-1",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
    )

    assert result.ok is True
    assert (git_repo / "default-runner.txt").read_text(encoding="utf-8") == "ok\n"
    assert result.tool_intents_count == 1
    assert result.tool_intents[0]["name"] == "write_file"


def test_default_runner_rejects_outside_workspace_write_via_global_dispatch(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=lambda **kwargs: _DispatchingFakeAgent(
            tool_name="write_file",
            tool_args={"path": "../escape.txt", "content": "nope\n"},
            **kwargs,
        ),
    )

    result = SubagentRunner(executor=bridge).run(
        runtime_result,
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-default-runner-2",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
    )

    assert result.ok is False
    assert result.error_code == "executor_exception"
    assert "path_outside_workspace" in (result.error_message or "")
    assert not (tmp_path / "escape.txt").exists()


def test_bridge_integrates_with_bounded_rework_loop_and_observed_git_delta(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    git_repo = _init_git_repo(tmp_path)

    def _runner(bridge, _agent, _request, runtime_plan):
        if runtime_plan.subagent_id == "hermes_engineer_core":
            bridge.execute_tool("write_file", {"path": "bridge_loop.txt", "content": "loop mutation\n"})
            return {
                "output_text": "engineer ok",
                "raw_metadata": {
                    "structured_output": {
                        "schema_version": "v1",
                        "subagent_id": "hermes_engineer_core",
                        "role": "engineer",
                        "status": "succeeded",
                        "summary": "Prepared patch.",
                        "findings": [],
                        "changes": [{"path": "bridge_loop.txt", "kind": "modify"}],
                        "blockers": [],
                        "artifacts": [],
                        "confidence": 0.9,
                        "requires_review": False,
                        "next_action": "none",
                    }
                },
            }
        return {
            "output_text": "review ok",
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_code_reviewer",
                    "role": "reviewer",
                    "status": "succeeded",
                    "summary": "approved",
                    "findings": [],
                    "changes": [],
                    "blockers": [],
                    "artifacts": [],
                    "confidence": 0.9,
                    "requires_review": False,
                    "next_action": "none",
                }
            },
        }

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=_runner,
    )

    result = execute_bounded_rework_loop(
        config={
            "pipelines": {
                "enabled": True,
                "execution": {
                    "mode": "autonomous",
                    "enable_gateway_execution_controller": True,
                    "allow_actual_subagent_invocation": True,
                    "allow_actual_reviewer_invocation": True,
                    "allow_actual_rework_loop": True,
                    "allow_pipelines": ["engineering_review_pipeline"],
                    "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                }
            }
        },
        session=_engineering_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bridge loop",
        repo_path=str(git_repo),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "executor_bridge": bridge,
            "invocation_client": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fake runtime must not be used")),
        },
    )

    assert result.completion_allowed is False
    assert result.blocked_reason is not None
    assert result.git_gate["changed_files"] == ["bridge_loop.txt"]
    assert result.reviewer_packet["safe_packet"]["git"]["changed_files"] == ["bridge_loop.txt"]
    assert (git_repo / "bridge_loop.txt").read_text(encoding="utf-8") == "loop mutation\n"
    assert result.subagent_runs[0]["actual_provider"] == "openrouter"
    assert result.subagent_runs[0]["tool_call_summaries"][0]["tool_name"] == "write_file"


def test_reviewer_bridge_requires_actual_packet_and_disallows_mutating_tools(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_reviewer_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentReviewerExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=lambda _bridge, _agent, request, _runtime: {
            "output_text": request.metadata["reviewer_packet"]["safe_packet"]["git"]["changed_files"][0],
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_code_reviewer",
                    "role": "reviewer",
                    "status": "succeeded",
                    "summary": "approved",
                    "findings": [],
                    "changes": [],
                    "blockers": [],
                    "artifacts": [],
                    "confidence": 0.9,
                    "requires_review": False,
                    "next_action": "none",
                }
            },
        },
    )

    for tool_name in ("write_file", "patch", "pytest", "terminal"):
        try:
            bridge.execute_tool(tool_name, {"path": "blocked.txt", "content": "nope"})
        except AIAgentExecutorBridgeError as exc:
            assert str(exc) == f"tool_not_allowed:{tool_name}"
        else:
            raise AssertionError("expected reviewer tool policy failure")

    try:
        bridge(
            SubagentInvocationRequest(
                subagent_id="hermes_code_reviewer",
                pipeline_session_id=runtime_result.pipeline_session_id,
                invocation_id="inv-reviewer-missing-packet",
                input_messages=[{"role": "user", "content": "Review change"}],
                metadata={},
            ),
            runtime_result,
        )
    except AIAgentExecutorBridgeError as exc:
        assert str(exc) == "reviewer_packet_missing"
    else:
        raise AssertionError("expected reviewer packet guard failure")


def test_bridge_loop_routes_reviewer_to_read_only_bridge_and_passes_actual_packet(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    git_repo = _init_git_repo(tmp_path)
    reviewer_packets: list[dict[str, object]] = []

    def _engineer_runner(bridge, _agent, request, _runtime):
        assert request.subagent_id == "hermes_engineer_core"
        bridge.execute_tool("write_file", {"path": "bridge_loop.txt", "content": "loop mutation\n"})
        return {
            "output_text": "engineer ok",
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_engineer_core",
                    "role": "engineer",
                    "status": "succeeded",
                    "summary": "Prepared patch.",
                    "findings": [],
                    "changes": [{"path": "bridge_loop.txt", "kind": "modify"}],
                    "blockers": [],
                    "artifacts": [],
                    "confidence": 0.9,
                    "requires_review": False,
                    "next_action": "none",
                }
            },
        }

    def _reviewer_runner(_bridge, _agent, request, _runtime):
        reviewer_packets.append(dict(request.metadata["reviewer_packet"]["safe_packet"]))
        return {
            "output_text": "review ok",
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_code_reviewer",
                    "role": "reviewer",
                    "status": "succeeded",
                    "summary": "approved",
                    "findings": [],
                    "changes": [],
                    "blockers": [],
                    "artifacts": [],
                    "confidence": 0.9,
                    "requires_review": False,
                    "next_action": "none",
                }
            },
        }

    engineer_bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=_engineer_runner,
    )
    reviewer_bridge = AIAgentReviewerExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=_reviewer_runner,
    )

    result = execute_bounded_rework_loop(
        config={
            "pipelines": {
                "enabled": True,
                "execution": {
                    "mode": "autonomous",
                    "enable_gateway_execution_controller": True,
                    "allow_actual_subagent_invocation": True,
                    "allow_actual_reviewer_invocation": True,
                    "allow_actual_rework_loop": True,
                    "allow_pipelines": ["engineering_review_pipeline"],
                    "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                }
            }
        },
        session=_engineering_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bridge loop",
        repo_path=str(git_repo),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "executor_bridge": {
                "hermes_engineer_core": engineer_bridge,
                "hermes_code_reviewer": reviewer_bridge,
            },
            "invocation_client": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fake runtime must not be used")),
        },
    )

    assert result.completion_allowed is True
    assert result.execution_report is not None
    assert result.execution_report.execution_mode == "autonomous"
    assert result.execution_report.executed is True
    assert reviewer_packets and reviewer_packets[0]["git"]["changed_files"] == ["bridge_loop.txt"]
    assert reviewer_packets[0]["packet_status"] == "ready_for_review"
    assert result.subagent_runs[1]["actual_provider"] == "openai-codex"
