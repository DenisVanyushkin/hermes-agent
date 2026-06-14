from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_specs import PipelineSpecValidationError


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
        "HeuristicPipelineRouter",
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
        selected_provider="openai-codex",
        selected_model="gpt-5.4-mini",
        actual_provider="openai-codex",
        actual_model="gpt-5.4",
    )

    class _FakeRouter:
        def route(self, user_message: str, *, pipeline_session_id: str, router_subagent_id: str = "hermes_pipeline_router"):
            assert user_message == "Implement Slice B2"
            assert pipeline_session_id
            assert router_subagent_id == "hermes_pipeline_router"
            return decision

    monkeypatch.setattr(pipeline_observe, "load_pipeline_specs", lambda **kwargs: object())
    monkeypatch.setattr(pipeline_observe, "HeuristicPipelineRouter", lambda **kwargs: _FakeRouter())

    with caplog.at_level(logging.INFO, logger="hermes_cli.pipeline_observe"):
        result = pipeline_observe.observe_pipeline_router_decision(
            config={"pipelines": {"router": {"mode": "observe"}}},
            user_message="Implement Slice B2",
            session_id="sess-3",
            session_key="agent:main:telegram:dm",
            platform="telegram",
            actual_provider="openai-codex",
            actual_model="gpt-5.4",
        )

    assert result == decision
    log_message = next(record.message for record in caplog.records if "pipeline_router_observe_decision" in record.message)
    assert '"event": "pipeline_router_observe_decision"' in log_message
    assert '"pipeline_session_id": "pipe-1"' in log_message
    assert '"selected_pipeline_id": "engineering_review_pipeline"' in log_message
    assert '"actual_model": "gpt-5.4"' in log_message


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

