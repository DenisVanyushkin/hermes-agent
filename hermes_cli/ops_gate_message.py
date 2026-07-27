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

    Записанный путь резолвится в ГЛАВНЫЙ чекаут репозитория. В autonomous-режиме
    прогон живёт в per-run воркtree на ветке `hermes-run/*`, и именно этот путь
    попадает в маркер; исполнитель такую ветку не обслуживает (refused_run_branch),
    так что одобренный план оказался бы невыполним. Ops-операции адресованы
    репозиторию, а не черновику одного прогона: `git push` публикует ветку
    репозитория, `service_restart` вообще не про рабочее дерево. Побочно это
    переживает уборку воркtree ночным gc между показом плана и ответом оператора.
    """
    raw = str(repo_path or "").strip()
    if raw:
        return _main_checkout_or(raw)
    return str(Path(__file__).resolve().parent.parent)


def _main_checkout_or(raw: str) -> str:
    """Главный чекаут репозитория, которому принадлежит ``raw``; сам ``raw``,
    если git не смог ответить (не репозиторий, каталога больше нет, git недоступен).

    Резолвер один на весь гейт: `pipeline_autonomous_execution.main_checkout_of`
    (`git rev-parse --git-common-dir`) -- тот же, которым коммитный гейт
    отображает воркtree обратно на репозиторий. Импорт ленивый: модуль сообщения
    обязан оставаться лёгким, а оба вызывающих процесса эту зависимость уже
    держат. Операция идемпотентна, поэтому маркер может хранить уже
    отрезолвленный путь, а интерцепт -- резолвить его второй раз.
    """
    try:
        from hermes_cli.pipeline_autonomous_execution import main_checkout_of

        resolved = main_checkout_of(Path(raw))
    except Exception:
        # Резолв -- уточнение, а не условие показа плана: его сбой не должен
        # ни ронять рендер, ни прятать план от оператора.
        return raw
    return str(resolved) if resolved is not None else raw


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
