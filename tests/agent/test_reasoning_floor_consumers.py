"""Потребители доподъёмного конфига.

Пол — потурновый и по-ролевой. Три места брали `agent.reasoning_config` как
«настройку» и тем самым разносили одноходовое решение дальше, чем оно значит:
делегированные дети наследовали поднятое значение, а TUI писал его в
персистентный runtime сессии.
"""
import pathlib


def test_delegation_inherits_the_pre_floor_value():
    """Ребёнок-scribe не должен идти на high только потому, что родитель инженер.

    И наоборот: delegation.reasoning_effort: low — это явная настройка оператора,
    её не имеет права перебивать автоматический пол.
    """
    src = pathlib.Path("tools/delegate_tool.py").read_text()

    assert "base_reasoning_config(parent_agent)" in src
    assert 'parent_reasoning = getattr(parent_agent, "reasoning_config", None)' not in src


def test_the_tui_persists_the_human_value_not_the_floor():
    """_runtime_model_config кормит _persist_live_session_runtime, то есть
    значение переживает resume сессии."""
    src = pathlib.Path("tui_gateway/server.py").read_text()

    assert "base_reasoning_config(agent)" in src


def test_the_cli_marks_an_explicit_session_level_as_an_override():
    """В CLI освобождения не было вовсе: /reasoning low молча поднимался."""
    src = pathlib.Path("hermes_cli/cli_commands_mixin.py").read_text()

    assert "_reasoning_session_override = parsed" in src


def test_the_cli_stamps_that_override_onto_both_agents_it_builds():
    """Пропущенная площадка = молчаливый отказ escape hatch на этом пути.

    Их две: агент сессии и агент фоновой задачи /bg. Проверять надо именно
    штампы: подстрока `_reasoning_session_override` есть в этом файле и от
    самого обработчика /reasoning, поэтому «есть хоть где-то» ничего не пиннит.
    """
    import pathlib

    setup = pathlib.Path("hermes_cli/cli_agent_setup_mixin.py").read_text()
    commands = pathlib.Path("hermes_cli/cli_commands_mixin.py").read_text()

    assert "self.agent._reasoning_session_override = " in setup
    assert "bg_agent._reasoning_session_override = " in commands


def test_the_tui_spawn_paths_do_not_inherit_the_raised_value():
    """/background и перезапуск превью порождают НОВЫХ агентов от родительского
    конфига — тот же класс бага, что чинили в делегировании."""
    import pathlib

    src = pathlib.Path("tui_gateway/server.py").read_text()

    assert src.count("base_reasoning_config(agent)") >= 2
    assert 'getattr(agent, "reasoning_config", None) or _load_reasoning_config' not in src
