"""Сообщение операторского гейта.

Показывает то, что реально уйдёт в execve, и дословный запрос рядом: оператор
сравнивает план не с описанием плана, а с тем, что сам просил.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def resolve_operation_cwd(repo_path: Any) -> str:
    """Директория, в которой операции реально выполнятся.

    Единственный источник этого решения: интерцепт берёт cwd отсюда же, поэтому
    показанный оператору `cwd:` не может разойтись с фактическим. Пустой путь --
    это «неизвестно», а не «текущая директория» (Path("") -> "."), и подставлять
    в сообщение пусто нельзя: «где» -- половина смысла `git push` и
    `git reset --hard`. Фолбэк тот же, что у коммитного гейта: корень
    репозитория, в котором лежит этот модуль.
    """
    raw = str(repo_path or "").strip()
    if raw:
        return raw
    return str(Path(__file__).resolve().parent.parent)


def render_ops_approval_message(pending: Mapping[str, Any]) -> str:
    plan = list(pending.get("plan") or [])
    cwd = resolve_operation_cwd(pending.get("repo_path"))
    lines = ["🔧 ПЛАН ОПЕРАЦИЙ — нужно подтверждение", ""]
    destroy_ids: list[str] = []
    for index, item in enumerate(plan, start=1):
        lines.append(f"{index}. {item.get('op_id')}  ({item.get('risk')})")
        lines.append(f"   argv: {' '.join(item.get('argv') or [])}")
        lines.append(f"   cwd:  {cwd}")
        lines.append(f"   эффект: {item.get('description') or ''}")
        lines.append(f"   необратимо: {item.get('irreversible') or 'нет'}")
        if str(item.get("risk") or "") == "destroy":
            destroy_ids.append(str(item.get("op_id")))
        lines.append("")
    lines.append(f"Запрос был: «{pending.get('original_task') or ''}»")
    lines.append("")
    if destroy_ids:
        confirmations = ", ".join(f"«подтверждаю {op_id}»" for op_id in destroy_ids)
        lines.append(f"План содержит необратимые операции. Ответь {confirmations} или «отмена».")
    else:
        lines.append("Ответь: «выполни» или «отмена».")
    return "\n".join(lines)
