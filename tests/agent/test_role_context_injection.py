from __future__ import annotations

from types import SimpleNamespace

from agent.conversation_loop import _compose_turn_user_message_content
from hermes_cli.profile_context import build_role_context_for_task


def test_role_context_is_injected_into_user_message_ephemeral_context():
    result = build_role_context_for_task("Check WebUI status and inspect logs")
    composed = _compose_turn_user_message_content(
        "Check WebUI status and inspect logs",
        role_context=result.context_text,
    )
    assert composed.startswith("Check WebUI status and inspect logs")
    assert "Engineer" in composed
    assert "Repo/code mutation is allowed" in composed


def test_role_context_is_not_injected_into_cached_system_prompt():
    agent = SimpleNamespace(_cached_system_prompt="SYSTEM PROMPT")
    result = build_role_context_for_task("Check WebUI status and inspect logs")
    composed = _compose_turn_user_message_content("Check WebUI status and inspect logs", role_context=result.context_text)
    assert agent._cached_system_prompt == "SYSTEM PROMPT"
    assert "SYSTEM PROMPT" not in composed


def test_cached_system_prompt_remains_unchanged():
    agent = SimpleNamespace(_cached_system_prompt="SYSTEM PROMPT")
    _compose_turn_user_message_content("Запиши меня на стрижку", role_context="role guidance")
    assert agent._cached_system_prompt == "SYSTEM PROMPT"


def test_model_selection_remains_unchanged():
    agent = SimpleNamespace(model="gpt-5.4-mini")
    _compose_turn_user_message_content("Check WebUI status and inspect logs", role_context="role guidance")
    assert agent.model == "gpt-5.4-mini"


def test_missing_contract_soft_falls_back_without_failing_the_turn():
    composed = _compose_turn_user_message_content("hello", role_context="")
    assert composed == "hello"


def test_general_operator_task_injects_personal_admin_context():
    result = build_role_context_for_task("Запиши меня на стрижку")
    composed = _compose_turn_user_message_content(
        "Запиши меня на стрижку",
        role_context=result.context_text,
    )
    assert result.selected_role == "general_operator"
    assert "General Operator" in composed
    assert "external commitment" in composed.lower()


def test_engineer_task_injects_engineering_context():
    result = build_role_context_for_task("Check WebUI status and inspect logs")
    composed = _compose_turn_user_message_content(
        "Check WebUI status and inspect logs",
        role_context=result.context_text,
    )
    assert result.selected_role == "engineer"
    assert "Engineer" in composed
    assert "Production/runtime mutation requires explicit approval" in composed


def test_security_auditor_and_scribe_are_not_auto_invoked_for_ordinary_tasks():
    result = build_role_context_for_task("Запиши меня на стрижку")
    composed = _compose_turn_user_message_content("Запиши меня на стрижку", role_context=result.context_text)
    assert "Security Auditor" not in composed
    assert "Scribe" not in composed


def test_role_context_module_path_is_additive_only():
    base = "Check WebUI status and inspect logs"
    role_context = "Role: Engineer\nPurpose: ..."
    composed = _compose_turn_user_message_content(base, role_context=role_context)
    assert composed == base + "\n\n" + role_context
