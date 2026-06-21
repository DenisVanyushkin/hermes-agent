"""Shared fake-only controlled manual dry-run helpers for gateway and smoke paths."""

from __future__ import annotations

import subprocess
from itertools import zip_longest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hermes_cli.pipeline_aiagent_executor import AIAgentReviewerExecutorBridge, AIAgentSubagentExecutorBridge
from hermes_cli.pipeline_report_artifacts import persist_controlled_execution_report_artifacts
from hermes_cli.pipeline_rework_loop import execute_bounded_rework_loop
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory, RuntimeFactoryPlan, build_runtime_factory_plan
from hermes_cli.subagent_runner import ControlledRuntimeRunner, SubagentRunner

ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
REVIEWER_SUBAGENT_ID = "hermes_code_reviewer"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
# Legacy helper/test names remain for dry-run compatibility, but they now
# exercise the autonomous execution path only.
AUTONOMOUS_MODE = "autonomous"
CONTROLLED_VALIDATION_TRIGGER = "HERMES CONTROLLED PIPELINE VALIDATION"
GATEWAY_WORKSPACE_ROOT = Path('/tmp/hermes-gateway-controlled-runs')


def run_controlled_engineering_e2e_dry_run(*, task: str, workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return _blocked_payload("workspace_required")

    repo_root = Path(__file__).resolve().parent.parent
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _controlled_manual_session(task)
    try:
        dry_run_workspace = prepare_controlled_e2e_workspace(repo_root=repo_root, workspace=workspace)
    except ValueError as exc:
        return _blocked_payload(str(exc))

    result = execute_bounded_rework_loop(
        config=_controlled_manual_config(),
        session=session,
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message=task,
        repo_path=str(dry_run_workspace),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "invocation_client": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fake runtime must not be used")),
            "controlled_runner": ControlledRuntimeRunner(),
            "allow_real_provider_execution": True,
            "request_real_provider_execution": True,
            "allowed_real_providers": ("openrouter", "openai-codex"),
            "allowed_real_models": ("xiaomi/mimo-v2.5-pro", "gpt-5.5"),
            "allowed_real_providers_by_role": {
                "engineer": ("openrouter",),
                "reviewer": ("openai-codex",),
            },
            "allowed_real_models_by_role": {
                "engineer": ("xiaomi/mimo-v2.5-pro",),
                "reviewer": ("gpt-5.5",),
            },
            "allowed_real_providers_by_subagent": {
                ENGINEER_SUBAGENT_ID: ("openrouter",),
                REVIEWER_SUBAGENT_ID: ("openai-codex",),
            },
            "allowed_real_models_by_subagent": {
                ENGINEER_SUBAGENT_ID: ("xiaomi/mimo-v2.5-pro",),
                REVIEWER_SUBAGENT_ID: ("gpt-5.5",),
            },
            "real_provider_client_factory": _manual_dry_run_provider_factory,
            "allow_mutations": True,
            "mutation_workspace": str(dry_run_workspace),
            "allow_test_commands": True,
            "test_workspace": str(dry_run_workspace),
        },
    )
    report = result.execution_report.to_safe_dict() if result.execution_report is not None else None
    report_artifacts = None
    if report is not None:
        report_artifacts = persist_controlled_execution_report_artifacts(
            session=session,
            state_snapshot=result.execution_report,
            controller_payload={
                "status": _result_status(result),
                "blocked_reason": result.blocked_reason,
                "actual_execution_invoked": True,
                "execution_mode": AUTONOMOUS_MODE,
                "helper_result_status": _result_status(result),
                "workspace_basename": dry_run_workspace.name,
            },
            pipeline_execution_report_payload=report,
            router_decision=RouterDecision(
                pipeline_session_id=session.pipeline_session_id,
                router_subagent_id="hermes_pipeline_router",
                status="selected",
                selected_pipeline_id=ENGINEERING_PIPELINE_ID,
                fallback_pipeline_id="default_conversation_pipeline",
                confidence=0.99,
                reasoning_summary="local fake smoke harness",
                selected_provider="openai-codex",
                selected_model="gpt-5.4-mini",
            ),
            workspace_path=dry_run_workspace,
            durable_root=None,
        )
    return {
        "scenario": "controlled-engineering-e2e",
        "runner_mode": "fake",
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
        "provider_execution_mode": "fake_real_provider_client",
        "network_access": "disabled",
        "sdk_import_mode": "not_used",
        "reviewer_approved": bool(report and report.get("review", {}).get("reviewer_approved")),
        "git_gate": dict(result.git_gate),
        "mutation_summary": dict(result.mutation_summary or {}),
        "test_summary": dict(result.test_summary or {}),
        "report_artifacts": {
            "run_id": (report_artifacts or {}).get("run_id"),
            "workspace_basename": (report_artifacts or {}).get("workspace_basename"),
            "report_written": bool(report_artifacts),
        },
        "report": report,
    }


def build_controlled_manual_helper_context(
    *,
    config: dict[str, Any] | None = None,
    user_message: str,
    session_id: str | None,
    pipeline_session_id: str | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    base_repo_root = Path(__file__).resolve().parent.parent if repo_root is None else Path(repo_root)
    workspace = gateway_controlled_workspace(session_id=session_id, pipeline_session_id=pipeline_session_id)
    controlled_runtime_context = _default_controlled_runtime_context(workspace)
    helper_context = {
        "runtime_factory": RuntimeFactory(repo_root=base_repo_root),
        "runner": SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        "user_message": user_message,
        "repo_path": str(workspace),
        "allow_completion_after_review": True,
        "controlled_runtime_context": controlled_runtime_context,
    }
    if not _allow_real_provider_execution(config):
        return helper_context

    controlled_runtime_context["allow_real_provider_execution"] = True
    controlled_runtime_context["request_real_provider_execution"] = True
    controlled_runtime_context["allow_mutations"] = True
    controlled_runtime_context["allow_test_commands"] = True

    try:
        workspace = prepare_controlled_e2e_workspace(repo_root=base_repo_root, workspace=workspace)
    except ValueError as exc:
        controlled_runtime_context["blocked_reason"] = str(exc)
        helper_context["repo_path"] = str(workspace)
        return helper_context

    helper_context["repo_path"] = str(workspace)
    controlled_runtime_context["mutation_workspace"] = str(workspace)
    controlled_runtime_context["test_workspace"] = str(workspace)

    loaded_specs = load_pipeline_specs(repo_root=base_repo_root)
    bridge_runtime_plans = _build_bridge_runtime_plans(
        loaded_specs=loaded_specs,
        pipeline_session_id=pipeline_session_id,
        user_message=user_message,
        config=config,
    )
    controlled_runtime_context["bridge_runtime_plans"] = {
        key: value.to_safe_dict() for key, value in bridge_runtime_plans.items()
    }

    engineer_plan = bridge_runtime_plans[ENGINEER_SUBAGENT_ID]
    reviewer_plan = bridge_runtime_plans[REVIEWER_SUBAGENT_ID]
    if engineer_plan.errors:
        controlled_runtime_context["blocked_reason"] = f"runtime_plan_blocked:{ENGINEER_SUBAGENT_ID}"
        return helper_context
    if reviewer_plan.errors:
        controlled_runtime_context["blocked_reason"] = f"runtime_plan_blocked:{REVIEWER_SUBAGENT_ID}"
        return helper_context

    controlled_runtime_context["executor_bridge"] = {
        ENGINEER_SUBAGENT_ID: AIAgentSubagentExecutorBridge(
            workspace_root=workspace,
            repo_root=base_repo_root,
        ),
        REVIEWER_SUBAGENT_ID: AIAgentReviewerExecutorBridge(
            workspace_root=workspace,
            repo_root=base_repo_root,
        ),
    }
    controlled_runtime_context["real_executor_ready"] = True
    controlled_runtime_context["blocked_reason"] = None
    return helper_context


def _default_controlled_runtime_context(workspace: Path) -> dict[str, Any]:
    return {
        "real_executor_ready": False,
        "blocked_reason": "real_subagent_executor_missing",
        "allow_real_provider_execution": False,
        "request_real_provider_execution": False,
        "allowed_real_providers": (),
        "allowed_real_models": (),
        "allowed_real_providers_by_role": {},
        "allowed_real_models_by_role": {},
        "allowed_real_providers_by_subagent": {},
        "allowed_real_models_by_subagent": {},
        "allow_mutations": False,
        "mutation_workspace": str(workspace),
        "allow_test_commands": False,
        "test_workspace": str(workspace),
    }


def _allow_real_provider_execution(config: dict[str, Any] | None) -> bool:
    return bool((((config or {}).get("pipelines") or {}).get("execution") or {}).get("allow_real_provider_execution", False))


def _build_bridge_runtime_plans(
    *,
    loaded_specs: Any,
    pipeline_session_id: str | None,
    user_message: str,
    config: dict[str, Any] | None,
) -> dict[str, RuntimeFactoryPlan]:
    session = SimpleNamespace(
        pipeline_session_id=pipeline_session_id or "controlled-manual",
        trace_id=pipeline_session_id or "controlled-manual",
        pipeline_id=ENGINEERING_PIPELINE_ID,
        user_message=user_message,
    )
    return {
        ENGINEER_SUBAGENT_ID: build_runtime_factory_plan(
            session=session,
            planned_step=SimpleNamespace(subagent_id=ENGINEER_SUBAGENT_ID, step_kind="engineer"),
            subagent_spec=loaded_specs.subagent_specs.get(ENGINEER_SUBAGENT_ID),
            config=config,
        ),
        REVIEWER_SUBAGENT_ID: build_runtime_factory_plan(
            session=session,
            planned_step=SimpleNamespace(subagent_id=REVIEWER_SUBAGENT_ID, step_kind="reviewer"),
            subagent_spec=loaded_specs.subagent_specs.get(REVIEWER_SUBAGENT_ID),
            config=config,
        ),
    }


def gateway_controlled_workspace(*, session_id: str | None, pipeline_session_id: str | None) -> Path:
    slug = pipeline_session_id or session_id or 'manual'
    slug = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '-' for ch in str(slug)).strip('-_') or 'manual'
    workspace = (GATEWAY_WORKSPACE_ROOT / slug).resolve()
    repo_root = Path(__file__).resolve().parent.parent.resolve()
    if workspace == repo_root:
        raise ValueError('workspace_matches_repo_root')
    return workspace


def prepare_controlled_e2e_workspace(*, repo_root: Path, workspace: Path) -> Path:
    workspace = workspace.expanduser()
    if workspace.exists() and not workspace.is_dir():
        raise ValueError('workspace_invalid')
    resolved_repo_root = repo_root.resolve()
    resolved_workspace = workspace.resolve() if workspace.exists() else workspace.resolve(strict=False)
    if resolved_workspace == resolved_repo_root:
        raise ValueError('workspace_matches_repo_root')
    if workspace.exists():
        if not (workspace / '.git').exists():
            raise ValueError('workspace_not_git_repo')
        _ensure_controlled_e2e_workspace_layout(workspace=workspace, repo_root=repo_root)
        return workspace.resolve()

    workspace.mkdir(parents=True, exist_ok=False)
    _git(workspace, 'init', '-b', 'main')
    _git(workspace, 'config', 'user.name', 'Hermes Dry Run')
    _git(workspace, 'config', 'user.email', 'hermes-dry-run@example.com')
    (workspace / '.gitignore').write_text('tests/__pycache__/\nvenv\n', encoding='utf-8')
    (workspace / 'tracked.txt').write_text('baseline\n', encoding='utf-8')
    _git(workspace, 'add', '.gitignore', 'tracked.txt')
    _git(workspace, 'commit', '-m', 'initial')
    _ensure_controlled_e2e_workspace_layout(workspace=workspace, repo_root=repo_root)
    return workspace.resolve()


def _ensure_controlled_e2e_workspace_layout(*, workspace: Path, repo_root: Path) -> None:
    expected_venv = (repo_root / 'venv').resolve(strict=False)
    workspace_venv = workspace / 'venv'
    if workspace_venv.is_symlink():
        if workspace_venv.resolve(strict=False) == expected_venv:
            return
        workspace_venv.unlink()
    elif workspace_venv.exists():
        raise ValueError('workspace_venv_conflict')
    workspace_venv.symlink_to(repo_root / 'venv')

def format_controlled_manual_summary(helper_result: dict[str, Any] | None, *, workspace_path: str | None = None) -> str | None:
    if not isinstance(helper_result, dict):
        return None
    report = helper_result.get("report")
    if isinstance(report, dict):
        return _format_controlled_manual_summary_from_report(
            report,
            report_artifacts=helper_result.get("report_artifacts"),
            workspace_path=workspace_path,
            controller_status=str(helper_result.get("status") or "").strip() or None,
        )
    mutation_summary = dict(helper_result.get('mutation_summary') or {})
    test_summary = dict(helper_result.get('test_summary') or {})
    workspace_name = Path(workspace_path).name if workspace_path else None
    
    # Extract test counts from results list
    test_results = list(test_summary.get('results') or [])
    total_tests = len(test_results)
    passed_tests = sum(1 for r in test_results if isinstance(r, dict) and r.get('status') == 'passed')
    
    lines = [
        'Controlled pipeline validation completed.',
        f"status: {helper_result.get('status', 'unknown')}",
        f"completion_allowed: {bool(helper_result.get('completion_allowed'))}",
        f"pipeline: {ENGINEERING_PIPELINE_ID}",
        'runtime: fake_real_provider_client',
        f"mutation: applied_count={int(mutation_summary.get('applied_count') or 0)} denied_count={int(mutation_summary.get('denied_count') or 0)}",
        f"tests: {test_summary.get('status', 'unknown')} {passed_tests}/{total_tests}",
    ]
    if workspace_name:
        lines.append(f'workspace: {workspace_name}')
    return '\n'.join(lines)


def _format_controlled_manual_summary_from_report(
    report: dict[str, Any],
    *,
    report_artifacts: Any,
    workspace_path: str | None,
    controller_status: str | None,
) -> str:
    routing = dict(report.get("routing") or {})
    controller = dict(report.get("controller") or {})
    completion = dict(report.get("completion") or {})
    tests = dict(report.get("tests") or {})
    usage = dict(report.get("usage_summary") or report.get("usage") or {})
    review = dict(report.get("review") or {})
    artifacts = dict(report_artifacts or {})
    changed_files = list(report.get("changed_files") or [])
    actual_execution_invoked = bool(controller.get("executed"))
    controller_invoked = controller_status is not None

    lines = [
        "Controlled pipeline validation report.",
        f"status: {report.get('status', 'unknown')}",
        f"pipeline: {routing.get('selected_pipeline_id') or routing.get('pipeline_id') or ENGINEERING_PIPELINE_ID}",
        f"execution_mode: {controller.get('execution_mode') or report.get('execution_mode') or 'unknown'}",
        f"final_verdict: {completion.get('final_verdict') or report.get('status') or 'unknown'}",
        f"blocked_reason: {completion.get('blocked_reason') or 'none'}",
        f"controller_invoked: {controller_invoked}",
        f"report_execution_invoked: {actual_execution_invoked}",
    ]

    if actual_execution_invoked:
        lines.append(_executed_mutation_line(changed_files))
        lines.append(_executed_tests_line(tests))
        lines.append(_executed_models_line(usage))
        lines.append(f"reviewer_invoked: {bool(review.get('reviewer_invoked'))}")
    else:
        lines.append("mutation: none")
        lines.append(f"tests: {_non_executed_tests_label(tests)}")
        lines.append("models_used: none")
        planned_models = _planned_models_lines(report)
        if planned_models:
            lines.extend(planned_models)

    report_run_id = _clean_placeholder(artifacts.get("run_id"))
    durable_report_path = _clean_placeholder(artifacts.get("durable_report_path"))
    workspace_report_path = _clean_placeholder(artifacts.get("workspace_report_path"))
    selected_report_path = durable_report_path or workspace_report_path
    selected_workspace_path = (
        workspace_report_path.rsplit("/", 1)[0]
        if workspace_report_path
        else _clean_placeholder(workspace_path)
    )

    if report_run_id:
        lines.append(f"report_run_id: {report_run_id}")
    lines.append(f"report_path: {selected_report_path or 'unavailable'}")
    if selected_workspace_path:
        lines.append(f"workspace: {selected_workspace_path}")

    return "\n".join(line for line in lines if line)


def _executed_mutation_line(changed_files: list[str]) -> str:
    if not changed_files:
        return "mutation: none"
    return f"mutation: changed_files={len(changed_files)}"


def _executed_tests_line(tests: dict[str, Any]) -> str:
    status = str(tests.get("status") or "unknown")
    summary = str(tests.get("summary") or "").strip()
    if summary:
        return f"tests: {status} ({summary})"
    return f"tests: {status}"


def _executed_models_line(usage: dict[str, Any]) -> str:
    providers = list(usage.get("providers_used") or [])
    models = list(usage.get("models_used") or [])
    if not providers and not models:
        return "models_used: none"
    pairs = [
        f"{provider or 'unknown'} / {model or 'unknown'}"
        for provider, model in zip_longest(providers, models, fillvalue="unknown")
    ]
    return f"models_used: {', '.join(str(item) for item in pairs)}"


def _non_executed_tests_label(tests: dict[str, Any]) -> str:
    status = str(tests.get("status") or "unavailable").strip().lower()
    if status in {"unavailable", "not_run", "not-run"}:
        return "not_run"
    return status


def _planned_models_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in report.get("models") or []:
        if not isinstance(item, dict):
            continue
        role_id = str(item.get("role_id") or "unknown")
        provider = str(item.get("provider") or "unknown")
        model = str(item.get("model") or "unknown")
        runtime_status = str(item.get("runtime_status") or item.get("execution_mode") or "unknown")
        lines.append(f"planned_model: {role_id}: {provider} / {model}, {runtime_status}")
    return lines


def _clean_placeholder(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "~"}:
        return None
    return text


def _blocked_payload(reason: str) -> dict[str, Any]:
    return {
        'scenario': 'controlled-engineering-e2e',
        'runner_mode': 'fake',
        'status': 'blocked',
        'candidate_complete': False,
        'completion_allowed': False,
        'user_action_required': True,
        'blocked_reason': reason,
        'review_iterations_completed': 0,
        'max_review_iterations': 0,
        'runner_call_order': [],
        'appended_rework_context': [],
        'iteration_history': [],
        'fuse': {
            'tools_allowed': False,
            'file_mutation_allowed': False,
            'model_escalation_allowed': False,
            'live_gateway_allowed': False,
        },
        'provider_execution_mode': 'fake_real_provider_client',
        'network_access': 'disabled',
        'sdk_import_mode': 'not_used',
        'reviewer_approved': False,
        'git_gate': {},
        'mutation_summary': {},
        'test_summary': {},
        'report': None,
    }


def _controlled_manual_config() -> dict[str, Any]:
    return {
        'pipelines': {
            'enabled': True,
            'execution': {
                'mode': AUTONOMOUS_MODE,
                'allow_pipelines': [ENGINEERING_PIPELINE_ID],
                'allowed_subagents': [ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID],
                'allow_actual_subagent_invocation': True,
                'allow_actual_reviewer_invocation': True,
                'allow_actual_rework_loop': True,
            },
        }
    }


def _controlled_manual_session(task: str):
    decision = RouterDecision(
        pipeline_session_id='pipeline-smoke-session',
        router_subagent_id='hermes_pipeline_router',
        status='selected',
        selected_pipeline_id=ENGINEERING_PIPELINE_ID,
        fallback_pipeline_id='default_conversation_pipeline',
        confidence=0.99,
        reasoning_summary='local fake smoke harness',
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode='observe',
            platform='local',
            session_id='pipeline-smoke-local',
            user_message=task,
            created_at='2026-06-17T00:00:00+00:00',
        )
    )


def _manual_dry_run_provider_factory(runtime):
    def _client(_request):
        if runtime.subagent_id == ENGINEER_SUBAGENT_ID:
            return {
                'provider': runtime.provider,
                'model': runtime.model,
                'structured_output': _manual_dry_run_engineer_output(),
                'output_text': 'engineer runtime completed',
                'token_usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
                'tool_calls': [{'tool_name': 'apply_patch', 'call_count': 1, 'status': 'not_invoked'}],
            }
        return {
            'provider': runtime.provider,
            'model': runtime.model,
            'structured_output': _reviewer_output(blockers=[]),
            'output_text': 'reviewer runtime approved',
            'token_usage': {'input_tokens': 4, 'output_tokens': 2, 'total_tokens': 6},
            'tool_calls': [{'tool_name': 'pytest', 'call_count': 1, 'status': 'not_invoked'}],
        }

    return _client


def _manual_dry_run_engineer_output() -> dict[str, Any]:
    payload = {
        'schema_version': 'v1',
        'subagent_id': ENGINEER_SUBAGENT_ID,
        'role': 'engineer',
        'status': 'succeeded',
        'summary': 'Added generated example test.',
        'findings': [{'code': 'test_added', 'summary': 'Added generated example test'}],
        'changes': [{'path': 'tests/test_generated_example.py', 'kind': 'modify'}],
        'blockers': [],
        'artifacts': [{'artifact_id': 'patch-1', 'kind': 'diff'}],
        'confidence': 0.93,
        'requires_review': False,
        'next_action': 'none',
        'mutations': [
            {
                'operation': 'write_text',
                'path': 'tests/test_generated_example.py',
                'content': 'def test_generated_example():\n    assert 1 + 1 == 2\n',
            }
        ],
        'tests': ['python -m pytest -q tests/test_generated_example.py'],
    }
    return payload


def _reviewer_output(*, blockers: list[str]) -> dict[str, Any]:
    return {
        'schema_version': 'v1',
        'subagent_id': REVIEWER_SUBAGENT_ID,
        'role': 'reviewer',
        'status': 'blocked' if blockers else 'succeeded',
        'summary': 'Needs changes.' if blockers else 'Approved.',
        'findings': [] if blockers else [{'code': 'review', 'summary': 'Approved the narrow patch'}],
        'changes': [],
        'blockers': blockers,
        'artifacts': [{'artifact_id': 'review-1', 'kind': 'review_note'}],
        'confidence': 0.91,
        'requires_review': bool(blockers),
        'next_action': 'rework' if blockers else 'none',
    }


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True, capture_output=True)


def _result_status(result: Any) -> str:
    if result.candidate_complete:
        return 'completed'
    if result.user_action_required or result.blocked_reason is not None:
        return 'blocked'
    return 'not_executed'


def _runner_call_order(result: Any) -> list[str]:
    order: list[str] = []
    for _iteration in result.iteration_history:
        order.append(ENGINEER_SUBAGENT_ID)
        order.append(REVIEWER_SUBAGENT_ID)
    return order


def _sanitize_iteration(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized.pop('engineer_message', None)
    sanitized.pop('reviewer_message', None)
    return sanitized
