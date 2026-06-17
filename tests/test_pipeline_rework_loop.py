from __future__ import annotations

import importlib
from pathlib import Path
import shutil

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


def _engineer_output() -> dict[str, object]:
    return {
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
