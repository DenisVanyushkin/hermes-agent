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
