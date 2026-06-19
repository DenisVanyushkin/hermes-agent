from __future__ import annotations

import logging
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

import hermes_logging
from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_specs import PipelineSpecValidationError


def test_router_llm_defaults_are_present():
    from hermes_cli.config import DEFAULT_CONFIG

    router_cfg = DEFAULT_CONFIG["pipelines"]["router"]

    assert router_cfg["strategy"] == "llm"
    assert router_cfg["llm"]["provider"] == "openai-codex"
    assert router_cfg["llm"]["model"] == "gpt-5.4-mini"
    assert router_cfg["llm"]["timeout_seconds"] == 10
    assert router_cfg["llm"]["fallback_strategy"] == "fail_closed"
    assert router_cfg["llm"]["min_confidence"] == 0.70


def test_observe_disabled_skips_router(monkeypatch):
    from hermes_cli import pipeline_observe

    calls: list[str] = []

    monkeypatch.setattr(
        pipeline_observe,
        "load_pipeline_specs",
        lambda **kwargs: calls.append("load"),
    )
    monkeypatch.setattr(
        pipeline_observe,
        "build_pipeline_router",
        lambda **kwargs: calls.append("router"),
    )

    result = pipeline_observe.observe_pipeline_router_decision(
        config={"pipelines": {"router": {"mode": "disabled"}}},
        user_message="Implement Slice B2",
        session_id="sess-1",
    )

    assert result is None
    assert calls == []


def test_observe_invalid_mode_is_treated_as_disabled(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    calls: list[str] = []

    monkeypatch.setattr(
        pipeline_observe,
        "load_pipeline_specs",
        lambda **kwargs: calls.append("load"),
    )

    with caplog.at_level(logging.WARNING, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "mystery"}}},
            user_message="Implement Slice B2",
            session_id="sess-2",
        )

    assert result is None
    assert calls == []
    assert any("Invalid pipelines.router.mode" in record.message for record in caplog.records)


def test_controlled_manual_router_mode_routes_without_invalid_mode_warning(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    decision = RouterDecision(
        pipeline_session_id="pipe-controlled-manual",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="controlled manual engineering request",
        requires_clarification=False,
        fallback_safe=False,
        policy_block_reason=None,
        routing_failure_reason=None,
        selected_provider="openrouter",
        selected_model="openrouter/owl-alpha",
        actual_provider="openrouter",
        actual_model="openrouter/owl-alpha",
        alternatives=(),
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            assert user_message == "HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run"
            assert pipeline_session_id
            assert router_subagent_id == "hermes_pipeline_router"
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", lambda **kwargs: _FakeRouter())

    with caplog.at_level(logging.INFO, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "controlled_manual"}}},
            user_message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run",
            session_id="sess-controlled-router",
            platform="telegram",
        )

    assert result == decision
    log_message = next(record.message for record in caplog.records if "pipeline_router_observe_decision" in record.message)
    assert '"event": "pipeline_router_observe_decision"' in log_message
    assert '"selected_pipeline_id": "engineering_review_pipeline"' in log_message
    assert not any("Invalid pipelines.router.mode" in record.message for record in caplog.records)


def test_observe_mode_routes_and_logs(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    decision = RouterDecision(
        pipeline_session_id="pipe-1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="engineering request",
        requires_clarification=False,
        fallback_safe=False,
        policy_block_reason=None,
        routing_failure_reason=None,
        selected_provider="openrouter",
        selected_model="openrouter/owl-alpha",
        actual_provider="openrouter",
        actual_model="openrouter/owl-alpha",
        alternatives=(),
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            assert user_message == "Implement Slice B2"
            assert pipeline_session_id
            assert router_subagent_id == "hermes_pipeline_router"
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", lambda **kwargs: _FakeRouter())

    with caplog.at_level(logging.INFO, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "observe"}}},
            user_message="Implement Slice B2",
            session_id="sess-3",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            actual_provider="openrouter",
            actual_model="openrouter/owl-alpha",
        )

    assert result == decision
    log_message = next(record.message for record in caplog.records if "pipeline_router_observe_decision" in record.message)
    assert '"event": "pipeline_router_observe_decision"' in log_message
    assert '"pipeline_session_id": "pipe-1"' in log_message
    assert '"selected_pipeline_id": "engineering_review_pipeline"' in log_message
    assert '"actual_model": "openrouter/owl-alpha"' in log_message
    assert '"router_strategy": "llm"' in log_message
    assert '"router_fallback_strategy": "fail_closed"' in log_message
    assert '"reasoning_summary": "engineering request"' in log_message
    assert '"alternatives": []' in log_message
    assert '"status": "selected"' in log_message


def test_observe_failure_is_logged_and_swallowed(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    monkeypatch.setattr(
        pipeline_observe,
        "load_pipeline_specs",
        lambda **kwargs: (_ for _ in ()).throw(
            PipelineSpecValidationError([])
        ),
    )

    with caplog.at_level(logging.WARNING, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "observe"}}},
            user_message="Implement Slice B2",
            session_id="sess-4",
            session_key="agent:main:telegram:dm",
            platform="telegram",
        )

    assert result is None
    log_message = next(record.message for record in caplog.records if "pipeline_router_observe_failed" in record.message)
    assert '"event": "pipeline_router_observe_failed"' in log_message
    assert '"session_id": "sess-4"' in log_message
    assert '"exception": "PipelineSpecValidationError"' in log_message


def test_observe_helper_logs_to_gateway_sink_with_injected_gateway_logger(monkeypatch):
    from hermes_cli import pipeline_observe

    decision = RouterDecision(
        pipeline_session_id="pipe-2",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.91,
        reasoning_summary="engineering request",
        requires_clarification=False,
        fallback_safe=True,
        selected_provider="openrouter",
        selected_model="openrouter/owl-alpha",
        actual_provider="openrouter",
        actual_model="openrouter/owl-alpha",
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", lambda **kwargs: _FakeRouter())

    with tempfile.TemporaryDirectory() as tmpdir:
        hermes_home = Path(tmpdir)
        hermes_logging.setup_logging(hermes_home=hermes_home, mode="gateway", force=True)
        gateway_logger = logging.getLogger("gateway.run")

        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "observe"}}},
            user_message="Implement Slice B2",
            session_id="sess-5",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            logger=gateway_logger,
        )

        assert result == decision
        gateway_log = (hermes_home / "logs" / "gateway.log").read_text(encoding="utf-8")
        assert "pipeline_router_observe_decision" in gateway_log
        assert '"status": "selected"' in gateway_log
        assert "gateway.run" in gateway_log


def test_observe_llm_strategy_builds_llm_router(monkeypatch):
    from hermes_cli import pipeline_observe

    captured: dict[str, object] = {}
    decision = RouterDecision(
        pipeline_session_id="pipe-llm",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.95,
        reasoning_summary="llm route",
        requires_clarification=False,
        fallback_safe=False,
        selected_provider="openrouter",
        selected_model="openrouter/owl-alpha",
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())

    def _fake_build_router(*, config, loaded_specs, repo_root):
        captured["config"] = config
        captured["loaded_specs"] = loaded_specs
        captured["repo_root"] = repo_root
        return _FakeRouter()

    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", _fake_build_router)

    result = pipeline_observe.observe_pipeline_router_decision(
        config={
            "pipelines": {
                "router": {
                    "mode": "observe",
                    "strategy": "llm",
                    "llm": {
                        "provider": "openrouter",
                        "model": "openrouter/owl-alpha",
                        "timeout_seconds": 7,
                        "fallback_strategy": "deterministic",
                        "min_confidence": 0.70,
                    },
                }
            }
        },
        user_message="Исправь код и тесты",
        session_id="sess-llm-observe",
        repo_root=Path("/tmp/fake-repo"),
    )

    assert result == decision
    assert captured["repo_root"] == Path("/tmp/fake-repo")


def test_observe_invalid_strategy_falls_back_to_deterministic(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    captured: list[str] = []

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            return RouterDecision(
                pipeline_session_id="pipe-det",
                router_subagent_id=router_subagent_id,
                status="no_specialized_pipeline",
                fallback_pipeline_id="default_conversation_pipeline",
                confidence=0.7,
                reasoning_summary="deterministic fallback",
                requires_clarification=False,
                fallback_safe=True,
            )

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())

    def _fake_build_router(*, config, loaded_specs, repo_root):
        captured.append("built")
        return _FakeRouter()

    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", _fake_build_router)

    with caplog.at_level(logging.WARNING, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "observe", "strategy": "mystery"}}},
            user_message="Explain architecture",
            session_id="sess-det-observe",
        )

    assert result is not None
    assert captured == ["built"]
    assert any("Invalid pipelines.router.strategy" in record.message for record in caplog.records)


def test_observe_llm_strategy_logs_diagnostics(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    decision = RouterDecision(
        pipeline_session_id="pipe-llm-observe",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.88,
        reasoning_summary="User explicitly asked for code and test changes.",
        requires_clarification=False,
        fallback_safe=False,
        policy_block_reason=None,
        routing_failure_reason=None,
        alternatives=(),
        selected_provider="openrouter",
        selected_model="openrouter/owl-alpha",
        actual_provider="openrouter",
        actual_model="openrouter/owl-alpha",
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", lambda **kwargs: _FakeRouter())

    with caplog.at_level(logging.INFO, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={
                "pipelines": {
                    "router": {
                        "mode": "observe",
                        "strategy": "llm",
                        "llm": {
                            "provider": "openrouter",
                            "model": "openrouter/owl-alpha",
                            "timeout_seconds": 7,
                            "fallback_strategy": "deterministic",
                            "min_confidence": 0.70,
                        },
                    }
                }
            },
            user_message="Исправь код и тесты",
            session_id="sess-llm-diag",
        )

    assert result == decision
    log_message = next(record.message for record in caplog.records if "pipeline_router_observe_decision" in record.message)
    assert '"router_strategy": "llm"' in log_message
    assert '"router_fallback_strategy": "deterministic"' in log_message
    assert '"reasoning_summary": "User explicitly asked for code and test changes."' in log_message
    assert '"matched_signals": []' in log_message
    assert '"alternatives": []' in log_message


def test_observe_low_confidence_fallback_logs_failure_reason(monkeypatch, caplog):
    from hermes_cli import pipeline_observe

    decision = RouterDecision(
        pipeline_session_id="pipe-low-confidence",
        router_subagent_id="hermes_pipeline_router",
        status="no_specialized_pipeline",
        selected_pipeline_id=None,
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.75,
        reasoning_summary="LLM confidence was below the configured threshold.",
        requires_clarification=False,
        fallback_safe=True,
        policy_block_reason=None,
        routing_failure_reason="llm_low_confidence",
        alternatives=(),
        selected_provider="openrouter",
        selected_model="openrouter/owl-alpha",
        actual_provider="openrouter",
        actual_model="openrouter/owl-alpha",
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "build_pipeline_router", lambda **kwargs: _FakeRouter())

    with caplog.at_level(logging.INFO, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={
                "pipelines": {
                    "router": {
                        "mode": "observe",
                        "strategy": "llm",
                        "llm": {
                            "provider": "openrouter",
                            "model": "openrouter/owl-alpha",
                            "timeout_seconds": 7,
                            "fallback_strategy": "deterministic",
                            "min_confidence": 0.70,
                        },
                    }
                }
            },
            user_message="Исправь код и тесты",
            session_id="sess-low-confidence",
        )

    assert result == decision
    log_message = next(record.message for record in caplog.records if "pipeline_router_observe_decision" in record.message)
    assert '"fallback_reason": "llm_low_confidence"' in log_message
    assert '"routing_failure_reason": "llm_low_confidence"' in log_message
