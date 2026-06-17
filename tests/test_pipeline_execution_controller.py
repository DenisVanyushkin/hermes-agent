from __future__ import annotations

import importlib
from pathlib import Path
import shutil

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


def _runtime_context(tmp_path: Path) -> dict[str, object]:
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
        "user_message": "Implement controller helper selection",
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
