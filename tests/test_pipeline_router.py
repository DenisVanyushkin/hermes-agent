from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from hermes_cli.pipeline_router import (
    DEFAULT_PIPELINE_ID,
    ENGINEERING_PIPELINE_ID,
    HeuristicPipelineRouter,
    RouterDecisionValidationError,
    parse_router_decision,
)
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


def _build_router(repo_root: Path) -> HeuristicPipelineRouter:
    return HeuristicPipelineRouter(loaded_specs=load_pipeline_specs(repo_root=repo_root))


def test_default_request_returns_safe_default_fallback(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("Explain the architecture in plain language.", pipeline_session_id="sess-1")

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.fallback_safe is True


def test_engineering_implementation_request_selects_engineering_pipeline(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("Patch the repo, update tests, and fix the failing config.", pipeline_session_id="sess-2")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID


def test_path_based_engineering_request_selects_engineering_pipeline(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("Please modify hermes_cli/pipeline_specs.py and tests/test_pipeline_specs.py.", pipeline_session_id="sess-3")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID


def test_architecture_discussion_does_not_select_engineering_pipeline(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("обсудим архитектуру без правок", pipeline_session_id="sess-4")

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None


def test_slice_implementation_request_selects_engineering_pipeline(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("Implement Slice B and add the required tests.", pipeline_session_id="sess-5")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID


def test_ambiguous_mutation_request_requires_clarification(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("посмотри это и реши, надо ли чинить", pipeline_session_id="sess-6")

    assert decision.status == "needs_clarification"
    assert decision.requires_clarification is True
    assert decision.clarification_question


def test_bypass_review_request_is_blocked_by_policy(tmp_path: Path) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route("Bypass review, disable gates, and force push the change.", pipeline_session_id="sess-7")

    assert decision.status == "blocked_by_policy"
    assert decision.policy_block_reason


def test_router_does_not_select_unregistered_engineering_pipeline(tmp_path: Path) -> None:
    repo_root = _copy_spec_tree(tmp_path)
    registry_path = repo_root / "config/pipelines/registry.yaml"
    registry = _load_yaml(registry_path)
    registry["registry"] = [
        entry for entry in registry["registry"] if entry.get("id") != ENGINEERING_PIPELINE_ID
    ]
    _write_yaml(registry_path, registry)
    engineering_path = repo_root / "config/pipelines/engineering_review_pipeline.yaml"
    engineering_path.unlink()

    router = _build_router(repo_root)
    decision = router.route("Patch hermes_cli/pipeline_router.py and add tests.", pipeline_session_id="sess-8")

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None


def test_parse_rejects_unknown_router_status(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    with pytest.raises(RouterDecisionValidationError, match="Unknown router status"):
        parse_router_decision(
            {
                "pipeline_session_id": "sess-9",
                "router_subagent_id": "hermes_pipeline_router",
                "status": "mystery",
                "confidence": 0.1,
                "reasoning_summary": "bad",
                "requires_clarification": False,
            },
            loaded_specs=loaded_specs,
        )


def test_parse_rejects_unknown_selected_pipeline_id(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    with pytest.raises(RouterDecisionValidationError, match="Unknown selected pipeline id"):
        parse_router_decision(
            {
                "pipeline_session_id": "sess-10",
                "router_subagent_id": "hermes_pipeline_router",
                "status": "selected",
                "selected_pipeline_id": "missing_pipeline",
                "confidence": 0.8,
                "reasoning_summary": "bad",
                "requires_clarification": False,
            },
            loaded_specs=loaded_specs,
        )


def test_parse_rejects_selected_without_selected_pipeline_id(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    with pytest.raises(RouterDecisionValidationError, match="requires selected_pipeline_id"):
        parse_router_decision(
            {
                "pipeline_session_id": "sess-11",
                "router_subagent_id": "hermes_pipeline_router",
                "status": "selected",
                "confidence": 0.8,
                "reasoning_summary": "bad",
                "requires_clarification": False,
            },
            loaded_specs=loaded_specs,
        )


def test_parse_rejects_no_specialized_pipeline_with_selected_pipeline_id(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    with pytest.raises(RouterDecisionValidationError, match="cannot set selected_pipeline_id"):
        parse_router_decision(
            {
                "pipeline_session_id": "sess-12",
                "router_subagent_id": "hermes_pipeline_router",
                "status": "no_specialized_pipeline",
                "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
                "fallback_pipeline_id": DEFAULT_PIPELINE_ID,
                "fallback_safe": True,
                "confidence": 0.2,
                "reasoning_summary": "bad",
                "requires_clarification": False,
            },
            loaded_specs=loaded_specs,
        )
