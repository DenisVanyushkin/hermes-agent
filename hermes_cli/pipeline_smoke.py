"""Local fake-only smoke harness for the controlled engineering pipeline loop."""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path
import subprocess
from typing import Any

from hermes_cli.config import load_config
from hermes_cli.pipeline_rework_loop import execute_bounded_rework_loop
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory
from hermes_cli.subagent_runner import SubagentRunner
from hermes_cli.pipeline_controlled_dry_run import run_controlled_engineering_e2e_dry_run


ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
REVIEWER_SUBAGENT_ID = "hermes_code_reviewer"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
DEFAULT_TASK = "Implement a narrow engineering slice with tests."
CONTROLLED_E2E_SCENARIO = "controlled-engineering-e2e"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hermes_cli.pipeline_smoke",
        description="Run a fake-only local smoke harness for the controlled engineering pipeline loop.",
    )
    parser.add_argument(
        "--scenario",
        choices=(
            "approval",
            "blocker_then_approval",
            "loop_limit_exceeded",
            "invalid_reviewer",
            "reviewer_failure",
            CONTROLLED_E2E_SCENARIO,
        ),
        default="approval",
        help="Which fake runner scenario to execute.",
    )
    parser.add_argument(
        "--runner-mode",
        choices=("fake", "real"),
        default="fake",
        help="Runner mode. 'real' is currently fail-closed and unsupported.",
    )
    parser.add_argument(
        "--task",
        default=DEFAULT_TASK,
        help="Task text used to seed the pipeline session. Not echoed in JSON output.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.add_argument(
        "--workspace",
        help="Explicit workspace for the controlled manual dry-run scenario.",
    )
    parser.add_argument(
        "--report-out",
        help="Optional path to write the safe JSON payload.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_smoke_scenario(
        scenario=args.scenario,
        runner_mode=args.runner_mode,
        task=args.task,
        workspace=Path(args.workspace).expanduser() if args.workspace else None,
    )
    if args.report_out:
        report_path = Path(args.report_out).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def run_smoke_scenario(
    *,
    scenario: str,
    runner_mode: str = "fake",
    task: str = DEFAULT_TASK,
    workspace: Path | None = None,
) -> dict[str, Any]:
    if runner_mode != "fake":
        return {
            "scenario": scenario,
            "runner_mode": runner_mode,
            "status": "blocked",
            "candidate_complete": False,
            "completion_allowed": False,
            "user_action_required": True,
            "blocked_reason": "real_runner_mode_unsupported",
            "review_iterations_completed": 0,
            "max_review_iterations": 0,
            "runner_call_order": [],
            "appended_rework_context": [],
            "iteration_history": [],
            "fuse": {
                "tools_allowed": False,
                "file_mutation_allowed": False,
                "model_escalation_allowed": False,
                "live_gateway_allowed": False,
            },
            "report": None,
        }
    if scenario == CONTROLLED_E2E_SCENARIO:
        return run_controlled_engineering_e2e_dry_run(task=task, workspace=workspace)

    repo_root = Path(__file__).resolve().parent.parent
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    if scenario == "loop_limit_exceeded":
        loaded_specs = replace_pipeline_loop_limit(loaded_specs, max_review_iterations=1)

    result = execute_bounded_rework_loop(
        config=_smoke_config(),
        session=_session(task),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=_fake_executor_for_scenario(scenario)),
        user_message=task,
    )
    report = result.execution_report.to_safe_dict() if result.execution_report is not None else None
    return {
        "scenario": scenario,
        "runner_mode": runner_mode,
        "status": _result_status(result),
        "candidate_complete": result.candidate_complete,
        "completion_allowed": result.completion_allowed,
        "user_action_required": result.user_action_required,
        "blocked_reason": result.blocked_reason,
        "review_iterations_completed": result.review_iterations_completed,
        "max_review_iterations": result.max_review_iterations,
        "runner_call_order": _runner_call_order(result),
        "appended_rework_context": list(result.appended_rework_context),
        "iteration_history": [_sanitize_iteration(item.to_safe_dict()) for item in result.iteration_history],
        "fuse": result.fuse.to_safe_dict(),
        "report": report,
    }


def _smoke_config() -> dict[str, Any]:
    config = copy.deepcopy(load_config() or {})
    pipelines = copy.deepcopy(config.get("pipelines") or {})
    execution = copy.deepcopy(pipelines.get("execution") or {})
    execution.update(
        {
            "mode": "controlled_one_step",
            "allow_pipelines": [ENGINEERING_PIPELINE_ID],
            "allowed_subagents": [ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID],
            "allow_actual_subagent_invocation": True,
            "allow_actual_reviewer_invocation": True,
            "allow_actual_rework_loop": True,
        }
    )
    pipelines["enabled"] = True
    pipelines["execution"] = execution
    config["pipelines"] = pipelines
    return config


def _session(task: str):
    decision = RouterDecision(
        pipeline_session_id="pipeline-smoke-session",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id=ENGINEERING_PIPELINE_ID,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.99,
        reasoning_summary="local fake smoke harness",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="local",
            session_id="pipeline-smoke-local",
            user_message=task,
            created_at="2026-06-17T00:00:00+00:00",
        )
    )


def replace_pipeline_loop_limit(loaded_specs: Any, *, max_review_iterations: int) -> Any:
    pipeline_specs = copy.deepcopy(getattr(loaded_specs, "pipeline_specs", {}))
    pipeline_spec = copy.deepcopy(pipeline_specs.get(ENGINEERING_PIPELINE_ID, {}))
    loop_policy = copy.deepcopy(pipeline_spec.get("loop_policy", {}))
    loop_policy["max_review_iterations"] = max_review_iterations
    pipeline_spec["loop_policy"] = loop_policy
    pipeline_specs[ENGINEERING_PIPELINE_ID] = pipeline_spec
    return replace(loaded_specs, pipeline_specs=pipeline_specs)


def _fake_executor_for_scenario(scenario: str):
    reviewer_round = {"count": 0}

    def _executor(request, _runtime_plan):
        if request.subagent_id == ENGINEER_SUBAGENT_ID:
            return _runner_payload(_engineer_output())
        if scenario == "invalid_reviewer":
            return _runner_payload({"status": "approved"})
        if scenario == "reviewer_failure":
            return {
                "output_text": "reviewer failed",
                "completion_reason": "failed",
                "execution_status": "failed",
                "error_code": "runner_failed",
                "raw_metadata": None,
            }
        reviewer_round["count"] += 1
        blockers: list[str] = []
        if scenario == "blocker_then_approval" and reviewer_round["count"] == 1:
            blockers = ["missing regression test"]
        elif scenario == "loop_limit_exceeded":
            blockers = ["still blocked"]
        return _runner_payload(_reviewer_output(blockers=blockers))

    return _executor


def _runner_payload(structured_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_text": "ok",
        "completion_reason": "completed",
        "execution_status": "completed",
        "token_usage": {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
        "raw_metadata": {"structured_output": structured_output},
    }


def _engineer_output() -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "subagent_id": ENGINEER_SUBAGENT_ID,
        "role": "engineer",
        "status": "succeeded",
        "summary": "Prepared a narrow patch.",
        "findings": [{"code": "patch", "summary": "Prepared a narrow patch"}],
        "changes": [{"path": "hermes_cli/pipeline_smoke.py", "kind": "modify"}],
        "blockers": [],
        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
        "confidence": 0.94,
        "requires_review": False,
        "next_action": "none",
    }


def _reviewer_output(*, blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "subagent_id": REVIEWER_SUBAGENT_ID,
        "role": "reviewer",
        "status": "blocked" if blockers else "succeeded",
        "summary": "Needs changes." if blockers else "Approved.",
        "findings": [] if blockers else [{"code": "review", "summary": "Approved the narrow patch"}],
        "changes": [],
        "blockers": blockers,
        "artifacts": [{"artifact_id": "review-1", "kind": "review_note"}],
        "confidence": 0.91,
        "requires_review": bool(blockers),
        "next_action": "rework" if blockers else "none",
    }


def _manual_dry_run_provider_factory(runtime):
    def _client(_request):
        if runtime.subagent_id == ENGINEER_SUBAGENT_ID:
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _manual_dry_run_engineer_output(),
                "output_text": "engineer runtime completed",
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "tool_calls": [{"tool_name": "apply_patch", "call_count": 1, "status": "not_invoked"}],
            }
        return {
            "provider": runtime.provider,
            "model": runtime.model,
            "structured_output": _reviewer_output(blockers=[]),
            "output_text": "reviewer runtime approved",
            "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            "tool_calls": [{"tool_name": "pytest", "call_count": 1, "status": "not_invoked"}],
        }

    return _client


def _manual_dry_run_engineer_output() -> dict[str, Any]:
    payload = _engineer_output()
    payload.update(
        {
            "summary": "Added generated example test.",
            "findings": [{"code": "test_added", "summary": "Added generated example test"}],
            "changes": [{"path": "tests/test_generated_example.py", "kind": "modify"}],
            "confidence": 0.93,
            "mutations": [
                {
                    "operation": "write_text",
                    "path": "tests/test_generated_example.py",
                    "content": "def test_generated_example():\n    assert 1 + 1 == 2\n",
                }
            ],
            "tests": ["python -m pytest -q tests/test_generated_example.py"],
        }
    )
    return payload


def _prepare_controlled_e2e_workspace(*, repo_root: Path, workspace: Path) -> Path:
    workspace = workspace.expanduser()
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("workspace_invalid")
    if workspace.exists():
        if not (workspace / ".git").exists():
            raise ValueError("workspace_not_git_repo")
        return workspace.resolve()

    workspace.mkdir(parents=True, exist_ok=False)
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.name", "Hermes Dry Run")
    _git(workspace, "config", "user.email", "hermes-dry-run@example.com")
    (workspace / ".gitignore").write_text("tests/__pycache__/\nvenv\n", encoding="utf-8")
    (workspace / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(workspace, "add", ".gitignore", "tracked.txt")
    _git(workspace, "commit", "-m", "initial")
    (workspace / "venv").symlink_to(repo_root / "venv")
    return workspace.resolve()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _result_status(result: Any) -> str:
    if result.candidate_complete:
        return "completed"
    if result.user_action_required or result.blocked_reason is not None:
        return "blocked"
    return "not_executed"


def _runner_call_order(result: Any) -> list[str]:
    order: list[str] = []
    for iteration in result.iteration_history:
        order.append(ENGINEER_SUBAGENT_ID)
        order.append(REVIEWER_SUBAGENT_ID)
    return order


def _sanitize_iteration(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized.pop("engineer_message", None)
    sanitized.pop("reviewer_message", None)
    return sanitized


if __name__ == "__main__":
    raise SystemExit(main())
