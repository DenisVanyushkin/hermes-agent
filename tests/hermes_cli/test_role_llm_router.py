"""Tests for the LLM role router and its wiring into build_role_context_for_task."""

from __future__ import annotations

import pytest

from hermes_cli.role_llm_router import (
    LLMRoleDecision,
    RoleRoutingConfig,
    SELECTABLE_ROLES,
    load_role_routing_config,
    select_role_via_llm,
)


def _cfg(**kw):
    return RoleRoutingConfig(**{"strategy": "llm", **kw})


def _call_returning(payload):
    def call(**kwargs):
        return payload
    return call


def test_selectable_roles_include_all_builtin_task_roles():
    assert "artist" in SELECTABLE_ROLES
    assert "chief_hermes" not in SELECTABLE_ROLES


def test_confident_decision_is_returned():
    decision = select_role_via_llm(
        "изобрази-ка мне закат как у Миядзаки",
        _cfg(),
        llm_call=_call_returning({"role": "artist", "confidence": 0.93, "reasoning_summary": "image"}),
    )
    assert decision == LLMRoleDecision(role="artist", confidence=0.93, reasoning_summary="image")


def test_low_confidence_returns_none():
    decision = select_role_via_llm(
        "сделай что-нибудь",
        _cfg(min_confidence=0.7),
        llm_call=_call_returning({"role": "artist", "confidence": 0.5, "reasoning_summary": ""}),
    )
    assert decision is None


def test_unknown_role_returns_none():
    decision = select_role_via_llm(
        "task",
        _cfg(),
        llm_call=_call_returning({"role": "wizard", "confidence": 0.99, "reasoning_summary": ""}),
    )
    assert decision is None


def test_llm_exception_returns_none():
    def boom(**kwargs):
        raise RuntimeError("provider down")

    assert select_role_via_llm("task", _cfg(), llm_call=boom) is None


def test_malformed_confidence_returns_none():
    decision = select_role_via_llm(
        "task",
        _cfg(),
        llm_call=_call_returning({"role": "artist", "confidence": "high"}),
    )
    assert decision is None


def test_empty_task_returns_none_without_calling_llm():
    def must_not_call(**kwargs):
        raise AssertionError("llm_call must not be invoked for empty task")

    assert select_role_via_llm("   ", _cfg(), llm_call=must_not_call) is None


def test_load_config_defaults_to_deterministic():
    assert load_role_routing_config({}).strategy == "deterministic"
    assert load_role_routing_config(None).strategy == "deterministic"
    assert load_role_routing_config({"role_routing": {"strategy": "nonsense"}}).strategy == "deterministic"


def test_load_config_llm_section():
    cfg = load_role_routing_config(
        {"role_routing": {"strategy": "llm", "min_confidence": 0.8, "model": "m", "provider": "p", "timeout_seconds": 3}}
    )
    assert cfg.strategy == "llm"
    assert cfg.min_confidence == 0.8
    assert cfg.model == "m"
    assert cfg.provider == "p"
    assert cfg.timeout_seconds == 3.0


# --- integration with build_role_context_for_task -------------------------


def test_llm_override_wins_over_cascade(monkeypatch):
    from hermes_cli import profile_context

    monkeypatch.setattr(
        profile_context,
        "_load_role_routing_config_cached",
        lambda: RoleRoutingConfig(strategy="llm"),
    )
    monkeypatch.setattr(
        profile_context,
        "select_role_via_llm",
        lambda task, cfg: LLMRoleDecision(role="artist", confidence=0.9, reasoning_summary="image"),
    )
    # "хочу картинку как у Миядзаки" has no cascade trigger -> general_operator without LLM
    result = profile_context.build_role_context_for_task("хочу закат как у Миядзаки на стену")
    assert result.selected_role == "artist"


def test_llm_none_falls_back_to_cascade(monkeypatch):
    from hermes_cli import profile_context

    monkeypatch.setattr(
        profile_context,
        "_load_role_routing_config_cached",
        lambda: RoleRoutingConfig(strategy="llm"),
    )
    monkeypatch.setattr(profile_context, "select_role_via_llm", lambda task, cfg: None)
    result = profile_context.build_role_context_for_task("Нарисуй кота-космонавта")
    assert result.selected_role == "artist"  # cascade still catches it


def test_deterministic_strategy_never_calls_llm(monkeypatch):
    from hermes_cli import profile_context

    monkeypatch.setattr(
        profile_context,
        "_load_role_routing_config_cached",
        lambda: RoleRoutingConfig(strategy="deterministic"),
    )

    def must_not_call(task, cfg):
        raise AssertionError("LLM must not be called in deterministic mode")

    monkeypatch.setattr(profile_context, "select_role_via_llm", must_not_call)
    result = profile_context.build_role_context_for_task("Нарисуй кота-космонавта")
    assert result.selected_role == "artist"
