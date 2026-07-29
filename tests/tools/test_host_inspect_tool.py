"""Разговорный агент спрашивает о хосте хост, а не свой контейнер.

Дважды за двое суток агент сделал вывод о сервере по наблюдению из песочницы и
оба раза ошибся в разные стороны. 2026-07-28 отчитался, что playwright не
установлен и порты 9222/9223 недоступны, — на хосте venv на месте, оба порта
слушают. 2026-07-29 отчитался обратным: «путь существует и рабочий», — на хосте
это висячий симлинк на несуществующий /usr/local/bin/python3, а в контейнере
образа python3.11 этот путь есть.

Блок «где выполнялись проверки» такие выводы показывает оператору, но самому
агенту знания не добавляет: он приклеивается к уже готовому ответу.
"""
from unittest.mock import patch

import pytest

from tools.host_inspect_tool import HOST_INSPECT_SCHEMA, _handle_host_inspect


def _enum() -> list[str]:
    return HOST_INSPECT_SCHEMA["parameters"]["properties"]["op_id"]["enum"]


def test_schema_offers_only_read_class_operations():
    from hermes_cli.ops_catalog import CATALOG, RISK_READ

    for op_id in _enum():
        assert CATALOG[op_id].risk == RISK_READ, op_id


def test_schema_offers_the_host_inspection_operations():
    assert {"host_path_stat", "host_listening_ports", "venv_packages"} <= set(_enum())


def test_schema_says_the_sandbox_is_not_the_host():
    # Описание -- единственный рычаг, которым инструмент попадает в рассуждение
    # модели. Если оно не говорит про разницу, инструмент не будет вызван там,
    # где он нужен.
    text = HOST_INSPECT_SCHEMA["description"].lower()
    assert "host" in text
    assert "sandbox" in text or "container" in text


def test_a_read_operation_is_executed_and_its_output_returned():
    with patch("tools.host_inspect_tool.execute_operation") as run:
        run.return_value = {
            "op_id": "host_path_stat",
            "status": 0,
            "output": "/etc/job-intel type=directory owner=root:hermes mode=640",
            "truncated": False,
        }
        result = _handle_host_inspect(
            {"op_id": "host_path_stat", "params": {"path": "/etc/job-intel"}}
        )

    assert "owner=root:hermes" in str(result)
    assert run.call_count == 1


def test_a_nonzero_status_is_reported_rather_than_hidden():
    # «Интерпретатора нет» -- это и есть искомый факт о хосте.
    with patch("tools.host_inspect_tool.execute_operation") as run:
        run.return_value = {
            "op_id": "venv_packages",
            "status": 127,
            "output": "/var/lib/browser-desktop/playwright-venv/bin/python: No such file",
            "truncated": False,
        }
        result = _handle_host_inspect(
            {"op_id": "venv_packages", "params": {"venv": "/var/lib/browser-desktop/playwright-venv"}}
        )

    assert "127" in str(result)
    assert "No such file" in str(result)


@pytest.mark.parametrize("op_id", ["git_push", "git_reset_hard", "service_restart"])
def test_operations_outside_the_read_class_are_refused(op_id):
    # Инструмент не должен уметь менять состояние даже случайно: апрува у него
    # нет по построению, потому что класс read его не требует.
    with patch("tools.host_inspect_tool.execute_operation") as run:
        result = _handle_host_inspect({"op_id": op_id, "params": {"branch": "main"}})

    assert run.call_count == 0
    assert "read" in str(result).lower() or "not permitted" in str(result).lower()


def test_an_unknown_operation_is_refused():
    with patch("tools.host_inspect_tool.execute_operation") as run:
        result = _handle_host_inspect({"op_id": "rm_minus_rf", "params": {}})

    assert run.call_count == 0
    assert run.call_count == 0


def test_bad_parameters_come_back_as_a_message_not_an_exception():
    with patch("tools.host_inspect_tool.execute_operation") as run:
        result = _handle_host_inspect({"op_id": "host_path_stat", "params": {"path": "/etc/shadow"}})

    assert run.call_count == 0
    assert "invalid_host_path" in str(result) or "path" in str(result).lower()


def test_a_missing_op_id_is_refused():
    with patch("tools.host_inspect_tool.execute_operation") as run:
        result = _handle_host_inspect({})

    assert run.call_count == 0


def test_executor_failure_is_reported_not_raised():
    from hermes_cli.ops_executor import OpsExecutionError

    with patch("tools.host_inspect_tool.execute_operation", side_effect=OpsExecutionError("timeout:x")):
        result = _handle_host_inspect({"op_id": "host_listening_ports", "params": {}})

    assert "timeout" in str(result).lower()


def test_the_tool_is_registered_and_reaches_the_conversational_toolset():
    import toolsets
    from tools.registry import registry

    entry = registry.get_entry("host_inspect")
    assert entry is not None, "инструмент не зарегистрирован"

    # Регистрации мало: набор разговорного агента перечисляет имена поимённо,
    # и незанесённый туда инструмент модели просто не виден.
    assert "host_inspect" in toolsets._HERMES_CORE_TOOLS
