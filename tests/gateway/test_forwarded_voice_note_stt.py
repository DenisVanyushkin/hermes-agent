"""
Forwarded WhatsApp voice notes must still reach the STT pipeline.

WhatsApp only sets the ``ptt`` flag on a voice note the user records in the
chat. When the same voice note is *forwarded*, the flag is gone: the bridge
reports ``mediaType: 'audio'`` (bridge_helpers.js) and the adapter builds a
``MessageType.AUDIO`` event. #24870 routed every AUDIO event away from STT to
keep real audio *files* (.mp3/.m4a) as files — which also, unintentionally,
stopped forwarded voice notes from ever being transcribed. The agent was then
told to "transcribe it yourself", had no transcription tool, and improvised a
shell heredoc that tripped the dangerous-command approval gate in the user's
chat.

The distinguishing signal is the MIME: a voice note is Opus/OGG, an audio file
attachment is not. Size is the second guard — a forwarded hour-long recording
keeps file semantics rather than stalling the STT provider.
"""

from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def _make_runner(stt_enabled: bool = True):
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=stt_enabled)
    runner.adapters = {}
    runner._model = "test-model"
    runner._base_url = ""
    runner._has_setup_skill = lambda: False
    return runner


def _source() -> SessionSource:
    return SessionSource(platform=Platform.WHATSAPP, chat_id="1", chat_type="dm")


def _audio_event(path: str, mime: str) -> MessageEvent:
    """An AUDIO-typed event — what a forwarded voice note looks like."""
    return MessageEvent(
        text="",
        message_type=MessageType.AUDIO,
        source=_source(),
        media_urls=[path],
        media_types=[mime],
    )


def _write(tmp_path, name: str, size: int) -> str:
    path = tmp_path / name
    path.write_bytes(b"\0" * size)
    return str(path)


@pytest.mark.asyncio
async def test_forwarded_voice_note_is_transcribed(tmp_path):
    """AUDIO + Opus/OGG mime = a forwarded voice note — it must reach STT."""
    runner = _make_runner()
    path = _write(tmp_path, "aud_forwarded.ogg", 40_000)
    event = _audio_event(path, "audio/ogg; codecs=opus")

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={"success": True, "transcript": "забери Таю в пять", "provider": "transcriber"},
    ) as mock_transcribe:
        result = await runner._prepare_inbound_message_text(
            event=event,
            source=_source(),
            history=[],
        )

    mock_transcribe.assert_called_once_with(path, None, "gateway")
    assert "забери Таю в пять" in result
    assert "audio file attachment" not in result.lower()


@pytest.mark.asyncio
async def test_audio_file_attachment_still_skips_stt(tmp_path):
    """Regression guard for #24870: a real audio file keeps file semantics."""
    runner = _make_runner()
    path = _write(tmp_path, "song.mp3", 40_000)
    event = _audio_event(path, "audio/mpeg")

    with patch(
        "tools.transcription_tools.transcribe_audio",
        side_effect=AssertionError("audio file attachments must not enter STT"),
    ):
        with patch(
            "tools.credential_files.to_agent_visible_cache_path",
            side_effect=lambda p: p,
        ):
            result = await runner._prepare_inbound_message_text(
                event=event,
                source=_source(),
                history=[],
            )

    assert path in result
    assert "audio file attachment" in result.lower()


@pytest.mark.asyncio
async def test_oversized_opus_audio_skips_stt(tmp_path):
    """A forwarded recording past the size ceiling stays a file, not an STT job."""
    from gateway.run import VOICE_NOTE_STT_MAX_BYTES

    runner = _make_runner()
    path = _write(tmp_path, "podcast.ogg", VOICE_NOTE_STT_MAX_BYTES + 1)
    event = _audio_event(path, "audio/ogg")

    with patch(
        "tools.transcription_tools.transcribe_audio",
        side_effect=AssertionError("oversized audio must not enter STT"),
    ):
        with patch(
            "tools.credential_files.to_agent_visible_cache_path",
            side_effect=lambda p: p,
        ):
            result = await runner._prepare_inbound_message_text(
                event=event,
                source=_source(),
                history=[],
            )

    assert path in result
    assert "audio file attachment" in result.lower()


@pytest.mark.asyncio
async def test_recorded_voice_note_still_transcribed(tmp_path):
    """Regression guard: a plain VOICE event is unaffected by the AUDIO rule."""
    runner = _make_runner()
    path = _write(tmp_path, "aud_recorded.ogg", 8_000)
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=_source(),
        media_urls=[path],
        media_types=["audio/ogg; codecs=opus"],
    )

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={"success": True, "transcript": "привет", "provider": "transcriber"},
    ) as mock_transcribe:
        result = await runner._prepare_inbound_message_text(
            event=event,
            source=_source(),
            history=[],
        )

    mock_transcribe.assert_called_once_with(path, None, "gateway")
    assert "привет" in result
