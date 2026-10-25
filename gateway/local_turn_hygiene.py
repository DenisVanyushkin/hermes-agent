"""Fork: what the gateway keeps out of a chat, and what it tells the agent when the user is gone.

* Clarify internals. The model's interim commentary before a ``clarify`` call
  ("clarify: asking which day"), the long-running heartbeat while it waits
  ("executing tool: clarify") and a queued follow-up that ends on such a
  summary are machinery, not replies: they reached the user as stray bubbles
  next to the clarify card itself.
* Clarify timeout. A bare "[user did not respond within Nm]" states a fact
  with no instruction, and agents read it as "keep waiting": a request that was
  already half-understood got dropped instead of recorded with a default
  (observed live: a reminder and an errand, neither written). The prose mirrors
  clarify_tool.TIMEOUT_RESPONSE, which the CLI path has always returned; the
  "[user did not respond" prefix is load-bearing for
  context_compressor._CLARIFY_NON_RESPONSE_PREFIXES.
* News ingest group. The Telegram news-source group is read by the news
  collector, never answered: its messages must not reach the agent, the
  pipeline router or the engineering path.

Moved from the pre-v0.22.5 gateway/run.py and agent/conversation_loop.py; the
upstream call sites are marked.
"""

from __future__ import annotations

from typing import Any

NEWS_INGEST_ONLY_TELEGRAM_CHAT_IDS = frozenset({"-1003989016070"})


def clarify_timeout_text(timeout_seconds: float) -> str:
    return (
        f"[user did not respond within {int(timeout_seconds / 60)}m] "
        "Use your best judgement: act on whatever is already clear, "
        "choose a sensible default for whatever is not, and say what "
        "you assumed so the user can correct it."
    )


def looks_like_internal_clarify_summary(text: Any) -> bool:
    """True for an interim/final text that is only the model's clarify summary."""
    return str(text or "").strip().lower().startswith("clarify:")


def is_internal_clarify_activity_text(text: Any) -> bool:
    """True for clarify-internal activity/status labels."""
    body = str(text or "").strip().lower()
    if not body:
        return False
    return (
        body == "clarify"
        or body.startswith("clarify:")
        or body.startswith("executing tool: clarify")
        or body.startswith("tool completed: clarify")
        or "waiting for user clarify response" in body
    )


def assistant_turn_has_tool_call_named(assistant_message: Any, tool_name: str) -> bool:
    """True when the assistant turn (dict or object) includes a call to ``tool_name``."""
    target = str(tool_name or "").strip()
    if not target:
        return False
    if isinstance(assistant_message, dict):
        tool_calls = assistant_message.get("tool_calls")
    else:
        tool_calls = getattr(assistant_message, "tool_calls", None)
    for tool_call in tool_calls or []:
        if isinstance(tool_call, dict):
            fn = tool_call.get("function")
            name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        else:
            name = getattr(getattr(tool_call, "function", None), "name", None)
        if str(name or "").strip() == target:
            return True
    return False


def classify_news_ingest_only_source(source: Any) -> dict[str, Any]:
    platform = getattr(source, "platform", None)
    platform_value = str(getattr(platform, "value", platform) or "").strip().lower()
    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    ingest_only = platform_value == "telegram" and chat_id in NEWS_INGEST_ONLY_TELEGRAM_CHAT_IDS
    return {
        "telegram_chat_mode": "news_ingest_only" if ingest_only else "interactive",
        "telegram_chat_id": chat_id or None,
        "telegram_reply_allowed": not ingest_only,
        "ingest_only": ingest_only,
        "news_digest_candidate": ingest_only,
        "model_invoked": False,
        "pipeline_routing_skipped_reason": "news_ingest_only" if ingest_only else None,
    }
