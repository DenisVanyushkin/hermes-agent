from __future__ import annotations

from pathlib import Path
import importlib
import shutil
from typing import Any


# Два разных смысла, которые раньше были одной константой.
# SPEC_ROOT -- дерево, в котором лежит сам тест: config/ и prompts/ берутся
# отсюда, иначе прогон в git-worktree копирует боевые спеки и проверяет чужое
# дерево вместо правок рядом с собой.
SPEC_ROOT = Path(__file__).resolve().parents[1]
# REPO_ROOT -- дерево, у которого есть venv. Worktree его не наследует, поэтому
# для реальных pytest-прогонов берём локальный venv, если он есть, иначе
# основной чекаут.
REPO_ROOT = SPEC_ROOT if (SPEC_ROOT / "venv").exists() else Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(SPEC_ROOT / "config", repo_root / "config")
    shutil.copytree(SPEC_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _engineer_models(repo_root: Path) -> dict[str, Any]:
    """Что спека инженера объявляет на самом деле.

    Здесь стояли литералы. 2026-07-04 коммит 2d23eb25ad перевёл спеку с
    openrouter/xiaomi/mimo-v2.5-pro на openai-codex/gpt-5.4, тест не тронули --
    и три проверки в этом файле падали 25 дней. Пока они лежали, разъезд
    моделей субагентов (5.4 в спеках, 5.5 у ревьюера и аудитора, 5.6 в
    политике) стало нечем заметить: это единственное место, которое на него
    ругалось бы.

    Проверяемое утверждение -- инвариант ФАБРИКИ: она строит ровно то, что
    объявлено в спеке, и ничего не подставляет от себя. Каким моделям там быть
    -- отдельное решение, и пинить его литералами отсюда значит ломать тест на
    каждой смене модели, что и произошло.
    """
    import yaml

    spec_path = repo_root / "config" / "subagents" / "hermes_engineer_core.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    return spec["models"]


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

    models = _engineer_models(repo_root)
    default = models["default"]
    fallback = models["fallback"]

    assert result.constructor_provider == default["provider"]
    assert result.constructor_model == default["model"]
    assert result.fallback_policy is not None
    assert result.fallback_policy.mode == fallback["mode"]
    assert result.fallback_policy.reason == fallback["reason"]
    assert result.fallback_policy.provider == fallback["provider"]
    assert result.fallback_policy.model == fallback["model"]
    assert result.fallback_policy.max_tokens == fallback["max_tokens"]


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

    models = _engineer_models(repo_root)
    default = models["default"]
    fallback = models["fallback"]

    assert kwargs["provider"] == default["provider"]
    assert kwargs["model"] == default["model"]
    assert kwargs["fallback_model"] == {
        "provider": fallback["provider"],
        "model": fallback["model"],
    }
    # Фолбэк едет одним словарём, а не россыпью ключей: строгая подпись
    # конструктора агента других не принимает.
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

    models = _engineer_models(repo_root)
    default = models["default"]
    fallback = models["fallback"]

    assert constructed["provider"] == default["provider"]
    assert constructed["model"] == default["model"]
    assert constructed["fallback_model"] == {
        "provider": fallback["provider"],
        "model": fallback["model"],
    }
