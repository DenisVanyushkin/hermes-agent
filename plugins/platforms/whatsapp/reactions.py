"""Emoji whitelist + normalisation for the WhatsApp dialogue reaction path.

A reaction that no ack consumed becomes an agent turn (spec:
reactions-dialogue, 2026-07-29). This module decides which reactions are
worth a turn at all.

Deliberately dependency-free and deliberately a *copy* of the normaliser in
custom/fam/fam/react.py: `custom/` is the operator's own package and is not
on the gateway's sys.path, so importing it here would make hermes core
depend on a per-deployment customisation. The two copies are pinned to the
same case table by their tests.
"""

import unicodedata

_SKIN_TONES = frozenset(chr(cp) for cp in range(0x1F3FB, 0x1F400))
_ZERO_WIDTH_JOINER = "‍"
_VARIATION_SELECTORS = ("️", "︎")

# Base forms AFTER normalize_emoji(). The six WhatsApp defaults plus the
# four the ack path already uses, so ❌ means the same thing on a reminder
# and in ordinary chat.
DIALOGUE_EMOJI = frozenset({
    "\U0001F44D",  # 👍
    "❤",      # ❤️
    "\U0001F602",  # 😂
    "\U0001F62E",  # 😮
    "\U0001F622",  # 😢
    "\U0001F64F",  # 🙏
    "\U0001F44E",  # 👎
    "❌",      # ❌
    "✅",      # ✅
    "\U0001F4AA",  # 💪
})


def normalize_emoji(emoji):
    """Strip variation selectors, ZWJ and skin-tone modifiers; NFC first."""
    if not emoji:
        return ""
    out = []
    for ch in unicodedata.normalize("NFC", str(emoji).strip()):
        if ch in _VARIATION_SELECTORS or ch == _ZERO_WIDTH_JOINER:
            continue
        if ch in _SKIN_TONES:
            continue
        out.append(ch)
    return "".join(out)


def is_dialogue_emoji(emoji):
    """True when this reaction is worth waking the agent for."""
    return normalize_emoji(emoji) in DIALOGUE_EMOJI
