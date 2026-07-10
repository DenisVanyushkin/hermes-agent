"""
Regression test: command-provider STT stderr must be surfaced on success.

Finding (final review): when a command-provider STT wrapper succeeds but
writes a diagnostic to stderr (e.g. "transcriber unavailable, falling back
to local faster-whisper"), the gateway silently discarded it — only the
transcript char count was logged. This degrades an operator's ability to
notice their primary STT backend is failing and quietly falling back.

Mirrors the conventions in ``tests/tools/test_transcription_command_providers.py``:
a portable ``python -c`` command stands in for a real STT wrapper so the
test runs identically on Linux/macOS/Windows without touching a real model.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from tools.transcription_tools import _transcribe_command_stt


def _make_silent_wav(path: Path, seconds: float = 0.1) -> Path:
    """Write a minimal silent .wav file so _validate_audio_file accepts it."""
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        frames = b"\x00\x00" * int(8000 * seconds)
        w.writeframes(frames)
    return path


def _python_emit_with_stderr_command(transcript_text: str, stderr_text: str) -> str:
    """Portable command: write ``stderr_text`` to stderr, then emit transcript to {output_path}."""
    interpreter = sys.executable
    payload = (
        "import sys; "
        f"sys.stderr.write({stderr_text!r}); "
        f"open(sys.argv[1], 'w').write({transcript_text!r})"
    )
    return f'"{interpreter}" -c "{payload}" {{output_path}}'


class TestCommandSTTStderrLoggedOnSuccess:
    def test_success_path_logs_stderr_from_internal_fallback(self, tmp_path, caplog):
        audio = _make_silent_wav(tmp_path / "input.wav")
        cfg = {
            "type": "command",
            "command": _python_emit_with_stderr_command(
                "hello", "transcriber unavailable, falling back to local faster-whisper",
            ),
        }

        with caplog.at_level(logging.INFO, logger="tools.transcription_tools"):
            result = _transcribe_command_stt(str(audio), "fake-cli", cfg, {})

        assert result["success"] is True
        assert result["transcript"] == "hello"

        stderr_records = [
            r for r in caplog.records
            if "stderr" in r.getMessage()
            and "transcriber unavailable, falling back to local faster-whisper" in r.getMessage()
        ]
        assert stderr_records, (
            "Expected an INFO log record surfacing the command provider's "
            f"stderr, got: {[r.getMessage() for r in caplog.records]}"
        )
        assert all(r.levelno == logging.INFO for r in stderr_records)

    def test_success_path_does_not_log_stderr_when_empty(self, tmp_path, caplog):
        audio = _make_silent_wav(tmp_path / "input.wav")
        cfg = {
            "type": "command",
            "command": _python_emit_with_stderr_command("hello", ""),
        }

        with caplog.at_level(logging.INFO, logger="tools.transcription_tools"):
            result = _transcribe_command_stt(str(audio), "fake-cli", cfg, {})

        assert result["success"] is True
        stderr_records = [r for r in caplog.records if "stderr" in r.getMessage()]
        assert not stderr_records
