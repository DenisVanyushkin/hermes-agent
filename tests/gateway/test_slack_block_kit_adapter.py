"""Integration tests: SlackAdapter wiring of Block Kit into send paths.

Verifies the opt-in behaviour contract:
  * rich_blocks off (default)  => no ``blocks`` kwarg, plain ``text`` only
  * rich_blocks on             => ``blocks`` present AND ``text`` fallback set
  * edit_message: blocks only on finalize (streaming edits stay plain)
  * multi-chunk (>39k) messages fall back to plain text
"""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.slack import adapter as slack_module
from plugins.platforms.slack.adapter import SlackAdapter
from slack_sdk.errors import SlackApiError


def _make_adapter(extra=None):
    config = PlatformConfig(enabled=True, token="xoxb-fake", extra=extra or {})
    a = SlackAdapter(config)
    a._app = MagicMock()
    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "111.222"})
    client.chat_update = AsyncMock(return_value={"ts": "111.222"})
    a._get_client = MagicMock(return_value=client)
    a.stop_typing = AsyncMock()
    a._running = True
    return a, client


RICH_MD = "# Title\n\n- a\n  - nested\n\n---\n\nbody text"
RICH_TABLE_MD = (
    "| Item | Status | Note |\n"
    "|---|---:|---|\n"
    "| Hermes | ok | table |"
)


class SlackRejectedBlocks(Exception):
    def __init__(self, error="invalid_blocks"):
        super().__init__(f"Slack API rejected blocks: {error}")
        self.response = {"error": error}


def _slack_msg_too_long() -> SlackApiError:
    return SlackApiError(
        "Slack rejected the update",
        {"ok": False, "error": "msg_too_long"},
    )


def _slack_connection_key():
    from aiohttp.client_reqrep import ConnectionKey

    return ConnectionKey(
        host="slack.com",
        port=443,
        is_ssl=True,
        ssl=True,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )


class TestSendMessageBlocks:
    @pytest.mark.asyncio
    async def test_disabled_by_default_no_blocks(self):
        adapter, client = _make_adapter()
        await adapter.send("C1", RICH_MD)
        kwargs = client.chat_postMessage.await_args.kwargs
        assert "blocks" not in kwargs
        assert kwargs["text"]  # plain text still sent


    @pytest.mark.asyncio
    async def test_enabled_but_unrenderable_falls_back_to_text(self):
        # 60 dividers -> renderer returns None -> no blocks kwarg, text stands
        adapter, client = _make_adapter({"rich_blocks": True})
        await adapter.send("C1", "\n\n".join(["---"] * 60))
        kwargs = client.chat_postMessage.await_args.kwargs
        assert "blocks" not in kwargs
        assert kwargs["text"]


    @pytest.mark.asyncio
    async def test_feedback_buttons_opt_in_appended_to_blocks(self):
        adapter, client = _make_adapter({"rich_blocks": True, "feedback_buttons": True})

        await adapter.send("C1", "final answer")

        blocks = client.chat_postMessage.await_args.kwargs["blocks"]
        feedback = blocks[-1]
        assert feedback["type"] == "context_actions"
        assert feedback["elements"][0]["type"] == "feedback_buttons"
        assert feedback["elements"][0]["action_id"] == "hermes_feedback"


class TestEditMessageBlocks:
    @pytest.mark.asyncio
    async def test_intermediate_edit_no_blocks(self):
        adapter, client = _make_adapter({"rich_blocks": True})
        await adapter.edit_message("C1", "111.222", RICH_MD, finalize=False)
        kwargs = client.chat_update.await_args.kwargs
        assert "blocks" not in kwargs
        assert kwargs["text"]

    @pytest.mark.asyncio
    async def test_finalize_edit_gets_blocks(self):
        adapter, client = _make_adapter({"rich_blocks": True})
        await adapter.edit_message("C1", "111.222", RICH_MD, finalize=True)
        kwargs = client.chat_update.await_args.kwargs
        assert "blocks" in kwargs and kwargs["blocks"]
        assert kwargs["text"]


    @pytest.mark.asyncio
    async def test_block_rejection_retries_edit_without_blocks_using_workspace_client(self):
        adapter, client = _make_adapter({"rich_blocks": True})
        client.chat_update = AsyncMock(
            side_effect=[SlackRejectedBlocks("invalid_blocks"), {"ts": "111.222"}]
        )

        result = await adapter.edit_message(
            "C1",
            "111.222",
            RICH_TABLE_MD,
            finalize=True,
            metadata={"team_id": "T_SECONDARY"},
        )

        assert result.success is True
        assert adapter._get_client.call_args_list == [
            call("C1", team_id="T_SECONDARY"),
            call("C1", team_id="T_SECONDARY"),
        ]
        assert client.chat_update.await_count == 2
        first = client.chat_update.await_args_list[0].kwargs
        second = client.chat_update.await_args_list[1].kwargs
        assert "blocks" in first and first["blocks"]
        assert second["blocks"] == []
        assert second["text"]

    @pytest.mark.asyncio
    async def test_timeout_error_on_edit_is_retryable_transient(self):
        adapter, client = _make_adapter()
        client.chat_update = AsyncMock(side_effect=TimeoutError("timed out"))

        result = await adapter.edit_message("C1", "111.222", RICH_MD, finalize=True)

        assert result.success is False
        assert result.retryable is True
        assert result.error_kind == "transient"

    @pytest.mark.asyncio
    async def test_msg_too_long_retries_once_with_shorter_plain_text_for_streaming_edit(self):
        adapter, client = _make_adapter({"rich_blocks": True})
        original = "streaming update " * 200
        client.chat_update = AsyncMock(
            side_effect=[_slack_msg_too_long(), {"ts": "111.222"}]
        )

        result = await adapter.edit_message("C1", "111.222", original, finalize=False)

        assert result.success is True
        assert client.chat_update.await_count == 2
        first, second = [call.kwargs for call in client.chat_update.await_args_list]
        assert "blocks" not in first
        assert "blocks" not in second
        assert len(second["text"]) < len(first["text"])
        assert "Reply truncated" in second["text"]

    @pytest.mark.asyncio
    async def test_msg_too_long_finalize_edit_retries_without_blocks_and_preserves_marker(self):
        adapter, client = _make_adapter({"rich_blocks": True})
        original = "# Final answer\n\n" + ("details " * 200)
        client.chat_update = AsyncMock(
            side_effect=[_slack_msg_too_long(), {"ts": "111.222"}]
        )

        result = await adapter.edit_message("C1", "111.222", original, finalize=True)

        assert result.success is True
        assert client.chat_update.await_count == 2
        first, second = [call.kwargs for call in client.chat_update.await_args_list]
        assert first["blocks"]
        assert second.get("blocks") in (None, [])
        assert len(second["text"]) < len(first["text"])
        assert "Reply truncated" in second["text"]

    @pytest.mark.asyncio
    async def test_msg_too_long_fallback_failure_is_safe_and_non_retryable(self):
        adapter, client = _make_adapter({"rich_blocks": True})
        original = "secret final answer " * 200
        client.chat_update = AsyncMock(
            side_effect=[_slack_msg_too_long(), _slack_msg_too_long()]
        )

        result = await adapter.edit_message("C1", "111.222", original, finalize=True)

        assert result.success is False
        assert result.error == "slack_api_error:msg_too_long"
        assert result.error_kind == "too_long"
        assert result.retryable is False
        diagnostic = result.raw_response
        assert diagnostic["error_code"] == "msg_too_long"
        assert diagnostic["original_text_length"] > diagnostic["retry_text_length"]
        assert diagnostic["original_block_count"] > 0
        assert diagnostic["retry_block_count"] == 0
        assert "secret final answer" not in repr(diagnostic)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [
            "Сводка по задаче ✅ " * 150,
            "[готовый отчёт](https://example.test/report)\n\n" * 120,
            "```python\nprint('ok')\n```\n" * 120,
        ],
    )
    async def test_msg_too_long_fallback_handles_unicode_links_and_code_fences(self, content):
        adapter, client = _make_adapter()
        client.chat_update = AsyncMock(
            side_effect=[_slack_msg_too_long(), {"ts": "111.222"}]
        )

        result = await adapter.edit_message("C1", "111.222", content, finalize=False)

        assert result.success is True
        first, second = [call.kwargs for call in client.chat_update.await_args_list]
        assert len(second["text"]) < len(first["text"])
        assert "Reply truncated" in second["text"]


# ---------------------------------------------------------------------------
# markdown_blocks mode — Slack's native ``markdown`` Block Kit block (#8552)
# ---------------------------------------------------------------------------


class TestMarkdownBlockMode:
    """Opt-in ``markdown_blocks`` renders raw standard markdown via Slack's
    native ``markdown`` block, keeping the mrkdwn ``text`` fallback."""

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        adapter, client = _make_adapter()
        await adapter.send("C1", RICH_TABLE_MD)
        kwargs = client.chat_postMessage.await_args.kwargs
        assert "blocks" not in kwargs

    @pytest.mark.asyncio
    async def test_enabled_sends_markdown_block_with_raw_content(self):
        adapter, client = _make_adapter({"markdown_blocks": True})
        await adapter.send("C1", RICH_TABLE_MD)
        kwargs = client.chat_postMessage.await_args.kwargs
        blocks = kwargs["blocks"]
        assert blocks[0]["type"] == "markdown"
        # RAW standard markdown, not mrkdwn-converted — Slack translates it
        assert blocks[0]["text"] == RICH_TABLE_MD
        # mrkdwn fallback text is still present for notifications/search
        assert kwargs["text"]


    @pytest.mark.asyncio
    async def test_edit_finalize_uses_markdown_block(self):
        adapter, client = _make_adapter({"markdown_blocks": True})
        await adapter.edit_message("C1", "111.222", RICH_TABLE_MD, finalize=True)
        kwargs = client.chat_update.await_args.kwargs
        assert kwargs["blocks"][0]["type"] == "markdown"
        assert kwargs["blocks"][0]["text"] == RICH_TABLE_MD
