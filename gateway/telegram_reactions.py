"""Telegram reaction-only response normalization helpers."""

from __future__ import annotations

from typing import Optional

_TELEGRAM_REACTION_ONLY_EMOJIS = {
    "👍",
    "👎",
    "👀",
    "🔥",
    "🚀",
    "✅",
    "❌",
}


def strip_telegram_reaction_only_response(text: Optional[str]) -> str:
    """Suppress bare emoji replies so Telegram shows only the reaction hook.

    When the model's final answer is just a reaction emoji, sending it as a
    normal Telegram message is noisy and duplicates the intent of the
    processing-lifecycle reaction hook. Returning an empty string here keeps the
    chat clean while still letting ``on_processing_complete()`` apply the
    actual Telegram reaction.
    """
    if not text:
        return ""

    candidate = text.strip().strip("*_`~|")
    if candidate in _TELEGRAM_REACTION_ONLY_EMOJIS:
        return ""
    return text
