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

# Exact, whole-message answer forms. The gate reply must BE the answer, not a
# sentence that merely contains one of these words -- "он написал: выполни"
# (quoted/reported speech) and "спасибо, выполнил задачу вчера" (an unrelated
# sentence with the same root) must not approve anything.
_EXECUTE_PHRASES = {"выполни", "выполняй", "execute", "да, выполняй"}
_CANCEL_PHRASES = {"отмена", "отмени", "отменить", "cancel", "не выполняй"}
_CONFIRM_PREFIXES = ("подтверждаю", "confirm")
_TRAILING_PUNCT = ".,!?;:…»›\"')]}~"


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
) -> bool:
    """Взвести маркер. True -- взведён, False -- слот занят живым маркером.

    Маркер один, а ответ оператора («выполни») адресован «текущему» плану, а не
    конкретному сообщению. Поэтому перезаписать чужой неистёкший маркер значит
    подставить свой план под чужое одобрение: оператор отвечает плану A, а
    выполняется план B. Новый прогон уступает -- пусть сначала ответят на
    висящий гейт. Истёкший маркер (и любой, который get_pending уже не считает
    отвечаемым) занимать слот не может и свободно замещается.
    """
    if get_pending() is not None:
        return False
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
    return True


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


def _normalize(text: str) -> str:
    """Strip surrounding whitespace and trailing punctuation, collapse internal
    whitespace, casefold. Used so the gate matches the whole message, not a
    substring buried inside a longer sentence."""
    t = (text or "").strip()
    t = t.rstrip(_TRAILING_PUNCT)
    t = re.sub(r"\s+", " ", t)
    return t.casefold()


def parse_ops_reply(text: str) -> str | None:
    """Узкий парсер: возвращает 'execute' | 'cancel' | None.

    Контракт: сообщение должно БЫТЬ ответом гейту целиком, а не просто
    содержать нужное слово -- иначе цитата ("он написал: выполни") или
    обычная фраза с тем же корнем ("выполнил задачу вчера") ошибочно
    одобрили бы операцию. Всё вне явного набора форм -> None; оператор
    перепечатывает точный ответ, ничего не выполняется молча.
    """
    t = _normalize(text)
    if not t or len(t) > 40:
        return None
    if t in _CANCEL_PHRASES:
        return "cancel"
    if t in _EXECUTE_PHRASES:
        return "execute"
    return None


def parse_destroy_confirmation(text: str, op_id: str) -> bool:
    """Деструктив требует эха id операции: короткое «да» ставят не глядя.

    Контракт узкий как и у parse_ops_reply: сообщение должно быть ровно
    "<префикс> <op_id>", а не предложением, которое просто упоминает оба.
    """
    t = _normalize(text)
    op = str(op_id or "").strip().casefold()
    if not t or not op:
        return False
    return any(t == f"{prefix} {op}" for prefix in _CONFIRM_PREFIXES)
