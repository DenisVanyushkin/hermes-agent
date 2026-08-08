"""
The agent needs a first-class way to transcribe an audio attachment.

Audio *file* attachments reach the agent as a path plus a note asking it to
"transcribe or process it yourself — for example by passing the path to a
transcription or media tool". No such tool existed: ``transcribe_audio`` was a
library function wired only into the gateway's inbound pipeline, there is no
``hermes transcribe`` subcommand, and the transcribe skill is not installed
everywhere. The agent therefore improvised in the sandbox shell, which tripped
the dangerous-command approval gate in the user's chat.

The path in that note is sandbox-visible (``/root/.hermes/cache/...``) while
the tool runs on the host — the same mismatch that made ``video_analyze`` fail
with EACCES — so resolving it back is part of the tool's job.
"""

import json
from unittest.mock import patch

import pytest


def _call(file_path: str) -> str:
    from tools.stt_tool import transcribe_audio_tool

    return transcribe_audio_tool(file_path)


def test_returns_transcript_for_a_host_path(tmp_path):
    audio = tmp_path / "note.ogg"
    audio.write_bytes(b"\0" * 512)

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={"success": True, "transcript": "забери Таю в пять", "provider": "transcriber"},
    ) as mock_transcribe:
        result = _call(str(audio))

    mock_transcribe.assert_called_once_with(str(audio))
    assert "забери Таю в пять" in result


def test_resolves_sandbox_path_to_host_path(tmp_path, monkeypatch):
    """The path handed to the agent is the container one; STT runs on the host."""
    host_audio_dir = tmp_path / "cache" / "audio"
    host_audio_dir.mkdir(parents=True)
    audio = host_audio_dir / "aud_forwarded.ogg"
    audio.write_bytes(b"\0" * 512)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_ENV", "docker")

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={"success": True, "transcript": "hello", "provider": "transcriber"},
    ) as mock_transcribe:
        result = _call("/root/.hermes/cache/audio/aud_forwarded.ogg")

    mock_transcribe.assert_called_once_with(str(audio))
    assert "hello" in result


def test_provider_failure_is_reported_not_raised(tmp_path):
    audio = tmp_path / "note.ogg"
    audio.write_bytes(b"\0" * 512)

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={"success": False, "error": "transcriber unavailable"},
    ):
        result = _call(str(audio))

    assert "transcriber unavailable" in json.loads(result)["error"]


def test_missing_file_is_reported_without_calling_the_provider(tmp_path):
    with patch(
        "tools.transcription_tools.transcribe_audio",
        side_effect=AssertionError("must not call STT for a path that does not exist"),
    ):
        result = _call(str(tmp_path / "nope.ogg"))

    assert "nope.ogg" in json.loads(result)["error"]


def test_registered_as_a_tool_in_the_core_toolset():
    """A registered tool the platform toolsets never list is invisible to the agent."""
    import tools.stt_tool  # noqa: F401  (registers on import)
    from toolsets import _HERMES_CORE_TOOLS
    from tools.registry import registry

    assert registry.get_entry("transcribe_audio") is not None
    assert "transcribe_audio" in _HERMES_CORE_TOOLS
