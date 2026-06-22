from __future__ import annotations

from pathlib import Path
import importlib
import shutil
from typing import Any


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def test_runtime_factory_exposes_explicit_engineering_fallback_metadata(tmp_path: Path) -> None:
    specs_module = importlib.import_module("hermes_cli.pipeline_specs")
    runtime_factory_module = importlib.import_module("hermes_cli.runtime_factory")
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = specs_module.load_pipeline_specs(repo_root=repo_root)
    factory = runtime_factory_module.RuntimeFactory(repo_root=repo_root)

    result = factory.build(
        runtime_factory_module.RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-fallback-1",
            invocation_id="inv-fallback-1",
        )
    )

    assert result.constructor_provider == "openrouter"
    assert result.constructor_model == "xiaomi/mimo-v2.5-pro"
    assert result.fallback_policy is not None
    assert result.fallback_policy.mode == "explicit_model"
    assert result.fallback_policy.reason == "preserve engineering capability without using a global free fallback"
    assert result.fallback_policy.provider == "openai-codex"
    assert result.fallback_policy.model == "gpt-5.4"
    assert result.fallback_policy.max_tokens == 16384


def test_runtime_factory_to_aiagent_kwargs_preserves_primary_and_explicit_fallback(tmp_path: Path) -> None:
    specs_module = importlib.import_module("hermes_cli.pipeline_specs")
    runtime_factory_module = importlib.import_module("hermes_cli.runtime_factory")
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = specs_module.load_pipeline_specs(repo_root=repo_root)
    factory = runtime_factory_module.RuntimeFactory(repo_root=repo_root)

    result = factory.build(
        runtime_factory_module.RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-fallback-2",
            invocation_id="inv-fallback-2",
        )
    )

    kwargs = result.to_aiagent_kwargs()

    assert kwargs["provider"] == "openrouter"
    assert kwargs["model"] == "xiaomi/mimo-v2.5-pro"
    assert kwargs["fallback_model"] == {
        "provider": "openai-codex",
        "model": "gpt-5.4",
    }
    assert "fallback_provider" not in kwargs
    assert "fallback_max_tokens" not in kwargs


class _StrictAgentFactory:
    def __call__(
        self,
        *,
        provider: str,
        model: str,
        fallback_model: dict[str, Any] | None = None,
        api_mode: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": provider,
            "model": model,
            "fallback_model": fallback_model,
            "api_mode": api_mode,
            "base_url": base_url,
        }


def test_runtime_factory_kwargs_fit_strict_aiagent_subset_signature(tmp_path: Path) -> None:
    specs_module = importlib.import_module("hermes_cli.pipeline_specs")
    runtime_factory_module = importlib.import_module("hermes_cli.runtime_factory")
    repo_root = _copy_spec_tree(tmp_path)
    loaded_specs = specs_module.load_pipeline_specs(repo_root=repo_root)
    factory = runtime_factory_module.RuntimeFactory(repo_root=repo_root)

    result = factory.build(
        runtime_factory_module.RuntimeBuildRequest(
            loaded_specs=loaded_specs,
            subagent_id="hermes_engineer_core",
            pipeline_session_id="pipe-fallback-3",
            invocation_id="inv-fallback-3",
        )
    )

    kwargs = result.to_aiagent_kwargs()
    constructed = _StrictAgentFactory()(**kwargs)

    assert constructed["provider"] == "openrouter"
    assert constructed["model"] == "xiaomi/mimo-v2.5-pro"
    assert constructed["fallback_model"] == {
        "provider": "openai-codex",
        "model": "gpt-5.4",
    }
