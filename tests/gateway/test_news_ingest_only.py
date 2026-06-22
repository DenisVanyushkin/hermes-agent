from __future__ import annotations

from types import SimpleNamespace
import importlib

import pytest


def test_classify_news_ingest_only_source_marks_configured_chat() -> None:
    module = importlib.import_module("gateway.run")
    policy = module._classify_news_ingest_only_source(
        SimpleNamespace(platform="telegram", chat_id="-1003989016070")
    )

    assert policy["telegram_chat_mode"] == "news_ingest_only"
    assert policy["telegram_reply_allowed"] is False
    assert policy["ingest_only"] is True
    assert policy["news_digest_candidate"] is True
    assert policy["pipeline_routing_skipped_reason"] == "news_ingest_only"


@pytest.mark.asyncio
async def test_handle_message_returns_none_before_agent_for_news_ingest_only_chat() -> None:
    module = importlib.import_module("gateway.run")
    source = SimpleNamespace(
        platform="telegram",
        chat_id="-1003989016070",
        user_id="user-1",
        user_name="news-bot",
        chat_type="group",
    )
    event = SimpleNamespace(source=source, text="fix update deploy test", internal=False)
    event.get_command = lambda: None

    result = await module.GatewayRunner._handle_message(SimpleNamespace(), event)

    assert result is None
