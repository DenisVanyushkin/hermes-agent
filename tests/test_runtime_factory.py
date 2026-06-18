from __future__ import annotations

import copy
import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, PipelineStepPlan, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _build_factory(repo_root: Path):
    from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeFactory

    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    factory = RuntimeFactory(repo_root=repo_root)
    return RuntimeBuildRequest, factory, loaded_specs


def _engineering_session():
    decision = RouterDecision(
        pipeline_session_id="pipe-runtime-contract",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.95,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-runtime-contract",
            user_message="implement code",
            created_at="2026-06-16T00:00:00+00:00",
        )
    )


def test_builds_planned_runtime_for_general_operator(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="general_operator",
            pipeline_session_id="pipe-1",
            invocation_id="inv-1",
        )
    )

    assert result.subagent_id == "general_operator"
    assert result.actual_runtime_status == "ready_to_construct"
    assert result.selection.selected_provider == "openai-codex"
    assert result.selection.selected_model == "gpt-5.4-mini"
    assert result.selection.selected_model_class == "general"
    assert result.constructor_provider == result.selection.selected_provider
    assert result.constructor_model == result.selection.selected_model


@pytest.mark.parametrize(
    ("step_kind", "subagent_id", "provider", "model", "can_mutate"),
    [
        ("engineer", "hermes_engineer_core", "openrouter", "xiaomi/mimo-v2.5-pro", True),
        ("reviewer", "hermes_code_reviewer", "openai-codex", "gpt-5.5", False),
    ],
)
def test_build_runtime_factory_plan_is_metadata_only_contract(
    tmp_path: Path,
    step_kind: str,
    subagent_id: str,
    provider: str,
    model: str,
    can_mutate: bool,
) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == step_kind)

    from hermes_cli.runtime_factory import RuntimeFactoryStatus, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs[subagent_id],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    assert plan.status == RuntimeFactoryStatus.PLAN_ONLY
    assert plan.pipeline_session_id == session.pipeline_session_id
    assert plan.trace_id == session.trace_id
    assert plan.pipeline_id == "engineering_review_pipeline"
    assert plan.subagent_id == subagent_id
    assert plan.role_id == step_kind
    assert plan.provider == provider
    assert plan.model == model
    assert plan.execution_mode == "observe_plan_only"
    assert plan.dry_run is True
    assert plan.system_prompt_source_id == f"prompt:{subagent_id}"
    assert plan.tool_policy.forbidden
    assert plan.environment_policy.can_mutate_files is can_mutate
    assert plan.environment_policy.secrets_env_access == "not_granted"

    payload = plan.to_safe_dict()
    assert payload["status"] == "plan_only"
    assert payload["provider"] == provider
    assert payload["model"] == model
    assert payload["tool_policy"]["forbidden"]
    assert payload["environment_policy"]["can_mutate_files"] is can_mutate
    assert payload["logging_hooks_policy"]["provider_model_selection"] is True
    assert payload["token_accounting_policy"]["token_usage"] is True
    assert "client" not in json.dumps(payload, sort_keys=True)
    for forbidden_key in (
        "selected_provider",
        "selected_model",
        "constructor_provider",
        "constructor_model",
        "runtime_bridge_allowed",
        "runtime_bridge_enabled",
    ):
        assert forbidden_key not in json.dumps(payload, sort_keys=True)


def test_runtime_factory_plan_unknown_subagent_fails_closed(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()

    from hermes_cli.runtime_factory import RuntimeFactoryStatus, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=PipelineStepPlan(step_kind="engineer", subagent_id="missing_subagent", condition=None),
        subagent_spec=None,
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    assert plan.status == RuntimeFactoryStatus.BLOCKED
    assert plan.errors
    assert plan.errors[0].code == "unknown_subagent"
    assert plan.to_safe_dict()["errors"][0]["field_path"] == "subagent_id"


@pytest.mark.parametrize(
    ("missing_field", "field_path"),
    [
        ("provider", "models.default.provider"),
        ("model", "models.default.model"),
    ],
)
def test_runtime_factory_plan_missing_provider_or_model_fails_closed(
    tmp_path: Path,
    missing_field: str,
    field_path: str,
) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    spec = copy.deepcopy(loaded_specs.subagent_specs["hermes_engineer_core"])
    del spec["models"]["default"][missing_field]

    from hermes_cli.runtime_factory import RuntimeFactoryStatus, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=session.planned_steps[0],
        subagent_spec=spec,
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    assert plan.status == RuntimeFactoryStatus.BLOCKED
    assert any(error.field_path == field_path for error in plan.errors)


@pytest.mark.parametrize(
    ("subagent_id", "provider", "model"),
    [
        ("hermes_engineer_core", "openrouter", "xiaomi/mimo-v2.5-pro"),
        ("hermes_code_reviewer", "openai-codex", "gpt-5.5"),
    ],
)
def test_builds_planned_runtime_for_specialized_subagents(
    tmp_path: Path,
    subagent_id: str,
    provider: str,
    model: str,
) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id=subagent_id,
            pipeline_session_id="pipe-2",
        )
    )

    assert result.actual_runtime_status == "ready_to_construct"
    assert result.selection.selected_provider == provider
    assert result.selection.selected_model == model
    assert result.constructor_provider == provider
    assert result.constructor_model == model


def test_records_session_default_separately_and_marks_mismatch(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-3",
            current_session_provider="openai-codex",
            current_session_model="gpt-5.4-mini",
        )
    )

    assert result.current_session_provider == "openai-codex"
    assert result.current_session_model == "gpt-5.4-mini"
    assert result.selection.selected_provider == "openrouter"
    assert result.selected_runtime_differs_from_session_default is True
    assert result.to_safe_dict()["session_default_mismatch"] is True


def test_records_prompt_artifact_without_full_prompt_text(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-4",
        )
    )

    assert result.prompt.path == "prompts/subagents/hermes_engineer_core.md"
    assert result.prompt.exists is True
    assert result.prompt.sha256
    assert result.prompt.artifact_id == result.prompt.sha256
    assert result.prompt.size_bytes > 0
    assert result.prompt.full_text_loaded is False
    assert not hasattr(result.prompt, "text")


def test_safe_dict_omits_full_prompt_text_and_sensitive_runtime_fields(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-safe",
        )
    )

    payload = result.to_safe_dict()

    assert payload["constructor_provider"] == "openrouter"
    assert payload["constructor_model"] == "xiaomi/mimo-v2.5-pro"
    assert "prompt_text" not in payload
    assert payload["prompt"]["full_text_loaded"] is False
    assert payload["constructor_base_url"] is None


def test_to_aiagent_kwargs_returns_selected_runtime_inputs(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_code_reviewer",
            pipeline_session_id="pipe-kwargs",
        )
    )

    kwargs = result.to_aiagent_kwargs()

    assert kwargs["provider"] == result.selection.selected_provider
    assert kwargs["model"] == result.selection.selected_model
    assert kwargs["api_mode"] == result.constructor_api_mode


def test_builds_tool_permission_plan(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-5",
        )
    )

    assert "patch" in result.tool_permission_plan.write
    assert "terminal" in result.tool_permission_plan.execute
    assert "git_commit" in result.tool_permission_plan.gated
    assert "credential_exfiltration" in result.tool_permission_plan.forbidden
    assert result.tool_permission_plan.unknown_permissions == []


def test_malformed_tool_permission_bucket_returns_blocked_result(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)
    mutated_specs = copy.deepcopy(loaded_specs)
    mutated_specs.subagent_specs["hermes_engineer_core"]["tools"]["write"] = {
        "unexpected": "shape"
    }

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=mutated_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-5b",
        )
    )

    assert result.actual_runtime_status == "blocked"
    assert any(error.code == "malformed_tool_permissions" for error in result.errors)


def test_unknown_subagent_returns_blocked_result(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="missing_subagent",
            pipeline_session_id="pipe-6",
        )
    )

    assert result.actual_runtime_status == "blocked"
    assert result.selection is None
    assert result.errors
    assert result.errors[0].field_path == "subagent_id"


def test_missing_prompt_path_returns_blocked_result(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)
    prompt_path = repo_root / "prompts/subagents/hermes_engineer_core.md"
    prompt_path.unlink()

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-7",
        )
    )

    assert result.actual_runtime_status == "blocked"
    assert any(error.field_path == "system_prompt.path" for error in result.errors)


def test_missing_provider_model_returns_blocked_result(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    subagent_path = repo_root / "config/subagents/general_operator.yaml"
    subagent = _load_yaml(subagent_path)
    del subagent["models"]["default"]["provider"]
    _write_yaml(subagent_path, subagent)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="general_operator",
            pipeline_session_id="pipe-8",
        )
    )

    assert result.actual_runtime_status == "blocked"
    assert any(error.field_path == "models.default.provider" for error in result.errors)


def test_requested_escalation_model_class_must_be_allowed(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    allowed = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-9",
            requested_model_class="senior_coding",
        )
    )
    blocked = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="general_operator",
            pipeline_session_id="pipe-10",
            requested_model_class="senior_coding",
        )
    )

    assert allowed.actual_runtime_status == "ready_to_construct"
    assert allowed.selection.selected_model_class == "senior_coding"
    assert allowed.selection.selected_model == "gpt-5.5"
    assert blocked.actual_runtime_status == "blocked"
    assert any("not allowed" in error.message for error in blocked.errors)
    assert any("general_operator" in error.message for error in blocked.errors)


def test_importing_runtime_factory_stays_import_light() -> None:
    before = set(sys.modules)
    for name in ("hermes_cli.runtime_factory", "gateway.run", "tools.tool_executor", "agent.conversation_loop", "run_agent"):
        sys.modules.pop(name, None)

    module = importlib.import_module("hermes_cli.runtime_factory")

    assert hasattr(module, "RuntimeFactory")
    imported = set(sys.modules) - before
    assert "gateway.run" not in imported
    assert "tools.tool_executor" not in imported
    assert "agent.conversation_loop" not in imported
    assert "run_agent" not in imported


def test_blocked_result_does_not_produce_aiagent_kwargs(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    subagent_path = repo_root / "config/subagents/general_operator.yaml"
    subagent = _load_yaml(subagent_path)
    del subagent["models"]["default"]["provider"]
    _write_yaml(subagent_path, subagent)
    RuntimeBuildRequest, factory, loaded_specs = _build_factory(repo_root)

    result = factory.build(
        RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="general_operator",
            pipeline_session_id="pipe-blocked-kwargs",
        )
    )

    assert result.actual_runtime_status == "blocked"
    with pytest.raises(RuntimeError):
        result.to_aiagent_kwargs()


def test_build_controlled_runtime_preserves_runtime_contract_metadata(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "engineer")

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs["hermes_engineer_core"],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "ok"}},
        working_directory=str(repo_root),
    )

    assert runtime.runtime_status == "ready"
    assert runtime.subagent_id == "hermes_engineer_core"
    assert runtime.role_id == "engineer"
    assert runtime.provider == "openrouter"
    assert runtime.model == "xiaomi/mimo-v2.5-pro"
    assert runtime.system_prompt_path == "prompts/subagents/hermes_engineer_core.md"
    assert runtime.tool_policy.write == ["patch", "write_file"]
    assert runtime.environment_policy.can_mutate_files is True
    assert runtime.environment_policy.secrets_env_access == "not_granted"
    assert runtime.working_directory == str(repo_root)
    assert runtime.invocation_client is not None

    payload = runtime.to_safe_dict()
    assert payload["provider"] == "openrouter"
    assert payload["model"] == "xiaomi/mimo-v2.5-pro"
    assert payload["working_directory"] == str(repo_root)
    assert "invocation_client" not in json.dumps(payload, sort_keys=True)
    assert payload["environment_policy"]["secrets_env_access"] == "not_granted"


def test_build_controlled_runtime_from_blocked_plan_fails_closed(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "engineer")
    spec = copy.deepcopy(loaded_specs.subagent_specs["hermes_engineer_core"])
    del spec["models"]["default"]["provider"]

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=spec,
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )
    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "unexpected"}},
        working_directory=str(repo_root),
    )

    assert plan.status.value == "blocked"
    assert runtime.runtime_status == "blocked"
    assert runtime.provider is None
    assert runtime.model is None


def test_build_controlled_runtime_real_provider_requires_explicit_gate(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "engineer")

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs["hermes_engineer_core"],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    factory_calls = {"count": 0}

    def _factory(_runtime):
        factory_calls["count"] += 1
        return lambda _request: {"structured_output": {"summary": "unexpected"}}

    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "fake ok"}},
        request_real_provider_execution=True,
        allow_real_provider_execution=False,
        allowed_real_providers=("openrouter",),
        allowed_real_models=("xiaomi/mimo-v2.5-pro",),
        real_provider_client_factory=_factory,
    )

    assert runtime.runtime_status == "blocked"
    assert runtime.runtime_mode == "blocked"
    assert runtime.real_provider_allowed is False
    assert runtime.provider_policy_status == "blocked"
    assert factory_calls["count"] == 0
    assert any(error.code == "real_provider_execution_disabled" for error in runtime.errors)


def test_build_controlled_runtime_real_provider_ready_when_gate_and_allowlist_match(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "reviewer")

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs["hermes_code_reviewer"],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "fake ok"}},
        request_real_provider_execution=True,
        allow_real_provider_execution=True,
        allowed_real_providers=("openai-codex",),
        allowed_real_models=("gpt-5.5",),
        real_provider_client_factory=lambda _runtime: (
            lambda _request: {
                "provider": "openai-codex",
                "model": "gpt-5.5",
                "structured_output": {"summary": "review ok", "status": "succeeded"},
            }
        ),
    )

    assert runtime.runtime_status == "ready"
    assert runtime.runtime_mode == "real_provider"
    assert runtime.real_provider_allowed is True
    assert runtime.provider_policy_status == "allowed"
    assert runtime.provider == "openai-codex"
    assert runtime.model == "gpt-5.5"


def test_build_controlled_runtime_missing_fake_invocation_client_has_diagnostic_error(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "engineer")

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs["hermes_engineer_core"],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )
    runtime = build_controlled_runtime(plan=plan, invocation_client=None)

    assert runtime.runtime_status == "blocked"
    assert runtime.runtime_mode == "blocked"
    assert any(error.code == "controlled_runtime_invocation_client_missing" for error in runtime.errors)


def test_build_controlled_runtime_real_provider_role_policy_blocks_reviewer_without_client_call(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "reviewer")

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs["hermes_code_reviewer"],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    factory_calls = {"count": 0}

    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "fake ok"}},
        request_real_provider_execution=True,
        allow_real_provider_execution=True,
        allowed_real_providers=("openrouter", "openai-codex"),
        allowed_real_models=("xiaomi/mimo-v2.5-pro", "gpt-5.5"),
        allowed_real_providers_by_role={"engineer": ("openrouter",)},
        allowed_real_models_by_role={"engineer": ("xiaomi/mimo-v2.5-pro",)},
        real_provider_client_factory=lambda _runtime: factory_calls.__setitem__("count", factory_calls["count"] + 1),
    )

    assert runtime.runtime_status == "blocked"
    assert runtime.real_provider_allowed is False
    assert runtime.provider_policy_status == "blocked"
    assert factory_calls["count"] == 0
    assert any(error.code == "real_provider_role_policy_missing" for error in runtime.errors)


def test_build_controlled_runtime_real_provider_rejects_non_callable_factory(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = load_pipeline_specs(repo_root=repo_root)
    session = _engineering_session()
    step = next(item for item in session.planned_steps if item.step_kind == "engineer")

    from hermes_cli.runtime_factory import build_controlled_runtime, build_runtime_factory_plan

    plan = build_runtime_factory_plan(
        session=session,
        planned_step=step,
        subagent_spec=loaded_specs.subagent_specs["hermes_engineer_core"],
        config=loaded_specs.pipeline_specs["engineering_review_pipeline"],
    )

    runtime = build_controlled_runtime(
        plan=plan,
        invocation_client=lambda *_args, **_kwargs: {"structured_output": {"summary": "fake ok"}},
        request_real_provider_execution=True,
        allow_real_provider_execution=True,
        allowed_real_providers=("openrouter",),
        allowed_real_models=("xiaomi/mimo-v2.5-pro",),
        allowed_real_providers_by_role={"engineer": ("openrouter",)},
        allowed_real_models_by_role={"engineer": ("xiaomi/mimo-v2.5-pro",)},
        real_provider_client_factory="not-callable",
    )

    assert runtime.runtime_status == "blocked"
    assert any(error.code == "real_provider_client_factory_invalid" for error in runtime.errors)
