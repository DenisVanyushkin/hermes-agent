"""Shared fake-only controlled manual dry-run helpers for gateway and smoke paths."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from hermes_cli.pipeline_rework_loop import execute_bounded_rework_loop
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory
from hermes_cli.subagent_runner import ControlledRuntimeRunner, SubagentRunner

ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
REVIEWER_SUBAGENT_ID = "hermes_code_reviewer"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
CONTROLLED_MANUAL_MODE = "controlled_manual"
CONTROLLED_VALIDATION_TRIGGER = "HERMES CONTROLLED PIPELINE VALIDATION"
GATEWAY_WORKSPACE_ROOT = Path('/tmp/hermes-gateway-controlled-runs')


def run_controlled_engineering_e2e_dry_run(*, task: str, workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return _blocked_payload("workspace_required")

    repo_root = Path(__file__).resolve().parent.parent
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    try:
        dry_run_workspace = prepare_controlled_e2e_workspace(repo_root=repo_root, workspace=workspace)
    except ValueError as exc:
        return _blocked_payload(str(exc))

    result = execute_bounded_rework_loop(
        config=_controlled_manual_config(),
        session=_controlled_manual_session(task),
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
        "report": report,
    }


def build_controlled_manual_helper_context(*, user_message: str, session_id: str | None, pipeline_session_id: str | None, repo_root: Path | None = None) -> dict[str, Any]:
    base_repo_root = Path(__file__).resolve().parent.parent if repo_root is None else Path(repo_root)
    workspace = gateway_controlled_workspace(session_id=session_id, pipeline_session_id=pipeline_session_id)
    return {
        "runtime_factory": RuntimeFactory(repo_root=base_repo_root),
        "runner": SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        "user_message": user_message,
        "repo_path": str(workspace),
        "allow_completion_after_review": True,
        "controlled_runtime_context": {
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
            "mutation_workspace": str(workspace),
            "allow_test_commands": True,
            "test_workspace": str(workspace),
        },
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
                'mode': CONTROLLED_MANUAL_MODE,
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
