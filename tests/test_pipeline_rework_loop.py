from __future__ import annotations

import importlib
from pathlib import Path
import shutil
from types import SimpleNamespace
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
                "mode": "autonomous",
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


def _escalated_reviewer_output(*, decision: str, blockers: list[str] | None = None, confidence: float = 0.91) -> dict[str, object]:
    blockers = list(blockers or [])
    return {
        "schema_version": "v1",
        "subagent_id": "hermes_code_reviewer_escalated",
        "role": "reviewer",
        "status": "succeeded" if decision == "approved" else "blocked",
        "decision": decision,
        "summary": "escalated approved" if decision == "approved" else "escalated blocked",
        "findings": [],
        "changes": [],
        "blockers": blockers,
        "artifacts": [],
        "confidence": confidence,
        "requires_review": False,
        "next_action": "none",
    }


def _invalid_output() -> dict[str, object]:
    return {
        "status": "approved",
    }


def test_engineer_fail_closed_reason_distinguishes_missing_structured_output() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    snapshot = SimpleNamespace(
        planned_steps=[
            SimpleNamespace(
                runner_result={"status": "succeeded", "structured_output": None},
                evaluation_result={"status": "blocked"},
            )
        ]
    )

    assert module._engineer_fail_closed_reason(snapshot) == "missing_structured_output"


def test_blocked_final_response_text_handles_missing_structured_output() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")

    text = module._blocked_final_response_text(
        blocked_reason="missing_structured_output",
        test_summary={"status": "not_requested"},
        reviewer_packet={},
    )

    assert text is not None
    assert "required structured output packet" in text


def test_finalize_loop_result_preserves_blocked_final_response_text(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    session_module = importlib.import_module("hermes_cli.pipeline_session")
    state_module = importlib.import_module("hermes_cli.pipeline_state_machine")
    loaded_specs = _loaded_specs(tmp_path)[1]
    session = session_module.PipelineSession(
        pipeline_session_id="pipe-1",
        trace_id="pipe-1",
        pipeline_id="engineering_review_pipeline",
        router_status="selected",
        router_confidence=0.98,
        platform="telegram",
        session_key="agent:main:telegram:dm:1",
        session_id="session-1",
        chat_id="1",
        thread_id=None,
        user_id="user-1",
        created_at="2026-06-22T00:00:00+00:00",
        user_message_hash="hash",
        mode="autonomous",
        current_state="rework_loop_reviewer_fail_closed",
        status="created",
        planned_steps=[],
        selected_subagent_ids=["hermes_engineer_core", "hermes_code_reviewer"],
        reviewer_condition="code_changes_require_review",
    )
    snapshot = state_module.build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded_specs.pipeline_specs["engineering_review_pipeline"],
        loaded_specs=loaded_specs,
    )
    snapshot = snapshot.__class__(**{
        **snapshot.__dict__,
        "executed": True,
        "completion_allowed": False,
        "completion_blocked_reason": "missing_structured_output",
        "final_verdict": "controlled_rework_loop_reviewer_fail_closed",
    })

    result = module._finalize_loop_result(
        fuse=module.PipelineExecutionFuseResult(
            execution_mode="autonomous",
            actual_invocation_allowed=True,
            blocked_reason=None,
            selected_pipeline_id="engineering_review_pipeline",
            selected_step_kind="reviewer",
            selected_subagent_id="hermes_code_reviewer",
        ),
        session=session,
        snapshot=snapshot,
        preflight_allowed=True,
        preflight_reason_code="rework_loop_fuse_allowed",
        iteration_history=[],
        review_iterations_completed=1,
        max_review_iterations=3,
        policy_source="pipeline_spec",
        original_task="task",
        appended_rework_context=[],
        completion_allowed=False,
        candidate_complete=False,
        user_action_required=True,
        blocked_reason="missing_structured_output",
        git_gate={},
        reviewer_packet={},
        subagent_runs=[],
        peer_messages=[],
        disagreements=[],
        decisive_subagent=None,
        model_escalations=[],
        tests={},
        mutation_summary={},
        review_overrides={},
        test_summary={"status": "not_requested"},
    )

    payload = result.execution_report.to_safe_dict()
    assert payload["final_response"]["text"] is not None
    assert "required structured output packet" in payload["final_response"]["text"]



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
    assert result.appended_rework_context[0]["reviewer_verdict"] == "blocked"
    assert result.appended_rework_context[0]["reviewer_blockers"] == ["missing regression test"]
    assert result.appended_rework_context[0]["review_iteration"] == 1


def test_engineer_allowed_mutation_flows_through_git_gate_and_report(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    class _InvocationClient:
        def __call__(self, runtime, payload):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "structured_output": _engineer_output(
                        mutations=[
                            {
                                "operation": "write_text",
                                "path": "tests/test_example.py",
                                "content": "def test_ok():\n    assert True\n",
                            }
                        ]
                    ),
                    "output_text": "ok",
                }
            return {
                "structured_output": _reviewer_output(blockers=[]),
                "output_text": "ok",
            }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        repo_path=str(repo),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "invocation_client": _InvocationClient(),
            "controlled_runner": module.ControlledRuntimeRunner(),
            "allow_mutations": True,
            "mutation_workspace": str(repo),
        },
    )

    assert result.completion_allowed is True
    assert result.mutation_summary["applied_count"] == 1
    assert "tests/test_example.py" in result.git_gate["changed_files"]
    assert result.execution_report.to_safe_dict()["mutation_summary"]["applied_count"] == 1


def test_engineer_mutation_denied_when_gate_disabled(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    class _InvocationClient:
        def __call__(self, _runtime, _payload):
            return {
                "structured_output": _engineer_output(
                    mutations=[{"operation": "write_text", "path": "safe.txt", "content": "hello\n"}]
                ),
                "output_text": "ok",
            }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        repo_path=str(repo),
        controlled_runtime_context={
            "invocation_client": _InvocationClient(),
            "controlled_runner": module.ControlledRuntimeRunner(),
            "allow_mutations": False,
            "mutation_workspace": str(repo),
        },
    )

    assert result.completion_allowed is False
    assert result.blocked_reason == "mutation_denied"
    assert result.mutation_summary["denied_count"] == 1
    assert not (repo / "safe.txt").exists()


def test_reviewer_mutation_request_is_denied(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    class _InvocationClient:
        def __call__(self, runtime, _payload):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "structured_output": _engineer_output(
                        mutations=[
                            {
                                "operation": "write_text",
                                "path": "safe.txt",
                                "content": "engineer-change\n",
                            }
                        ]
                    ),
                    "output_text": "ok",
                }
            return {
                "structured_output": _reviewer_output(blockers=[])
                | {"mutations": [{"operation": "write_text", "path": "reviewer.txt", "content": "nope\n"}]},
                "output_text": "ok",
            }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        repo_path=str(repo),
        controlled_runtime_context={
            "invocation_client": _InvocationClient(),
            "controlled_runner": module.ControlledRuntimeRunner(),
            "allow_mutations": True,
            "mutation_workspace": str(repo),
        },
    )

    assert result.blocked_reason == "mutation_denied"
    assert result.mutation_summary["denied_count"] == 1
    assert not (repo / "reviewer.txt").exists()


def test_engineer_mixed_mutation_batch_fails_closed_without_partial_writes(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    class _InvocationClient:
        def __call__(self, _runtime, _payload):
            return {
                "structured_output": _engineer_output(
                    mutations=[
                        {
                            "operation": "write_text",
                            "path": "safe.txt",
                            "content": "safe\n",
                        },
                        {
                            "operation": "write_text",
                            "path": "../secret.env",
                            "content": "unsafe\n",
                        },
                    ]
                ),
                "output_text": "ok",
            }

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        repo_path=str(repo),
        controlled_runtime_context={
            "invocation_client": _InvocationClient(),
            "controlled_runner": module.ControlledRuntimeRunner(),
            "allow_mutations": True,
            "mutation_workspace": str(repo),
        },
    )

    assert result.blocked_reason == "mutation_denied"
    assert result.mutation_summary["applied_count"] == 0
    assert result.mutation_summary["denied_count"] >= 1
    assert not (repo / "safe.txt").exists()


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


def test_missing_reviewer_bridge_mapping_fails_closed_without_uncaught_exception(tmp_path: Path) -> None:
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
            "executor_bridge": {
                "hermes_engineer_core": lambda _request, _runtime_plan: {
                    "output_text": "ok",
                    "completion_reason": "completed",
                    "execution_status": "completed",
                    "raw_metadata": {"structured_output": _engineer_output()},
                }
            },
        },
    )

    assert result.iteration_history == []
    assert result.blocked_reason == "executor_bridge_missing:hermes_code_reviewer"
    assert result.completion_allowed is False
    assert result.user_action_required is True


def test_invalid_reviewer_bridge_mapping_fails_closed_without_uncaught_exception(tmp_path: Path) -> None:
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
            "executor_bridge": {
                "hermes_engineer_core": lambda _request, _runtime_plan: {
                    "output_text": "ok",
                    "completion_reason": "completed",
                    "execution_status": "completed",
                    "raw_metadata": {"structured_output": _engineer_output()},
                },
                "hermes_code_reviewer": "not-callable",
            },
        },
    )

    assert result.iteration_history == []
    assert result.blocked_reason == "executor_bridge_invalid:hermes_code_reviewer"
    assert result.completion_allowed is False
    assert result.user_action_required is True


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


def test_rework_context_and_reviewer_packet_rebuild_are_structured_and_cumulative(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)
    engineer_messages: list[str] = []
    reviewer_packets: list[dict[str, object]] = []
    first_request = {"seen": False}

    def _engineer_executor(request, _runtime_plan):
        engineer_messages.append(request.input_messages[0]["content"])
        if not first_request["seen"]:
            first_request["seen"] = True
            _write(git_repo, "feature.txt", "first pass\n")
            payload = _engineer_output(
                summary="Initial implementation",
                changes=[{"path": "feature.txt", "kind": "modify"}],
            )
        else:
            _write(git_repo, "feature.txt", "first pass\nsecond pass\n")
            payload = _engineer_output(
                summary="Addressed reviewer feedback",
                changes=[{"path": "feature.txt", "kind": "modify"}],
            )
        return {
            "output_text": "ok",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": payload},
        }

    def _reviewer_executor(request, _runtime_plan):
        reviewer_packets.append(dict(request.metadata["reviewer_packet"]["safe_packet"]))
        if len(reviewer_packets) == 1:
            return {
                "output_text": "needs changes",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {
                    "structured_output": {
                        **_reviewer_output(blockers=["missing regression test"]),
                        "findings": [
                            {"severity": "high", "summary": "missing regression test"},
                            {"severity": "medium", "summary": "add note in summary"},
                        ],
                    }
                },
            }
        return {
            "output_text": "approved",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": _reviewer_output(blockers=[])},
        }

    result = module.execute_bounded_rework_loop(
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
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "executor_bridge": {
                "hermes_engineer_core": _engineer_executor,
                "hermes_code_reviewer": _reviewer_executor,
            },
        },
    )

    assert len(engineer_messages) == 2
    assert len(reviewer_packets) == 2
    assert result.completion_allowed is True
    assert result.reviewer_packet["safe_packet"]["git"]["changed_files"] == ["feature.txt"]
    assert reviewer_packets[0]["git"]["changed_files"] == ["feature.txt"]
    assert reviewer_packets[1]["git"]["changed_files"] == ["feature.txt"]
    assert "second pass" in (git_repo / "feature.txt").read_text(encoding="utf-8")
    assert result.appended_rework_context[0]["reviewer_verdict"] == "blocked"
    assert result.appended_rework_context[0]["reviewer_blockers"] == ["missing regression test"]
    assert result.appended_rework_context[0]["blocking_findings"] == ["missing regression test"]
    assert result.appended_rework_context[0]["non_blocking_findings"] == ["add note in summary"]
    assert result.appended_rework_context[0]["reviewer_packet_summary"]["changed_files"] == ["feature.txt"]
    assert '"reviewer_verdict": "blocked"' in engineer_messages[1]
    assert '"reviewer_blockers": [' in engineer_messages[1]



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



def _controlled_runtime_context(
    *,
    mutate_repo: Path | None = None,
    responses: list[dict[str, object]] | None = None,
    allow_model_escalation: bool = False,
):
    from hermes_cli.subagent_runner import ControlledRuntimeRunner

    queued = list(responses or [])

    def _default_response(runtime):
        if runtime.role_id == "engineer":
            if mutate_repo is not None:
                _write(mutate_repo, "new.txt", "created by engineer\n")
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _engineer_output(
                    summary="Created new.txt" if mutate_repo is not None else "Controlled engineer patch prepared"
                ),
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

    def _client(runtime, _payload):
        if queued:
            response = dict(queued.pop(0))
            if runtime.role_id == "engineer" and mutate_repo is not None:
                _write(mutate_repo, "new.txt", "created by engineer\n")
            response.setdefault("provider", runtime.provider)
            response.setdefault("model", runtime.model)
            return response
        return _default_response(runtime)

    return {
        "invocation_client": _client,
        "controlled_runner": ControlledRuntimeRunner(),
        "allow_model_escalation": allow_model_escalation,
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
    assert payload["usage_summary"]["planned_subagent_count"] == 2
    assert payload["usage_summary"]["executed_subagent_count"] == 2
    assert payload["usage_summary"]["subagent_run_instance_count"] == 2
    assert payload["usage_summary"]["execution_round_count"] == 1
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


def test_controlled_runtime_context_real_provider_path_respects_mutation_gate(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    factory_calls: list[str] = []

    def _real_provider_factory(runtime):
        factory_calls.append(runtime.subagent_id)

        def _client(_request):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "provider": runtime.provider,
                    "model": runtime.model,
                    "structured_output": _engineer_output(
                        mutations=[{"operation": "write_text", "path": "safe.txt", "content": "hello\n"}]
                    ),
                    "output_text": "engineer real provider ok",
                    "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                }
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _reviewer_output(blockers=[]),
                "output_text": "reviewer real provider ok",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            }

        return _client

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(repo),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "invocation_client": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fake runtime must not be used")),
            "controlled_runner": module.ControlledRuntimeRunner(),
            "allow_real_provider_execution": True,
            "request_real_provider_execution": True,
            "allowed_real_providers": ("openrouter", "openai-codex"),
            "allowed_real_models": ("xiaomi/mimo-v2.5-pro", "gpt-5.5"),
            "real_provider_client_factory": _real_provider_factory,
            "allow_mutations": True,
            "mutation_workspace": str(repo),
        },
    )

    payload = result.to_safe_dict()
    assert result.completion_allowed is True
    assert result.mutation_summary["applied_count"] == 1
    assert factory_calls == ["hermes_engineer_core", "hermes_code_reviewer"]
    assert payload["subagent_runs"][0]["runtime_mode"] == "real_provider"
    assert payload["subagent_runs"][0]["provider_policy_status"] == "allowed"
    assert payload["subagent_runs"][0]["real_provider_allowed"] is True
    assert (repo / "safe.txt").exists()


def test_escalated_reviewer_subagent_policy_can_be_distinct_from_engineer(tmp_path: Path) -> None:
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    session = _session()
    reviewer_step = next(item for item in session.planned_steps if item.step_kind == "reviewer")
    reviewer_spec = dict(loaded_specs.subagent_specs["hermes_code_reviewer"])

    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    escalated_spec = module._build_escalated_reviewer_spec(reviewer_spec)

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=reviewer_step.__class__(
            step_kind=reviewer_step.step_kind,
            subagent_id="hermes_code_reviewer_escalated",
            condition=reviewer_step.condition,
        ),
        subagent_spec=escalated_spec,
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    factory_calls = {"count": 0}
    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "fake"}},
        request_real_provider_execution=True,
        allow_real_provider_execution=True,
        allowed_real_providers=("openrouter", "openai-codex"),
        allowed_real_models=("xiaomi/mimo-v2.5-pro", "gpt-5.5"),
        allowed_real_providers_by_role={"engineer": ("openrouter",), "reviewer": ("openai-codex",)},
        allowed_real_models_by_role={"engineer": ("xiaomi/mimo-v2.5-pro",), "reviewer": ("gpt-5.5",)},
        allowed_real_providers_by_subagent={"hermes_code_reviewer": ("openai-codex",)},
        allowed_real_models_by_subagent={"hermes_code_reviewer": ("gpt-5.5",)},
        real_provider_client_factory=lambda _runtime: factory_calls.__setitem__("count", factory_calls["count"] + 1),
    )

    assert runtime.runtime_status == "blocked"
    assert runtime.real_provider_allowed is False
    assert runtime.provider_policy_status == "blocked"
    assert factory_calls["count"] == 0
    assert any(error.code == "real_provider_subagent_policy_missing" for error in runtime.errors)


def test_disagreement_resolved_by_reviewer_approval_updates_safe_report(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {
            "structured_output": _engineer_output(summary="Created new.txt"),
            "output_text": "engineer ok",
            "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        },
        {
            "structured_output": _reviewer_output(blockers=["missing regression test"]),
            "output_text": "review blocked",
            "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
            "token_usage": {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9},
        },
        {
            "structured_output": _reviewer_output(blockers=[]),
            "output_text": "review approved",
            "token_usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        },
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        allow_completion_after_review=True,
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses),
    )

    payload = result.execution_report.to_safe_dict()

    assert result.completion_allowed is True
    assert result.candidate_complete is True
    assert payload["peer_messages"] and len(payload["peer_messages"]) == 1
    assert payload["disagreements"][0]["status"] == "resolved"
    assert payload["disagreements"][0]["decisive_subagent"] == "hermes_code_reviewer"
    assert payload["model_escalations"][0]["status"] == "not_required"
    assert [item["role_id"] for item in payload["subagent_runs"]] == ["engineer", "reviewer", "engineer", "reviewer"]
    assert payload["review"]["reviewer_approved"] is True
    encoded = __import__("json").dumps(payload, sort_keys=True)
    assert "Implement bounded rework loop" not in encoded
    assert "Engineer candidate follows" not in encoded


def test_disagreement_unresolved_blocks_with_reviewer_decisive_semantics(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {
            "structured_output": _engineer_output(summary="Created new.txt"),
            "output_text": "engineer ok",
        },
        {
            "structured_output": _reviewer_output(blockers=["missing regression test"]),
            "output_text": "review blocked",
        },
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
        },
        {
            "structured_output": _reviewer_output(blockers=["maintained blocker"]),
            "output_text": "review still blocked",
        },
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses),
    )

    payload = result.execution_report.to_safe_dict()

    assert result.completion_allowed is False
    assert result.candidate_complete is False
    assert result.user_action_required is True
    assert result.blocked_reason == "reviewer_decisive_after_disagreement"
    assert len(payload["peer_messages"]) == 1
    assert payload["disagreements"][0]["status"] == "reviewer_maintained_blocker"
    assert payload["disagreements"][0]["decisive_subagent"] == "hermes_code_reviewer"
    assert payload["model_escalations"][0]["status"] == "block_and_escalate_to_user"
    assert payload["completion"]["blocked_reason"] == "reviewer_decisive_after_disagreement"


def test_disagreement_escalation_enabled_approves_and_updates_decisive_subagent(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {"structured_output": _engineer_output(summary="Created new.txt"), "output_text": "engineer ok"},
        {"structured_output": _reviewer_output(blockers=["missing regression test"]), "output_text": "review blocked"},
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
        },
        {"structured_output": _reviewer_output(blockers=["maintained blocker"]), "output_text": "review still blocked"},
        {"structured_output": _escalated_reviewer_output(decision="approved"), "output_text": "escalated approved", "token_usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}},
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        allow_completion_after_review=True,
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses, allow_model_escalation=True),
    )

    payload = result.execution_report.to_safe_dict()

    assert result.completion_allowed is True
    assert result.candidate_complete is True
    assert payload["review"]["escalation_invoked"] is True
    assert payload["review"]["escalation_approved"] is True
    assert payload["review"]["final_review_decision"] == "approved"
    assert payload["decisive_subagent"] == "hermes_code_reviewer_escalated"
    assert payload["disagreements"][0]["status"] == "resolved_by_escalation"
    assert payload["model_escalations"][0]["status"] == "executed"
    assert payload["model_escalations"][0]["verdict"] == "approved"
    assert payload["model_escalations"][0]["actual_model"] == "gpt-5.5"
    assert payload["subagent_runs"][-1]["subagent_id"] == "hermes_code_reviewer_escalated"
    assert payload["usage_summary"]["subagent_run_instance_count"] == 5
    assert payload["usage_summary"]["total_tokens"] >= 10


def test_disagreement_escalation_approved_still_respects_allow_completion_after_review_false(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {"structured_output": _engineer_output(summary="Created new.txt"), "output_text": "engineer ok"},
        {"structured_output": _reviewer_output(blockers=["missing regression test"]), "output_text": "review blocked"},
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
        },
        {"structured_output": _reviewer_output(blockers=["maintained blocker"]), "output_text": "review still blocked"},
        {"structured_output": _escalated_reviewer_output(decision="approved"), "output_text": "escalated approved", "token_usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}},
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        allow_completion_after_review=False,
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses, allow_model_escalation=True),
    )

    payload = result.execution_report.to_safe_dict()
    encoded = __import__("json").dumps(payload, sort_keys=True)

    assert payload["subagent_runs"][-1]["subagent_id"] == "hermes_code_reviewer_escalated"
    assert payload["model_escalations"][0]["status"] == "executed"
    assert payload["model_escalations"][0]["verdict"] == "approved"
    assert result.completion_allowed is False
    assert payload["completion"]["completion_allowed"] is False
    assert "escalated approved" not in encoded
    assert "Implement bounded rework loop" not in encoded


def test_disagreement_escalation_enabled_maintains_blocker(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {"structured_output": _engineer_output(summary="Created new.txt"), "output_text": "engineer ok"},
        {"structured_output": _reviewer_output(blockers=["missing regression test"]), "output_text": "review blocked"},
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
        },
        {"structured_output": _reviewer_output(blockers=["maintained blocker"]), "output_text": "review still blocked"},
        {"structured_output": _escalated_reviewer_output(decision="blocker_maintained", blockers=["maintained blocker"]), "output_text": "escalated blocked"},
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses, allow_model_escalation=True),
    )

    payload = result.execution_report.to_safe_dict()

    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "escalation_maintained_blocker"
    assert payload["review"]["final_review_decision"] == "blocker_maintained"
    assert payload["model_escalations"][0]["status"] == "executed"
    assert payload["model_escalations"][0]["verdict"] == "blocker_maintained"
    assert payload["completion"]["blocked_reason"] == "escalation_maintained_blocker"


def test_disagreement_escalation_invalid_output_fails_closed(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {"structured_output": _engineer_output(summary="Created new.txt"), "output_text": "engineer ok"},
        {"structured_output": _reviewer_output(blockers=["missing regression test"]), "output_text": "review blocked"},
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
        },
        {"structured_output": _reviewer_output(blockers=["maintained blocker"]), "output_text": "review still blocked"},
        {"structured_output": _reviewer_output(blockers=[]), "output_text": "invalid escalation"},
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses, allow_model_escalation=True),
    )

    payload = result.execution_report.to_safe_dict()

    assert result.completion_allowed is False
    assert result.user_action_required is True
    assert result.blocked_reason == "invalid_escalation_output"
    assert payload["review"]["escalation_invoked"] is True
    assert payload["review"]["escalation_approved"] is False
    assert payload["review"]["final_review_decision"] == "unable_to_arbitrate"
    assert payload["model_escalations"][0]["blocked_reason"] == "invalid_escalation_output"
    assert payload["model_escalations"][0]["status"] == "executed"


def test_disagreement_peer_round_is_bounded_to_one_follow_up(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    responses = [
        {"structured_output": _engineer_output(summary="Created new.txt"), "output_text": "engineer ok"},
        {"structured_output": _reviewer_output(blockers=["missing regression test"]), "output_text": "review blocked"},
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer disagrees with reviewer blocker.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["missing regression test"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.8,
            ),
            "output_text": "engineer disagreement",
        },
        {"structured_output": _reviewer_output(blockers=["still blocked"]), "output_text": "review still blocked"},
        {
            "structured_output": _engineer_output(
                status="disagree_with_reviewer",
                summary="Engineer attempts second disagreement.",
                findings=[],
                blockers=[],
                artifacts=[],
                requires_review=True,
                next_action="disagreement",
                reviewer_objections=["still blocked"],
                evidence=["tests/test_pipeline_rework_loop.py"],
                risks=[],
                confidence=0.7,
            ),
            "output_text": "engineer disagreement again",
        },
    ]

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Implement bounded rework loop",
        repo_path=str(git_repo),
        controlled_runtime_context=_controlled_runtime_context(mutate_repo=git_repo, responses=responses),
    )

    payload = result.execution_report.to_safe_dict()

    assert len(payload["peer_messages"]) == 1
    assert len(payload["subagent_runs"]) == 4
    assert payload["disagreements"][0]["peer_round_limit_status"] == "max_peer_discussion_rounds_reached"
    assert payload["disagreements"][0]["status"] == "reviewer_maintained_blocker"
    assert result.blocked_reason == "reviewer_decisive_after_disagreement"


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



def test_usage_summary_from_subagent_runs_computes_totals_and_executed_counts() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")

    payload = module._usage_summary_from_subagent_runs([
        {
            "status": "succeeded",
            "actual_provider": "openrouter",
            "actual_model": "qwen/qwen3-coder",
            "token_usage": {
                "input_tokens": 11,
                "output_tokens": 4,
                "source": "reported",
            },
            "cache": {
                "source": "reported",
            },
        },
        {
            "status": "not_invoked",
            "failure_reason": "observe_mode_plan_only",
            "actual_provider": "openai-codex",
            "actual_model": "gpt-5.5",
            "token_usage": {
                "input_tokens": 99,
                "output_tokens": 1,
                "source": "reported",
            },
            "cache": {},
        },
    ])

    assert payload["total_input_tokens"] == 11
    assert payload["total_output_tokens"] == 4
    assert payload["total_tokens"] == 15
    assert payload["planned_subagent_count"] == 2
    assert payload["executed_subagent_count"] == 1
    assert payload["subagent_count"] == 2
    assert payload["providers_used"] == ["openrouter"]
    assert payload["models_used"] == ["qwen/qwen3-coder"]



def test_disagreement_result_preserves_accumulated_runs_and_blocks_unchanged(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    reviewer_round = {"count": 0}

    def _executor(request, _runtime_plan):
        if request.subagent_id == "hermes_engineer_core":
            payload = _engineer_output(
                status="disagree_with_reviewer" if reviewer_round["count"] > 0 else "succeeded",
                next_action="disagreement" if reviewer_round["count"] > 0 else "none",
                reviewer_objections=["missing regression test"],
                evidence=["/tmp/repo/hermes_cli/foo.py", "../../secret.env", "the raw task was SECRET_TOKEN=abc123"],
            )
        else:
            reviewer_round["count"] += 1
            payload = _reviewer_output(blockers=["missing regression test"])
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
        allow_completion_after_review=True,
    )

    payload = result.execution_report.to_safe_dict()

    assert result.blocked_reason == "reviewer_decisive_after_disagreement"
    assert payload["loop"]["final_verdict"] == "reviewer_decisive_after_disagreement"
    assert payload["disagreements"][-1]["status"] == "reviewer_maintained_blocker"
    assert payload["model_escalations"][-1]["status"] == "block_and_escalate_to_user"
    assert payload["usage_summary"]["planned_subagent_count"] == 2
    assert payload["usage_summary"]["executed_subagent_count"] == 4
    assert payload["usage_summary"]["subagent_run_instance_count"] == 4
    assert payload["usage_summary"]["execution_round_count"] == 2
    assert len(result.subagent_runs) == 2
    assert [item["role_id"] for item in result.subagent_runs] == ["engineer", "reviewer"]
    assert result.usage_summary["planned_subagent_count"] == 2
    assert result.usage_summary["executed_subagent_count"] == 2


def test_safe_path_evidence_redacts_traversal_and_free_text() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")

    assert module._safe_path_evidence("/tmp/repo/hermes_cli/foo.py") == "foo.py"
    traversal = module._safe_path_evidence("../../secret.env")
    assert traversal.startswith("[redacted:")
    assert "secret.env" not in traversal

    free_text = module._safe_path_evidence("the raw task was SECRET_TOKEN=abc123")
    assert free_text.startswith("[redacted:")
    assert "SECRET_TOKEN=abc123" not in free_text


def test_peer_message_evidence_is_sanitized_and_bounded() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    message = module._build_peer_message(
        session=_session(),
        engineer_output={
            "summary": "Need to challenge reviewer blocker.",
            "reviewer_objections": ["missing regression test"],
            "evidence": [
                "/tmp/repo/hermes_cli/foo.py",
                "../../secret.env",
                "the raw task was SECRET_TOKEN=abc123",
                "sk-live-1234567890",
                "/tmp/repo/hermes_cli/bar.py",
                "/tmp/repo/hermes_cli/baz.py",
            ],
        },
        reviewer_blockers=["missing regression test"],
        related_verdict_id="review-1",
    )

    encoded = __import__("json").dumps(message, sort_keys=True)

    assert message["content"]["evidence"][0] == "foo.py"
    assert len(message["content"]["evidence"]) == 5
    assert "../../secret.env" not in encoded
    assert "SECRET_TOKEN=abc123" not in encoded
    assert "sk-live-1234567890" not in encoded


def test_usage_summary_from_subagent_runs_keeps_plan_size_separate_from_instances() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")

    payload = module._usage_summary_from_subagent_runs(
        [
            {
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"source": "reported"},
            },
            {
                "status": "succeeded",
                "actual_provider": "openai-codex",
                "actual_model": "gpt-5.5",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                "cache": {"source": "reported"},
            },
            {
                "status": "succeeded",
                "actual_provider": "openrouter",
                "actual_model": "xiaomi/mimo-v2.5-pro",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "cache": {"source": "reported"},
            },
            {
                "status": "succeeded",
                "actual_provider": "openai-codex",
                "actual_model": "gpt-5.5",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                "cache": {"source": "reported"},
            },
        ],
        planned_subagent_count=2,
    )

    assert payload["planned_subagent_count"] == 2
    assert payload["executed_subagent_count"] == 4
    assert payload["subagent_run_instance_count"] == 4
    assert payload["execution_round_count"] == 2
    assert payload["subagent_count"] == 4


def test_engineer_tests_flow_into_report_and_reviewer_packet(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)
    _write(repo, "tests/test_example.py", "def test_ok():\n    assert True\n")
    (repo / "venv").symlink_to(REPO_ROOT / "venv")

    class _InvocationClient:
        def __call__(self, runtime, payload):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "structured_output": _engineer_output(
                        mutations=[
                            {
                                "operation": "write_text",
                                "path": "package/module.py",
                                "content": "VALUE = 1\n",
                            }
                        ],
                        tests=["venv/bin/pytest -q tests/test_example.py"],
                    ),
                    "output_text": "ok",
                }
            return {
                "structured_output": _reviewer_output(blockers=[]),
                "output_text": "ok",
            }

    runtime_context = module.ControlledRuntimeContext(
        invocation_client=_InvocationClient(),
        controlled_runner=module.ControlledRuntimeRunner(),
        allow_mutations=True,
        mutation_workspace=str(repo),
        allow_test_commands=True,
        test_workspace=str(repo),
    )

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        repo_path=str(repo),
        controlled_runtime_context=runtime_context,
    )

    payload = result.execution_report.to_safe_dict()

    assert result.test_summary["status"] == "passed"
    assert payload["tests"]["status"] == "passed"
    assert payload["reviewer_packet"]["safe_packet"]["tests"]["status"] == "passed"
    assert payload["reviewer_packet"]["safe_packet"]["tests"]["results"][0]["command"] == [
        "venv/bin/pytest",
        "-q",
        "tests/test_example.py",
    ]


def test_reviewer_test_request_fails_closed(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    class _InvocationClient:
        def __call__(self, runtime, payload):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "structured_output": _engineer_output(),
                    "output_text": "ok",
                }
            return {
                "structured_output": _reviewer_output(blockers=["missing regression test"]) | {
                    "tests": ["venv/bin/pytest -q tests/test_example.py"]
                },
                "output_text": "ok",
            }

    runtime_context = module.ControlledRuntimeContext(
        invocation_client=_InvocationClient(),
        controlled_runner=module.ControlledRuntimeRunner(),
        allow_test_commands=True,
        test_workspace=str(repo),
    )

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        controlled_runtime_context=runtime_context,
    )

    assert result.blocked_reason == "test_command_role_not_permitted"
    assert result.user_action_required is True
    assert result.test_summary["blocked_reason"] == "test_command_role_not_permitted"


def test_engineer_too_many_test_commands_fail_closed_without_crashing(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    class _InvocationClient:
        def __call__(self, runtime, payload):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "structured_output": _engineer_output(
                        tests=[
                            "venv/bin/pytest -q tests/test_one.py",
                            "venv/bin/pytest -q tests/test_two.py",
                            "venv/bin/pytest -q tests/test_three.py",
                            "venv/bin/pytest -q tests/test_four.py",
                        ]
                    ),
                    "output_text": "ok",
                }
            return {
                "structured_output": _reviewer_output(blockers=[]),
                "output_text": "ok",
            }

    runtime_context = module.ControlledRuntimeContext(
        invocation_client=_InvocationClient(),
        controlled_runner=module.ControlledRuntimeRunner(),
        allow_test_commands=True,
        test_workspace=str(repo),
    )

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        controlled_runtime_context=runtime_context,
    )

    assert result.completion_allowed is False
    assert result.blocked_reason == "test_command_denied"
    assert result.test_summary["blocked_reason"] == "test_command_denied"
    assert result.test_summary["executed_count"] == 0


def test_blocked_result_without_test_summary_keeps_safe_and_report_payloads_consistent(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    result = module.execute_bounded_rework_loop(
        config={"pipelines": {"enabled": True, "execution": {"mode": "controlled_one_step"}}},
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
    )

    safe_payload = result.to_safe_dict()
    report_payload = result.execution_report.to_safe_dict()

    assert result.test_summary is None
    assert safe_payload["test_summary"]["status"] == "unavailable"
    assert report_payload["tests"]["status"] == "unavailable"
    assert safe_payload["test_summary"] == report_payload["tests"]


def test_test_command_denied_report_uses_runtime_block_reason_not_execution_disabled(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    class _InvocationClient:
        def __call__(self, runtime, _payload):
            if runtime.role_id == "engineer":
                return {
                    "provider": runtime.provider,
                    "model": runtime.model,
                    "structured_output": _engineer_output(
                        summary="Prepared patch.",
                        changes=[{"path": "tests/test_example.py", "kind": "modify"}],
                        tests=["pytest -q tests/../escape.py"],
                    ),
                    "output_text": "ok",
                }
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _reviewer_output(blockers=[]),
                "output_text": "ok",
            }

    runtime_context = module.ControlledRuntimeContext(
        invocation_client=_InvocationClient(),
        controlled_runner=module.ControlledRuntimeRunner(),
        allow_test_commands=True,
        test_workspace=str(repo),
    )

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        controlled_runtime_context=runtime_context,
    )

    report_payload = result.execution_report.to_safe_dict()

    assert result.blocked_reason == "test_command_denied"
    assert report_payload["executed"] is True
    assert report_payload["completion"]["blocked_reason"] == "test_command_denied"
    assert report_payload["final_response"]["placeholder_reason"] == "test_command_denied"
    assert report_payload["tests"]["blocked_reason"] == "test_command_denied"
    assert report_payload["reviewer_packet"]["blocked_reason"] is None


def test_test_command_denied_final_response_is_honest_and_reviewer_not_invoked(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    class _InvocationClient:
        def __call__(self, runtime, _payload):
            if runtime.role_id == "engineer":
                return {
                    "provider": runtime.provider,
                    "model": runtime.model,
                    "structured_output": _engineer_output(
                        summary="Prepared patch.",
                        changes=[{"path": "tests/test_example.py", "kind": "modify"}],
                        tests=["pytest -q tests/../escape.py"],
                    ),
                    "output_text": "Tests PASSED",
                }
            raise AssertionError("reviewer must not be invoked after denied engineer tests")

    runtime_context = module.ControlledRuntimeContext(
        invocation_client=_InvocationClient(),
        controlled_runner=module.ControlledRuntimeRunner(),
        allow_test_commands=True,
        test_workspace=str(repo),
    )

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: None),
        user_message="Implement bounded rework loop",
        controlled_runtime_context=runtime_context,
    )

    report_payload = result.execution_report.to_safe_dict()
    final_text = report_payload["final_response"]["text"] or ""

    assert result.blocked_reason == "test_command_denied"
    assert report_payload["review"]["reviewer_invoked"] is False
    assert "Tests PASSED" not in final_text
    assert "Pytest was requested but blocked" in final_text
    assert "No verified passing test result is available." in final_text


def test_safe_test_text_keeps_venv_and_environment_diagnostics() -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")

    assert module._safe_test_text("venv/bin/pytest not found") == "venv/bin/pytest not found"
    assert module._safe_test_text("environment marker failed") == "environment marker failed"
    assert module._safe_test_text("password=abc123") is None
