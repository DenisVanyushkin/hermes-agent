from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from hermes_cli.pipeline_router import (
    DEFAULT_PIPELINE_ID,
    ENGINEERING_PIPELINE_ID,
    DEFAULT_ROUTER_SUBAGENT_ID,
    HeuristicPipelineRouter,
    LlmPipelineRouter,
    RouterDecisionValidationError,
    build_pipeline_router,
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


@pytest.mark.parametrize(
    "prompt",
    [
        "поправь тесты в Hermes",
        "измени код и добавь regression test",
        "нужно исправить баг в gateway/run.py",
        "добавь проверку в pipeline_execution_controller",
        "сделай ревью изменений в hermes_cli/orchestrator.py",
        "почему pytest падает и как починить",
        "обнови config/pipelines/engineering_review_pipeline.yaml",
        "исправь баг в Hermes router и обнови тесты",
    ],
)
def test_russian_engineering_requests_select_engineering_pipeline(tmp_path: Path, prompt: str) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route(prompt, pipeline_session_id="sess-russian-engineering")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID


@pytest.mark.parametrize(
    "prompt",
    [
        "обычный вопрос: что дальше?",
        "подготовь письмо рекрутеру",
        "оцени вакансию",
        "что такое nootropics?",
        "расскажи про рынок труда",
    ],
)
def test_non_engineering_requests_stay_on_default_fallback(tmp_path: Path, prompt: str) -> None:
    router = _build_router(_copy_spec_tree(tmp_path))

    decision = router.route(prompt, pipeline_session_id="sess-non-engineering")

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.fallback_safe is True


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


def test_llm_router_selects_engineering_pipeline_for_russian_mutation_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))
    captured: dict[str, object] = {}

    def _fake_llm_call(*, provider: str, model: str, timeout_seconds: float, messages: list[dict[str, str]]) -> dict:
        captured["provider"] = provider
        captured["model"] = model
        captured["timeout_seconds"] = timeout_seconds
        captured["messages"] = messages
        return {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": 0.97,
            "reasoning_summary": "The prompt asks for a code patch and tests in Russian.",
            "requires_clarification": False,
            "alternatives": [
                {
                    "pipeline_id": DEFAULT_PIPELINE_ID,
                    "confidence": 0.08,
                    "reasoning_summary": "Use only if the request is resoped to discussion.",
                }
            ],
        }

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        timeout_seconds=9,
        llm_call=_fake_llm_call,
    )

    decision = router.route(
        "Исправь баг в hermes_cli/pipeline_router.py и добавь pytest на regression.",
        pipeline_session_id="sess-llm-1",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.selected_provider == "openrouter"
    assert decision.selected_model == "openrouter/owl-alpha"
    assert captured["provider"] == "openrouter"
    assert captured["model"] == "openrouter/owl-alpha"
    assert captured["timeout_seconds"] == 9


def test_build_pipeline_router_defaults_to_llm_strategy(tmp_path: Path) -> None:
    router = build_pipeline_router(
        config={"pipelines": {"router": {}}},
        loaded_specs=load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path)),
    )

    assert isinstance(router, LlmPipelineRouter)


def test_llm_router_uses_router_spec_defaults_and_context(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))
    captured: dict[str, object] = {}

    def _fake_llm_call(*, provider: str, model: str, timeout_seconds: float, messages: list[dict[str, str]]) -> dict:
        captured["provider"] = provider
        captured["model"] = model
        captured["timeout_seconds"] = timeout_seconds
        captured["messages"] = messages
        return {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": 0.97,
            "reasoning_summary": "semantic llm route",
            "requires_clarification": False,
            "matched_signals": ["llm:semantic_engineering_request"],
            "alternatives": [],
        }

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        timeout_seconds=10,
        fallback_strategy="fail_closed",
        llm_call=_fake_llm_call,
    )

    decision = router.route(
        "поправь тесты в Hermes",
        pipeline_session_id="sess-llm-defaults",
        router_subagent_id=DEFAULT_ROUTER_SUBAGENT_ID,
        routing_context={
            "platform_context": {"platform": "telegram"},
            "session_context": {"session_id": "sess-llm-defaults", "session_key": "agent:main:telegram:dm"},
            "safety_constraints": {"execution_mode": "disabled"},
        },
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.reasoning_summary == "semantic llm route"
    assert decision.matched_signals == ("llm:semantic_engineering_request",)
    assert decision.selected_provider == "openai-codex"
    assert decision.selected_model == "gpt-5.4-mini"
    assert captured["provider"] == "openai-codex"
    assert captured["model"] == "gpt-5.4-mini"
    joined = "\n".join(message["content"] for message in captured["messages"])
    assert "platform_context" in joined
    assert "session_context" in joined
    assert "safety_constraints" in joined
    assert "candidate_hints" in joined


def test_llm_router_unknown_pipeline_id_is_rejected_safely_when_fail_closed(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": "missing_pipeline",
            "confidence": 0.97,
            "reasoning_summary": "bad llm output",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-llm-unknown")

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert "Unknown selected pipeline id" in (decision.routing_failure_reason or "")


def test_llm_router_malformed_output_is_rejected_safely_when_fail_closed(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: "not json at all",
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-llm-malformed")

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert "JSONDecodeError" in (decision.routing_failure_reason or "")


def test_llm_router_low_confidence_needs_clarification_when_fail_closed(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": 0.42,
            "reasoning_summary": "low confidence semantic route",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-llm-low-confidence")

    assert decision.status == "needs_clarification"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.routing_failure_reason == "llm_low_confidence"
    assert decision.requires_clarification is True


def test_llm_router_falls_back_to_deterministic_strategy_on_failure(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="deterministic",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("router unavailable")),
    )

    decision = router.route(
        "Please modify hermes_cli/pipeline_router.py and tests/test_pipeline_router.py.",
        pipeline_session_id="sess-llm-2",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID


def test_llm_router_returns_routing_failed_when_fail_closed(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad gateway")),
    )

    decision = router.route(
        "Исправь баг в коде и обнови тесты.",
        pipeline_session_id="sess-llm-3",
    )

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert "RuntimeError" in (decision.routing_failure_reason or "")


@pytest.mark.parametrize(
    ("confidence", "expect_selected"),
    [
        (0.70, True),
        (0.95, True),
        (0.69, False),
        (0.01, False),
    ],
)
def test_llm_router_enforces_min_confidence_threshold(
    tmp_path: Path,
    confidence: float,
    expect_selected: bool,
) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="deterministic",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": confidence,
            "reasoning_summary": "classification result",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route(
        "Исправь баг в hermes_cli/pipeline_router.py и добавь pytest на regression.",
        pipeline_session_id=f"sess-min-confidence-{confidence}",
    )

    if expect_selected:
        assert decision.status == "selected"
        assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
        assert decision.routing_failure_reason is None
    else:
        assert decision.status != "selected"
        assert decision.selected_pipeline_id != ENGINEERING_PIPELINE_ID
        assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
        assert decision.routing_failure_reason == "llm_low_confidence"


@pytest.mark.parametrize("confidence", [1.01, -0.01, None, "high"])
def test_llm_router_rejects_invalid_confidence(tmp_path: Path, confidence: object) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="deterministic",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": confidence,
            "reasoning_summary": "classification result",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route(
        "Исправь баг в hermes_cli/pipeline_router.py и добавь pytest на regression.",
        pipeline_session_id="sess-invalid-confidence",
    )

    assert decision.status != "selected"
    assert decision.selected_pipeline_id != ENGINEERING_PIPELINE_ID
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.routing_failure_reason == "llm_invalid_confidence"


def test_llm_router_treats_ambiguous_as_fail_closed(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="deterministic",
        llm_call=lambda **kwargs: {
            "status": "ambiguous",
            "confidence": 0.83,
            "reasoning_summary": "The request may be either an audit or a patch request.",
            "requires_clarification": True,
            "clarification_question": "Нужен аудит или патч?",
            "alternatives": [],
        },
    )

    decision = router.route(
        "посмотри это и реши, надо ли чинить",
        pipeline_session_id="sess-ambiguous",
    )

    assert decision.status != "selected"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.routing_failure_reason == "llm_ambiguous"


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
