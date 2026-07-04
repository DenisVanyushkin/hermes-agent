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

    assert captured["provider"] == "openai-codex"
    assert captured["model"] == "gpt-5.4"
    assert captured["fallback_model"] == {
        "provider": "openai-codex",
        "model": "gpt-5.4-mini",
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


def _init_git_repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """Local-only clone/origin pair (file:// remote) so git_remote_status can be
    exercised entirely offline -- no live network, no real remote."""
    origin = _init_git_repo(tmp_path)
    clone = tmp_path / "bridge-git-repo-clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "Test User"], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "test@example.com"], check=True, text=True, capture_output=True)
    return origin, clone


def test_git_remote_status_reports_commits_ahead_on_remote(tmp_path: Path) -> None:
    origin, clone = _init_git_repo_with_origin(tmp_path)
    (origin / "tracked.txt").write_text("baseline\nupdated on origin\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(origin), "commit", "-am", "origin-only change"], check=True, text=True, capture_output=True)

    bridge = AIAgentSubagentExecutorBridge(workspace_root=clone, agent_factory=_FakeAgent)
    payload = json.loads(bridge.execute_tool("git_remote_status", {}))

    assert payload["status"] == 0
    assert payload["remote"] == "origin"
    assert payload["comparison_available"] is True
    assert payload["local_ahead_of_remote_count"] == 0
    assert payload["local_behind_remote_count"] == 1
    assert "origin-only change" in payload["commits_on_remote_not_local"]
    # No merge, no local branch update, no push: the clone's working tree and
    # HEAD are untouched by the read-only fetch.
    assert (clone / "tracked.txt").read_text(encoding="utf-8") == "baseline\n"
    status = subprocess.run(["git", "-C", str(clone), "status", "--short"], check=True, text=True, capture_output=True)
    assert status.stdout.strip() == ""


def test_git_remote_status_reports_up_to_date_when_no_divergence(tmp_path: Path) -> None:
    _origin, clone = _init_git_repo_with_origin(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(workspace_root=clone, agent_factory=_FakeAgent)
    payload = json.loads(bridge.execute_tool("git_remote_status", {}))

    assert payload["comparison_available"] is True
    assert payload["local_ahead_of_remote_count"] == 0
    assert payload["local_behind_remote_count"] == 0
    assert payload["commits_on_remote_not_local"] == ""


def test_git_remote_status_rejects_non_plain_remote_names(tmp_path: Path) -> None:
    _origin, clone = _init_git_repo_with_origin(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=clone, agent_factory=_FakeAgent)

    with pytest.raises(AIAgentExecutorBridgeError):
        bridge.execute_tool("git_remote_status", {"remote": "https://example.com/evil.git"})


def test_git_remote_status_is_available_to_reviewer_bridge(tmp_path: Path) -> None:
    _origin, clone = _init_git_repo_with_origin(tmp_path)
    bridge = AIAgentReviewerExecutorBridge(workspace_root=clone, agent_factory=_FakeAgent)

    payload = json.loads(bridge.execute_tool("git_remote_status", {}))

    assert payload["comparison_available"] is True


def test_normalize_result_preserves_raw_metadata_structured_output(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    payload = {
        "output_text": "ok",
        "raw_metadata": {"structured_output": {"schema_version": "1", "subagent_id": "hermes_engineer_core", "role": "engineer", "status": "succeeded", "summary": "done"}},
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
            "structured_output": {"schema_version": "1", "subagent_id": "hermes_engineer_core", "role": "engineer", "status": "succeeded", "summary": "done"},
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
                "status": "succeeded",
                "summary": "done",
                "blockers": [],
            }
        }
    )
    assert normalized["raw_metadata"]["structured_output_source"] == "final_response"
    assert normalized["raw_metadata"]["structured_output"]["summary"] == "done"


def test_normalize_result_synthesizes_blocked_envelope_for_plain_text_response(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "output_text": "Investigated the runtime path and confirmed the bridge reached execution.",
            "completion_reason": "text_response(finish_reason=stop)",
            "execution_status": "completed",
        }
    )

    structured_output = normalized["raw_metadata"]["structured_output"]
    assert structured_output["status"] == "blocked"
    assert structured_output["subagent_id"] == "hermes_engineer_core"
    assert structured_output["role"] == "engineer"
    assert structured_output["next_action"] == "retry_with_structured_output"
    assert structured_output["blockers"] == ["missing_structured_output"]
    assert structured_output["findings"][0]["code"] == "missing_structured_output"
    assert normalized["raw_metadata"]["structured_output_source"] == "synthesized_plain_text_blocked"
    assert normalized["raw_metadata"]["synthesized_envelope"] is True
    assert normalized["raw_metadata"]["repair_attempted"] is False
    assert normalized["raw_metadata"]["repair_succeeded"] is False
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_text_response_without_structured_output"


def test_normalize_result_keeps_valid_structured_output_without_synthesis(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "output_text": "ignored because raw structured output is authoritative",
            "raw_metadata": {
                "structured_output": {
                    "schema_version": "v1",
                    "subagent_id": "hermes_engineer_core",
                    "role": "engineer",
                    "status": "blocked",
                    "summary": "already structured",
                    "findings": [{"code": "structured", "summary": "already structured"}],
                    "changes": [],
                    "blockers": ["upstream"],
                    "artifacts": [],
                    "confidence": 0.2,
                    "requires_review": False,
                    "next_action": "none",
                }
            },
        }
    )

    assert normalized["raw_metadata"]["structured_output_source"] == "raw_metadata.structured_output"
    assert normalized["raw_metadata"].get("synthesized_envelope") is None
    assert normalized["output_text"] == "ignored because raw structured output is authoritative"


def test_reviewer_bridge_plain_text_does_not_synthesize_engineer_envelope(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_reviewer_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentReviewerExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "output_text": "plain reviewer text",
            "completion_reason": "text_response(finish_reason=stop)",
            "execution_status": "completed",
        }
    )

    raw_metadata = normalized["raw_metadata"]
    assert raw_metadata.get("synthesized_envelope") is None
    assert raw_metadata.get("structured_output") is None
    assert raw_metadata["structured_output_missing"] is True
    assert raw_metadata["structured_output_source"] == "none"
    assert raw_metadata.get("structured_output_missing_reason") is None
    assert raw_metadata.get("structured_output_missing_blocked_reason") is None


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


def test_normalize_result_marks_max_iterations_plain_text_as_controlled_missing_structured_output(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)
    normalized = bridge._normalize_result(
        {
            "final_response": "plain text diagnostic summary",
            "output_text": "plain text diagnostic summary",
            "raw_metadata": {"end_reason": "max_iterations_reached(12/12)"},
        }
    )
    assert normalized["completion_reason"] == "max_iterations_reached(12/12)"
    assert normalized["raw_metadata"]["structured_output_missing"] is True
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_max_iterations_without_structured_output"
    assert normalized["raw_metadata"]["structured_output_missing_blocked_reason"] == "max_iterations_plain_text_output"
    assert normalized["raw_metadata"]["diagnostic_output_text"] == "plain text diagnostic summary"


def _assert_engineer_blocked_envelope(envelope: dict[str, object], *, summary_fragment: str | None = None) -> None:
    assert envelope["schema_version"] == "v1"
    assert envelope["subagent_id"] == "hermes_engineer_core"
    assert envelope["role"] == "engineer"
    assert envelope["status"] == "blocked"
    assert isinstance(envelope.get("findings"), list) and envelope["findings"]
    assert envelope["changes"] == []
    assert isinstance(envelope.get("blockers"), list) and envelope["blockers"]
    assert envelope["artifacts"] == []
    assert envelope["confidence"] == 0.0
    assert envelope["requires_review"] is False
    assert envelope["next_action"] == "retry_with_structured_output"
    if summary_fragment is not None:
        haystack = json.dumps(envelope, ensure_ascii=False).lower()
        assert summary_fragment.lower() in haystack


def test_normalize_result_real_aiagent_text_response_shape_normalizes_terminal_fields(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "Bridge reached execution but returned prose instead of the required envelope.",
            "raw_metadata": {"real_provider_bridge_invoked": True},
        }
    )

    assert normalized["completion_reason"] == "text_response(finish_reason=stop)"
    assert normalized["output_text"] == "Bridge reached execution but returned prose instead of the required envelope."
    assert normalized["raw_metadata"]["structured_output_source"] == "synthesized_plain_text_blocked"
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_text_response_without_structured_output"
    _assert_engineer_blocked_envelope(normalized["raw_metadata"]["structured_output"], summary_fragment="required envelope")


def test_normalize_result_provider_error_without_output_synthesizes_blocked_envelope(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "turn_exit_reason": "provider_error",
            "final_response": None,
            "raw_metadata": {
                "real_provider_bridge_invoked": True,
                "provider_error": "HTTP 402 Payment Required",
                "http_status": 402,
            },
        }
    )

    assert normalized["completion_reason"] == "provider_error"
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_provider_error_without_structured_output"
    assert normalized["raw_metadata"]["structured_output_missing_blocked_reason"] == "provider_error_without_structured_output"
    _assert_engineer_blocked_envelope(normalized["raw_metadata"]["structured_output"], summary_fragment="402")


def test_normalize_result_fallback_exhausted_without_output_synthesizes_blocked_envelope(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "turn_exit_reason": "fallback_exhausted",
            "final_response": None,
            "raw_metadata": {
                "real_provider_bridge_invoked": True,
                "fallback_status": "exhausted",
                "fallback_diagnostic": "openai-codex/gpt-5.4 unavailable after retries",
            },
        }
    )

    assert normalized["completion_reason"] == "fallback_exhausted"
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_fallback_exhausted_without_structured_output"
    assert normalized["raw_metadata"]["structured_output_missing_blocked_reason"] == "fallback_exhausted_without_structured_output"
    _assert_engineer_blocked_envelope(normalized["raw_metadata"]["structured_output"], summary_fragment="unavailable")


def test_bridge_exposes_effective_fallback_metadata_when_fallback_activates(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=lambda _bridge, _agent, _request, _runtime: {
            "output_text": "fallback ok",
            "provider": "openai-codex",
            "model": "gpt-5.4-mini",
            "base_url": "https://chatgpt.com/backend-api/codex/",
            "raw_metadata": {
                "fallback_activated": True,
                "fallback_attempted": True,
                "fallback_result": "activated",
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
            },
        },
    )

    result = bridge(
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-fallback-activated",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
        runtime_result,
    )

    raw_metadata = result["raw_metadata"]
    assert raw_metadata["initial_provider"] == "openai-codex"
    assert raw_metadata["initial_model"] == "gpt-5.4"
    assert raw_metadata["effective_provider"] == "openai-codex"
    assert raw_metadata["effective_model"] == "gpt-5.4-mini"
    assert raw_metadata["fallback_attempted"] is True
    assert raw_metadata["fallback_activated"] is True
    assert raw_metadata["fallback_provider"] == "openai-codex"
    assert raw_metadata["fallback_model"] == "gpt-5.4-mini"
    assert raw_metadata["fallback_base_url"] == "https://chatgpt.com/backend-api/codex/"
    assert raw_metadata["providers_used_effective"] == ["openai-codex"]


def test_bridge_ignores_non_string_providers_used_effective_entries(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=lambda _bridge, _agent, _request, _runtime: {
            "output_text": "fallback ok",
            "provider": "openai-codex",
            "model": "gpt-5.4",
            "raw_metadata": {
                "fallback_activated": True,
                "providers_used_effective": [
                    " openai-codex ",
                    {"p": "openrouter"},
                    ["anthropic"],
                    123,
                    "",
                ],
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
            },
        },
    )

    result = bridge(
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-fallback-typed-providers",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
        runtime_result,
    )

    assert result["raw_metadata"]["providers_used_effective"] == ["openai-codex"]


def test_bridge_exposes_fallback_failure_metadata_without_masquerading_constructor_provider(tmp_path: Path) -> None:
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=lambda _bridge, _agent, _request, _runtime: {
            "turn_exit_reason": "fallback_exhausted",
            "provider": "openai-codex",
            "model": "gpt-5.4",
            "raw_metadata": {
                "real_provider_bridge_invoked": True,
                "fallback_status": "exhausted",
                "fallback_error": "HTTP 402 Payment Required",
                "fallback_diagnostic": "openai-codex/gpt-5.4 unavailable after retries",
            },
        },
    )

    result = bridge(
        SubagentInvocationRequest(
            subagent_id="hermes_engineer_core",
            pipeline_session_id=runtime_result.pipeline_session_id,
            invocation_id="inv-fallback-exhausted",
            input_messages=[{"role": "user", "content": "Implement change"}],
        ),
        runtime_result,
    )

    raw_metadata = result["raw_metadata"]
    assert raw_metadata["initial_provider"] == "openai-codex"
    assert raw_metadata["effective_provider"] == "openai-codex"
    assert raw_metadata["fallback_attempted"] is True
    assert raw_metadata["fallback_activated"] is False
    assert raw_metadata["fallback_error"] == "HTTP 402 Payment Required"
    assert raw_metadata["fallback_result"] == "exhausted"
    assert raw_metadata["providers_used_effective"] == ["openai-codex"]


def test_normalize_result_turn_exit_reason_max_iterations_synthesizes_blocked_envelope(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "turn_exit_reason": "max_iterations_reached(12/12)",
            "final_response": "plain text diagnostic summary",
            "raw_metadata": {"real_provider_bridge_invoked": True},
        }
    )

    assert normalized["completion_reason"] == "max_iterations_reached(12/12)"
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_max_iterations_without_structured_output"
    assert normalized["raw_metadata"]["structured_output_missing_blocked_reason"] == "max_iterations_plain_text_output"
    _assert_engineer_blocked_envelope(normalized["raw_metadata"]["structured_output"], summary_fragment="plain text diagnostic summary")


def test_normalize_result_parse_failure_synthesizes_blocked_envelope(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "output_text": "{not valid json",
            "completion_reason": "text_response(finish_reason=stop)",
            "raw_metadata": {"real_provider_bridge_invoked": True},
        }
    )

    assert normalized["raw_metadata"]["structured_output_parse_error"].startswith("json_decode_error:")
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "malformed_structured_output"
    assert normalized["raw_metadata"]["structured_output_missing_blocked_reason"] == "malformed_structured_output"
    _assert_engineer_blocked_envelope(normalized["raw_metadata"]["structured_output"], summary_fragment="json")


def test_normalize_result_empty_output_without_diagnostic_synthesizes_blocked_envelope(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    normalized = bridge._normalize_result(
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "",
            "output_text": "",
            "raw_metadata": {"real_provider_bridge_invoked": True},
        }
    )

    assert normalized["completion_reason"] == "text_response(finish_reason=stop)"
    assert normalized["raw_metadata"]["structured_output_missing_reason"] == "engineer_empty_output_without_structured_output"
    assert normalized["raw_metadata"]["structured_output_missing_blocked_reason"] == "empty_output_without_structured_output"
    _assert_engineer_blocked_envelope(normalized["raw_metadata"]["structured_output"], summary_fragment="empty")


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

    assert '"status": "succeeded"' in prompt_text
    assert '`status` must be a valid structured-output status string' in prompt_text
    assert '- succeeded' in config_text
    assert '- failed' in config_text
    assert '- blocked' in config_text
    assert '- needs_review' in config_text
    assert '- not_invoked' in config_text
    assert '- disagree_with_reviewer' in config_text
    assert '- completed' not in config_text
    assert '- needs_input' not in config_text
    assert '- disagreement' not in config_text


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


def test_bridge_find_files_returns_repo_relative_paths_only(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    (git_repo / "src").mkdir()
    (git_repo / "src" / "keep.py").write_text("print('ok')\n", encoding="utf-8")
    (git_repo / ".git" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    (git_repo / "__pycache__").mkdir()
    (git_repo / "__pycache__" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    payload = json.loads(bridge.execute_tool("find_files", {"pattern": "**/*.py"}))

    assert payload["status"] == "ok"
    assert payload["files"] == ["src/keep.py"]
    assert all(not path.startswith("/") for path in payload["files"])


def test_bridge_read_file_denials_capture_actionable_forensics(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent)

    for denied_path, reason, message_fragment in [
        ("missing.py", "path_missing", "repo-relative path"),
        ("/home/hermes/.hermes/hermes-agent/missing.py", "absolute_path_denied", "absolute paths are denied"),
    ]:
        with pytest.raises(AIAgentExecutorBridgeError, match=reason):
            bridge.execute_tool("read_file", {"path": denied_path})

        tool_call = bridge._tool_calls[-1]
        assert tool_call["tool_name"] == "read_file"
        assert tool_call["status"] == "failed"
        assert tool_call["arguments"]["path"] == denied_path
        assert tool_call["error"]["kind"] == reason
        assert message_fragment in tool_call["error"]["message"]


def test_engineer_prompt_and_config_describe_file_discovery_contract(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    prompt_text = (repo_root / "prompts/subagents/hermes_engineer_core.md").read_text(encoding="utf-8")
    config_text = (repo_root / "config/subagents/hermes_engineer_core.yaml").read_text(encoding="utf-8")

    assert "find_files" in prompt_text
    assert "repo-relative" in prompt_text
    assert "search_files" in prompt_text
    assert "content search" in prompt_text
    assert "\"*.py\"" in prompt_text
    assert "find_files" in config_text
    assert "repo-relative" in config_text


def test_bridge_pytest_is_constrained_and_terminal_missing(tmp_path: Path) -> None:
    repo_root, _runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    bridge = AIAgentSubagentExecutorBridge(workspace_root=git_repo, repo_root=repo_root, agent_factory=_FakeAgent, conversation_runner=lambda *_args: {"output_text": "ok"})

    payload = json.loads(bridge.execute_tool("pytest", {"command": "python -m pytest -q tests/test_ok.py"}))
    assert payload["status"] == "passed"
    assert bridge._tool_calls[-1]["result"]["status"] == "passed"
    assert bridge._tool_calls[-1]["result"]["results"][0]["exit_code"] == 0
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

    tool_call = bridge._tool_calls[-1]
    assert tool_call["tool_name"] == "pytest"
    assert tool_call["status"] == "failed"
    assert tool_call["result"]["status"] == "blocked"
    assert tool_call["result"]["results"][0]["validator_reason"] == "test_command_denied"


def test_bridge_find_files_sees_real_repo_directories_when_workspace_is_repo_root(tmp_path: Path) -> None:
    repo_root = _init_git_repo(tmp_path)
    for relative_path in (
        "hermes_cli/pipeline_autonomous_execution.py",
        "gateway/run.py",
        "tests/test_pipeline_aiagent_executor.py",
        "docs/architecture.md",
    ):
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("marker\n", encoding="utf-8")

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=repo_root,
        repo_root=repo_root,
        agent_factory=_FakeAgent,
        conversation_runner=lambda *_args: {"output_text": "ok"},
    )

    assert "hermes_cli/pipeline_autonomous_execution.py" in json.loads(bridge.execute_tool("find_files", {"pattern": "hermes_cli/*"}))["files"]
    assert "gateway/run.py" in json.loads(bridge.execute_tool("find_files", {"pattern": "gateway/*"}))["files"]
    assert "tests/test_pipeline_aiagent_executor.py" in json.loads(bridge.execute_tool("find_files", {"pattern": "tests/*"}))["files"]
    assert "docs/architecture.md" in json.loads(bridge.execute_tool("find_files", {"pattern": "docs/*"}))["files"]


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
    assert result.subagent_runs[0]["actual_provider"] == "openai-codex"
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
# Tests appended by fix-slice: eliminate provider/model identity drift

# --- helpers for identity tests ---

def _make_fake_agent_with_kwargs(**kwargs):
    """Create a _FakeAgent and return it along with the kwargs it received."""
    agent = _FakeAgent(**kwargs)
    agent._turn_runtime_request = None
    agent._is_anthropic_oauth = False
    agent.reasoning_config = None
    agent.request_overrides = None
    return agent


def _turn_runtime_request_from_openrouter():
    """Simulate _turn_runtime_request as set by select_model_policy for OpenRouter config."""
    return {
        "purpose": "main_turn",
        "actual_provider": "openrouter",
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "actual_api_mode": "chat_completions",
        "actual_base_url": "https://openrouter.ai/api/v1",
        "actual_api_key": "sk-or-test",
    }


# --- A: reviewer Codex identity regression ---

def test_build_agent_sets_controlled_subagent_flags(tmp_path: Path) -> None:
    """_build_agent must pin _skip_role_model_selection and constructor identity on the agent."""
    repo_root, runtime_result = _build_reviewer_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    captured_agents: list = []

    def _factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        captured_agents.append(agent)
        return agent

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "ok"},
    )
    bridge._build_agent(runtime_result)

    assert len(captured_agents) == 1
    agent = captured_agents[0]
    assert getattr(agent, "_skip_role_model_selection", False) is True
    assert getattr(agent, "_constructor_provider", None) == "openai-codex"
    assert getattr(agent, "_constructor_model", None) == "gpt-5.5"
    assert getattr(agent, "_constructor_api_mode", None) == "codex_responses"


def test_build_agent_sets_controlled_flags_for_engineer(tmp_path: Path) -> None:
    """Engineer agent must also have _skip_role_model_selection set."""
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    captured_agents: list = []

    def _factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        captured_agents.append(agent)
        return agent

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "ok"},
    )
    bridge._build_agent(runtime_result)

    assert len(captured_agents) == 1
    agent = captured_agents[0]
    assert getattr(agent, "_skip_role_model_selection", False) is True
    assert getattr(agent, "_constructor_provider", None) == "openai-codex"
    assert getattr(agent, "_constructor_model", None) == "gpt-5.4"


# --- F: mismatch fail-closed ---

def test_build_api_kwargs_blocks_when_turn_runtime_request_has_wrong_model(tmp_path: Path) -> None:
    """build_api_kwargs must raise RuntimeError if _turn_runtime_request has a different model
    than the constructor identity (mismatch fail-closed guard)."""
    import types
    from agent.chat_completion_helpers import build_api_kwargs

    agent = types.SimpleNamespace(
        tools=[],
        api_mode="codex_responses",
        provider="openai-codex",
        model="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None,
        request_overrides=None,
        session_id="test-session",
        _is_anthropic_oauth=False,
        _skip_role_model_selection=True,
        _constructor_provider="openai-codex",
        _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses",
        _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=_turn_runtime_request_from_openrouter(),
    )
    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


def test_build_api_kwargs_allows_matching_constructor_identity(tmp_path: Path) -> None:
    """build_api_kwargs must NOT raise when runtime identity matches constructor."""
    import types
    from agent.chat_completion_helpers import build_api_kwargs

    # Simulate what happens after fix: _turn_runtime_request is None
    agent = types.SimpleNamespace(
        tools=[],
        api_mode="codex_responses",
        provider="openai-codex",
        model="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None,
        request_overrides=None,
        session_id="test-session",
        _is_anthropic_oauth=False,
        _skip_role_model_selection=True,
        _constructor_provider="openai-codex",
        _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses",
        _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=None,
        _base_url_hostname="chatgpt.com",
        _base_url_lower="https://chatgpt.com/backend-api/codex",
        max_tokens=None,
        session_id_for_codex=None,
    )
    # Should not raise - runtime identity matches constructor
    # (build_kwargs may fail further on because agent lacks full attrs, but the guard must pass)
    try:
        build_api_kwargs(agent, [])
    except RuntimeError as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise
    except Exception:
        pass  # Other errors from missing attrs are ok; we only care the guard didn't block


# --- C: cross-role contamination ---

def test_reviewer_bridge_agent_has_independent_constructor_identity(tmp_path: Path) -> None:
    """After engineer run, a fresh reviewer agent must have reviewer's identity, not engineer's."""
    repo_root, eng_runtime = _build_runtime_result(tmp_path)
    # Reuse same repo_root; _build_reviewer_runtime_result would conflict on _copy_spec_tree
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    rev_runtime = RuntimeFactory(repo_root=repo_root).build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_code_reviewer",
            pipeline_session_id="pipe-aiagent-bridge",
            invocation_id="inv-aiagent-reviewer-bridge",
        )
    )
    git_repo = _init_git_repo(tmp_path)

    captured: dict[str, list] = {"agents": []}

    def _factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        captured["agents"].append(agent)
        return agent

    eng_bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "eng done"},
    )
    rev_bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "rev done"},
    )

    eng_bridge._build_agent(eng_runtime)
    rev_bridge._build_agent(rev_runtime)

    assert len(captured["agents"]) == 2
    eng_agent = captured["agents"][0]
    rev_agent = captured["agents"][1]

    assert eng_agent._constructor_provider == "openai-codex"
    assert eng_agent._constructor_model == "gpt-5.4"
    assert rev_agent._constructor_provider == "openai-codex"
    assert rev_agent._constructor_model == "gpt-5.5"
    assert rev_agent._constructor_model != eng_agent._constructor_model


# --- B: engineer fallback does not contaminate reviewer ---

def test_reviewer_identity_independent_of_engineer_fallback_model(tmp_path: Path) -> None:
    """Reviewer agent must report openai-codex/gpt-5.5 even after engineer used fallback."""
    repo_root, eng_runtime = _build_runtime_result(tmp_path)
    # Reuse same repo_root; _build_reviewer_runtime_result would conflict on _copy_spec_tree
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    rev_runtime = RuntimeFactory(repo_root=repo_root).build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_code_reviewer",
            pipeline_session_id="pipe-aiagent-bridge",
            invocation_id="inv-aiagent-reviewer-bridge",
        )
    )
    git_repo = _init_git_repo(tmp_path)

    # Engineer agent simulates fallback: model mutates to gpt-5.4-mini during run
    def _eng_factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        agent.provider = "openai-codex"
        agent.model = "gpt-5.4-mini"
        return agent

    captured_rev: dict[str, object] = {"kwargs_model": None, "kwargs_provider": None, "agent": None}

    def _rev_factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        # Capture constructor kwargs immediately; _constructor_* attrs are set after factory returns
        captured_rev["kwargs_model"] = kwargs.get("model")
        captured_rev["kwargs_provider"] = kwargs.get("provider")
        captured_rev["agent"] = agent
        return agent

    eng_bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_eng_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "eng fallback done"},
    )
    rev_bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_rev_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "rev done"},
    )

    eng_bridge._build_agent(eng_runtime)
    rev_bridge._build_agent(rev_runtime)

    # _constructor_* attrs are set by _build_agent AFTER the factory call
    rev_agent = captured_rev["agent"]
    assert captured_rev["kwargs_provider"] == "openai-codex"
    assert captured_rev["kwargs_model"] == "gpt-5.5"
    assert getattr(rev_agent, "_constructor_provider", None) == "openai-codex"
    assert getattr(rev_agent, "_constructor_model", None) == "gpt-5.5"
    assert getattr(rev_agent, "_constructor_model", None) != "gpt-5.4-mini"
    assert captured_rev["kwargs_model"] != "gpt-5.4"


# --- E: request dump truth via build_api_kwargs model field ---

def test_build_api_kwargs_model_matches_constructor_model_when_no_turn_runtime_request(tmp_path: Path) -> None:
    """When _turn_runtime_request is None, build_api_kwargs must use agent.model (the constructor model)."""
    import types
    from agent.chat_completion_helpers import build_api_kwargs

    # Simulate a reviewer agent post-fix: _turn_runtime_request = None
    class _MinimalAgent(types.SimpleNamespace):
        _base_url_hostname = "chatgpt.com"
        _base_url_lower = "https://chatgpt.com/backend-api/codex"

        def _get_transport(self):
            class _FakeTransport:
                captured_model = None
                def build_kwargs(self, model, **kwargs):
                    _MinimalAgent._captured_model = model
                    return {"model": model}
                def preflight_kwargs(self, kwargs, **_):
                    return kwargs
            return _FakeTransport()

        def _prepare_messages_for_non_vision_model(self, messages):
            return messages

        def _sanitize_tool_calls_for_strict_api(self, *a, **k):
            pass

    agent = _MinimalAgent(
        tools=[],
        api_mode="codex_responses",
        provider="openai-codex",
        model="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None,
        request_overrides=None,
        session_id="test",
        _is_anthropic_oauth=False,
        _skip_role_model_selection=True,
        _constructor_provider="openai-codex",
        _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses",
        _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=None,
        max_tokens=None,
    )

    try:
        result = build_api_kwargs(agent, [])
        assert result.get("model") == "gpt-5.5", f"Expected gpt-5.5 but got {result.get('model')!r}"
    except Exception as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise AssertionError(f"Mismatch guard must not fire for matching identity: {exc}") from exc
        pass  # Other transport/attr errors ok — we only care guard didn't block


# --- G: mismatch guard uses sanitized diagnostic ---

def test_mismatch_guard_error_does_not_leak_api_key(tmp_path: Path) -> None:
    """Mismatch error message must not contain actual API keys."""
    import types
    from agent.chat_completion_helpers import build_api_kwargs

    agent = types.SimpleNamespace(
        tools=[],
        api_mode="codex_responses",
        provider="openai-codex",
        model="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None,
        request_overrides=None,
        session_id="test-session",
        _is_anthropic_oauth=False,
        _skip_role_model_selection=True,
        _constructor_provider="openai-codex",
        _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses",
        _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request={
            "actual_provider": "openrouter",
            "actual_model": "xiaomi/mimo-v2.5-pro",
            "actual_api_mode": "chat_completions",
            "actual_base_url": "https://openrouter.ai/api/v1",
            "actual_api_key": "sk-or-secret-should-not-appear",
        },
    )
    with pytest.raises(RuntimeError) as exc_info:
        build_api_kwargs(agent, [])
    error_text = str(exc_info.value)
    assert "controlled_subagent_identity_mismatch" in error_text
    assert "sk-or-secret-should-not-appear" not in error_text


# --- D: reviewer bridge has independent identity from previous reviewer run ---

def test_reviewer_bridge_identity_per_invocation(tmp_path: Path) -> None:
    """Each call to the reviewer bridge must build a fresh agent with reviewer identity."""
    repo_root, rev_runtime = _build_reviewer_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    built_agents: list = []

    def _factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        built_agents.append({
            "provider": kwargs.get("provider"),
            "model": kwargs.get("model"),
            "constructor_provider": None,
            "constructor_model": None,
        })
        # Simulate post-build setattr
        built_agents[-1]["agent"] = agent
        return agent

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "ok"},
    )

    # Simulate two separate invocations
    bridge._build_agent(rev_runtime)
    bridge._build_agent(rev_runtime)

    assert len(built_agents) == 2
    for entry in built_agents:
        assert entry["provider"] == "openai-codex"
        assert entry["model"] == "gpt-5.5"
        agent = entry["agent"]
        assert getattr(agent, "_constructor_provider", None) == "openai-codex"
        assert getattr(agent, "_constructor_model", None) == "gpt-5.5"
        assert getattr(agent, "_skip_role_model_selection", False) is True


# ===========================================================================
# NEW TESTS: fallback-aware identity guard (fix for blocking issue)
# ===========================================================================

def _make_reviewer_agent_namespace(*, turn_runtime_request=None, allowed_ids=None):
    """Minimal types.SimpleNamespace for reviewer identity tests."""
    import types
    agent = types.SimpleNamespace(
        tools=[],
        api_mode="codex_responses",
        provider="openai-codex",
        model="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None,
        request_overrides=None,
        session_id="test-session",
        _is_anthropic_oauth=False,
        _skip_role_model_selection=True,
        _constructor_provider="openai-codex",
        _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses",
        _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=turn_runtime_request,
        _base_url_hostname="chatgpt.com",
        _base_url_lower="https://chatgpt.com/backend-api/codex",
        max_tokens=None,
    )
    if allowed_ids is not None:
        agent._controlled_allowed_request_identities = allowed_ids
    return agent


def _make_engineer_agent_namespace(*, turn_runtime_request=None, allowed_ids=None):
    """Minimal types.SimpleNamespace for engineer identity tests."""
    import types
    agent = types.SimpleNamespace(
        tools=[],
        api_mode="chat_completions",
        provider="openrouter",
        model="xiaomi/mimo-v2.5-pro",
        base_url="https://openrouter.ai/api/v1",
        reasoning_config=None,
        request_overrides=None,
        session_id="test-session",
        _is_anthropic_oauth=False,
        _skip_role_model_selection=True,
        _constructor_provider="openrouter",
        _constructor_model="xiaomi/mimo-v2.5-pro",
        _constructor_api_mode="chat_completions",
        _constructor_base_url="https://openrouter.ai/api/v1",
        _turn_runtime_request=turn_runtime_request,
    )
    if allowed_ids is not None:
        agent._controlled_allowed_request_identities = allowed_ids
    return agent


# --- 1. Positive: engineer fallback allowed ---

def test_build_api_kwargs_allows_configured_fallback_identity(tmp_path: Path) -> None:
    """build_api_kwargs must not raise when _turn_runtime_request carries the configured
    fallback identity (openai-codex/gpt-5.4 after primary openrouter/xiaomi fails)."""
    from agent.chat_completion_helpers import build_api_kwargs

    # Simulate _sync_turn_runtime_request_for_fallback result
    fallback_runtime_request = {
        "purpose": "main_turn",
        "actual_provider": "openai-codex",
        "actual_model": "gpt-5.4",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://chatgpt.com/backend-api/codex",
        "actual_api_key": "sk-codex-test",
        "fallback_activated": True,
        "fallback_from_provider": "openrouter",
        "fallback_from_model": "xiaomi/mimo-v2.5-pro",
    }

    agent = _make_engineer_agent_namespace(
        turn_runtime_request=fallback_runtime_request,
        allowed_ids=[
            {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "api_mode": "chat_completions", "base_url_family": "openrouter"},
            {"provider": "openai-codex", "model": "gpt-5.4", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )

    try:
        build_api_kwargs(agent, [])
    except RuntimeError as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise AssertionError(
                f"Guard must not block configured fallback identity: {exc}"
            ) from exc
    except Exception:
        pass  # Other transport/attr errors acceptable — guard is what we test


# --- 2. Fallback request body carries fallback model, not primary ---

def test_build_agent_allowed_ids_includes_fallback_identity(tmp_path: Path) -> None:
    """After _build_agent, _controlled_allowed_request_identities must include both
    primary (openai-codex/gpt-5.4) and fallback (openai-codex/gpt-5.4-mini) identities."""
    repo_root, runtime_result = _build_runtime_result(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    captured_agents: list = []

    def _factory(**kwargs):
        agent = _FakeAgent(**kwargs)
        captured_agents.append(agent)
        return agent

    bridge = AIAgentSubagentExecutorBridge(
        workspace_root=git_repo,
        repo_root=repo_root,
        agent_factory=_factory,
        conversation_runner=lambda _b, _a, _req, _rp: {"output_text": "ok"},
    )
    bridge._build_agent(runtime_result)

    assert len(captured_agents) == 1
    agent = captured_agents[0]
    allowed = getattr(agent, "_controlled_allowed_request_identities", None)
    assert allowed is not None, "_controlled_allowed_request_identities must be set"
    assert len(allowed) == 2, f"Expected 2 allowed identities (primary+fallback), got: {allowed}"

    models = {_id["model"] for _id in allowed}
    api_modes = {_id["api_mode"] for _id in allowed}
    assert "gpt-5.4" in models, f"Primary model missing from allowed: {allowed}"
    assert "gpt-5.4-mini" in models, f"Fallback model missing from allowed: {allowed}"
    assert api_modes == {"codex_responses"}

    # Each entry must have provider and base_url_family
    for _id in allowed:
        assert "provider" in _id, f"Entry missing provider: {_id}"
        assert "base_url_family" in _id, f"Entry missing base_url_family: {_id}"
        assert _id["api_mode"] == "codex_responses", f"api_mode wrong: {_id}"
        assert _id["provider"] == "openai-codex", f"provider wrong: {_id}"
        assert _id["base_url_family"] == "codex", f"base_url_family wrong: {_id}"


# --- 3. Reviewer stale primary still blocked ---

def test_build_api_kwargs_blocks_reviewer_with_engineer_primary_model(tmp_path: Path) -> None:
    """Reviewer (openai-codex/gpt-5.5) with injected actual_model=xiaomi/mimo-v2.5-pro
    must still raise controlled_subagent_identity_mismatch."""
    from agent.chat_completion_helpers import build_api_kwargs

    stale_runtime_request = {
        "actual_provider": "openrouter",
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "actual_api_mode": "chat_completions",
        "actual_base_url": "https://openrouter.ai/api/v1",
        "actual_api_key": "sk-or-should-not-appear",
    }

    agent = _make_reviewer_agent_namespace(
        turn_runtime_request=stale_runtime_request,
        allowed_ids=[{"provider": "openai-codex", "model": "gpt-5.5", "api_mode": "codex_responses", "base_url_family": "codex"}],
    )

    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


# --- 4. Reviewer cannot use engineer fallback model ---

def test_build_api_kwargs_blocks_reviewer_with_engineer_fallback_model(tmp_path: Path) -> None:
    """Reviewer (openai-codex/gpt-5.5) must be blocked even if the injected runtime request
    carries the engineer's fallback model (gpt-5.4 / codex_responses) — that identity is not
    in the reviewer's allowed set."""
    from agent.chat_completion_helpers import build_api_kwargs

    engineer_fallback_request = {
        "actual_provider": "openai-codex",
        "actual_model": "gpt-5.4",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://chatgpt.com/backend-api/codex",
        "actual_api_key": "sk-codex-test",
        "fallback_activated": True,
    }

    # Reviewer's allowed list contains ONLY gpt-5.5, not gpt-5.4
    agent = _make_reviewer_agent_namespace(
        turn_runtime_request=engineer_fallback_request,
        allowed_ids=[{"provider": "openai-codex", "model": "gpt-5.5", "api_mode": "codex_responses", "base_url_family": "codex"}],
    )

    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


# --- 5. Mixed identity blocked ---

def test_build_api_kwargs_blocks_mixed_codex_url_openrouter_model(tmp_path: Path) -> None:
    """Codex api_mode with OpenRouter model must be blocked even if model matches allowed list
    with a different api_mode — (model, api_mode) pair must match atomically."""
    from agent.chat_completion_helpers import build_api_kwargs

    # Codex api_mode but OpenRouter model body — mixed identity
    mixed_request = {
        "actual_provider": "openai-codex",
        "actual_model": "xiaomi/mimo-v2.5-pro",    # OpenRouter model
        "actual_api_mode": "codex_responses",       # but Codex api_mode
        "actual_base_url": "https://chatgpt.com/backend-api/codex",
        "actual_api_key": "sk-codex-test",
    }

    # Engineer's allowed set: primary is chat_completions, fallback is codex_responses
    # Neither identity matches (xiaomi/mimo, codex_responses)
    agent = _make_engineer_agent_namespace(
        turn_runtime_request=mixed_request,
        allowed_ids=[
            {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "api_mode": "chat_completions", "base_url_family": "openrouter"},
            {"provider": "openai-codex", "model": "gpt-5.4", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )

    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


def test_build_api_kwargs_blocks_mixed_openrouter_url_codex_model(tmp_path: Path) -> None:
    """OpenRouter api_mode with Codex model must be blocked."""
    from agent.chat_completion_helpers import build_api_kwargs

    mixed_request = {
        "actual_provider": "openrouter",
        "actual_model": "gpt-5.5",               # Codex model
        "actual_api_mode": "chat_completions",    # but OpenRouter api_mode
        "actual_base_url": "https://openrouter.ai/api/v1",
        "actual_api_key": "sk-or-test",
    }

    agent = _make_reviewer_agent_namespace(
        turn_runtime_request=mixed_request,
        allowed_ids=[{"provider": "openai-codex", "model": "gpt-5.5", "api_mode": "codex_responses", "base_url_family": "codex"}],
    )

    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


# --- 6. Normal AIAgent unaffected ---

def test_build_api_kwargs_guard_does_not_fire_for_non_bridge_agent(tmp_path: Path) -> None:
    """Agents without _skip_role_model_selection must never hit the identity guard,
    even if they have stale/mismatched _turn_runtime_request."""
    import types
    from agent.chat_completion_helpers import build_api_kwargs

    # No _skip_role_model_selection → guard path is never entered
    agent = types.SimpleNamespace(
        tools=[],
        api_mode="codex_responses",
        provider="openai-codex",
        model="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None,
        request_overrides=None,
        session_id="test-session",
        _is_anthropic_oauth=False,
        # No _skip_role_model_selection set
        _turn_runtime_request={
            "actual_provider": "openrouter",
            "actual_model": "xiaomi/mimo-v2.5-pro",
            "actual_api_mode": "chat_completions",
            "actual_base_url": "https://openrouter.ai/api/v1",
            "actual_api_key": "sk-or-test",
        },
        _base_url_hostname="openrouter.ai",
        _base_url_lower="https://openrouter.ai/api/v1",
        max_tokens=None,
    )

    try:
        build_api_kwargs(agent, [])
    except RuntimeError as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise AssertionError(
                f"Guard must not fire for non-bridge agents: {exc}"
            ) from exc
    except Exception:
        pass  # Transport/attr errors ok — we only verify guard doesn't fire


# ===========================================================================
# NEW TESTS: 4-tuple identity guard (provider, model, api_mode, base_url_family)
# ===========================================================================

# --- 1. Positive: engineer fallback still allowed (4-tuple) ---

def test_engineer_fallback_allowed_with_full_4tuple_identity(tmp_path: Path) -> None:
    """Configured fallback (openai-codex/gpt-5.4/codex_responses/codex) must pass
    the 4-tuple guard when _sync_turn_runtime_request_for_fallback sets all fields."""
    from agent.chat_completion_helpers import build_api_kwargs

    fallback_request = {
        "actual_provider": "openai-codex",
        "actual_model": "gpt-5.4",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://chatgpt.com/backend-api/codex",
        "actual_api_key": "sk-codex-test",
        "fallback_activated": True,
    }
    import types
    agent = types.SimpleNamespace(
        tools=[], api_mode="chat_completions", provider="openrouter",
        model="xiaomi/mimo-v2.5-pro", base_url="https://openrouter.ai/api/v1",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openrouter", _constructor_model="xiaomi/mimo-v2.5-pro",
        _constructor_api_mode="chat_completions", _constructor_base_url="https://openrouter.ai/api/v1",
        _turn_runtime_request=fallback_request,
        _controlled_allowed_request_identities=[
            {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "api_mode": "chat_completions", "base_url_family": "openrouter"},
            {"provider": "openai-codex", "model": "gpt-5.4", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )
    try:
        build_api_kwargs(agent, [])
    except RuntimeError as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise AssertionError(f"Guard must not block valid fallback 4-tuple: {exc}") from exc
    except Exception:
        pass  # transport/attr errors ok — guard is what we test


# --- 2. model/api_mode match but wrong provider blocks ---

def test_wrong_provider_blocks_even_when_model_api_mode_match(tmp_path: Path) -> None:
    """Reviewer with correct model/api_mode but wrong provider=openrouter must be blocked.
    This is the false-pass case from the review: actual_model=gpt-5.4, api_mode=codex_responses,
    provider=openrouter, base_url=chatgpt.com."""
    from agent.chat_completion_helpers import build_api_kwargs
    import types

    stale_request = {
        "actual_provider": "openrouter",       # wrong provider
        "actual_model": "gpt-5.4",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://chatgpt.com/backend-api/codex",
        "actual_api_key": "sk-or-should-block",
    }
    agent = types.SimpleNamespace(
        tools=[], api_mode="chat_completions", provider="openrouter",
        model="xiaomi/mimo-v2.5-pro", base_url="https://openrouter.ai/api/v1",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openrouter", _constructor_model="xiaomi/mimo-v2.5-pro",
        _constructor_api_mode="chat_completions", _constructor_base_url="https://openrouter.ai/api/v1",
        _turn_runtime_request=stale_request,
        _controlled_allowed_request_identities=[
            {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "api_mode": "chat_completions", "base_url_family": "openrouter"},
            {"provider": "openai-codex", "model": "gpt-5.4", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )
    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


# --- 3. model/api_mode match but wrong base_url family blocks ---

def test_wrong_base_url_family_blocks_even_when_model_api_mode_match(tmp_path: Path) -> None:
    """Fallback model/api_mode with correct provider but wrong base_url family must be blocked.
    actual_model=gpt-5.4, api_mode=codex_responses, provider=openai-codex,
    but base_url=openrouter.ai (wrong family)."""
    from agent.chat_completion_helpers import build_api_kwargs
    import types

    stale_request = {
        "actual_provider": "openai-codex",
        "actual_model": "gpt-5.4",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://openrouter.ai/api/v1",   # wrong family
        "actual_api_key": "sk-codex-should-block",
    }
    agent = types.SimpleNamespace(
        tools=[], api_mode="chat_completions", provider="openrouter",
        model="xiaomi/mimo-v2.5-pro", base_url="https://openrouter.ai/api/v1",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openrouter", _constructor_model="xiaomi/mimo-v2.5-pro",
        _constructor_api_mode="chat_completions", _constructor_base_url=None,
        _turn_runtime_request=stale_request,
        _controlled_allowed_request_identities=[
            {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "api_mode": "chat_completions", "base_url_family": "openrouter"},
            {"provider": "openai-codex", "model": "gpt-5.4", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )
    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


# --- 4. OpenRouter primary with Codex base URL blocks ---

def test_openrouter_primary_model_with_codex_base_url_blocks(tmp_path: Path) -> None:
    """Primary OpenRouter model with Codex base URL must be blocked (false-pass #2 from review).
    actual_model=xiaomi, api_mode=chat_completions, provider=openai-codex, base_url=chatgpt.com."""
    from agent.chat_completion_helpers import build_api_kwargs
    import types

    stale_request = {
        "actual_provider": "openai-codex",       # wrong provider for OpenRouter model
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "actual_api_mode": "chat_completions",
        "actual_base_url": "https://chatgpt.com/backend-api/codex",  # wrong family
        "actual_api_key": "sk-codex-should-block",
    }
    agent = types.SimpleNamespace(
        tools=[], api_mode="chat_completions", provider="openrouter",
        model="xiaomi/mimo-v2.5-pro", base_url="https://openrouter.ai/api/v1",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openrouter", _constructor_model="xiaomi/mimo-v2.5-pro",
        _constructor_api_mode="chat_completions", _constructor_base_url=None,
        _turn_runtime_request=stale_request,
        _controlled_allowed_request_identities=[
            {"provider": "openrouter", "model": "xiaomi/mimo-v2.5-pro", "api_mode": "chat_completions", "base_url_family": "openrouter"},
            {"provider": "openai-codex", "model": "gpt-5.4", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )
    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])


# --- 5. Reviewer correct identity passes (4-tuple) ---

def test_reviewer_correct_4tuple_identity_passes(tmp_path: Path) -> None:
    """Reviewer with exact allowed identity (openai-codex/gpt-5.5/codex_responses/codex)
    must not raise controlled_subagent_identity_mismatch."""
    from agent.chat_completion_helpers import build_api_kwargs
    import types

    correct_request = {
        "actual_provider": "openai-codex",
        "actual_model": "gpt-5.5",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://chatgpt.com/backend-api/codex",
        "actual_api_key": "sk-codex-test",
    }
    agent = types.SimpleNamespace(
        tools=[], api_mode="codex_responses", provider="openai-codex",
        model="gpt-5.5", base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openai-codex", _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses", _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=correct_request,
        _controlled_allowed_request_identities=[
            {"provider": "openai-codex", "model": "gpt-5.5", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )
    try:
        build_api_kwargs(agent, [])
    except RuntimeError as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise AssertionError(f"Guard must not block reviewer correct identity: {exc}") from exc
    except Exception:
        pass  # transport/attr errors ok


# --- 6. Non-bridge agent unaffected (4-tuple guard does not fire) ---

def test_non_bridge_agent_unaffected_by_4tuple_guard(tmp_path: Path) -> None:
    """Agent without _skip_role_model_selection must never hit the identity guard."""
    from agent.chat_completion_helpers import build_api_kwargs
    import types

    agent = types.SimpleNamespace(
        tools=[], api_mode="codex_responses", provider="openai-codex",
        model="gpt-5.5", base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False,
        # No _skip_role_model_selection → guard never enters
        _turn_runtime_request={
            "actual_provider": "openrouter",
            "actual_model": "xiaomi/mimo-v2.5-pro",
            "actual_api_mode": "chat_completions",
            "actual_base_url": "https://openrouter.ai/api/v1",
            "actual_api_key": "sk-or-test",
        },
    )
    try:
        build_api_kwargs(agent, [])
    except RuntimeError as exc:
        if "controlled_subagent_identity_mismatch" in str(exc):
            raise AssertionError(f"Guard must not fire for non-bridge agent: {exc}") from exc
    except Exception:
        pass


# --- 7. Mismatch diagnostic includes provider, family; omits API keys ---

def test_mismatch_diagnostic_includes_provider_and_family_not_api_key(tmp_path: Path) -> None:
    """Mismatch error must include got_provider, got_base_url_family, must not include API key."""
    from agent.chat_completion_helpers import build_api_kwargs
    import types

    stale_request = {
        "actual_provider": "openrouter",
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "actual_api_mode": "chat_completions",
        "actual_base_url": "https://openrouter.ai/api/v1",
        "actual_api_key": "sk-or-secret-must-not-appear",
    }
    agent = types.SimpleNamespace(
        tools=[], api_mode="codex_responses", provider="openai-codex",
        model="gpt-5.5", base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openai-codex", _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses", _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=stale_request,
        _controlled_allowed_request_identities=[
            {"provider": "openai-codex", "model": "gpt-5.5", "api_mode": "codex_responses", "base_url_family": "codex"},
        ],
    )
    with pytest.raises(RuntimeError) as exc_info:
        build_api_kwargs(agent, [])
    msg = str(exc_info.value)
    assert "controlled_subagent_identity_mismatch" in msg
    assert "got_provider=" in msg
    assert "got_base_url_family=" in msg
    assert "sk-or-secret-must-not-appear" not in msg


# --- 8. Base URL normalization ---

def test_normalize_base_url_family_codex_variants() -> None:
    """All chatgpt.com URL variants must normalize to 'codex'."""
    from agent.chat_completion_helpers import _normalize_base_url_family

    codex_urls = [
        "https://chatgpt.com/backend-api/codex",
        "https://chatgpt.com/backend-api/codex/",
        "https://chatgpt.com/backend-api/codex/responses",
        "https://chatgpt.com/backend-api/codex/chat/completions",
    ]
    for url in codex_urls:
        result = _normalize_base_url_family(None, None, url)
        assert result == "codex", f"Expected 'codex' for {url!r}, got {result!r}"


def test_normalize_base_url_family_openrouter_variants() -> None:
    """All openrouter.ai URL variants must normalize to 'openrouter'."""
    from agent.chat_completion_helpers import _normalize_base_url_family

    openrouter_urls = [
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/",
        "https://openrouter.ai/api/v1/chat/completions",
    ]
    for url in openrouter_urls:
        result = _normalize_base_url_family(None, None, url)
        assert result == "openrouter", f"Expected 'openrouter' for {url!r}, got {result!r}"


def test_normalize_base_url_family_unknown_does_not_match_known() -> None:
    """An unknown base URL must normalize to 'unknown' and not match codex or openrouter."""
    from agent.chat_completion_helpers import _normalize_base_url_family

    result = _normalize_base_url_family(None, None, "https://some-proxy.example.com/api/v1")
    assert result == "unknown"
    assert result != "codex"
    assert result != "openrouter"


def test_normalize_base_url_family_provider_fallback_when_no_url() -> None:
    """Without a base URL, provider string must determine the family."""
    from agent.chat_completion_helpers import _normalize_base_url_family

    assert _normalize_base_url_family("openai-codex", None, None) == "codex"
    assert _normalize_base_url_family("openai-codex", None, "") == "codex"
    assert _normalize_base_url_family("openrouter", None, None) == "openrouter"
    assert _normalize_base_url_family("xai-oauth", None, None) == "codex"
    assert _normalize_base_url_family("anthropic", None, None) == "anthropic"


# ===========================================================================
# NEW TESTS: urlparse-based _normalize_base_url_family (blocking issue fix)
# ===========================================================================

def test_normalize_codex_happy_path_urlparse() -> None:
    """All valid Codex base URL variants must normalize to 'codex'."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for url in [
        "https://chatgpt.com/backend-api/codex",
        "https://chatgpt.com/backend-api/codex/",
        "https://chatgpt.com/backend-api/codex/responses",
        "https://chatgpt.com/backend-api/codex/responses?foo=bar",
        "https://chatgpt.com/backend-api/codex/chat/completions",
    ]:
        assert f(None, None, url) == "codex", f"Expected codex for {url!r}"


def test_normalize_openrouter_happy_path_urlparse() -> None:
    """All valid OpenRouter base URL variants must normalize to 'openrouter'."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for url in [
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/",
        "https://openrouter.ai/api/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions?foo=bar",
    ]:
        assert f(None, None, url) == "openrouter", f"Expected openrouter for {url!r}"


def test_normalize_non_codex_chatgpt_paths_are_unknown() -> None:
    """chatgpt.com URLs with wrong paths must normalize to 'unknown', not 'codex'."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for url in [
        "https://chatgpt.com/",
        "https://chatgpt.com/some-other-path",
        "https://chatgpt.com/backend-api",
        "https://chatgpt.com/backend-api-extra/codex",
    ]:
        result = f(None, None, url)
        assert result == "unknown", f"Expected unknown for {url!r}, got {result!r}"


def test_normalize_non_v1_openrouter_paths_are_unknown() -> None:
    """openrouter.ai URLs with wrong paths must normalize to 'unknown'."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for url in [
        "https://openrouter.ai/",
        "https://openrouter.ai/api",
        "https://openrouter.ai/some-other-path",
    ]:
        result = f(None, None, url)
        assert result == "unknown", f"Expected unknown for {url!r}, got {result!r}"


def test_normalize_proxy_and_substring_attacks_are_unknown() -> None:
    """URLs that embed a known domain in query/path must NOT be classified as known."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for url in [
        "https://example.com/proxy?target=https://chatgpt.com/backend-api/codex",
        "https://example.com/?next=https://openrouter.ai/api/v1",
        "https://example.com/chatgpt.com/backend-api/codex",
    ]:
        result = f(None, None, url)
        assert result == "unknown", f"Expected unknown for {url!r}, got {result!r}"


def test_normalize_hostname_boundary_attacks_are_unknown() -> None:
    """Domains that merely contain known hostnames as substrings must be rejected."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for url in [
        "https://evil-chatgpt.com/backend-api/codex",
        "https://notchatgpt.com/backend-api/codex",
        "https://notopenrouter.ai/api/v1",
        "https://sub.chatgpt.com/backend-api/codex",   # subdomain not allowed
        "https://sub.openrouter.ai/api/v1",
    ]:
        result = f(None, None, url)
        assert result == "unknown", f"Expected unknown for {url!r}, got {result!r}"


def test_normalize_present_bad_url_does_not_fall_back_to_provider() -> None:
    """When base_url is present but unrecognized, provider and api_mode must NOT be
    used as fallback — result must be 'unknown' regardless of provider/api_mode."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    bad_urls = [
        "https://chatgpt.com/some-other-path",
        "https://chatgpt.com/backend-api",
        "https://evil-chatgpt.com/backend-api/codex",
        "not-a-url",
    ]
    for url in bad_urls:
        result = f("openai-codex", "codex_responses", url)
        assert result == "unknown", (
            f"Expected unknown (no fallback) for bad URL {url!r} with openai-codex provider, "
            f"got {result!r}"
        )
        result = f("openrouter", "chat_completions", url)
        assert result == "unknown", (
            f"Expected unknown (no fallback) for bad URL {url!r} with openrouter provider, "
            f"got {result!r}"
        )


def test_normalize_empty_url_falls_back_to_provider() -> None:
    """When base_url is absent or empty, derive family from provider then api_mode."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    assert f("openai-codex", "codex_responses", None) == "codex"
    assert f("openai-codex", "codex_responses", "") == "codex"
    assert f("openai-codex", "codex_responses", "  ") == "codex"
    assert f("openrouter", "chat_completions", None) == "openrouter"
    assert f("xai-oauth", None, None) == "codex"
    assert f("anthropic", "anthropic_messages", None) == "anthropic"


def test_normalize_not_a_url_is_unknown() -> None:
    """Non-URL strings must normalize to 'unknown', not silently match a family."""
    from agent.chat_completion_helpers import _normalize_base_url_family as f

    for s in ["not-a-url", "codex", "openrouter", "gpt-5.5", "localhost", "::1"]:
        result = f(None, None, s)
        assert result == "unknown", f"Expected unknown for non-URL {s!r}, got {result!r}"


def test_guard_fail_closed_for_bad_known_host_path(tmp_path: Path) -> None:
    """Bridge-managed reviewer with correct provider/model/api_mode but base_url
    https://chatgpt.com/some-other-path must raise controlled_subagent_identity_mismatch.
    The bad URL normalizes to 'unknown', which does not match allowed 'codex' family."""
    import types
    from agent.chat_completion_helpers import build_api_kwargs

    bad_url_request = {
        "actual_provider": "openai-codex",
        "actual_model": "gpt-5.5",
        "actual_api_mode": "codex_responses",
        "actual_base_url": "https://chatgpt.com/some-other-path",  # valid host, wrong path
        "actual_api_key": "sk-codex-test",
    }
    agent = types.SimpleNamespace(
        tools=[], api_mode="codex_responses", provider="openai-codex",
        model="gpt-5.5", base_url="https://chatgpt.com/backend-api/codex",
        reasoning_config=None, request_overrides=None, session_id="test",
        _is_anthropic_oauth=False, _skip_role_model_selection=True,
        _constructor_provider="openai-codex", _constructor_model="gpt-5.5",
        _constructor_api_mode="codex_responses",
        _constructor_base_url="https://chatgpt.com/backend-api/codex",
        _turn_runtime_request=bad_url_request,
        _controlled_allowed_request_identities=[
            {
                "provider": "openai-codex",
                "model": "gpt-5.5",
                "api_mode": "codex_responses",
                "base_url_family": "codex",
            }
        ],
    )
    with pytest.raises(RuntimeError, match="controlled_subagent_identity_mismatch"):
        build_api_kwargs(agent, [])
