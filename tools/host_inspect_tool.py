"""Инструмент `host_inspect`: спросить о хосте сам хост.

Агент в песочнице видит вокруг себя контейнер и по умолчанию принимает его за
сервер. 2026-07-28 он отчитался, что playwright не установлен и порты 9222/9223
недоступны -- на хосте venv был на месте, оба порта слушали, и вся последующая
починка строилась на этих трёх фантомах. 2026-07-29 ошибка повторилась зеркально:
«путь существует и рабочий» -- на хосте это висячий симлинк на несуществующий
`/usr/local/bin/python3`, а в образе `python3.11` такой путь есть.

Блок «где выполнялись проверки» показывает такие выводы оператору, но самому
агенту знания не добавляет: он приклеивается к уже готовому ответу. Этот
инструмент даёт агенту спросить до того, как вывод сделан.

Собственной логики исполнения здесь нет: op_id резолвится каталогом
(`hermes_cli.ops_catalog`) и выполняется общим исполнителем
(`hermes_cli.ops_executor`) -- без shell, с валидацией параметров, таймаутом и
ограничением вывода. Единственное, что добавлено, -- жёсткая граница класса:
принимаются только операции `read`, апрув которым не нужен по построению.
"""
from __future__ import annotations

from typing import Any

from hermes_cli.ops_catalog import CATALOG, RISK_READ, OpsCatalogError, resolve_operation
from hermes_cli.ops_executor import OpsExecutionError, execute_operation
from hermes_cli.ops_gate_message import resolve_operation_cwd
from tools.registry import registry, tool_error

#: Только read-операции. Список строится из каталога, а не переписывается
#: руками: добавили read-операцию -- она появилась здесь, добавили mutate --
#: не появилась.
READ_OP_IDS = sorted(op_id for op_id, op in CATALOG.items() if op.risk == RISK_READ)

HOST_INSPECT_SCHEMA = {
    "name": "host_inspect",
    "description": (
        "Ask the HOST about its own state. Your terminal runs inside a sandbox "
        "container with its own filesystem, ports, processes and installed "
        "packages, so nothing you observe there describes the server. Use this "
        "tool before making any claim about the host: whether a path exists and "
        "who owns it, which TCP ports are listening, what a venv actually has "
        "installed, the state of a service or container. Read-only: it cannot "
        "change anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "op_id": {
                "type": "string",
                "enum": READ_OP_IDS,
                "description": (
                    "Which question to ask. host_path_stat returns metadata for a "
                    "path (type, owner, mode, size) and never file contents; "
                    "host_listening_ports lists listening TCP sockets; "
                    "venv_packages lists what a venv has installed."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Operation parameters, e.g. {\"path\": \"/var/lib/job-intel\"} for "
                    "host_path_stat, {\"venv\": \"...\"} for venv_packages, "
                    "{\"unit\": \"job-intel-daily.service\"} for service_status. "
                    "Paths and units are allowlisted; anything outside is refused."
                ),
            },
        },
        "required": ["op_id"],
    },
}


def _handle_host_inspect(args: dict[str, Any], **_kw: Any) -> str:
    op_id = str((args or {}).get("op_id") or "").strip()
    params = (args or {}).get("params") or {}
    if not isinstance(params, dict):
        return tool_error("host_inspect: 'params' must be an object")

    operation = CATALOG.get(op_id)
    if operation is None:
        return tool_error(
            f"host_inspect: unknown operation '{op_id}'. "
            f"Available: {', '.join(READ_OP_IDS)}"
        )
    if operation.risk != RISK_READ:
        # Не «забыли разрешить», а граница: этот инструмент не спрашивает
        # апрува, поэтому ему доступен только класс, которому апрув не нужен.
        return tool_error(
            f"host_inspect: '{op_id}' is not a read operation and is not permitted "
            "here. Only read operations run without operator approval."
        )

    try:
        resolved = resolve_operation(op_id, params)
    except OpsCatalogError as exc:
        return tool_error(f"host_inspect: {exc}")

    try:
        result = execute_operation(resolved, cwd=resolve_operation_cwd(None))
    except OpsExecutionError as exc:
        return tool_error(f"host_inspect: {exc}")
    except Exception as exc:  # исполнитель не должен ронять ход агента
        return tool_error(f"host_inspect: {type(exc).__name__}: {exc}")

    status = result.get("status")
    output = (result.get("output") or "").strip() or "(no output)"
    header = f"host: {resolved.description} (exit {status})"
    if result.get("truncated"):
        header += " [output truncated]"
    return f"{header}\n{output}"


registry.register(
    name="host_inspect",
    toolset="terminal",
    schema=HOST_INSPECT_SCHEMA,
    handler=_handle_host_inspect,
    emoji="🔎",
)
