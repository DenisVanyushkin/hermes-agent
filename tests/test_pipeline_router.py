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
    _ROUTER_RESPONSE_FORMAT,
    _build_router_messages,
    _summarize_confidence_value,
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


def test_llm_router_timeout_uses_narrow_engineering_fallback_for_strong_smoke_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        (
            "HERMES AUTONOMOUS PIPELINE VALIDATION\n\n"
            "Create a tiny autonomous-runtime smoke marker file:\n"
            "tests/autonomous_runtime_smoke_marker.py\n\n"
            "The file should contain one trivial pytest test with a unique marker string.\n"
            "Do not modify production behavior.\n"
            "Do not touch DB persistence.\n"
        ),
        pipeline_session_id="sess-timeout-fallback",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.routing_fallback_used is True
    assert decision.routing_fallback_reason is not None
    assert "TimeoutError" in decision.routing_fallback_reason
    assert "TimeoutError" in (decision.routing_failure_reason or "")
    assert decision.router_strategy == "heuristic_timeout_fallback"
    assert decision.confidence >= 0.70


def test_llm_router_invalid_confidence_uses_narrow_engineering_fallback_for_strong_smoke_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": "high",
            "reasoning_summary": "broken confidence contract",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route(
        (
            "HERMES AUTONOMOUS PIPELINE VALIDATION\n\n"
            "Create tests/autonomous_runtime_smoke_marker.py and add one trivial pytest test.\n"
            "Do not modify production behavior.\n"
            "Do not touch DB persistence.\n"
        ),
        pipeline_session_id="sess-invalid-confidence-fallback",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.routing_fallback_used is True
    assert decision.routing_confidence_source == "heuristic_strict"
    assert decision.invalid_confidence_kind == "non_numeric"
    assert decision.routing_fallback_reason is not None
    assert "confidence" in decision.routing_fallback_reason.lower()


def test_llm_router_timeout_strict_engineering_fallback_exposes_confidence_source(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        (
            "HERMES AUTONOMOUS PIPELINE VALIDATION\n\n"
            "Create tests/autonomous_runtime_smoke_marker.py and add one trivial pytest test.\n"
            "Do not modify production behavior.\n"
            "Do not touch DB persistence.\n"
        ),
        pipeline_session_id="sess-timeout-confidence-source",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.routing_fallback_used is True
    assert decision.routing_confidence_source == "heuristic_strict"


def test_llm_router_timeout_uses_engineering_fallback_for_runtime_analysis_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        (
            "HERMES-AUTO-SMOKE-20260623-ALMATY-005\n\n"
            "This is an autonomous engineering pipeline runtime smoke for "
            "engineering_review_pipeline.\n"
            "Validate the post-fix runtime smoke after commit 81100a4e.\n"
            "Use find_files, read_file, and search_files with repo-relative paths.\n"
            "Report whether the autonomous execution controller, helper/subagent bridge, "
            "and pipeline_execution_report were exercised.\n"
            "Do not change code. Do not commit. Do not push.\n"
        ),
        pipeline_session_id="sess-timeout-runtime-analysis",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.routing_fallback_used is True
    assert decision.router_strategy == "heuristic_timeout_fallback"
    assert decision.routing_confidence_source == "heuristic_strict"
    assert decision.routing_fallback_reason is not None
    assert "TimeoutError" in decision.routing_fallback_reason
    assert "TimeoutError" in (decision.routing_failure_reason or "")


def test_llm_router_timeout_keeps_fail_closed_for_vague_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        "Help me with Hermes.",
        pipeline_session_id="sess-timeout-vague",
    )

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.routing_fallback_used is False
    assert "TimeoutError" in (decision.routing_failure_reason or "")


def test_llm_router_timeout_uses_default_fallback_for_clear_generic_chat(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        "Привет, что ты умеешь?",
        pipeline_session_id="sess-timeout-default-chat",
    )

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.fallback_safe is True
    assert decision.routing_fallback_used is True
    assert decision.routing_fallback_reason is not None
    assert "TimeoutError" in decision.routing_fallback_reason
    assert "TimeoutError" in (decision.routing_failure_reason or "")
    assert decision.router_strategy == "heuristic_timeout_default_fallback"


def test_llm_router_invalid_response_uses_default_fallback_for_clear_generic_chat(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: "not json at all",
    )

    decision = router.route(
        "Помоги сформулировать короткое письмо.",
        pipeline_session_id="sess-invalid-default-chat",
    )

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.fallback_safe is True
    assert decision.routing_fallback_used is True
    assert decision.routing_fallback_reason is not None
    assert "JSONDecodeError" in decision.routing_fallback_reason
    assert "JSONDecodeError" in (decision.routing_failure_reason or "")
    assert decision.router_strategy == "heuristic_timeout_default_fallback"


def test_llm_router_timeout_keeps_fail_closed_for_non_engineering_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        "Summarize the latest autonomous smoke and explain whether it was green.",
        pipeline_session_id="sess-timeout-non-engineering",
    )

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.routing_fallback_used is False
    assert "TimeoutError" in (decision.routing_failure_reason or "")


def test_llm_router_timeout_keeps_fail_closed_for_ambiguous_engineeringish_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        "почини Hermes",
        pipeline_session_id="sess-timeout-ambiguous-engineeringish",
    )

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert decision.routing_fallback_used is False
    assert "TimeoutError" in (decision.routing_failure_reason or "")


def test_llm_router_timeout_keeps_fail_closed_for_ambiguous_repo_opinion_prompt(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: (_ for _ in ()).throw(
            TimeoutError("Codex auxiliary Responses stream exceeded 10.0s total timeout")
        ),
    )

    decision = router.route(
        "проверь репозиторий и скажи что думаешь",
        pipeline_session_id="sess-timeout-ambiguous-repo-opinion",
    )

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.routing_fallback_used is False
    assert "TimeoutError" in (decision.routing_failure_reason or "")


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


def test_router_prompt_explicitly_requires_numeric_confidence_contract(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    messages = _build_router_messages(loaded_specs, "Fix the router bug.")

    system_prompt = messages[0]["content"]
    assert "confidence must be a JSON number between 0 and 1 inclusive" in system_prompt
    assert "Do not return confidence as a string" in system_prompt
    assert "0..100 scale" in system_prompt
    assert "labels" in system_prompt
    assert "alternatives" in system_prompt


@pytest.mark.parametrize(
    ("confidence", "expected_kind"),
    [
        pytest.param(1.01, "out_of_range_high", id="high"),
        pytest.param(-0.01, "out_of_range_low", id="low"),
        pytest.param(None, "null", id="null"),
        pytest.param("high", "non_numeric", id="string"),
        pytest.param(True, "non_numeric", id="bool-true"),
        pytest.param(False, "non_numeric", id="bool-false"),
    ],
)
def test_llm_router_rejects_invalid_confidence_with_diagnostics(
    tmp_path: Path,
    confidence: object,
    expected_kind: str,
) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
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

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert decision.routing_failure_reason == "llm_invalid_confidence"
    assert decision.invalid_confidence_kind == expected_kind


def test_llm_router_rejects_missing_confidence_with_diagnostics(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "reasoning_summary": "classification result",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route(
        "Исправь баг в hermes_cli/pipeline_router.py и добавь pytest на regression.",
        pipeline_session_id="sess-missing-confidence",
    )

    assert decision.status == "routing_failed"
    assert decision.routing_failure_reason == "llm_invalid_confidence"
    assert decision.invalid_confidence_kind == "missing"


def test_llm_router_redacts_sensitive_string_confidence_summary(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))
    sensitive = "sk-live-super-secret-token-value"

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": sensitive,
            "reasoning_summary": "classification result",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route(
        "Исправь баг в hermes_cli/pipeline_router.py и добавь pytest на regression.",
        pipeline_session_id="sess-sensitive-confidence",
    )

    assert decision.invalid_confidence_kind == "non_numeric"
    assert decision.invalid_confidence_summary is not None
    assert sensitive not in decision.invalid_confidence_summary
    assert "redacted" in decision.invalid_confidence_summary


def test_llm_router_low_numeric_confidence_needs_clarification_not_invalid(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openrouter",
        model="openrouter/owl-alpha",
        fallback_strategy="fail_closed",
        min_confidence=0.70,
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "confidence": 0.2,
            "reasoning_summary": "classification result",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route(
        "Исправь баг в hermes_cli/pipeline_router.py и добавь pytest на regression.",
        pipeline_session_id="sess-low-valid-confidence",
    )

    assert decision.status == "needs_clarification"
    assert decision.routing_failure_reason == "llm_low_confidence"
    assert decision.invalid_confidence_kind is None


def test_llm_router_rejects_pipeline_id_shaped_status_with_diagnostics(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "default_conversation_pipeline",
            "confidence": 0.8,
            "reasoning_summary": "ordinary prompt",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route("Explain the architecture in plain language.", pipeline_session_id="sess-invalid-status")

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert decision.routing_failure_reason == "Unknown router status: 'default_conversation_pipeline'"
    assert decision.invalid_router_contract_kind == "invalid_status"
    assert decision.invalid_router_contract_summary is not None
    assert "pipeline_id_like" in decision.invalid_router_contract_summary


def test_llm_router_drops_null_alternative_when_default_primary_is_valid(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "no_specialized_pipeline",
            "selected_pipeline_id": None,
            "fallback_pipeline_id": DEFAULT_PIPELINE_ID,
            "confidence": 0.8,
            "reasoning_summary": "ordinary prompt",
            "requires_clarification": False,
            "fallback_safe": True,
            "alternatives": [
                {
                    "pipeline_id": None,
                    "confidence": 0.2,
                    "reasoning_summary": "invalid alt should be dropped",
                }
            ],
        },
    )

    decision = router.route("что дальше?", pipeline_session_id="sess-null-alt-default")

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.alternatives == ()
    assert decision.dropped_alternatives_count == 1
    assert decision.dropped_alternatives_reasons == ("null_pipeline_id",)


def test_llm_router_drops_null_alternative_when_selected_primary_is_valid(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "fallback_pipeline_id": None,
            "confidence": 0.95,
            "reasoning_summary": "engineering mutation request",
            "requires_clarification": False,
            "fallback_safe": False,
            "alternatives": [
                {
                    "pipeline_id": None,
                    "confidence": 0.1,
                    "reasoning_summary": "invalid alt should be dropped",
                }
            ],
        },
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-null-alt-selected")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.alternatives == ()
    assert decision.dropped_alternatives_count == 1
    assert decision.dropped_alternatives_reasons == ("null_pipeline_id",)


def test_llm_router_drops_unknown_and_non_string_alternatives_but_keeps_valid_ones(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "fallback_pipeline_id": None,
            "confidence": 0.93,
            "reasoning_summary": "engineering request",
            "requires_clarification": False,
            "fallback_safe": False,
            "alternatives": [
                {
                    "pipeline_id": DEFAULT_PIPELINE_ID,
                    "confidence": 0.2,
                    "reasoning_summary": "valid fallback if scope changes",
                },
                {
                    "pipeline_id": "missing_pipeline",
                    "confidence": 0.15,
                    "reasoning_summary": "unknown alt should be dropped",
                },
                {
                    "pipeline_id": 42,
                    "confidence": 0.11,
                    "reasoning_summary": "non-string alt should be dropped",
                },
            ],
        },
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-bad-alternatives")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.alternatives == (
        decision.alternatives[0].__class__(
            pipeline_id=DEFAULT_PIPELINE_ID,
            confidence=0.2,
            reasoning_summary="valid fallback if scope changes",
        ),
    )
    assert decision.dropped_alternatives_count == 2
    assert decision.dropped_alternatives_reasons == ("unknown_pipeline_id", "non_string_pipeline_id")


def test_llm_router_drops_missing_alternative_pipeline_id_without_logging_raw_value(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))
    sensitive = "sk-live-router-secret"

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "fallback_pipeline_id": None,
            "confidence": 0.91,
            "reasoning_summary": "engineering request",
            "requires_clarification": False,
            "fallback_safe": False,
            "alternatives": [
                {
                    "confidence": 0.05,
                    "reasoning_summary": sensitive,
                }
            ],
        },
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-missing-alt-pipeline-id")

    assert decision.status == "selected"
    assert decision.dropped_alternatives_count == 1
    assert decision.dropped_alternatives_reasons == ("missing_pipeline_id",)
    assert sensitive not in " ".join(decision.dropped_alternatives_reasons)


def test_llm_router_invalid_primary_selected_pipeline_id_still_fails_closed(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": "missing_pipeline",
            "confidence": 0.94,
            "reasoning_summary": "bad primary selection",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route("исправь баг в Hermes", pipeline_session_id="sess-invalid-primary-selected")

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.routing_failure_reason is not None
    assert "Unknown selected pipeline id: 'missing_pipeline'" in decision.routing_failure_reason


@pytest.mark.parametrize(
    ("selected_pipeline_id", "expected_summary"),
    [
        pytest.param(None, "NoneType(null)", id="missing"),
        pytest.param(123, "int(123)", id="non-string"),
    ],
)
def test_llm_router_rejects_selected_without_pipeline_id_with_diagnostics(
    tmp_path: Path,
    selected_pipeline_id: object,
    expected_summary: str,
) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    payload = {
        "status": "selected",
        "confidence": 0.8,
        "reasoning_summary": "The request asks to modify code or tests.",
        "requires_clarification": False,
        "alternatives": [],
    }
    if selected_pipeline_id is not None:
        payload["selected_pipeline_id"] = selected_pipeline_id

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: payload,
    )

    decision = router.route("Patch the repo and update tests.", pipeline_session_id="sess-selected-missing-id")

    assert decision.status == "routing_failed"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id is None
    assert decision.routing_failure_reason == "Router status 'selected' requires selected_pipeline_id"
    assert decision.invalid_router_contract_kind == "selected_missing_pipeline_id"
    assert decision.invalid_router_contract_summary == f"selected_pipeline_id={expected_summary}"


def test_llm_router_accepts_valid_default_shape(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "no_specialized_pipeline",
            "selected_pipeline_id": None,
            "fallback_pipeline_id": DEFAULT_PIPELINE_ID,
            "confidence": 0.8,
            "reasoning_summary": "Ordinary conversation should use the default pipeline.",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route("Explain the architecture in plain language.", pipeline_session_id="sess-valid-default")

    assert decision.status == "no_specialized_pipeline"
    assert decision.selected_pipeline_id is None
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID
    assert decision.invalid_router_contract_kind is None


def test_llm_router_accepts_valid_engineering_shape(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    router = LlmPipelineRouter(
        loaded_specs=loaded_specs,
        provider="openai-codex",
        model="gpt-5.4-mini",
        fallback_strategy="fail_closed",
        llm_call=lambda **kwargs: {
            "status": "selected",
            "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
            "fallback_pipeline_id": None,
            "confidence": 0.8,
            "reasoning_summary": "The request asks to modify code or tests.",
            "requires_clarification": False,
            "alternatives": [],
        },
    )

    decision = router.route("Patch the repo and update tests.", pipeline_session_id="sess-valid-engineering")

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID
    assert decision.fallback_pipeline_id is None
    assert decision.invalid_router_contract_kind is None


def test_default_router_llm_call_passes_response_format_payload(monkeypatch):
    from hermes_cli import pipeline_router as module
    from agent import auxiliary_client

    captured = {}

    class _FakeResponse:
        usage = {"input_tokens": 12, "output_tokens": 7}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(auxiliary_client, "resolve_provider_client", lambda provider, model: (_FakeClient(), "resolved-model"))
    monkeypatch.setattr(auxiliary_client, "extract_content_or_reasoning", lambda response: '{"status": "no_specialized_pipeline", "confidence": 0.8, "reasoning_summary": "ok", "requires_clarification": false, "fallback_safe": true, "fallback_pipeline_id": "default_conversation_pipeline", "alternatives": []}')

    result = module._default_router_llm_call(
        provider="openai-codex",
        model="gpt-5.4-mini",
        timeout_seconds=5,
        messages=[{"role": "user", "content": "route"}],
    )

    assert captured["extra_body"] == _ROUTER_RESPONSE_FORMAT
    assert captured["model"] == "resolved-model"
    assert result["token_usage"] == {"input_tokens": 12, "output_tokens": 7}


def test_router_prompt_explicitly_requires_status_contract_and_examples(tmp_path: Path) -> None:
    loaded_specs = load_pipeline_specs(repo_root=_copy_spec_tree(tmp_path))

    messages = _build_router_messages(loaded_specs, "Fix the router bug.")

    combined = "\n".join(message["content"] for message in messages)
    assert "status must be exactly one of: selected, no_specialized_pipeline, needs_clarification, blocked_by_policy, routing_failed" in combined
    assert "Pipeline ids must never appear in status" in combined
    assert "default_conversation_pipeline is a pipeline id or fallback, never a status" in combined
    assert "Example A - default or ordinary prompt" in combined
    assert '"status": "no_specialized_pipeline"' in combined
    assert '"requires_clarification": false' in combined
    assert '"fallback_safe": true' in combined
    assert "Example B - engineering prompt" in combined
    assert '"selected_pipeline_id": "engineering_review_pipeline"' in combined
    assert '"fallback_safe": false' in combined
    assert "Example C - recruiter or career writing prompt" in combined


def test_summarize_confidence_value_redacts_strings() -> None:
    assert _summarize_confidence_value(0.2) == "float(0.2)"
    assert _summarize_confidence_value(None) == "NoneType(null)"
    summary = _summarize_confidence_value("0.2")
    assert summary.startswith("str(len=3")
    assert "0.2" not in summary


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
