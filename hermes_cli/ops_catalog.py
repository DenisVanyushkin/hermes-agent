"""Каталог операций: единственный способ выразить ops-действие.

Агент заполняет параметры, argv строит код. Опасные операции защищены не
запретом, а отсутствием: чего нет в CATALOG, то невозможно вызвать -- ни
промптом, ни инъекцией из лога или веб-страницы.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from hermes_cli.ops_params import (
    OpsParamError,
    validate_branch,
    validate_container,
    validate_remote,
    validate_unit,
)

RISK_READ = "read"
RISK_MUTATE = "mutate"
RISK_DESTROY = "destroy"


class OpsCatalogError(ValueError):
    """Операция неизвестна либо её параметры не прошли валидацию."""


@dataclass(frozen=True)
class ResolvedOperation:
    op_id: str
    risk: str
    argv: tuple[str, ...]
    description: str
    irreversible: str | None


@dataclass(frozen=True)
class OpsOperation:
    op_id: str
    risk: str
    build_argv: Callable[[Mapping[str, object]], tuple[str, ...]]
    describe: Callable[[Mapping[str, object]], str]
    irreversible: str | None = None


def _git_status_argv(_params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "status", "--short", "--branch")


def _git_log_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    branch = validate_branch(params.get("branch") or "HEAD") if params.get("branch") else "HEAD"
    return ("git", "log", "--oneline", "--max-count=20", branch)


def _git_fetch_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "fetch", "--quiet", validate_remote(params.get("remote") or "origin"))


def _git_branch_list_argv(_params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "branch", "--list", "-vv")


def _service_status_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("systemctl", "status", "--no-pager", validate_unit(params.get("unit")))


def _journal_tail_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("journalctl", "--no-pager", "--lines=200", "--unit", validate_unit(params.get("unit")))


def _docker_ps_argv(_params: Mapping[str, object]) -> tuple[str, ...]:
    return ("docker", "ps", "--format", "{{.Names}}\\t{{.Status}}")


def _docker_logs_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("docker", "logs", "--tail", "200", validate_container(params.get("container")))


CATALOG: dict[str, OpsOperation] = {
    op.op_id: op
    for op in (
        OpsOperation("git_status", RISK_READ, _git_status_argv, lambda p: "состояние рабочего дерева"),
        OpsOperation("git_log", RISK_READ, _git_log_argv, lambda p: "последние 20 коммитов"),
        OpsOperation("git_fetch", RISK_READ, _git_fetch_argv, lambda p: "обновить remote-tracking ссылки"),
        OpsOperation("git_branch_list", RISK_READ, _git_branch_list_argv, lambda p: "список веток"),
        OpsOperation("service_status", RISK_READ, _service_status_argv, lambda p: f"статус юнита {p.get('unit')}"),
        OpsOperation("journal_tail", RISK_READ, _journal_tail_argv, lambda p: f"последние логи {p.get('unit')}"),
        OpsOperation("docker_ps", RISK_READ, _docker_ps_argv, lambda p: "список контейнеров"),
        OpsOperation("docker_logs", RISK_READ, _docker_logs_argv, lambda p: f"логи контейнера {p.get('container')}"),
    )
}


def resolve_operation(op_id: str, params: Mapping[str, object] | None = None) -> ResolvedOperation:
    operation = CATALOG.get(str(op_id or "").strip())
    if operation is None:
        raise OpsCatalogError(f"unknown_operation:{op_id}")
    safe_params = dict(params or {})
    try:
        argv = operation.build_argv(safe_params)
        description = operation.describe(safe_params)
    except OpsParamError as exc:
        raise OpsCatalogError(f"invalid_params:{op_id}:{exc}") from exc
    return ResolvedOperation(
        op_id=operation.op_id,
        risk=operation.risk,
        argv=tuple(argv),
        description=description,
        irreversible=operation.irreversible,
    )
