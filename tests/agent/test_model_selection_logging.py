"""The model-selection log line must describe what actually ran."""
import logging


def test_model_selection_logs_effective_model(caplog, monkeypatch):
    """policy/class may be advisory, but 'model=' must equal the model used."""
    caplog.set_level(logging.INFO, logger="agent.conversation_loop")
    from agent.conversation_loop import log_model_selection

    log_model_selection(
        session="s1", policy="coding_high_reasoning", model_class="coding",
        role="engineer", provider="openai-codex",
        policy_model="gpt-5.4", effective_model="gpt-5.6-luna",
    )
    line = caplog.records[-1].getMessage()
    assert "effective_model=gpt-5.6-luna" in line
    assert "policy_model=gpt-5.4" in line


def test_bare_model_field_is_gone(caplog):
    """The ambiguous 'model=' field is what made the line unreadable.

    It advertised the policy's preference as if it were the model in use, so
    'model selection: ... model=gpt-5.4' contradicted 'Turn ended: ... model=
    gpt-5.6-luna' for the same turn. Both names must now be explicit.
    """
    caplog.set_level(logging.INFO, logger="agent.conversation_loop")
    from agent.conversation_loop import log_model_selection

    log_model_selection(
        session="s1", policy="general", model_class="default",
        role="scribe", provider="openai-codex",
        policy_model="gpt-5.4-mini", effective_model="gpt-5.6-luna",
    )
    line = caplog.records[-1].getMessage()
    assert " model=" not in line


def test_both_fields_present_when_they_agree(caplog):
    caplog.set_level(logging.INFO, logger="agent.conversation_loop")
    from agent.conversation_loop import log_model_selection

    log_model_selection(
        session="s1", policy="general", model_class="default",
        role="general_operator", provider="openai-codex",
        policy_model="gpt-5.6-luna", effective_model="gpt-5.6-luna",
    )
    line = caplog.records[-1].getMessage()
    assert "policy_model=gpt-5.6-luna" in line
    assert "effective_model=gpt-5.6-luna" in line


def test_unknown_effective_model_is_not_silently_blank(caplog):
    """A missing effective model must read as unknown, not as agreement."""
    caplog.set_level(logging.INFO, logger="agent.conversation_loop")
    from agent.conversation_loop import log_model_selection

    log_model_selection(
        session="s1", policy="general", model_class="default",
        role="scribe", provider="openai-codex",
        policy_model="gpt-5.4-mini", effective_model=None,
    )
    line = caplog.records[-1].getMessage()
    assert "effective_model=unknown" in line


def test_fallback_fields_are_preserved(caplog):
    """Task 2 rewrites the line; it must not drop the fields already logged."""
    caplog.set_level(logging.INFO, logger="agent.conversation_loop")
    from agent.conversation_loop import log_model_selection

    log_model_selection(
        session="s1", policy="general", model_class="default",
        role="scribe", provider="openai-codex",
        policy_model="gpt-5.4-mini", effective_model="gpt-5.6-luna",
        fallback="general_fallback", allow_fallback=False,
    )
    line = caplog.records[-1].getMessage()
    assert line.startswith("model selection:")
    assert "session=s1" in line
    assert "policy=general" in line
    assert "class=default" in line
    assert "role=scribe" in line
    assert "provider=openai-codex" in line
    assert "fallback=general_fallback" in line
    assert "allow_fallback=False" in line
