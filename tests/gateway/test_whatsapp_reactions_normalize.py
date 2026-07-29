"""Emoji normalisation + whitelist for the WhatsApp dialogue reaction path.

WhatsApp sends ❤️ as U+2764 U+FE0F and 👍🏽 with a skin-tone modifier, so a
naive `==` against a bare constant silently fails on real traffic. These
tests pin the same case table that custom/fam/fam/react.py is held to —
the two copies must not drift.
"""

import pytest

from plugins.platforms.whatsapp.reactions import (
    DIALOGUE_EMOJI,
    is_dialogue_emoji,
    normalize_emoji,
)


def test_whitelist_has_exactly_ten_entries():
    assert len(DIALOGUE_EMOJI) == 10


@pytest.mark.parametrize("emoji", ["👍", "❤️", "😂", "😮", "😢", "🙏",
                                   "👎", "❌", "✅", "💪"])
def test_all_ten_basic_reactions_pass_the_whitelist(emoji):
    assert is_dialogue_emoji(emoji) is True


def test_variation_selector_is_stripped():
    assert normalize_emoji("❤️") == "❤"
    assert is_dialogue_emoji("❤️") is True


def test_skin_tone_modifier_is_stripped():
    assert normalize_emoji("\U0001F44D\U0001F3FD") == "\U0001F44D"
    assert is_dialogue_emoji("\U0001F44D\U0001F3FD") is True


def test_surrounding_whitespace_is_stripped():
    assert is_dialogue_emoji("  👍 ") is True


@pytest.mark.parametrize("emoji", ["🦆", "🎉", "🔥", "😡", ""])
def test_emoji_outside_the_whitelist_is_rejected(emoji):
    assert is_dialogue_emoji(emoji) is False


def test_normalize_emoji_tolerates_none_like_input():
    assert normalize_emoji("") == ""
