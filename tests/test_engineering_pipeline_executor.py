from __future__ import annotations

import copy
import importlib
import shutil
import sys
from pathlib import Path

from dataclasses import replace

from hermes_cli.pipeline_specs import load_pipeline_specs


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _build_loaded_specs(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    return repo_root, load_pipeline_specs(repo_root=repo_root)


def _build_executor(
    tmp_path: Path,
    *,
    engineer_outputs: list[dict] | None = None,
    reviewer_outputs: list[dict] | None = None,
    max_iterations: int = 2,
    mode: str = "execute",
):
    from hermes_cli.pipeline_executor import EngineeringReviewPipelineExecutor, PipelineExecutionRequest
    from hermes_cli.runtime_factory import RuntimeFactory
    from hermes_cli.subagent_runner import SubagentRunner

    repo_root, loaded_specs = _build_loaded_specs(tmp_path)
    engineer_queue = list(engineer_outputs or [])
    reviewer_queue = list(reviewer_outputs or [])

    def engineer_executor(_request, _runtime_plan):
        if not engineer_queue:
            raise AssertionError("engineer executor queue exhausted")
        return engineer_queue.pop(0)

    def reviewer_executor(_request, _runtime_plan):
        if not reviewer_queue:
            raise AssertionError("reviewer executor queue exhausted")
        return reviewer_queue.pop(0)

    executor = EngineeringReviewPipelineExecutor(
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        engineer_runner=SubagentRunner(executor=engineer_executor),
        reviewer_runner=SubagentRunner(executor=reviewer_executor),
    )
    request = PipelineExecutionRequest(
        loaded_specs=loaded_specs,
        pipeline_session_id="pipe-f1",
        task_summary="Implement F1",
        repo_path=str(repo_root),
        max_iterations=max_iterations,
        mode=mode,
    )
    return executor, request


def test_engineer_only_path_skips_reviewer_when_no_code_changes(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "No repo edits needed",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": False,
                    "change_summary": "No code changes required",
                    "files_changed": [],
                    "needs_review": False,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "approved"
    assert result.completion_reason == "no_code_changes"
    assert result.reviewer_required is False
    assert result.reviewer_ran is False
    assert result.final_approval_status == "not_required"
    assert len(result.iterations) == 1
    assert result.iterations[0].reviewer is None
    assert result.step_records[0].constructor_provider == "openrouter"


def test_engineer_changes_then_reviewer_approves(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patched pipeline executor",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Added pipeline executor",
                    "files_changed": ["hermes_cli/pipeline_executor.py"],
                    "needs_review": True,
                },
            }
        ],
        reviewer_outputs=[
            {
                "output_text": "Looks good",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": [],
                    "nonblocking_findings": [{"code": "nit", "summary": "Rename helper"}],
                    "approved": True,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "approved"
    assert result.completion_reason == "review_approved"
    assert result.reviewer_required is True
    assert result.reviewer_ran is True
    assert result.blocking_findings_count == 0
    assert result.final_approval_status == "approved"
    assert [step.step_kind for step in result.step_records] == ["engineer", "reviewer"]
    assert result.step_records[1].constructor_provider == "openai-codex"
    assert result.step_records[1].constructor_model == "gpt-5.5"


def test_blocking_review_causes_second_engineer_iteration(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "First patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "First pass",
                    "files_changed": ["a.py"],
                    "needs_review": True,
                },
            },
            {
                "output_text": "Second patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Addressed blockers",
                    "files_changed": ["a.py", "b.py"],
                    "needs_review": True,
                },
            },
        ],
        reviewer_outputs=[
            {
                "output_text": "Need fixes",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": [{"code": "bug", "summary": "Edge case broken"}],
                    "nonblocking_findings": [],
                    "approved": False,
                },
            },
            {
                "output_text": "Approved",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": [],
                    "nonblocking_findings": [],
                    "approved": True,
                },
            },
        ],
        max_iterations=2,
    )

    result = executor.execute(request)

    assert result.status.value == "approved"
    assert result.completion_reason == "review_approved"
    assert len(result.iterations) == 2
    assert result.iterations[0].blocking_findings_count == 1
    assert result.iterations[1].blocking_findings_count == 0
    assert [step.step_kind for step in result.step_records] == ["engineer", "reviewer", "engineer", "reviewer"]


def test_max_iterations_with_open_blockers_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "files_changed": ["a.py"],
                    "needs_review": True,
                },
            }
        ],
        reviewer_outputs=[
            {
                "output_text": "Still blocked",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": [{"code": "blocker", "summary": "Still broken"}],
                    "nonblocking_findings": [],
                    "approved": False,
                },
            }
        ],
        max_iterations=1,
    )

    result = executor.execute(request)

    assert result.status.value == "blocked"
    assert result.completion_reason == "max_iterations_reached"
    assert result.blocking_findings_count == 1
    assert result.final_approval_status == "blocked"


def test_runtime_factory_blocked_result_fails_pipeline_structurally(tmp_path: Path) -> None:
    from hermes_cli.pipeline_executor import EngineeringReviewPipelineExecutor, PipelineExecutionRequest
    from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeBuildResult, RuntimeFactory
    from hermes_cli.subagent_runner import SubagentRunner

    repo_root, loaded_specs = _build_loaded_specs(tmp_path)

    class BlockedRuntimeFactory(RuntimeFactory):
        def build(self, request: RuntimeBuildRequest) -> RuntimeBuildResult:
            result = super().build(request)
            if request.subagent_id == "hermes_code_reviewer":
                return replace(result, actual_runtime_status="blocked")
            return result

    executor = EngineeringReviewPipelineExecutor(
        runtime_factory=BlockedRuntimeFactory(repo_root=repo_root),
        engineer_runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "files_changed": ["a.py"],
                    "needs_review": True,
                },
            }
        ),
        reviewer_runner=SubagentRunner(executor=lambda *_args, **_kwargs: {"output_text": "unexpected"}),
    )

    result = executor.execute(
        PipelineExecutionRequest(
            loaded_specs=loaded_specs,
            pipeline_session_id="pipe-blocked-runtime",
            task_summary="Implement F1",
            repo_path=str(repo_root),
        )
    )

    assert result.status.value == "failed"
    assert result.completion_reason == "runtime_plan_failed"
    assert result.error_code == "runtime_plan_failed"


def test_plan_only_builds_engineer_and_reviewer_runtime_plans_without_execution(tmp_path: Path) -> None:
    executed = {"engineer": 0, "reviewer": 0}

    def engineer_executor(_request, _runtime_plan):
        executed["engineer"] += 1
        return {"output_text": "unexpected"}

    def reviewer_executor(_request, _runtime_plan):
        executed["reviewer"] += 1
        return {"output_text": "unexpected"}

    from hermes_cli.pipeline_executor import EngineeringReviewPipelineExecutor, PipelineExecutionRequest
    from hermes_cli.runtime_factory import RuntimeFactory
    from hermes_cli.subagent_runner import SubagentRunner

    repo_root, loaded_specs = _build_loaded_specs(tmp_path)
    executor = EngineeringReviewPipelineExecutor(
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        engineer_runner=SubagentRunner(executor=engineer_executor),
        reviewer_runner=SubagentRunner(executor=reviewer_executor),
    )

    result = executor.execute(
        PipelineExecutionRequest(
            loaded_specs=loaded_specs,
            pipeline_session_id="pipe-plan-only",
            task_summary="Implement F2 with SECRET_TOKEN=abc123",
            repo_path=str(repo_root),
            mode="plan_only",
        )
    )

    assert result.status.value == "planned"
    assert result.completion_reason == "plan_only"
    assert result.reviewer_required is None
    assert result.reviewer_ran is False
    assert result.final_approval_status == "planned"
    assert [step.step_kind for step in result.step_records] == ["engineer", "reviewer"]
    assert result.step_records[0].execution_status == "planned"
    assert result.step_records[1].execution_status == "planned"
    assert result.step_records[0].constructor_provider == "openrouter"
    assert result.step_records[0].constructor_model == "xiaomi/mimo-v2.5-pro"
    assert result.step_records[1].constructor_provider == "openai-codex"
    assert result.step_records[1].constructor_model == "gpt-5.5"
    assert result.step_records[0].selected_model_class == "base_coding"
    assert result.step_records[1].selected_model_class == "senior_review"
    assert result.step_records[0].condition is None
    assert result.step_records[1].condition == "code_changes_require_review"
    assert result.step_records[0].prompt_artifact["path"] == "prompts/subagents/hermes_engineer_core.md"
    assert result.step_records[1].prompt_artifact["path"] == "prompts/subagents/hermes_code_reviewer.md"
    assert "patch" in result.step_records[0].tool_permission_plan_summary["write"]
    assert "git_commit" in result.step_records[0].tool_permission_plan_summary["gated"]
    assert executed == {"engineer": 0, "reviewer": 0}


def test_plan_only_mode_works_without_providing_executors(tmp_path: Path) -> None:
    executor, request = _build_executor(tmp_path, mode="plan_only")

    result = executor.execute(request)

    assert result.status.value == "planned"
    assert result.completion_reason == "plan_only"
    assert [step.step_kind for step in result.step_records] == ["engineer", "reviewer"]


def test_plan_only_blocked_runtime_fails_before_any_runner_call(tmp_path: Path) -> None:
    from hermes_cli.pipeline_executor import EngineeringReviewPipelineExecutor, PipelineExecutionRequest
    from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeBuildResult, RuntimeFactory
    from hermes_cli.subagent_runner import SubagentRunner

    repo_root, loaded_specs = _build_loaded_specs(tmp_path)
    executed = {"count": 0}

    class BlockedRuntimeFactory(RuntimeFactory):
        def build(self, request: RuntimeBuildRequest) -> RuntimeBuildResult:
            result = super().build(request)
            if request.subagent_id == "hermes_code_reviewer":
                return replace(result, actual_runtime_status="blocked")
            return result

    def fail_if_called(_request, _runtime_plan):
        executed["count"] += 1
        return {"output_text": "unexpected"}

    executor = EngineeringReviewPipelineExecutor(
        runtime_factory=BlockedRuntimeFactory(repo_root=repo_root),
        engineer_runner=SubagentRunner(executor=fail_if_called),
        reviewer_runner=SubagentRunner(executor=fail_if_called),
    )

    result = executor.execute(
        PipelineExecutionRequest(
            loaded_specs=loaded_specs,
            pipeline_session_id="pipe-plan-blocked",
            task_summary="Implement F2",
            repo_path=str(repo_root),
            mode="plan_only",
        )
    )

    assert result.status.value == "failed"
    assert result.completion_reason == "runtime_plan_failed"
    assert result.error_code == "runtime_plan_failed"
    assert "hermes_code_reviewer" in (result.error_message or "")
    assert executed["count"] == 0


def test_plan_only_safe_dict_excludes_task_text_prompt_text_secrets_and_raw_output(tmp_path: Path) -> None:
    executor, request = _build_executor(tmp_path, mode="plan_only")
    request = replace(request, task_summary="Implement F2 with prompt_text=leak and api_key=secret-value")

    result = executor.execute(request)
    payload = result.to_safe_dict()

    assert payload["status"] == "planned"
    assert payload["completion_reason"] == "plan_only"
    assert "Implement F2" not in str(payload)
    assert "secret-value" not in str(payload)
    assert "prompt_text=leak" not in str(payload)
    assert "full_text_loaded" in str(payload)
    assert "tool_intents" not in str(payload)
    assert payload["final_approval_status"] == "planned"


def test_subagent_runner_failure_becomes_structured_pipeline_failure(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "subagent_execution_failed"
    assert result.error_code == "subagent_execution_failed"


def test_malformed_engineer_metadata_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": "yes",
                    "change_summary": "Patch",
                    "files_changed": [],
                    "needs_review": True,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_engineer_metadata"
    assert result.error_code == "malformed_engineer_metadata"


def test_missing_engineer_change_summary_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "files_changed": [],
                    "needs_review": True,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_engineer_metadata"
    assert result.error_code == "malformed_engineer_metadata"


def test_missing_engineer_files_changed_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "needs_review": True,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_engineer_metadata"
    assert result.error_code == "malformed_engineer_metadata"


def test_missing_engineer_needs_review_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "files_changed": [],
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_engineer_metadata"
    assert result.error_code == "malformed_engineer_metadata"


def test_engineer_files_changed_requires_string_items(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "files_changed": ["ok.py", 7],
                    "needs_review": True,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_engineer_metadata"
    assert result.error_code == "malformed_engineer_metadata"


def test_malformed_reviewer_metadata_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "files_changed": ["a.py"],
                    "needs_review": True,
                },
            }
        ],
        reviewer_outputs=[
            {
                "output_text": "Review",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": "bad-shape",
                    "nonblocking_findings": [],
                    "approved": False,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_reviewer_metadata"
    assert result.error_code == "malformed_reviewer_metadata"


def test_missing_reviewer_nonblocking_findings_fails_closed(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patch",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Patch",
                    "files_changed": ["a.py"],
                    "needs_review": True,
                },
            }
        ],
        reviewer_outputs=[
            {
                "output_text": "Review",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": [],
                    "approved": True,
                },
            }
        ],
    )

    result = executor.execute(request)

    assert result.status.value == "failed"
    assert result.completion_reason == "malformed_reviewer_metadata"
    assert result.error_code == "malformed_reviewer_metadata"


def test_safe_dict_excludes_task_text_and_secret_like_payloads(tmp_path: Path) -> None:
    executor, request = _build_executor(
        tmp_path,
        engineer_outputs=[
            {
                "output_text": "Patched SECRET_TOKEN=abc123 in prompt",
                "completion_reason": "completed",
                "raw_metadata": {
                    "code_changed": True,
                    "change_summary": "Updated executor",
                    "files_changed": ["hermes_cli/pipeline_executor.py"],
                    "needs_review": True,
                    "prompt_text": "leak me",
                    "api_key": "secret-value",
                },
            }
        ],
        reviewer_outputs=[
            {
                "output_text": "Approved with token secret-value",
                "completion_reason": "completed",
                "raw_metadata": {
                    "blocking_findings": [],
                    "nonblocking_findings": [{"code": "nit", "summary": "Minor"}],
                    "approved": True,
                    "env": {"TOKEN": "abc123"},
                },
            }
        ],
    )

    result = executor.execute(request)
    payload = result.to_safe_dict()

    assert "Implement F1" not in str(payload)
    assert "secret-value" not in str(payload)
    assert "leak me" not in str(payload)
    assert "abc123" not in str(payload)
    assert payload["final_approval_status"] == "approved"


def test_importing_pipeline_executor_stays_import_light() -> None:
    before = set(sys.modules)
    for name in (
        "hermes_cli.pipeline_executor",
        "gateway.run",
        "gateway.adapters.slack",
        "gateway.adapters.telegram",
        "agent.conversation_loop",
        "tools.tool_executor",
        "sdk",
    ):
        sys.modules.pop(name, None)

    module = importlib.import_module("hermes_cli.pipeline_executor")

    assert hasattr(module, "EngineeringReviewPipelineExecutor")
    imported = set(sys.modules) - before
    assert "gateway.run" not in imported
    assert "gateway.adapters.slack" not in imported
    assert "gateway.adapters.telegram" not in imported
    assert "agent.conversation_loop" not in imported
    assert "tools.tool_executor" not in imported
