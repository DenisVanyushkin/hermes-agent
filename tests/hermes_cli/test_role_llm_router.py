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
    assert "lawyer" in SELECTABLE_ROLES
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


# --- explicit role boundaries ---------------------------------------------


def test_prompt_carries_explicit_boundaries():
    from hermes_cli.role_llm_router import _build_messages

    system = _build_messages("task")[0]["content"]
    # The vague "anything else" catch-all made general_operator a dumping ground.
    assert "anything else" not in system
    assert "add an idea or task to the backlog" in system
    assert "Ordinary code review is engineer" in system
    assert "[Replying to:" in system


# --- Jev (OpenRouter typed decisions) first tier ---------------------------


def _jev_cfg(**kw):
    return _cfg(jev_enabled=True, **kw)


def _must_not_call(**kwargs):
    raise AssertionError("must not be called")


def test_confident_jev_decision_skips_chat_router():
    decision = select_role_via_llm(
        "добавь идею в бэклог",
        _jev_cfg(),
        llm_call=_must_not_call,
        jev_call=_call_returning({"role": "scribe", "confidence": 0.93}),
    )
    assert decision == LLMRoleDecision(role="scribe", confidence=0.93, reasoning_summary="jev")


@pytest.mark.parametrize(
    "jev_payload",
    [
        {"role": "scribe", "confidence": 0.4},  # not confident
        {"role": "wizard", "confidence": 0.99},  # unknown role
        {"role": "scribe", "confidence": "high"},  # malformed
        {"role": "scribe", "confidence": float("nan")},  # non-finite
        ["scribe", 0.99],  # not an object at all
    ],
)
def test_unusable_jev_answer_falls_through_to_chat_router(jev_payload):
    decision = select_role_via_llm(
        "task",
        _jev_cfg(),
        llm_call=_call_returning({"role": "engineer", "confidence": 0.9, "reasoning_summary": "code"}),
        jev_call=_call_returning(jev_payload),
    )
    assert decision == LLMRoleDecision(role="engineer", confidence=0.9, reasoning_summary="code")


def test_jev_failure_falls_through_to_chat_router():
    def boom(**kwargs):
        raise TimeoutError("jev down")

    decision = select_role_via_llm(
        "task",
        _jev_cfg(),
        llm_call=_call_returning({"role": "engineer", "confidence": 0.9, "reasoning_summary": "code"}),
        jev_call=boom,
    )
    assert decision is not None and decision.role == "engineer"


def test_jev_disabled_is_never_called():
    decision = select_role_via_llm(
        "task",
        _cfg(),
        llm_call=_call_returning({"role": "engineer", "confidence": 0.9, "reasoning_summary": "code"}),
        jev_call=_must_not_call,
    )
    assert decision.role == "engineer"


def test_load_config_jev_section():
    cfg = load_role_routing_config(
        {"role_routing": {"strategy": "llm", "jev": {"enabled": True, "model": "~x/y", "timeout_seconds": 2, "min_confidence": 0.8}}}
    )
    assert cfg.jev_enabled is True
    assert cfg.jev_model == "~x/y"
    assert cfg.jev_timeout_seconds == 2.0
    assert cfg.jev_min_confidence == 0.8
    assert load_role_routing_config({"role_routing": {"strategy": "llm"}}).jev_enabled is False
    assert load_role_routing_config({"role_routing": {"jev": {"enabled": "yes"}}}).jev_enabled is False


def test_default_jev_call_sends_boundaries_and_parses_choice(monkeypatch):
    import io
    import json as _json
    from hermes_cli import role_llm_router as rlr

    seen = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = _json.loads(req.data)
        seen["timeout"] = timeout
        payload = {"answers": {"role": {"choice": "artist", "confidence": 0.97}}}
        return _Resp(_json.dumps(payload).encode())

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(rlr.urllib.request, "urlopen", fake_urlopen)
    out = rlr._default_jev_call(model="~typesafe/jev-latest", timeout_seconds=3.0, task="нарисуй кота")
    assert out == {"role": "artist", "confidence": 0.97}
    assert seen["url"] == rlr.JEV_DECISIONS_URL
    assert seen["auth"] == "Bearer sk-test"
    assert seen["timeout"] == 3.0
    question = seen["body"]["questions"]["role"]
    assert question["type"] == "choice"
    assert question["criteria"] == rlr._ROLE_DESCRIPTIONS
    assert seen["body"]["state"] == "нарисуй кота"


def test_default_jev_call_without_key_raises(monkeypatch):
    from hermes_cli import role_llm_router as rlr

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        rlr._default_jev_call(model="m", timeout_seconds=1.0, task="t")
