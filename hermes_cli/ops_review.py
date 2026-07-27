"""Что ревьюер видит в плане операций и что его блокирует.

Политика та же, что в review_gate: блокирует ТИП находки, а не её тяжесть.
Операция, которой не просили, опасна независимо от того, насколько мягко
ревьюер о ней написал.
"""
from __future__ import annotations

from typing import Any, Mapping

OPS_HARD_FINDING_TYPES = frozenset({
    "ops_not_requested",
    "ops_wrong_target",
    "ops_risk_class_mismatch",
    "ops_unjustified_destroy",
})


def render_ops_review_block(plan: list[Mapping[str, Any]], original_task: str) -> str:
    lines = ["ПЛАН ОПЕРАЦИЙ НА РЕВЬЮ", ""]
    for index, item in enumerate(plan or [], start=1):
        lines.append(f"{index}. {item.get('op_id')}  ({item.get('risk')})")
        lines.append(f"   argv: {' '.join(item.get('argv') or [])}")
        lines.append(f"   эффект: {item.get('description') or ''}")
        if item.get("irreversible"):
            lines.append(f"   необратимо: {item['irreversible']}")
    lines.extend(["", "ДОСЛОВНЫЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ:", str(original_task or "")])
    lines.extend([
        "",
        "Проверь: нет ли операций шире запроса (ops_not_requested); та ли цель "
        "(ops_wrong_target); верен ли класс риска (ops_risk_class_mismatch); "
        "обоснован ли деструктив (ops_unjustified_destroy).",
    ])
    return "\n".join(lines)


def has_blocking_ops_finding(findings: list[Mapping[str, Any]] | None) -> bool:
    for finding in findings or []:
        if str(finding.get("type") or "").strip() in OPS_HARD_FINDING_TYPES:
            return True
    return False
