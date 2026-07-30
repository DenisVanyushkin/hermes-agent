"""Emoji normalisation + whitelist for the WhatsApp dialogue reaction path.

WhatsApp sends ❤️ as U+2764 U+FE0F and 👍🏽 with a skin-tone modifier, so a
naive `==` against a bare constant silently fails on real traffic. These
tests pin the same case table that custom/fam/fam/react.py is held to —
the two copies must not drift.
"""

import sys
from pathlib import Path

import pytest

from plugins.platforms.whatsapp.reactions import (
    DIALOGUE_EMOJI,
    is_dialogue_emoji,
    normalize_emoji,
)

# custom/fam is the operator's own package (see reactions.py's module
# docstring for why it isn't on the gateway's sys.path by default) --
# reach into it the same way custom/fam/tests do (`from fam import ...`),
# just with the path inserted explicitly since we're outside that package.
_CUSTOM_FAM = Path(__file__).resolve().parents[2] / "custom" / "fam"
if str(_CUSTOM_FAM) not in sys.path:
    sys.path.insert(0, str(_CUSTOM_FAM))

from fam.react import EMOJI_CONFIRM, EMOJI_SKIP, EMOJI_SNOOZE  # noqa: E402


def test_whitelist_has_exactly_thirteen_entries():
    assert len(DIALOGUE_EMOJI) == 13


@pytest.mark.parametrize("emoji", ["👍", "❤️", "😂", "😮", "😢", "🙏",
                                   "👎", "❌", "✅", "💪",
                                   "⏰", "🕐", "⏳"])
def test_all_thirteen_basic_reactions_pass_the_whitelist(emoji):
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


def test_ack_emoji_maps_are_a_subset_of_the_dialogue_whitelist():
    """custom/fam/fam/react.py's EMOJI_CONFIRM | EMOJI_SKIP | EMOJI_SNOOZE
    must stay a subset of DIALOGUE_EMOJI here.

    The removal filter and this whitelist now run BEFORE the ack hook
    (fam react-hook) is ever invoked, so an ack/snooze emoji outside
    DIALOGUE_EMOJI would silently never reach the hook -- no error,
    just a dropped ack. Nothing else enforces this relationship; this
    test is it.
    """
    ack_emoji = EMOJI_CONFIRM | EMOJI_SKIP | EMOJI_SNOOZE
    missing = ack_emoji - DIALOGUE_EMOJI
    assert not missing, (
        f"{missing} are in EMOJI_CONFIRM/EMOJI_SKIP/EMOJI_SNOOZE but not "
        "in DIALOGUE_EMOJI -- reactions with these emoji would silently "
        "never reach fam react-hook"
    )
