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
from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot

REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _snapshot_for(
    pipeline_id: str = "engineering_review_pipeline",
    *,
    router_status: str = "selected",
):
    decision = RouterDecision(
        pipeline_session_id="pipe-controller-1",
        router_subagent_id="hermes_pipeline_router",
        status=router_status,
        selected_pipeline_id=pipeline_id if router_status == "selected" else None,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.96,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-controller-1",
            user_message="Implement controller slice",
            created_at="2026-06-17T00:00:00+00:00",
        )
    )
    loaded = load_pipeline_specs()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs[session.pipeline_id],
    )
    return session, snapshot


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _runtime_context(tmp_path: Path, *, user_message: str = "Implement controller helper selection") -> dict[str, object]:
    repo_root = _copy_spec_tree(tmp_path)
    return {
        "runtime_factory": RuntimeFactory(repo_root=repo_root),
        "runner": SubagentRunner(
            executor=lambda *_args, **_kwargs: {
                "output_text": "ok",
                "completion_reason": "completed",
                "execution_status": "completed",
                "raw_metadata": {
                    "structured_output": {
                        "schema_version": "v1",
                        "subagent_id": "hermes_engineer_core",
                        "role": "engineer",
                        "status": "succeeded",
                        "summary": "Prepared patch.",
                        "findings": [{"code": "patch", "summary": "Prepared patch"}],
                        "changes": [{"path": "hermes_cli/pipeline_execution_controller.py", "kind": "modify"}],
                        "blockers": [],
                        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
                        "confidence": 0.91,
                        "requires_review": False,
                        "next_action": "none",
                    }
                },
            }
        ),
        "user_message": user_message,
    }


def _engineer_output(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "v1",
        "subagent_id": "hermes_engineer_core",
        "role": "engineer",
        "status": "succeeded",
        "summary": "Prepared patch.",
        "findings": [{"code": "patch", "summary": "Prepared patch"}],
        "changes": [{"path": "hermes_cli/pipeline_execution_controller.py", "kind": "modify"}],
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
    repo = tmp_path / "controller-git-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _write(repo, "tracked.txt", "baseline\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _controlled_runtime_context(*, mutate_repo: Path | None = None) -> dict[str, object]:
    from hermes_cli.subagent_runner import ControlledRuntimeRunner

    def _client(runtime, _payload):
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

    return {
        "invocation_client": _client,
        "controlled_runner": ControlledRuntimeRunner(),
        "real_executor_ready": True,
    }


def _config(
    *,
    mode: str = "controlled_one_step",
    controller_enabled: bool = True,
    allow_actual_subagent_invocation: bool = True,
    allow_actual_reviewer_invocation: bool = True,
    allow_actual_rework_loop: bool = True,
    allow_pipelines: list[str] | None = None,
    allowed_subagents: list[str] | None = None,
) -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": mode,
                "enable_gateway_execution_controller": controller_enabled,
                "allow_actual_subagent_invocation": allow_actual_subagent_invocation,
                "allow_actual_reviewer_invocation": allow_actual_reviewer_invocation,
                "allow_actual_rework_loop": allow_actual_rework_loop,
                "allow_pipelines": ["engineering_review_pipeline"] if allow_pipelines is None else allow_pipelines,
                "allowed_subagents": (
                    ["hermes_engineer_core", "hermes_code_reviewer"]
                    if allowed_subagents is None
                    else allowed_subagents
                ),
            },
        }
    }


def test_default_config_returns_disabled() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_controller(
        config=None,
        session=session,
        state_snapshot=snapshot,
    )

    assert result.status == "disabled"
    assert result.execution_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"
    assert result.selected_pipeline_id == "engineering_review_pipeline"
    assert result.would_call == "bounded_rework_loop"
    assert result.actual_execution_invoked is False


def test_default_config_does_not_resolve_helper(monkeypatch) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    session, snapshot = _snapshot_for()

    def _boom(**_kwargs):
        raise AssertionError("helper resolver must not run under default config")

    monkeypatch.setattr(helpers, "resolve_pipeline_execution_helper", _boom)

    result = module.evaluate_pipeline_execution_controller(
        config=None,
        session=session,
        state_snapshot=snapshot,
    )

    assert result.status == "disabled"
    assert result.actual_execution_invoked is False


def test_controller_disabled_does_not_call_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(controller_enabled=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "would_execute"
    assert result.execution_allowed is False
    assert result.blocked_reason == "gateway_execution_not_enabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_execution_mode_disabled_does_not_call_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="disabled"),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "disabled"
    assert result.execution_allowed is False
    assert result.blocked_reason == "execution_mode_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_controlled_manual_unknown_context_without_trigger_remains_blocked(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    session = type(session)(
        **{
            **session.__dict__,
            "platform": None,
            "chat_id": None,
            "thread_id": None,
            "user_id": None,
            "session_id": None,
        }
    )

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="controlled_manual"),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context=_runtime_context(tmp_path),
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "controlled_manual_trigger_missing"
    assert result.actual_execution_invoked is False


def test_controlled_manual_authorized_manual_operator_without_real_executor_fails_closed(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    session = type(session)(
        **{
            **session.__dict__,
            "chat_id": "chat-1",
            "user_id": "user-1",
        }
    )
    repo_root = _copy_spec_tree(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="controlled_manual"),
        session=session,
        state_snapshot=snapshot,
        helper_execution_context={
            "runtime_factory": RuntimeFactory(repo_root=repo_root),
            "runner": SubagentRunner(
                executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))
            ),
            "user_message": "Implement controller helper selection",
            "repo_path": str(git_repo),
            "allow_completion_after_review": True,
            "controlled_runtime_context": {
                "real_executor_ready": False,
                "blocked_reason": "real_subagent_executor_missing",
            },
        },
    )

    assert result.actual_execution_invoked is True
    assert result.blocked_reason == "real_subagent_executor_missing"
    assert result.helper_result_status == "blocked"
    assert result.helper_result is not None
    assert result.helper_result["blocked_reason"] == "real_subagent_executor_missing"
    assert result.helper_result["completion_allowed"] is False
    assert result.helper_result["report"]["review"]["reviewer_invoked"] is False
    assert result.helper_result["report"]["changed_files"] == []
    assert result.helper_result is not None
    assert not (git_repo / "tests" / "test_generated_example.py").exists()


def test_controlled_manual_with_explicit_trigger_executes_registered_helper(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    repo_root = _copy_spec_tree(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="controlled_manual"),
        session=session,
        state_snapshot=snapshot,
        helper_execution_context={
            "runtime_factory": RuntimeFactory(repo_root=repo_root),
            "runner": SubagentRunner(
                executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))
            ),
            "user_message": "HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run",
            "repo_path": str(git_repo),
            "allow_completion_after_review": True,
            "controlled_runtime_context": _controlled_runtime_context(mutate_repo=git_repo),
        },
    )

    assert result.actual_execution_invoked is True
    assert result.helper_result is not None
    assert result.final_response_text is not None
    assert "Controlled pipeline validation completed." in result.final_response_text
    assert "pipeline: engineering_review_pipeline" in result.final_response_text
    assert "workspace:" in result.final_response_text
    assert result.workspace_basename == git_repo.name
    safe_payload = result.to_safe_dict()
    assert safe_payload["final_response_text"] is not None
    assert "/tmp/hermes-gateway-controlled-runs" not in safe_payload["final_response_text"]
    assert "/home/hermes/.hermes/controlled-runs" not in safe_payload["final_response_text"]
    assert "workspace: <redacted_absolute_path>/" in safe_payload["final_response_text"]


def test_controlled_manual_registered_helper_does_not_use_manual_dry_run_provider_factory(monkeypatch, tmp_path: Path) -> None:
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    dry_run = importlib.import_module("hermes_cli.pipeline_controlled_dry_run")
    session, _snapshot = _snapshot_for()
    git_repo = _init_git_repo(tmp_path)

    def _boom(*_args, **_kwargs):
        raise AssertionError("_manual_dry_run_provider_factory must stay smoke-only")

    monkeypatch.setattr(dry_run, "_manual_dry_run_provider_factory", _boom)

    result = helpers.execute_engineering_review_helper(
        config=_config(mode="controlled_manual"),
        session=session,
        loaded_specs=load_pipeline_specs(),
        runtime_factory=None,
        runner=None,
        user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run",
        repo_path=str(git_repo),
        controlled_runtime_context={"real_executor_ready": False},
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "real_subagent_executor_missing"
    assert result["completion_allowed"] is False
    assert result["report"]["review"]["reviewer_invoked"] is False
    assert result["report"]["changed_files"] == []
    assert not (git_repo / "tests" / "test_generated_example.py").exists()


def test_controlled_manual_cron_context_without_trigger_remains_blocked(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    session = type(session)(
        **{
            **session.__dict__,
            "platform": "cron",
            "chat_id": "cron-room",
            "user_id": "cron-user",
        }
    )

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="controlled_manual"),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context=_runtime_context(tmp_path),
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "controlled_manual_trigger_missing"
    assert result.actual_execution_invoked is False


def test_controlled_manual_authorized_operator_still_respects_destructive_fuses(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    session = type(session)(
        **{
            **session.__dict__,
            "chat_id": "chat-1",
            "user_id": "user-1",
        }
    )

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="controlled_manual", allow_actual_subagent_invocation=False),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context=_runtime_context(tmp_path),
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "actual_invocation_fuse_disabled"
    assert result.actual_execution_invoked is False


def test_enabled_like_config_without_helper_is_not_wired() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=None,
        allow_test_execution=True,
    )

    assert result.status == "not_wired"
    assert result.execution_allowed is False
    assert result.blocked_reason == "live_execution_not_wired"
    assert result.actual_execution_invoked is False


def test_engineer_fuse_failure_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_subagent_invocation=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "actual_invocation_fuse_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_reviewer_fuse_failure_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_reviewer_invocation=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "reviewer_invocation_fuse_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_rework_loop_fuse_failure_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_rework_loop=False),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "rework_loop_fuse_disabled"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_helper_resolver_is_not_called_if_earlier_fuse_fails(monkeypatch) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    session, snapshot = _snapshot_for()

    def _boom(**_kwargs):
        raise AssertionError("helper resolver must not run before fuses pass")

    monkeypatch.setattr(helpers, "resolve_pipeline_execution_helper", _boom)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allow_actual_subagent_invocation=False),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "actual_invocation_fuse_disabled"


def test_allowed_subagents_gate_blocks_before_helper() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(allowed_subagents=["hermes_engineer_core"]),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "unsupported_subagent"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_unknown_pipeline_helper_is_not_resolved() -> None:
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")

    result = helpers.resolve_pipeline_execution_helper(
        pipeline_id="default_conversation_pipeline",
        allow_registered_helper_selection=True,
    )

    assert result.resolved is False
    assert result.status == "not_wired"
    assert result.blocked_reason == "unsupported_pipeline_helper"
    assert result.helper is None


def test_registered_engineering_helper_resolves_only_in_explicit_controlled_path() -> None:
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")

    blocked = helpers.resolve_pipeline_execution_helper(
        pipeline_id="engineering_review_pipeline",
        allow_registered_helper_selection=False,
    )
    allowed = helpers.resolve_pipeline_execution_helper(
        pipeline_id="engineering_review_pipeline",
        allow_registered_helper_selection=True,
    )

    assert blocked.resolved is False
    assert blocked.blocked_reason == "live_execution_not_wired"
    assert allowed.resolved is True
    assert allowed.helper_name == "bounded_rework_loop"


def test_all_fuses_pass_calls_injected_helper_exactly_once() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**kwargs):
        helper_calls.append(kwargs["session"].pipeline_session_id)
        return {"status": "executed", "execution_report": {"status": "completed"}}

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "executed"
    assert result.execution_allowed is True
    assert result.blocked_reason is None
    assert result.actual_execution_invoked is True
    assert helper_calls == ["pipe-controller-1"]
    assert result.helper_result == {"status": "executed", "execution_report": {"status": "completed"}}
    assert result.helper_result_status == "executed"
    assert result.resolved_helper_name == "injected_helper"


def test_all_fuses_pass_can_call_resolved_registered_helper_exactly_once(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**kwargs):
        helper_calls.append(kwargs["session"].pipeline_session_id)
        return {"status": "executed", "execution_report": {"status": "completed"}}

    monkeypatch.setattr(helpers, "execute_engineering_review_helper", _helper)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context=_runtime_context(tmp_path),
    )

    assert result.status == "executed"
    assert result.execution_allowed is True
    assert result.blocked_reason is None
    assert result.actual_execution_invoked is True
    assert result.resolved_helper_name == "bounded_rework_loop"
    assert helper_calls == ["pipe-controller-1"]


def test_helper_exception_is_fail_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")
        raise RuntimeError("helper exploded")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "execution_failed"
    assert result.execution_allowed is False
    assert result.blocked_reason == "controller_helper_failed"
    assert result.actual_execution_invoked is True
    assert result.helper_result is None
    assert result.helper_result_status == "controller_helper_failed"
    assert result.helper_error == "RuntimeError"
    assert helper_calls == ["called"]


def test_registered_helper_exception_is_fail_closed(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")
        raise RuntimeError("helper exploded")

    monkeypatch.setattr(helpers, "execute_engineering_review_helper", _helper)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context=_runtime_context(tmp_path),
    )

    assert result.status == "execution_failed"
    assert result.execution_allowed is False
    assert result.blocked_reason == "controller_helper_failed"
    assert result.actual_execution_invoked is True
    assert result.helper_result is None
    assert result.helper_result_status == "controller_helper_failed"
    assert result.helper_error == "RuntimeError"
    assert result.resolved_helper_name == "bounded_rework_loop"
    assert helper_calls == ["called"]


def test_registered_helper_controlled_context_no_material_change_uses_engineer_only_and_redacts_task(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    repo_root = _copy_spec_tree(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context={
            "runtime_factory": RuntimeFactory(repo_root=repo_root),
            "runner": SubagentRunner(
                executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))
            ),
            "user_message": "Implement controller helper selection with controlled runtime",
            "repo_path": str(git_repo),
            "controlled_runtime_context": _controlled_runtime_context(),
        },
    )

    assert result.status == "executed"
    assert result.execution_allowed is True
    assert result.blocked_reason is None
    assert result.actual_execution_invoked is True
    assert result.resolved_helper_name == "bounded_rework_loop"
    assert result.helper_result_status == "executed"
    assert result.helper_result is not None
    assert result.helper_result["completion_allowed"] is True
    assert result.helper_result["git_gate"]["material_change_status"] == "no_material_changes"
    assert [item["role_id"] for item in result.helper_result["subagent_runs"]] == ["engineer", "reviewer"]
    assert result.helper_result["subagent_runs"][0]["status"] == "succeeded"
    assert result.helper_result["subagent_runs"][1]["status"] == "not_invoked"
    assert result.helper_result["subagent_runs"][1]["failure_reason"] == "observe_mode_plan_only"
    assert result.helper_result["usage_summary"]["planned_subagent_count"] == 2
    assert result.helper_result["usage_summary"]["executed_subagent_count"] == 1
    assert result.helper_result["usage_summary"]["subagent_count"] == 2
    assert result.helper_result["usage_summary"]["total_tokens"] == 15
    assert result.helper_result["usage_summary"]["providers_used"] == ["openrouter"]
    encoded = __import__("json").dumps(result.helper_result, sort_keys=True)
    assert "Implement controller helper selection with controlled runtime" not in encoded
    assert "engineer ok" not in encoded


def test_registered_helper_controlled_context_material_change_requires_reviewer_approval(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    repo_root = _copy_spec_tree(tmp_path)
    git_repo = _init_git_repo(tmp_path)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context={
            "runtime_factory": RuntimeFactory(repo_root=repo_root),
            "runner": SubagentRunner(
                executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))
            ),
            "user_message": "Implement controller helper selection with reviewer approval",
            "repo_path": str(git_repo),
            "allow_completion_after_review": True,
            "controlled_runtime_context": _controlled_runtime_context(mutate_repo=git_repo),
        },
    )

    assert result.status == "executed"
    assert result.execution_allowed is True
    assert result.helper_result is not None
    assert result.helper_result["completion_allowed"] is True
    assert result.helper_result["candidate_complete"] is True
    assert result.helper_result["blocked_reason"] is None
    assert result.helper_result["git_gate"]["material_change_status"] == "material_changes_detected"
    assert result.helper_result["reviewer_packet"]["present"] is True
    assert result.helper_result["reviewer_packet"]["packet_status"] == "ready_for_review"
    assert [item["role_id"] for item in result.helper_result["subagent_runs"]] == ["engineer", "reviewer"]
    assert result.helper_result["subagent_runs"][0]["actual_provider"] == "openrouter"
    assert result.helper_result["subagent_runs"][1]["actual_provider"] == "openai-codex"
    assert result.helper_result["subagent_runs"][0]["actual_model"] == "xiaomi/mimo-v2.5-pro"
    assert result.helper_result["subagent_runs"][1]["actual_model"] == "gpt-5.5"
    assert result.helper_result["usage_summary"]["subagent_count"] == 2
    assert result.helper_result["usage_summary"]["total_tokens"] == 21
    encoded = __import__("json").dumps(result.helper_result, sort_keys=True)
    assert "Implement controller helper selection with reviewer approval" not in encoded
    assert "created by engineer" not in encoded


def test_registered_helper_invalid_controlled_context_fails_closed_with_structured_reason(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    repo_root = _copy_spec_tree(tmp_path)

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        allow_test_execution=True,
        allow_registered_helper_selection=True,
        helper_execution_context={
            "runtime_factory": RuntimeFactory(repo_root=repo_root),
            "runner": SubagentRunner(executor=lambda *_args, **_kwargs: {}),
            "user_message": "Implement controller helper selection with malformed runtime context",
            "controlled_runtime_context": {},
        },
    )

    assert result.status == "blocked"
    assert result.execution_allowed is True
    assert result.blocked_reason == "invalid_controlled_runtime_context"
    assert result.actual_execution_invoked is True
    assert result.helper_result_status == "blocked"
    assert result.helper_error is None
    assert result.helper_result == {
        "status": "blocked",
        "blocked_reason": "invalid_controlled_runtime_context",
        "completion_allowed": False,
        "candidate_complete": False,
        "user_action_required": True,
        "subagent_runs": [],
        "usage_summary": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "token_sources": [],
            "cache_sources": [],
            "planned_subagent_count": 0,
            "executed_subagent_count": 0,
            "subagent_run_instance_count": 0,
            "execution_round_count": 0,
            "subagent_count": 0,
            "models_used": [],
            "providers_used": [],
        },
    }


def test_missing_pipeline_context_is_fail_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for()
    helper_calls: list[str] = []
    snapshot = type(
        "AnonymousSnapshot",
        (),
        {"pipeline_id": None, "pipeline_session_id": snapshot.pipeline_session_id, "planned_steps": []},
    )()

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "missing_pipeline_selection"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_ineligible_pipeline_context_is_fail_closed() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for(pipeline_id="default_conversation_pipeline", router_status="no_specialized_pipeline")
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(
            allow_pipelines=["engineering_review_pipeline", "default_conversation_pipeline"],
            allowed_subagents=["general_operator"],
        ),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "ineligible_pipeline_execution_context"
    assert result.actual_execution_invoked is False
    assert helper_calls == []


def test_controlled_manual_trigger_does_not_make_default_pipeline_eligible() -> None:
    module = importlib.import_module("hermes_cli.pipeline_execution_controller")
    session, snapshot = _snapshot_for(pipeline_id="default_conversation_pipeline", router_status="no_specialized_pipeline")
    helper_calls: list[str] = []

    def _helper(**_kwargs):
        helper_calls.append("called")

    result = module.evaluate_pipeline_execution_controller(
        config=_config(mode="controlled_manual", allow_pipelines=["engineering_review_pipeline", "default_conversation_pipeline"]),
        session=session,
        state_snapshot=snapshot,
        execution_helper=_helper,
        allow_test_execution=True,
        helper_execution_context={"user_message": "HERMES CONTROLLED PIPELINE VALIDATION - dry-run"},
    )

    assert result.status == "blocked"
    assert result.execution_allowed is False
    assert result.blocked_reason == "ineligible_pipeline_execution_context"
    assert result.actual_execution_invoked is False
    assert helper_calls == []
