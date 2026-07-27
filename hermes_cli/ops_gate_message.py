"""Сообщение операторского гейта.

Показывает то, что реально уйдёт в execve, и дословный запрос рядом: оператор
сравнивает план не с описанием плана, а с тем, что сам просил.
"""
from __future__ import annotations

from typing import Any, Mapping


def render_ops_approval_message(pending: Mapping[str, Any]) -> str:
    plan = list(pending.get("plan") or [])
    lines = ["🔧 ПЛАН ОПЕРАЦИЙ — нужно подтверждение", ""]
    destroy_ids: list[str] = []
    for index, item in enumerate(plan, start=1):
        lines.append(f"{index}. {item.get('op_id')}  ({item.get('risk')})")
        lines.append(f"   argv: {' '.join(item.get('argv') or [])}")
        lines.append(f"   cwd:  {pending.get('repo_path') or ''}")
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
