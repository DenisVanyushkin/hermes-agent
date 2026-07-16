"""The thread_id delivery diagnostic must only fire for the origin conversation.

The warning compared origin.thread_id against every target's thread_id without
checking the target was the *same* conversation.  Cross-channel and
cross-platform fan-out (deliver="origin,slack:C0OTHER", deliver="telegram")
therefore logged "delivery target lost it" on every fire -- a false positive
that ran daily from 2026-07-03 and drove the morning report to recommend
fixing a non-bug.
"""

from __future__ import annotations

from cron.scheduler import _thread_id_dropped_for_origin_conversation

SLACK_ORIGIN = {
    "platform": "slack",
    "chat_id": "C0B3JFDM6NB",
    "thread_id": "1779345524.480199",
}


def test_real_drop_same_conversation_is_reported() -> None:
    """Same platform + same chat, thread silently gone: a genuine regression."""
    target = {"platform": "slack", "chat_id": "C0B3JFDM6NB", "thread_id": None}
    assert _thread_id_dropped_for_origin_conversation(SLACK_ORIGIN, target) is True


def test_thread_preserved_is_not_reported() -> None:
    target = {
        "platform": "slack",
        "chat_id": "C0B3JFDM6NB",
        "thread_id": "1779345524.480199",
    }
    assert _thread_id_dropped_for_origin_conversation(SLACK_ORIGIN, target) is False


def test_other_channel_same_platform_is_not_reported() -> None:
    """idle-idea-prompt: deliver='origin,slack:C0B55FPG5B7'.

    A different channel has no origin thread by definition -- posting at its
    root is correct, not a loss.
    """
    target = {"platform": "slack", "chat_id": "C0B55FPG5B7", "thread_id": None}
    assert _thread_id_dropped_for_origin_conversation(SLACK_ORIGIN, target) is False


def test_other_platform_is_not_reported() -> None:
    """Hermes morning digest: deliver='telegram' from a Slack origin.

    A Slack thread ts is meaningless on Telegram.
    """
    target = {"platform": "telegram", "chat_id": "79564752", "thread_id": None}
    assert _thread_id_dropped_for_origin_conversation(SLACK_ORIGIN, target) is False


def test_origin_without_thread_is_not_reported() -> None:
    origin = {"platform": "slack", "chat_id": "C0B3JFDM6NB", "thread_id": None}
    target = {"platform": "slack", "chat_id": "C0B3JFDM6NB", "thread_id": None}
    assert _thread_id_dropped_for_origin_conversation(origin, target) is False


def test_missing_origin_is_not_reported() -> None:
    target = {"platform": "slack", "chat_id": "C0B3JFDM6NB", "thread_id": None}
    assert _thread_id_dropped_for_origin_conversation({}, target) is False


def test_platform_and_chat_comparison_is_case_and_type_insensitive() -> None:
    """Targets are built from free-form deliver strings and jobs.json."""
    origin = {"platform": "Slack", "chat_id": 12345, "thread_id": "1.2"}
    target = {"platform": "slack", "chat_id": "12345", "thread_id": None}
    assert _thread_id_dropped_for_origin_conversation(origin, target) is True
