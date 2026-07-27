"""Исполнение resolved-операции на хосте.

Инварианты собраны в одном месте, чтобы их можно было прочитать целиком:
никакого shell, фиксированный cwd, отказ в per-run воркtree, таймаут,
обрезка вывода.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from hermes_cli.ops_catalog import ResolvedOperation

_OUTPUT_LIMIT = 8000

# Совпадает с pipeline_autonomous_execution.RUN_BRANCH_PREFIX; дублируется, чтобы
# модуль оставался лёгким (та же причина, что в commit_gate_service).
RUN_BRANCH_PREFIX = "hermes-run/"

# Только эти операции обращаются к remote и нуждаются в токене; всё остальное
# выполняется без единого credential в окружении.
_REMOTE_OPERATIONS = frozenset({"git_push", "git_push_force_with_lease", "git_fetch"})

# Токен уходит в окружение процесса, а не в argv: командная строка видна в `ps`
# любому пользователю хоста, окружение чужого процесса -- нет. Хелпер печатает
# username/password на стандартный вывод по запросу git, забирая пароль из
# переменной окружения, а не из своего собственного текста.
_CREDENTIAL_HELPER = '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'


def _git_credential_env(op_id: str) -> dict[str, str]:
    """Переменные окружения для операции, обращающейся к remote.

    Возвращает пустой словарь для всего, что не входит в _REMOTE_OPERATIONS --
    остальные операции не получают ни токена, ни какого-либо credential.
    """
    if op_id not in _REMOTE_OPERATIONS:
        return {}
    home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    try:
        for line in (home / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("GITHUB_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                return {"GITHUB_TOKEN": token} if token else {}
    except OSError:
        return {}
    return {}


class OpsExecutionError(RuntimeError):
    """Операция не была выполнена."""


def _current_branch(cwd: Path, runner: Callable[..., Any]) -> str:
    completed = runner(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(cwd), text=True, capture_output=True, check=False,
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        # Fail closed: an unresolved branch must never be treated as "not a
        # run branch". A rev-parse failure (not a repo, lock contention,
        # permission error) is not evidence of safety.
        raise OpsExecutionError(f"branch_unresolved:{cwd}")
    branch = (getattr(completed, "stdout", "") or "").strip()
    if not branch:
        raise OpsExecutionError(f"branch_unresolved:{cwd}")
    return branch


def execute_operation(
    operation: ResolvedOperation,
    *,
    cwd: Path,
    timeout: int = 120,
    subprocess_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    cwd = Path(cwd)
    try:
        branch = _current_branch(cwd, subprocess_runner)
    except subprocess.TimeoutExpired as exc:
        # Тот же subprocess_runner используется для проверки ветки и для самой
        # операции — таймаут на любом из двух вызовов обязан стать
        # OpsExecutionError, а не голым исключением.
        raise OpsExecutionError(f"timeout:{operation.op_id}") from exc
    if branch.startswith(RUN_BRANCH_PREFIX):
        # Per-run ветка интегрируется review-шагом, а не публикуется и не
        # обслуживается ops-операциями: та же граница, что в commit_gate_service.
        raise OpsExecutionError(f"refused_run_branch:{branch}")

    argv = list(operation.argv)
    credential_env = _git_credential_env(operation.op_id)
    if credential_env:
        # Временный helper только для этого вызова: git читает его через -c,
        # ничего не пишется в .git/config и в URL remote не попадает.
        argv = [argv[0], "-c", f"credential.helper={_CREDENTIAL_HELPER}", *argv[1:]]
        run_env = {**os.environ, **credential_env}
    else:
        # env=None would inherit the ambient process environment as-is --
        # and hermes_cli.env_loader.load_hermes_dotenv() (called at import
        # time from cli.py, main.py, gateway/run.py, the very processes that
        # host this executor) already loaded GITHUB_TOKEN into os.environ.
        # So a bare inherit would leak the token into every non-remote
        # operation's subprocess too, silently defeating the whole point of
        # _git_credential_env returning {} for them. Strip *only*
        # GITHUB_TOKEN, not a *_TOKEN/*_KEY/*_SECRET pattern: several
        # operations (gateway_restart in particular) spawn real, separately
        # privileged processes with their own legitimate credential needs,
        # and a broad strip could silently break one in a way no offline
        # test would catch.
        run_env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}

    try:
        completed = subprocess_runner(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=run_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpsExecutionError(f"timeout:{operation.op_id}") from exc

    raw = ((getattr(completed, "stdout", "") or "") + (getattr(completed, "stderr", "") or "")).strip()
    truncated = len(raw) > _OUTPUT_LIMIT
    return {
        "op_id": operation.op_id,
        "status": int(getattr(completed, "returncode", 1)),
        "output": raw[:_OUTPUT_LIMIT],
        "truncated": truncated,
    }
