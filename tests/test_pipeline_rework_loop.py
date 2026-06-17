from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import subprocess

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory
from hermes_cli.subagent_runner import SubagentRunner


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _loaded_specs(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    return repo_root, load_pipeline_specs(repo_root=repo_root)


def _session():
    decision = RouterDecision(
        pipeline_session_id="pipe-loop-1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.97,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-loop-1",
            user_message="Implement bounded rework loop",
            created_at="2026-06-17T00:00:00+00:00",
        )
    )


def _config() -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": "controlled_one_step",
                "allow_pipelines": ["engineering_review_pipeline"],
                "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                "allow_actual_subagent_invocation": True,
                "allow_actual_reviewer_invocation": True,
                "allow_actual_rework_loop": True,
            }
        }
    }


def _engineer_output(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "v1",
        "subagent_id": "hermes_engineer_core",
        "role": "engineer",
        "status": "succeeded",
        "summary": "Prepared patch.",
        "findings": [{"code": "patch", "summary": "Prepared patch"}],
        "changes": [{"path": "hermes_cli/pipeline_rework_loop.py", "kind": "modify"}],
        "blockers": [],
        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
        "confidence": 0.91,
        "requires_review": False,
        "next_action": "none",
    }
    payload.update(overrides)
    return payload


def _reviewer_output(*, blockers: list[str]) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "subagent_id": "hermes_code_reviewer",
        "role": "reviewer",
        "status": "blocked" if blockers else "succeeded",
        "summary": "needs changes" if blockers else "approved",
        "findings": [],
        "changes": [],
        "blockers": blockers,
        "artifacts": [],
        "confidence": 0.88,
        "requires_review": bool(blockers),
        "next_action": "rework" if blockers else "none",
    }


def _invalid_output() -> dict[str, object]:
    return {
        "status": "approved",
    }



def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _write(repo: Path, relative_path: str, content: str) -> Path:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "loop-git-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _write(repo, "tracked.txt", "baseline\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_first_review_approval_stops_without_rework(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        payload = _engineer_output() if request.subagent_id == "hermes_engineer_core" else _reviewer_output(blockers=[])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert len(result.iteration_history) == 1
    assert result.candidate_complete is True
    assert result.completion_allowed is False
    assert result.blocked_reason == "loop_harness_not_live_final"


def test_blockers_trigger_one_rework_then_approval(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []
    reviewer_round = {"count": 0}

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        if request.subagent_id == "hermes_engineer_core":
            payload = _engineer_output()
        else:
            reviewer_round["count"] += 1
            payload = _reviewer_output(blockers=["missing regression test"] if reviewer_round["count"] == 1 else [])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == [
        "hermes_engineer_core",
        "hermes_code_reviewer",
        "hermes_engineer_core",
        "hermes_code_reviewer",
    ]
    assert len(result.iteration_history) == 2
    assert result.appended_rework_context == ["Reviewer blockers after iteration 1: missing regression test"]


def test_loop_limit_exceeded_blocks_before_extra_rework(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []
    loaded_specs.pipeline_specs["engineering_review_pipeline"]["loop_policy"]["max_review_iterations"] = 1

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        payload = _engineer_output() if request.subagent_id == "hermes_engineer_core" else _reviewer_output(blockers=["still blocked"])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert result.user_action_required is True
    assert result.blocked_reason == "review_loop_limit_exceeded"


def test_invalid_reviewer_structured_output_fails_closed_without_rework(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        payload = _engineer_output() if request.subagent_id == "hermes_engineer_core" else _invalid_output()
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert result.iteration_history[0].reviewer_evaluation_status == "invalid_structured_output"
    assert result.candidate_complete is False
    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "reviewer_result_invalid"


def test_reviewer_runner_failure_fails_closed_without_rework(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        if request.subagent_id == "hermes_engineer_core":
            payload = _engineer_output()
            return {
                "output_text": "ok",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": payload},
            }
        return {
            "output_text": "failed",
            "completion_reason": "failed",
            "execution_status": "failed",
            "error_code": "runner_failed",
            "raw_metadata": None,
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert result.iteration_history[0].reviewer_evaluation_status == "blocked"
    assert result.candidate_complete is False
    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "reviewer_verdict_blocked"


def test_invalid_engineer_result_fails_closed_before_reviewer(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        return {
            "output_text": "failed",
            "completion_reason": "failed",
            "execution_status": "failed",
            "error_code": "engineer_failed",
            "raw_metadata": None,
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == ["hermes_engineer_core"]
    assert result.iteration_history == []
    assert result.candidate_complete is False
    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "engineer_result_failed"


def test_missing_reviewer_status_fails_closed_without_keyerror(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        if request.subagent_id == "hermes_engineer_core":
            payload = _engineer_output()
        else:
            payload = _reviewer_output(blockers=[])
            payload.pop("status")
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert result.iteration_history[0].reviewer_evaluation_status == "invalid_structured_output"
    assert result.candidate_complete is False
    assert result.user_action_required is True
    assert result.blocked_reason == "reviewer_result_invalid"



def test_git_gate_disabled_preserves_existing_behavior(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    def _executor(request, _runtime_plan):
        payload = _engineer_output() if request.subagent_id == "hermes_engineer_core" else _reviewer_output(blockers=[])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
    )

    assert result.git_gate["status"] == "disabled"
    assert result.reviewer_packet["present"] is False
    assert result.blocked_reason == "loop_harness_not_live_final"


def test_git_gate_no_material_changes_can_skip_reviewer_and_allow_completion(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": _engineer_output()},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
    )

    assert calls == ["hermes_engineer_core"]
    assert result.git_gate["status"] == "enabled"
    assert result.git_gate["material_change_status"] == "no_material_changes"
    assert result.git_gate["material_changes_present"] is False
    assert result.reviewer_packet["packet_status"] == "review_not_required"
    assert result.completion_allowed is True
    assert result.candidate_complete is True
    assert result.user_action_required is False
    assert result.blocked_reason is None


def test_git_gate_material_changes_require_reviewer_before_completion(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        if request.subagent_id == "hermes_engineer_core":
            _write(git_repo, "new.txt", "created by engineer\n")
            payload = _engineer_output(summary="Created new.txt")
        else:
            payload = _reviewer_output(blockers=[])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
    )

    assert calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert result.git_gate["material_change_status"] == "material_changes_detected"
    assert result.git_gate["review_required"] is True
    assert result.git_gate["changed_files"] == ["new.txt"]
    assert result.reviewer_packet["present"] is True
    assert result.reviewer_packet["packet_status"] == "ready_for_review"
    assert result.completion_allowed is False
    assert result.candidate_complete is True
    assert result.blocked_reason == "loop_harness_not_live_final"


def test_git_gate_material_changes_with_reviewer_approval_allows_completion(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    def _executor(request, _runtime_plan):
        if request.subagent_id == "hermes_engineer_core":
            _write(git_repo, "new.txt", "created by engineer\n")
            payload = _engineer_output(summary="Created new.txt")
        else:
            payload = _reviewer_output(blockers=[])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        allow_completion_after_review=True,
        test_summary={"status": "passed", "command": "pytest -q", "summary": "3 passed"},
    )

    assert result.completion_allowed is True
    assert result.candidate_complete is True
    assert result.blocked_reason is None
    assert result.reviewer_packet["present"] is True
    assert result.reviewer_packet["safe_packet"]["tests"]["status"] == "passed"


def test_git_gate_dirty_baseline_fails_closed_without_attributing_existing_changes(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    _write(git_repo, "tracked.txt", "dirty before engineer\n")
    calls: list[str] = []

    def _executor(request, _runtime_plan):
        calls.append(request.subagent_id)
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": _engineer_output()},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
    )

    assert calls == ["hermes_engineer_core"]
    assert result.git_gate["baseline_capture_status"] == "captured"
    assert result.git_gate["material_change_status"] == "baseline_invalid"
    assert result.git_gate["baseline_dirty"] is True
    assert result.git_gate["changed_files"] == []
    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "baseline_dirty"


def test_git_gate_invalid_repo_path_fails_closed_without_exception(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    def _executor(request, _runtime_plan):
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": _engineer_output()},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
        repo_path=str(tmp_path / "missing-repo"),
    )

    assert result.git_gate["baseline_capture_status"] == "invalid_repo"
    assert result.git_gate["material_change_status"] == "baseline_invalid"
    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "baseline_invalid"



def _controlled_runtime_context():
    from hermes_cli.subagent_runner import ControlledRuntimeRunner

    def _client(runtime, _payload):
        if runtime.role_id == "engineer":
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _engineer_output(summary="Controlled engineer patch prepared"),
                "output_text": "engineer ok",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"read_hit": True, "write": False},
                "tool_calls": [{"tool_name": "apply_patch", "call_count": 1, "status": "not_invoked"}],
            }
        return {
            "provider": runtime.provider,
            "model": runtime.model,
            "structured_output": _reviewer_output(blockers=[]),
            "output_text": "review ok",
            "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            "cache": {"read_hit": False, "write": False},
            "tool_calls": [{"tool_name": "pytest", "call_count": 1, "status": "not_invoked"}],
        }

    return {
        "invocation_client": _client,
        "controlled_runner": ControlledRuntimeRunner(),
    }


def test_controlled_runtime_context_invokes_engineer_backend_and_exposes_safe_telemetry(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        controlled_runtime_context=_controlled_runtime_context(),
    )

    payload = result.to_safe_dict()
    assert result.iteration_history[0].engineer_runner_status == "succeeded"
    assert payload["subagent_runs"][0]["role_id"] == "engineer"
    assert payload["subagent_runs"][0]["actual_provider"] == "openrouter"
    assert payload["subagent_runs"][0]["actual_model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["subagent_runs"][0]["input_hash"]
    assert payload["subagent_runs"][0]["prompt_hash"]
    assert payload["subagent_runs"][0]["response_output_hash"]
    assert payload["subagent_runs"][0]["raw_output_redacted"] is True
    assert payload["usage_summary"]["total_tokens"] == 21
    assert payload["usage_summary"]["subagent_count"] == 2
    assert payload["usage_summary"]["providers_used"] == ["openrouter", "openai-codex"]
    assert set(payload["usage_summary"]["models_used"]) == {"xiaomi/mimo-v2.5-pro", "gpt-5.5"}
    assert payload["original_task"] == "[redacted]"
    assert payload["appended_rework_context"] == []
    assert payload["original_task_hash"]
    assert payload["appended_rework_context_hashes"] == []
    encoded = __import__("json").dumps(payload, sort_keys=True)
    assert "engineer ok" not in encoded
    assert "Implement bounded rework loop" not in encoded


def test_controlled_runtime_context_preserves_distinct_engineer_and_reviewer_models(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    def _client(runtime, _payload):
        if runtime.role_id == "engineer":
            _write(git_repo, "new.txt", "created by engineer\n")
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _engineer_output(summary="Created new.txt"),
                "output_text": "engineer ok",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        return {
            "provider": runtime.provider,
            "model": runtime.model,
            "structured_output": _reviewer_output(blockers=[]),
            "output_text": "review ok",
            "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        controlled_runtime_context={"invocation_client": _client},
    )

    payload = result.to_safe_dict()
    assert [item["role_id"] for item in payload["subagent_runs"]] == ["engineer", "reviewer"]
    assert payload["subagent_runs"][0]["actual_provider"] == "openrouter"
    assert payload["subagent_runs"][1]["actual_provider"] == "openai-codex"
    assert payload["subagent_runs"][0]["actual_model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["subagent_runs"][1]["actual_model"] == "gpt-5.5"
    assert result.execution_report.to_safe_dict()["usage"]["total_tokens"] == 21


def test_controlled_runtime_context_fails_closed_on_provider_model_mismatch(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        controlled_runtime_context={
            "invocation_client": lambda _runtime, _payload: {
                "provider": "openai-codex",
                "model": "gpt-5.4-mini",
                "structured_output": _engineer_output(summary="wrong runtime"),
            }
        },
    )

    payload = result.to_safe_dict()
    assert result.blocked_reason == "engineer_result_failed"
    assert payload["subagent_runs"][0]["status"] == "blocked"
    assert payload["subagent_runs"][0]["failure_reason"] == "runtime_contract_mismatch"
    assert payload["subagent_runs"][0]["error_type"] == "runtime_contract_mismatch"
    assert result.candidate_complete is False


def test_git_gate_report_omits_diff_and_file_contents(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    def _executor(request, _runtime_plan):
        if request.subagent_id == "hermes_engineer_core":
            _write(git_repo, "secret.env", "API_KEY=123\n")
            payload = _engineer_output(summary="Applied patch\n@@\n+++ secret.env")
        else:
            payload = _reviewer_output(blockers=[])
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_executor),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        test_summary={"status": "failed", "summary": "+++ secret.env\npassword=123"},
    )

    payload = result.to_safe_dict()
    encoded = __import__("json").dumps(payload, sort_keys=True)

    assert "API_KEY=123" not in encoded
    assert "password=123" not in encoded
    assert "+++ secret.env" not in encoded
