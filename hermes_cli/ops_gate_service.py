"""Операторский гейт для плана операций.

Зеркало commit_gate_service: маркер на диске, авторизация оператора, узкий
парсер ответа. Текстовый ответ, а не реакция -- реакции премиальны в WhatsApp,
текст работает на всех платформах.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

PENDING_TTL_SECONDS = 3600

_EXECUTE_WORDS = ("выполни", "выполняй", "execute")
_CANCEL_WORDS = ("отмена", "отмени", "отменить", "cancel", "не выполняй")
_CONFIRM_PREFIXES = ("подтверждаю", "confirm")


def _pending_path() -> Path:
    home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "state" / "ops_gate_pending.json"


def is_operator(user_id: str) -> bool:
    expected = (os.getenv("HERMES_OPERATOR_SLACK_UID") or "").strip()
    return bool(expected) and (user_id or "").strip() == expected


def record_pending(
    *,
    session_id: str,
    repo_path: str,
    plan: list[dict[str, Any]],
    original_task: str,
    created_at: float | None = None,
) -> None:
    path = _pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "session_id": str(session_id or ""),
        "repo_path": str(repo_path or ""),
        "plan": list(plan or []),
        "original_task": str(original_task or ""),
        "created_at": float(created_at if created_at is not None else time.time()),
        "status": "awaiting_ops",
    }))


def get_pending() -> dict[str, Any] | None:
    path = _pending_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("status") != "awaiting_ops":
        return None
    if time.time() - float(data.get("created_at") or 0) > PENDING_TTL_SECONDS:
        return None
    return data


def clear_pending() -> None:
    try:
        _pending_path().unlink()
    except FileNotFoundError:
        pass


def _contains_word(t: str, words: tuple[str, ...]) -> bool:
    # Word-boundary match, not substring: a naive `w in t` check makes
    # "выполни" match inside "выполнил" ("...выполнил задачу вчера" is
    # ordinary chat, not a gate reply), which is exactly the false-positive
    # this parser exists to avoid.
    return any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words)


def parse_ops_reply(text: str) -> str | None:
    """Узкий парсер: возвращает 'execute' | 'cancel' | None.

    None на всём, что не является недвусмысленным ответом гейту, чтобы обычная
    переписка никогда не запускала операции.
    """
    t = (text or "").strip().lower()
    if not t or len(t) > 40:
        return None
    if _contains_word(t, _CANCEL_WORDS):
        return "cancel"
    if _contains_word(t, _EXECUTE_WORDS):
        return "execute"
    return None


def parse_destroy_confirmation(text: str, op_id: str) -> bool:
    """Деструктив требует эха id операции: короткое «да» ставят не глядя."""
    t = (text or "").strip().lower()
    if not any(t.startswith(prefix) for prefix in _CONFIRM_PREFIXES):
        return False
    return str(op_id or "").strip().lower() in t
