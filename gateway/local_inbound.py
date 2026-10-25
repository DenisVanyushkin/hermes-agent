"""Fork: inbound media rules on top of upstream's gateway routing.

A voice note forwarded in WhatsApp loses its ``ptt`` flag: the bridge reports
``mediaType: 'audio'``, the adapter builds MessageType.AUDIO, and upstream
routes every AUDIO event past STT so .mp3/.m4a stay files (#24870). The
forwarded voice note went with them -- 2026-08-07 Amina asked for voice notes
to be transcribed and got a dangerous-command approval instead of text, because
the model improvised ffprobe/execute_code in the sandbox. Opus/OGG is what every
messenger records voice notes in, so an AUDIO attachment of that MIME (under a
size ceiling, so a forwarded hour-long recording keeps file semantics) goes to
STT again. Moved from the pre-v0.22.5 gateway/run.py.
"""

from __future__ import annotations

import os
from typing import Any

VOICE_NOTE_MIME_TYPES = frozenset({"audio/ogg", "audio/opus", "audio/x-opus+ogg"})
# Real voice notes are tens of kilobytes; past this it would only stall the STT provider.
VOICE_NOTE_STT_MAX_BYTES = 10 * 1024 * 1024


def is_voice_note_mime(mtype: str) -> bool:
    """True for the container/codec a recorded voice note arrives in (WhatsApp sends
    ``audio/ogg; codecs=opus``, so the bare type is compared)."""
    return str(mtype or "").split(";", 1)[0].strip().lower() in VOICE_NOTE_MIME_TYPES


def is_forwarded_voice_note(event: Any, index: int, media_type: str) -> bool:
    """True when a MessageType.AUDIO attachment is really a voice note.

    An unknown size (stat failure: path not materialised yet) does not veto
    transcription -- STT reads the file itself and is the right place for that
    to surface.
    """
    if not is_voice_note_mime(media_type):
        return False
    media_urls = getattr(event, "media_urls", None) or []
    if index >= len(media_urls):
        return False
    try:
        return os.path.getsize(media_urls[index]) <= VOICE_NOTE_STT_MAX_BYTES
    except OSError:
        return True
