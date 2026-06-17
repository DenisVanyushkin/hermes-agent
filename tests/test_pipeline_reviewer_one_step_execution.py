from __future__ import annotations

import importlib
from pathlib import Path

from hermes_cli.runtime_factory import RuntimeFactory
from hermes_cli.subagent_runner import SubagentRunner

from tests.test_pipeline_one_step_execution import (
    _config,
    _loaded_specs,
    _session,
    _valid_structured_output,
)


def _reviewer_structured_output(*, blockers: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "subagent_id": "hermes_code_reviewer",
        "role": "reviewer",
        "status": "succeeded",
        "summary": "Reviewed the engineer result.",
        "findings": [{"code": "review", "summary": "One controlled review completed"}],
        "changes": [],
        "blockers": blockers or [],
        "artifacts": [{"artifact_id": "review-1", "kind": "review_note"}],
        "confidence": 0.93,
        "requires_review": False,
        "next_action": "none" if not blockers else "rework_required",
    }


def _runtime_factory(repo_root: Path) -> RuntimeFactory:
    return RuntimeFactory(repo_root=repo_root)


def _engineer_result(module, tmp_path: Path):
    repo_root, loaded_specs = _loaded_specs(tmp_path)

    result = module.execute_controlled_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "Prepared one controlled patch.",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": _valid_structured_output()},
            }
        ),
        user_message="Implement one step",
    )
    return repo_root, loaded_specs, result


def test_reviewer_disabled_does_not_call_fake_runner(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs, engineer_result = _engineer_result(module, tmp_path)
    called = {"count": 0}

    result = module.execute_controlled_reviewer_one_step(
        config=_config(mode="controlled_one_step", allow_actual_subagent_invocation=True),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: called.__setitem__("count", called["count"] + 1)
        ),
        prior_result=engineer_result,
        user_message="Review the engineer result",
    )

    assert called["count"] == 0
    assert result.fuse.actual_invocation_allowed is False
    assert result.state_snapshot.planned_steps[1].runner_result["status"] == "not_invoked"


def test_reviewer_allowed_calls_fake_runner_exactly_once_without_rerunning_engineer(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs, engineer_result = _engineer_result(module, tmp_path)
    called = {"count": 0}

    def _reviewer_executor(_request, _runtime_plan):
        called["count"] += 1
        return {
            "output_text": "Approved",
            "completion_reason": "completed",
            "execution_status": "completed",
            "raw_metadata": {"structured_output": _reviewer_structured_output()},
        }

    result = module.execute_controlled_reviewer_one_step(
        config=_config(
            mode="controlled_one_step",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(executor=_reviewer_executor),
        prior_result=engineer_result,
        user_message="Review the engineer result",
    )

    assert called["count"] == 1
    assert result.state_snapshot.planned_steps[0].runner_result["status"] == "succeeded"
    assert result.state_snapshot.planned_steps[1].runner_result["status"] == "succeeded"


def test_reviewer_invalid_output_fails_closed(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs, engineer_result = _engineer_result(module, tmp_path)

    result = module.execute_controlled_reviewer_one_step(
        config=_config(
            mode="controlled_one_step",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "bad reviewer envelope",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": {"status": "approved"}},
            }
        ),
        prior_result=engineer_result,
        user_message="Review the engineer result",
    )

    assert result.state_snapshot.planned_steps[1].evaluation_result["status"] == "invalid_structured_output"
    assert result.execution_report.completion.completion_allowed is False


def test_reviewer_blockers_are_preserved_and_completion_is_blocked(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs, engineer_result = _engineer_result(module, tmp_path)

    result = module.execute_controlled_reviewer_one_step(
        config=_config(
            mode="controlled_one_step",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "changes requested",
                "completion_reason": "completed",
                "execution_status": "completed",
        "raw_metadata": {
                    "structured_output": _reviewer_structured_output(
                        blockers=["missing regression test"],
                    )
                },
            }
        ),
        prior_result=engineer_result,
        user_message="Review the engineer result",
    )

    assert result.state_snapshot.planned_steps[1].evaluation_result["blockers"] == ["missing regression test"]
    assert result.execution_report.completion.completion_allowed is False
    assert result.execution_report.status.value == "blocked"


def test_reviewer_approval_produces_safe_report_with_reviewer_metadata(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs, engineer_result = _engineer_result(module, tmp_path)

    result = module.execute_controlled_reviewer_one_step(
        config=_config(
            mode="controlled_one_step",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "approved",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": _reviewer_structured_output()},
            }
        ),
        prior_result=engineer_result,
        user_message="Review the engineer result",
    )

    assert result.execution_report.executed is True
    assert result.execution_report.subagents[1].runner_status == "succeeded"
    assert result.execution_report.subagents[1].evaluation_status == "candidate_complete"
    assert result.execution_report.safety.executed is True


def test_reviewer_helper_never_executes_loop_rework_tools_or_file_mutation(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_one_step_execution")
    repo_root, loaded_specs, engineer_result = _engineer_result(module, tmp_path)

    result = module.execute_controlled_reviewer_one_step(
        config=_config(
            mode="controlled_one_step",
            allow_actual_subagent_invocation=True,
            allow_actual_reviewer_invocation=True,
            allowed_subagents=["hermes_engineer_core", "hermes_code_reviewer"],
        ),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=_runtime_factory(repo_root),
        runner=SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "approved",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {"structured_output": _reviewer_structured_output()},
            }
        ),
        prior_result=engineer_result,
        user_message="Review the engineer result",
    )

    assert result.fuse.tools_allowed is False
    assert result.fuse.file_mutation_allowed is False
    assert result.fuse.loop_allowed is False
    assert result.fuse.model_escalation_allowed is False
