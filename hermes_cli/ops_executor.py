"""Исполнение resolved-операции на хосте.

Инварианты собраны в одном месте, чтобы их можно было прочитать целиком:
никакого shell, фиксированный cwd, отказ в per-run воркtree, таймаут,
обрезка вывода.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from hermes_cli.ops_catalog import ResolvedOperation

_OUTPUT_LIMIT = 8000

# Совпадает с pipeline_autonomous_execution.RUN_BRANCH_PREFIX; дублируется, чтобы
# модуль оставался лёгким (та же причина, что в commit_gate_service).
RUN_BRANCH_PREFIX = "hermes-run/"


class OpsExecutionError(RuntimeError):
    """Операция не была выполнена."""


def _current_branch(cwd: Path, runner: Callable[..., Any]) -> str:
    completed = runner(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(cwd), text=True, capture_output=True, check=False,
    )
    return (getattr(completed, "stdout", "") or "").strip()


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

    try:
        completed = subprocess_runner(
            list(operation.argv),
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
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
