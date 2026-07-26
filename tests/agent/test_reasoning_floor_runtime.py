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
    agent = _agent(
        {"enabled": True, "effort": "low"},
        _reasoning_session_override={"enabled": True, "effort": "low"},
    )

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


def test_the_raise_does_not_stick_across_turns_on_a_reused_agent():
    """CLI chat и TUI строят агента один раз на всю сессию.

    Никто не переприсваивает reasoning_config между ходами, поэтому без отката
    один инженерный вопрос закрепил бы high до конца сессии — включая ходы,
    политика которых об усилии мнения не имеет.
    """
    from hermes_cli.role_reasoning import apply_reasoning_floor

    agent = _agent({"enabled": True, "effort": "medium"})

    apply_reasoning_floor(agent, "high")          # инженерный ход
    assert agent.reasoning_config == {"enabled": True, "effort": "high"}

    effective = apply_reasoning_floor(agent, None)  # следующий ход, роль scribe

    assert agent.reasoning_config == {"enabled": True, "effort": "medium"}
    assert effective == "medium"


def test_an_external_change_between_turns_is_not_clobbered():
    """Откат снимает только наш собственный подъём, опознанный по идентичности."""
    from hermes_cli.role_reasoning import apply_reasoning_floor

    agent = _agent({"enabled": True, "effort": "medium"})
    apply_reasoning_floor(agent, "high")

    agent.reasoning_config = {"enabled": True, "effort": "xhigh"}   # /reasoning xhigh
    effective = apply_reasoning_floor(agent, None)

    assert agent.reasoning_config == {"enabled": True, "effort": "xhigh"}
    assert effective == "xhigh"


def test_the_session_level_is_restored_after_a_role_model_switch():
    """switch_model() перерезолвливает конфиг из файла и стирает сессионное
    значение ДО того, как пол его увидит. Освобождение обязано его вернуть,
    иначе /reasoning low не работает ровно на инженерных ходах."""
    from hermes_cli.role_reasoning import apply_reasoning_floor

    agent = _agent(
        {"enabled": True, "effort": "low"},
        _reasoning_session_override={"enabled": True, "effort": "low"},
    )
    # switch_model() затирает сессионное значение конфиговым:
    agent.reasoning_config = {"enabled": True, "effort": "medium"}

    effective = apply_reasoning_floor(agent, "high")

    assert agent.reasoning_config == {"enabled": True, "effort": "low"}
    assert effective == "low"


def test_the_restored_session_value_is_a_copy():
    """Гейтвей отдаёт сохранённый словарь по ссылке; мутация испортила бы
    настройку сессии навсегда."""
    from hermes_cli.role_reasoning import apply_reasoning_floor

    stored = {"enabled": True, "effort": "low"}
    agent = _agent({"enabled": True, "effort": "medium"}, _reasoning_session_override=stored)

    apply_reasoning_floor(agent, "high")

    assert agent.reasoning_config == stored
    assert agent.reasoning_config is not stored


def test_base_reasoning_config_reports_the_human_value_not_the_floor():
    """TUI пишет это значение в персистентный runtime сессии."""
    from hermes_cli.role_reasoning import apply_reasoning_floor, base_reasoning_config

    agent = _agent({"enabled": True, "effort": "medium"})
    apply_reasoning_floor(agent, "high")

    assert base_reasoning_config(agent) == {"enabled": True, "effort": "medium"}


def test_base_reasoning_config_passes_through_when_no_floor_was_applied():
    from hermes_cli.role_reasoning import base_reasoning_config

    agent = _agent({"enabled": True, "effort": "low"})

    assert base_reasoning_config(agent) == {"enabled": True, "effort": "low"}


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

    Агент здесь без атрибута _reasoning_session_override вовсе — это ровно форма
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
    assert not hasattr(agent, "_reasoning_session_override")

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


def test_the_turn_loop_actually_wires_the_floor_in():
    """Тесты выше проверяют ингредиенты. Этот — что их позвали.

    Настоящий блок живёт внутри хода и не поднимается в юнит-тесте без живого
    провайдера, поэтому здесь проверяется факт вызова и его место: пол берётся
    из решения политики, применяется ПОСЛЕ выбора модели и ДО лога, а лог
    получает все три новых поля. Опечатка в ключе или забытый вызов оставили
    бы всю остальную сюиту зелёной.
    """
    import pathlib

    src = pathlib.Path("agent/conversation_loop.py").read_text()
    start = src.index("_effective_model = apply_role_model(")
    block = src[start:start + 1800]

    assert 'agent._model_selection.get("reasoning_level", "")' in block
    assert "apply_reasoning_floor(agent, _effort_floor)" in block
    assert block.index("apply_role_model(") < block.index("apply_reasoning_floor(")
    assert block.index("apply_reasoning_floor(") < block.index("log_model_selection(")
    for field in ("policy_effort_floor=", "effective_effort=", "floor_exempt="):
        assert field in block, field


def test_the_policy_decision_really_carries_that_key():
    """Гайд-тест выше сверяет строку. Этот доказывает, что строка — не выдумка."""
    from hermes_cli.model_selection import model_selection_to_dict, select_model_policy

    decision = model_selection_to_dict(
        select_model_policy(
            selected_role="engineer",
            canonical_role="engineer",
            task_text="rebase",
            critical_approval_required=False,
        )
    )

    assert decision["reasoning_level"] == "high"


def test_the_fallback_reresolve_does_not_drop_the_floor():
    """Перерезолвка при фолбэке читает только конфиг и теряет пол — причём
    ровно на тех ходах, где основная модель уже отказала."""
    from hermes_cli.role_reasoning import apply_reasoning_floor, raise_to_floor

    agent = _agent({"enabled": True, "effort": "medium"})
    apply_reasoning_floor(agent, "high")
    assert agent.reasoning_config == {"enabled": True, "effort": "high"}

    # Имитируем то, что делает chat_completion_helpers при свопе модели:
    # конфиг перечитан с диска и снова говорит medium.
    agent.model = "gpt-5.6-luna"
    agent.reasoning_config = {"enabled": True, "effort": "medium"}

    restored = apply_reasoning_floor(agent, getattr(agent, "_reasoning_effort_floor", None))

    assert agent.reasoning_config == {"enabled": True, "effort": "high"}
    assert restored == "high"
    assert raise_to_floor(agent.reasoning_config, "high") is agent.reasoning_config


def test_the_production_fallback_path_restores_the_floor():
    """Контракт из теста выше бесполезен, если продакшн-путь его не зовёт.

    Настоящий фолбэк живёт глубоко внутри цикла вызова модели и не поднимается
    в тесте без сети и живого провайдера, поэтому здесь проверяется сам факт
    вызова и его порядок: восстановление обязано идти ПОСЛЕ перерезолвки,
    иначе оно перезатрётся тем же конфигом.
    """
    import pathlib

    src = pathlib.Path("agent/chat_completion_helpers.py").read_text()
    start = src.index("Re-resolve reasoning_config for the new fallback model")
    block = src[start:start + 1400]

    assert "apply_reasoning_floor(" in block, "фолбэк не восстанавливает пол"
    assert block.index("resolve_reasoning_config(") < block.index("apply_reasoning_floor(")
    assert 'apply_reasoning_floor(agent, getattr(agent, "_reasoning_effort_floor", None))' in block
    def _indent(needle: str) -> int:
        line_start = block.rindex("\n", 0, block.index(needle)) + 1
        return len(block[line_start:block.index(needle)])

    assert _indent("apply_reasoning_floor(agent, getattr(") == _indent("agent.reasoning_config = resolve_reasoning_config(")
