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
    validate_host_path,
    validate_remote,
    validate_unit,
    validate_venv_path,
)

# Классы риска описывают воздействие на СОСТОЯНИЕ, а не факт записи на диск.
# read   -- не меняет ни репозиторий, ни сервис, ни remote. git_fetch сюда входит:
#           он обновляет только кэш remote-tracking ссылок (прецедент -- живой
#           git_remote_status, который делает fetch без апрува).
# mutate -- меняет состояние обратимо.
# destroy -- меняет состояние необратимо; каждая такая операция обязана заполнить
#           irreversible.
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


def _git_push_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "push", validate_remote(params.get("remote") or "origin"),
            validate_branch(params.get("branch")))


def _git_push_force_with_lease_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    # --force-with-lease, а не --force: отказывается перезаписать чужие коммиты,
    # появившиеся на remote после последнего fetch.
    return ("git", "push", "--force-with-lease", validate_remote(params.get("remote") or "origin"),
            validate_branch(params.get("branch")))


def _git_merge_ff_only_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "merge", "--ff-only", validate_branch(params.get("branch")))


def _git_branch_create_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "branch", validate_branch(params.get("branch")))


def _git_branch_delete_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "branch", "-D", validate_branch(params.get("branch")))


def _git_tag_create_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "tag", validate_branch(params.get("tag")))


def _git_tag_delete_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "tag", "-d", validate_branch(params.get("tag")))


def _git_reset_hard_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("git", "reset", "--hard", validate_branch(params.get("branch")))


def _service_restart_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("sudo", "-n", "systemctl", "restart", validate_unit(params.get("unit")))


def _docker_restart_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    return ("docker", "restart", validate_container(params.get("container")))


def _gateway_restart_argv(_params: Mapping[str, object]) -> tuple[str, ...]:
    return ("hermes", "gateway", "restart")


def _host_path_stat_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    # Только метаданные: имя, тип, владелец, режим, размер, время изменения.
    # Содержимое не читается ни здесь, ни где-либо ещё в классе read.
    return (
        "stat",
        "--format=%n type=%F owner=%U:%G mode=%a size=%s mtime=%y",
        validate_host_path(params.get("path")),
    )


def _host_listening_ports_argv(_params: Mapping[str, object]) -> tuple[str, ...]:
    return ("ss", "--listening", "--tcp", "--numeric", "--processes")


def _venv_packages_argv(params: Mapping[str, object]) -> tuple[str, ...]:
    venv = validate_venv_path(params.get("venv"))
    return (f"{venv}/bin/python", "-m", "pip", "list", "--format=json")


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
        OpsOperation("host_path_stat", RISK_READ, _host_path_stat_argv,
                     lambda p: f"метаданные пути {p.get('path')} на хосте"),
        OpsOperation("host_listening_ports", RISK_READ, _host_listening_ports_argv,
                     lambda p: "слушающие TCP-порты хоста"),
        OpsOperation("venv_packages", RISK_READ, _venv_packages_argv,
                     lambda p: f"состав пакетов venv {p.get('venv')}"),
        OpsOperation("git_push", RISK_MUTATE, _git_push_argv,
                     lambda p: f"опубликовать {p.get('branch')} в {p.get('remote') or 'origin'}"),
        OpsOperation("git_merge_ff_only", RISK_MUTATE, _git_merge_ff_only_argv,
                     lambda p: f"влить {p.get('branch')} fast-forward"),
        OpsOperation("git_branch_create", RISK_MUTATE, _git_branch_create_argv,
                     lambda p: f"создать ветку {p.get('branch')}"),
        OpsOperation("git_tag_create", RISK_MUTATE, _git_tag_create_argv,
                     lambda p: f"создать тег {p.get('tag')}"),
        OpsOperation("service_restart", RISK_MUTATE, _service_restart_argv,
                     lambda p: f"перезапустить {p.get('unit')}"),
        OpsOperation("docker_restart", RISK_MUTATE, _docker_restart_argv,
                     lambda p: f"перезапустить контейнер {p.get('container')}"),
        OpsOperation("gateway_restart", RISK_MUTATE, _gateway_restart_argv,
                     lambda p: "перезапустить гейтвей"),
        OpsOperation("git_push_force_with_lease", RISK_DESTROY, _git_push_force_with_lease_argv,
                     lambda p: f"перезаписать {p.get('branch')} в origin",
                     irreversible="коммиты, бывшие на origin, перестанут быть достижимы по ветке"),
        OpsOperation("git_branch_delete", RISK_DESTROY, _git_branch_delete_argv,
                     lambda p: f"удалить ветку {p.get('branch')}",
                     irreversible="невлитые коммиты ветки восстановимы только по SHA из вывода команды"),
        OpsOperation("git_reset_hard", RISK_DESTROY, _git_reset_hard_argv,
                     lambda p: f"сбросить рабочее дерево на {p.get('branch')}",
                     irreversible="незакоммиченные изменения пропадут, а незапушенные коммиты после цели станут недостижимы"),
        OpsOperation("git_tag_delete", RISK_DESTROY, _git_tag_delete_argv,
                     lambda p: f"удалить тег {p.get('tag')}",
                     irreversible="тег придётся восстанавливать по SHA вручную"),
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
