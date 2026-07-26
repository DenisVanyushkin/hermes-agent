"""Пол усилия, применённый к живому объекту агента.

Чистое сравнение живёт в tests/hermes_cli/test_role_reasoning.py. Здесь — три
вещи, которые видны только на агенте: что конфиг действительно переписывается,
что сессионное освобождение блокирует пол целиком, и что пол запоминается,
чтобы фолбэк мог его восстановить.
"""
from types import SimpleNamespace

from hermes_cli.role_reasoning import apply_reasoning_floor


def _agent(reasoning_config=None, **kw):
    return SimpleNamespace(
        model="gpt-5.6-terra",
        provider="openai-codex",
        session_id="s1",
        reasoning_config=reasoning_config,
        **kw,
    )


def test_a_lower_config_is_raised_on_the_agent():
    agent = _agent({"enabled": True, "effort": "medium"})

    effective = apply_reasoning_floor(agent, "high")

    assert agent.reasoning_config == {"enabled": True, "effort": "high"}
    assert effective == "high"


def test_a_session_override_is_exempt():
    """Явное /reasoning low для этой сессии главнее автоматики."""
    agent = _agent({"enabled": True, "effort": "low"}, _reasoning_floor_exempt=True)

    effective = apply_reasoning_floor(agent, "high")

    assert agent.reasoning_config == {"enabled": True, "effort": "low"}
    assert effective == "low"


def test_the_floor_is_remembered_for_the_fallback_path():
    """Фолбэк перерезолвливает конфиг из файла и обязан вернуть пол назад."""
    agent = _agent({"enabled": True, "effort": "medium"})

    apply_reasoning_floor(agent, "high")

    assert agent._reasoning_effort_floor == "high"


def test_no_floor_leaves_everything_alone():
    agent = _agent({"enabled": True, "effort": "medium"})

    effective = apply_reasoning_floor(agent, None)

    assert agent.reasoning_config == {"enabled": True, "effort": "medium"}
    assert effective == "medium"


def test_a_broken_agent_never_breaks_the_turn():
    """Выбор параметров модели не имеет права уронить ход."""

    class Hostile:
        model = "gpt-5.6-terra"
        reasoning_config = {"enabled": True, "effort": "low"}

        def __setattr__(self, name, value):
            raise RuntimeError("read-only agent")

    effective = apply_reasoning_floor(Hostile(), "high")

    assert effective in {"low", "-"}   # ход продолжается, исключение не летит
def test_the_log_line_separates_the_policy_demand_from_the_fact(caplog):
    """policy_model= и effective_model= уже печатаются раздельно ровно потому,
    что лог однажды утверждал одно, а ход шёл на другом. Усилие — тот же случай."""
    import logging

    from agent.conversation_loop import log_model_selection

    with caplog.at_level(logging.INFO, logger="agent.conversation_loop"):
        log_model_selection(
            session="s1",
            policy="coding_high_reasoning",
            model_class="coding",
            role="engineer",
            provider="openai-codex",
            policy_model="gpt-5.6-terra",
            effective_model="gpt-5.6-terra",
            policy_effort_floor="high",
            effective_effort="high",
            floor_exempt=False,
        )

    line = caplog.text
    assert "policy_effort_floor=high" in line
    assert "effective_effort=high" in line
    assert "effort_floor_exempt=-" in line


def test_the_log_line_names_the_session_exemption(caplog):
    import logging

    from agent.conversation_loop import log_model_selection

    with caplog.at_level(logging.INFO, logger="agent.conversation_loop"):
        log_model_selection(
            session="s1",
            policy="coding_high_reasoning",
            model_class="coding",
            role="engineer",
            provider="openai-codex",
            policy_model="gpt-5.6-terra",
            effective_model="gpt-5.6-terra",
            policy_effort_floor="high",
            effective_effort="low",
            floor_exempt=True,
        )

    assert "effort_floor_exempt=session" in caplog.text


def test_an_engineer_turn_goes_from_the_configured_medium_to_high():
    """Сквозной путь от решения политики до конфига на агенте.

    Агент здесь без атрибута _reasoning_floor_exempt вовсе — это ровно форма
    кроновского прогона: сессионного слоя там нет, поэтому ни одна джоба не
    нуждается в новых полях в jobs.json, чтобы получить своё усилие.
    """
    from hermes_cli.model_selection import select_model_policy
    from hermes_cli.role_reasoning import (
        apply_reasoning_floor,
        resolve_role_effort_floor,
    )

    decision = select_model_policy(
        selected_role="engineer",
        canonical_role="engineer",
        task_text="upstream sync run: rebase local customizations",
        critical_approval_required=False,
    )
    agent = _agent({"enabled": True, "effort": "medium"})
    assert not hasattr(agent, "_reasoning_floor_exempt")

    effective = apply_reasoning_floor(
        agent, resolve_role_effort_floor(decision.reasoning_level)
    )

    assert decision.policy_name == "coding_high_reasoning"
    assert effective == "high"
    assert agent.reasoning_config == {"enabled": True, "effort": "high"}


def test_a_scribe_turn_is_left_on_the_configured_level():
    """Политика scribe называет уровень "stable" — это не усилие, и пол не
    появляется. Ход остаётся ровно там, где его поставил конфиг."""
    from hermes_cli.model_selection import select_model_policy
    from hermes_cli.role_reasoning import (
        apply_reasoning_floor,
        resolve_role_effort_floor,
    )

    decision = select_model_policy(
        selected_role="scribe",
        canonical_role="scribe",
        task_text="оформи отчёт",
        critical_approval_required=False,
    )
    agent = _agent({"enabled": True, "effort": "medium"})

    effective = apply_reasoning_floor(
        agent, resolve_role_effort_floor(decision.reasoning_level)
    )

    assert effective == "medium"
    assert agent.reasoning_config == {"enabled": True, "effort": "medium"}
