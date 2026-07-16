"""Turn-error alerts: notify Denis's Telegram admin channel when a
conversational turn degrades to a user-facing error stub.

Spec: agent-gateway docs/superpowers/specs/2026-07-16-amina-turn-error-alerts-design.md.
Feature is config-gated by `gateway.error_alerts.channel` (absent => off,
VPS-safe, same pattern as pipelines.allowed_platforms). Detection matches
the stable stub texts produced in gateway/run.py of THIS fork
(_gateway_provider_error_reply / _normalize_empty_agent_response / the
"(empty)" conversion) plus agent_result flags, so run.py needs no exports
here and there is no import cycle.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_MSG_LIMIT = 200
_ERR_LIMIT = 600
_SIG_ERR_PREFIX = 120
_ALMATY = timezone(timedelta(hours=5))

# category -> stable prefix of the user-facing stub (produced in run.py)
_STUB_PREFIXES = (
    ("provider-auth", "⚠️ Provider authentication failed."),
    ("provider-policy", "⚠️ The model provider rejected the request."),
    ("rate-limit", "⏱️ The model provider is rate-limiting requests."),
    ("provider-fail", "⚠️ The model provider failed after retries."),
    ("empty", "⚠️ The model returned no response after processing tool"),
    ("no-response", "⚠️ Processing completed but no response was generated."),
    ("context-overflow", "⚠️ Session too large for the model's context window."),
)

_ALERT_STATE: dict = {}  # signature -> {"last_sent": float, "suppressed": int}


def get_alert_config(config) -> dict | None:
    """Parse gateway.error_alerts; None when the feature is off/malformed."""
    from hermes_cli.config import cfg_get

    block = cfg_get(config, "gateway", "error_alerts", default=None)
    if not isinstance(block, dict):
        return None
    channel = str(block.get("channel") or "").strip()
    if not channel:
        return None
    try:
        dedup = int(block.get("dedup_minutes", 15))
    except (TypeError, ValueError):
        dedup = 15
    return {
        "channel": channel,
        "dedup_minutes": dedup,
        "include_user_message": bool(block.get("include_user_message", True)),
    }


def detect_turn_degradation(agent_result: dict, final_response: str) -> str | None:
    """Category of a degraded turn, or None for a healthy reply.

    Stub-prefix match runs before the flag checks so a `failed` turn whose
    stub is the context-overflow message classifies as the more informative
    `context-overflow`.
    """
    result = agent_result if isinstance(agent_result, dict) else {}
    text = str(final_response or "").strip()
    if result.get("interrupted"):
        return None  # /stop-driven, user-caused either way (spec §3)
    for category, prefix in _STUB_PREFIXES:
        if text.startswith(prefix):
            return category
    if result.get("failed"):
        return "failed"
    if result.get("partial") and text.startswith("⚠️ Processing stopped:"):
        return "partial"
    if text.startswith("The request failed:"):
        return "failed"
    return None


def dedup_decision(signature: tuple, now: float, window_minutes: int) -> tuple:
    """(send_now, accumulated_repeats). In-memory, reset on gateway restart."""
    entry = _ALERT_STATE.get(signature)
    if entry is None or now - entry["last_sent"] >= window_minutes * 60:
        repeats = entry["suppressed"] if entry else 0
        _ALERT_STATE[signature] = {"last_sent": now, "suppressed": 0}
        return True, repeats
    entry["suppressed"] += 1
    return False, entry["suppressed"]


def format_alert(*, category, platform, chat_label, user_message,
                 error_detail, repeats, now_utc) -> str:
    local = datetime.fromtimestamp(now_utc, tz=_ALMATY).strftime("%H:%M")
    lines = [
        "⚠️ Гермес: ошибка хода",
        f"Канал: {platform} / {chat_label}, {local} Алматы",
        f"Категория: {category}",
    ]
    if user_message:
        lines.append(f"Сообщение: «{str(user_message)[:_MSG_LIMIT]}»")
    if error_detail:
        lines.append(f"Ошибка: {str(error_detail)[:_ERR_LIMIT]}")
    if repeats:
        lines.append(f"(повторилась {repeats} раза за окно дедупа)")
    return "\n".join(lines)


def _resolve_hermes_argv() -> list | None:
    """`hermes` on PATH, else venv `python -m hermes_cli.main` (same
    fallback order as run.py:_resolve_hermes_bin — not imported to avoid
    a run.py import cycle)."""
    binary = shutil.which("hermes")
    if binary:
        return [binary]
    try:
        import importlib.util
        if importlib.util.find_spec("hermes_cli") is not None:
            return [sys.executable, "-m", "hermes_cli.main"]
    except Exception:
        pass
    return None


def _send_alert(channel: str, text: str) -> None:
    """Fire-and-forget `hermes send -t <channel>` in a daemon thread.
    Mirrors fam gate.notify_denis (text via stdin, 60s timeout); failures
    are logged, never raised."""
    def _worker():
        try:
            argv = _resolve_hermes_argv()
            if not argv:
                logger.warning("turn-error alert skipped: hermes CLI not resolvable")
                return
            result = subprocess.run(
                argv + ["send", "-t", channel],
                input=text, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning(
                    "turn-error alert send failed rc=%s stderr=%s",
                    result.returncode, (result.stderr or "")[:200],
                )
        except Exception:
            logger.warning("turn-error alert send crashed", exc_info=True)

    threading.Thread(target=_worker, daemon=True, name="turn-error-alert").start()


def maybe_alert_turn_error(config, *, platform, chat_id, chat_label=None,
                           user_message, agent_result, final_response,
                           now=None) -> None:
    """Single entry point for run.py. Detect a degraded turn and alert the
    admin channel. NEVER raises — the turn must not be affected."""
    try:
        alert_cfg = get_alert_config(config)
        if alert_cfg is None:
            return
        category = detect_turn_degradation(agent_result, final_response)
        if category is None:
            return
        platform_key = str(platform or "").strip().lower()
        if f"{platform_key}:{chat_id}" == alert_cfg["channel"].strip().lower():
            return  # the admin channel itself: visible live + no alert loops
        error_detail = None
        raw_error = (agent_result or {}).get("error") if isinstance(agent_result, dict) else None
        detail_source = raw_error or final_response
        if detail_source:
            try:
                from agent.redact import redact_sensitive_text
                error_detail = redact_sensitive_text(str(detail_source), force=True)
            except Exception:
                error_detail = str(detail_source)
        ts = now if now is not None else time.time()
        signature = (category, str(error_detail or "")[:_SIG_ERR_PREFIX])
        send_now, repeats = dedup_decision(signature, ts, alert_cfg["dedup_minutes"])
        if not send_now:
            return
        text = format_alert(
            category=category,
            platform=platform_key or "?",
            chat_label=str(chat_label or chat_id or "?"),
            user_message=user_message if alert_cfg["include_user_message"] else None,
            error_detail=error_detail,
            repeats=repeats,
            now_utc=ts,
        )
        _send_alert(alert_cfg["channel"], text)
    except Exception:
        logger.warning("maybe_alert_turn_error crashed", exc_info=True)
