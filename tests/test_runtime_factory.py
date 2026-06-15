from __future__ import annotations

import copy
import importlib
import shutil
import sys
from pathlib import Path

import pytest
import yaml

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
    assert result.actual_runtime_status == "planned_only"
    assert result.selection.selected_provider == "openai-codex"
    assert result.selection.selected_model == "gpt-5.4-mini"
    assert result.selection.selected_model_class == "general"


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

    assert result.actual_runtime_status == "planned_only"
    assert result.selection.selected_provider == provider
    assert result.selection.selected_model == model


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
    assert result.prompt.size_bytes > 0
    assert not hasattr(result.prompt, "text")


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

    assert allowed.actual_runtime_status == "planned_only"
    assert allowed.selection.selected_model_class == "senior_coding"
    assert allowed.selection.selected_model == "gpt-5.5"
    assert blocked.actual_runtime_status == "blocked"
    assert any("not allowed" in error.message for error in blocked.errors)


def test_importing_runtime_factory_stays_import_light() -> None:
    before = set(sys.modules)
    sys.modules.pop("hermes_cli.runtime_factory", None)

    module = importlib.import_module("hermes_cli.runtime_factory")

    assert hasattr(module, "RuntimeFactory")
    imported = set(sys.modules) - before
    assert "agent.conversation_loop" not in imported
    assert "gateway.run" not in imported
    assert "tools.tool_executor" not in imported
