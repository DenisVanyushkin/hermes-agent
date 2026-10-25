"""Fork: tell the operator once when a session's transcript stops being written.

Upstream classifies a failed session-DB flush, heals what it can and ends the
turn with an explanation -- but every failure is one WARNING in the log. A
second consecutive failure means a deterministic storage problem, not a blip:
it is logged as an error and sent once (deduplicated) to the operator alert
channel, when ``gateway.error_alerts`` is configured. Moved from the
pre-v0.22.5 run_agent.py.
"""

from __future__ import annotations

import logging
import time
from typing import Any

# Imported at module level on purpose: a lazy import would read the alerter from disk at the
# moment of failure, possibly a different version than the rest of the process -- breaking
# exactly when it is the only one who can report the breakage.
from gateway.turn_error_alerts import dedup_decision, get_alert_config, send_operator_alert

logger = logging.getLogger("run_agent")  # the name the flush path has always logged under

_ERR_LIMIT = 600  # same as turn_error_alerts' private _ERR_LIMIT


def _format_alert(session_id: Any, exc: BaseException, streak: int) -> str:
    return "\n".join([
        "🔴 Гермес: транскрипт не пишется",
        f"Сессия: {session_id or '<нет id>'}",
        f"Сбоев подряд: {streak}",
        f"Ошибка: {str(exc)[:_ERR_LIMIT]}",
        "Диалог этой сессии в state.db не сохраняется.",
    ])


def _maybe_alert(session_id: Any, exc: BaseException, streak: int) -> None:
    """One deduplicated operator alert. Never raises: the alert must not break the write it guards."""
    try:
        from hermes_cli.config import load_config

        cfg = get_alert_config(load_config() or {})
        if not cfg:
            return  # no channel configured
        send_now, _repeats = dedup_decision(
            ("db_persist_failure", session_id, type(exc).__name__), time.time(), cfg["dedup_minutes"])
        if send_now:
            send_operator_alert(cfg["channel"], _format_alert(session_id, exc, streak))
    except Exception:
        logger.warning("persistence-failure alert crashed", exc_info=True)


def note_flush_failure(agent: Any, exc: BaseException) -> None:
    """Count consecutive flush failures; from the second on, error + one operator alert."""
    streak = getattr(agent, "_consecutive_flush_failures", 0) + 1
    agent._consecutive_flush_failures = streak
    if streak >= 2:
        logger.error("Session DB append_message failed %d× подряд — транскрипт сессии не сохраняется: %s",
                     streak, exc)
        _maybe_alert(getattr(agent, "session_id", None), exc, streak)
    else:
        logger.warning("Session DB append_message failed: %s", exc)


def note_flush_success(agent: Any) -> None:
    agent._consecutive_flush_failures = 0
