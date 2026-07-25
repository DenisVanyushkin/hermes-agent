"""The model a turn runs on now follows the role.

Until now select_model_policy() was advisory by construction and nothing applied
its answer: agent.model came from the gateway config and every role ran on
gpt-5.6-luna regardless of what the policy file said. This applies it.

The selector is the authority, not profile_tiers, because it is the only one of
the two that can express a task-dependent choice -- researcher takes luna for a
simple lookup and terra for synthesis over conflicting sources, which a flat tier
cannot say. Everywhere else the two must agree, and a test holds them to it.
"""
from types import SimpleNamespace

import pytest
import yaml
from pathlib import Path

from hermes_cli.model_selection import select_model_policy
from hermes_cli.profile_validation import DEFAULT_MODEL_POLICY_PATH

POLICY = yaml.safe_load(Path(DEFAULT_MODEL_POLICY_PATH).read_text())


def _config_model(role: str) -> str:
    return POLICY["tiers"][POLICY["profile_tiers"][role]]["model"]


def _selector_model(role: str, task: str = "сделай задачу") -> str:
    return select_model_policy(
        selected_role=role, canonical_role=role,
        task_text=task, critical_approval_required=False,
    ).preferred_model


# ── The two sources agree ───────────────────────────────────────────────────

@pytest.mark.parametrize("role", sorted(set(POLICY["profile_tiers"]) - {"researcher"}))
def test_selector_agrees_with_the_policy_file(role: str):
    assert _selector_model(role) == _config_model(role)


def test_chief_hermes_gets_the_reasoning_tier():
    """It had no branch at all and silently fell through to the default."""
    assert _selector_model("chief_hermes") == "gpt-5.6-terra"


def test_researcher_is_the_documented_exception():
    """A flat tier cannot express "cheap for lookups, strong for synthesis"."""
    assert _selector_model("researcher", "what is the weather today") == "gpt-5.6-luna"
    assert _selector_model(
        "researcher", "deep research: synthesize conflicting sources into a brief"
    ) == "gpt-5.6-terra"
    # The config records the cheap end of that range.
    assert _config_model("researcher") == "gpt-5.6-luna"


def test_the_cheap_research_path_only_fires_in_english():
    """A real limit of the heuristic, recorded rather than assumed away.

    _SIMPLE_RESEARCH_HINTS is an English word list ("weather", "news",
    "digest"...). This operator writes in Russian, so the lookup half of the
    researcher heuristic almost never fires for them and research turns land on
    the reasoning tier. That is a cost consequence of keeping the selector
    authoritative, and it should be visible here rather than discovered on a bill.
    """
    assert _selector_model("researcher", "какая погода в Алматы") == "gpt-5.6-terra"
    assert _selector_model("researcher", "what is the weather in Almaty") == "gpt-5.6-luna"


# ── The decision is applied ─────────────────────────────────────────────────

@pytest.fixture
def switches(monkeypatch):
    """Record calls to switch_model instead of performing them."""
    from agent import conversation_loop

    calls = []

    def _fake_switch(agent, new_model, new_provider, **kw):
        # The real switch_model assigns agent.model; apply_role_model reports what
        # actually took effect, so the fake has to do the same or the test would
        # be asserting against a state production never reaches.
        calls.append((new_model, new_provider))
        agent.model = new_model
        agent.provider = new_provider

    monkeypatch.setattr(conversation_loop, "switch_model", _fake_switch, raising=False)
    return calls


def _agent(model="gpt-5.6-luna", provider="openai-codex"):
    return SimpleNamespace(model=model, provider=provider, session_id="s1")


def test_a_role_with_a_different_model_switches(switches):
    from agent.conversation_loop import apply_role_model

    applied = apply_role_model(
        _agent(), preferred_model="gpt-5.6-terra", preferred_provider="openai-codex"
    )

    assert applied == "gpt-5.6-terra"
    assert switches == [("gpt-5.6-terra", "openai-codex")]


def test_a_role_already_on_its_model_does_not_switch(switches):
    from agent.conversation_loop import apply_role_model

    applied = apply_role_model(
        _agent(), preferred_model="gpt-5.6-luna", preferred_provider="openai-codex"
    )

    assert applied == "gpt-5.6-luna"
    assert switches == []


def test_a_model_outside_the_lineup_is_refused(switches):
    """A stale or typo'd policy entry must not be able to repoint the runtime."""
    from agent.conversation_loop import apply_role_model

    agent = _agent()
    applied = apply_role_model(
        agent, preferred_model="gpt-4o-mini", preferred_provider="openai-codex"
    )

    assert applied == "gpt-5.6-luna"   # unchanged
    assert switches == []


def test_an_empty_policy_model_is_a_no_op(switches):
    from agent.conversation_loop import apply_role_model

    applied = apply_role_model(_agent(), preferred_model="", preferred_provider="")
    assert applied == "gpt-5.6-luna"
    assert switches == []


def test_a_failing_switch_leaves_the_turn_on_its_current_model(monkeypatch):
    """Model selection must never be able to break a turn."""
    from agent import conversation_loop
    from agent.conversation_loop import apply_role_model

    def _boom(*_a, **_k):
        raise RuntimeError("provider refused")

    monkeypatch.setattr(conversation_loop, "switch_model", _boom, raising=False)

    agent = _agent()
    applied = apply_role_model(
        agent, preferred_model="gpt-5.6-terra", preferred_provider="openai-codex"
    )
    assert applied == "gpt-5.6-luna"
