from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from hermes_cli.pipeline_specs import PipelineSpecValidationError, load_pipeline_specs


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


def _assert_validation_error(repo_root: Path, needle: str) -> list:
    with pytest.raises(PipelineSpecValidationError) as exc_info:
        load_pipeline_specs(repo_root=repo_root)
    messages = [error.message for error in exc_info.value.errors]
    assert any(needle in message for message in messages), messages
    return exc_info.value.errors


def test_valid_committed_specs_load_successfully() -> None:
    loaded = load_pipeline_specs(repo_root=REPO_ROOT)

    assert "default_conversation_pipeline" in loaded.pipeline_specs
    assert "engineering_review_pipeline" in loaded.pipeline_specs
    assert "general_operator" in loaded.subagent_specs
    assert "hermes_pipeline_router" in loaded.subagent_specs


def test_missing_registry_config_path_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    registry_path = repo_root / "config/pipelines/registry.yaml"
    registry = _load_yaml(registry_path)
    registry["registry"][0]["config_path"] = "config/pipelines/missing_pipeline.yaml"
    _write_yaml(registry_path, registry)

    errors = _assert_validation_error(repo_root, "Referenced pipeline spec does not exist")
    assert any(error.file_path == "config/pipelines/missing_pipeline.yaml" for error in errors)


def test_missing_subagent_reference_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    pipeline_path = repo_root / "config/pipelines/engineering_review_pipeline.yaml"
    pipeline = _load_yaml(pipeline_path)
    pipeline["subagents"]["reviewer"] = "missing_reviewer"
    _write_yaml(pipeline_path, pipeline)

    _assert_validation_error(repo_root, "Referenced subagent 'missing_reviewer' does not exist")


def test_missing_prompt_path_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    subagent_path = repo_root / "config/subagents/hermes_engineer_core.yaml"
    subagent = _load_yaml(subagent_path)
    subagent["system_prompt"]["path"] = "prompts/subagents/missing_prompt.md"
    _write_yaml(subagent_path, subagent)

    errors = _assert_validation_error(repo_root, "Referenced prompt file does not exist")
    assert any(error.file_path == "prompts/subagents/missing_prompt.md" for error in errors)


def test_duplicate_pipeline_id_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    registry_path = repo_root / "config/pipelines/registry.yaml"
    registry = _load_yaml(registry_path)
    duplicate_entry = dict(registry["registry"][0])
    duplicate_entry["config_path"] = "config/pipelines/engineering_review_pipeline.yaml"
    registry["registry"].append(duplicate_entry)
    _write_yaml(registry_path, registry)

    _assert_validation_error(repo_root, "Duplicate pipeline id 'default_conversation_pipeline'")


def test_duplicate_subagent_id_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    duplicate_path = repo_root / "config/subagents/hermes_pipeline_router.yaml"
    duplicate = _load_yaml(duplicate_path)
    duplicate["id"] = "general_operator"
    _write_yaml(duplicate_path, duplicate)

    _assert_validation_error(repo_root, "Duplicate subagent id 'general_operator'")


def test_peer_communication_without_decisive_authority_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    pipeline_path = repo_root / "config/pipelines/engineering_review_pipeline.yaml"
    pipeline = _load_yaml(pipeline_path)
    pipeline["disagreement_policy"]["decisive_subagent"] = None
    pipeline["disagreement_policy"]["arbitrator_subagent"] = None
    _write_yaml(pipeline_path, pipeline)

    _assert_validation_error(repo_root, "must define decisive_subagent or arbitrator_subagent")


def test_engineering_pipeline_without_required_loop_limits_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    pipeline_path = repo_root / "config/pipelines/engineering_review_pipeline.yaml"
    pipeline = _load_yaml(pipeline_path)
    del pipeline["loop_policy"]["max_review_iterations"]
    _write_yaml(pipeline_path, pipeline)

    _assert_validation_error(repo_root, "Engineering/cyclic pipeline must define loop limits")


def test_reviewer_escalation_to_non_allowed_class_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    pipeline_path = repo_root / "config/pipelines/engineering_review_pipeline.yaml"
    pipeline = _load_yaml(pipeline_path)
    pipeline["model_escalation_policy"]["rules"][1]["escalate_to_model_class"] = "senior_coding"
    _write_yaml(pipeline_path, pipeline)

    _assert_validation_error(repo_root, "is not allowed for subagent 'hermes_code_reviewer'")


def test_default_pipeline_without_general_operator_fails(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    pipeline_path = repo_root / "config/pipelines/default_conversation_pipeline.yaml"
    pipeline = _load_yaml(pipeline_path)
    pipeline["subagents"]["primary"] = "hermes_engineer_core"
    _write_yaml(pipeline_path, pipeline)

    _assert_validation_error(repo_root, "Default pipeline must reference general_operator as primary")
