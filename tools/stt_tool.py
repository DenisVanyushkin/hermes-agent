#!/usr/bin/env python3
"""
Speech-to-text tool
===================

Exposes the configured STT provider (``stt.provider`` in config.yaml — the
same one the gateway uses to transcribe inbound voice messages) to the agent
as a normal tool call.

Why this exists: audio *file* attachments are handed to the agent as a path
plus a note asking it to transcribe them itself. Without a tool for that, the
model's only remaining option is the sandbox shell — where probing for
``whisper``/``ffmpeg`` and running an inline ``python <<'PY'`` heredoc trips
the dangerous-command approval gate, so the user gets an engineering prompt
instead of their transcript.

The path in that note is sandbox-visible (``/root/.hermes/cache/...``) while
this tool runs in the gateway process on the host, so container paths are
resolved back before the provider ever sees them.

Counterpart of ``tools/tts_tool.py`` (``text_to_speech``).
"""

import logging
import os
from typing import Any, Dict

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


def _resolve_host_path(file_path: str) -> str:
    """Return the host path for *file_path*, which may be sandbox-visible.

    A path that already exists on the host is used as-is; otherwise it is run
    through the cache-mount inverse mapping. Anything outside a mounted cache
    directory comes back unchanged and is reported as missing below.
    """
    if os.path.exists(file_path):
        return file_path
    from tools.credential_files import from_agent_visible_cache_path

    return from_agent_visible_cache_path(file_path)


def transcribe_audio_tool(file_path: str) -> str:
    """Transcribe *file_path* with the configured STT provider."""
    path = (file_path or "").strip()
    if not path:
        return tool_error("file_path is required")

    resolved = _resolve_host_path(path)
    if not os.path.isfile(resolved):
        return tool_error(f"audio file not found: {path}")

    from tools.transcription_tools import transcribe_audio

    try:
        result = transcribe_audio(resolved)
    except Exception as exc:  # provider crash must not kill the turn
        logger.warning("transcribe_audio failed for %s: %s", resolved, exc, exc_info=True)
        return tool_error(f"transcription failed: {exc}")

    if not result.get("success"):
        return tool_error(result.get("error") or "transcription failed")

    transcript = (result.get("transcript") or "").strip()
    if not transcript:
        return tool_error("transcription produced no text (silent or unintelligible audio)")
    return transcript


def check_stt_requirements() -> bool:
    """Whether STT is configured — mirrors the gateway's own voice pipeline."""
    try:
        from tools.transcription_tools import is_stt_enabled

        return is_stt_enabled()
    except Exception:
        return False


TRANSCRIBE_AUDIO_SCHEMA = {
    "name": "transcribe_audio",
    "description": (
        "Transcribe speech from an audio file to text using the configured "
        "speech-to-text provider. Use this whenever a message points you at an "
        "audio attachment (voice note, forwarded recording, .mp3/.m4a/.ogg/.wav) "
        "and the user wants to know what was said. Always prefer this over "
        "running whisper/ffmpeg or any other transcription command in the "
        "terminal. Accepts the path exactly as given in the message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the audio file, as given in the message.",
            },
        },
        "required": ["file_path"],
    },
}


def _handle_transcribe_audio(args: Dict[str, Any], **kw: Any) -> str:
    return transcribe_audio_tool(args.get("file_path", ""))


registry.register(
    name="transcribe_audio",
    toolset="stt",
    schema=TRANSCRIBE_AUDIO_SCHEMA,
    handler=_handle_transcribe_audio,
    check_fn=check_stt_requirements,
    emoji="🎙️",
)
