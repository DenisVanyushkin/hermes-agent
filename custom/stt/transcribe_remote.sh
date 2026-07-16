#!/usr/bin/env bash
# STT для Гермеса: пробуем homelab-transcriber, при отказе — локальный faster-whisper.
set -u
IN="$1"; OUT="$2"
URL="${TRANSCRIBER_URL:-http://192.168.1.20:5001}"

resp=$(curl -sf --max-time 90 -F "file=@${IN}" "$URL/transcribe" 2>/dev/null)
if [ $? -eq 0 ] && [ -n "$resp" ]; then
  text=$(printf '%s' "$resp" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print((d.get("translation") or d.get("transcription") or "").strip())')
  if [ -n "$text" ]; then printf '%s\n' "$text" > "$OUT"; exit 0; fi
fi

echo "transcriber unavailable, falling back to local faster-whisper" >&2
# faster-whisper is installed into hermes-agent's venv, not necessarily on
# the caller's PATH, so prefer the venv interpreter when it exists.
VENV_PY="$(dirname "$0")/../../venv/bin/python3"
if [ -x "$VENV_PY" ]; then PY="$VENV_PY"; else PY="python3"; fi
"$PY" - "$IN" "$OUT" <<'EOF'
import sys
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cpu", compute_type="int8")
segments, _ = model.transcribe(sys.argv[1], language="ru")
text = " ".join(s.text.strip() for s in segments).strip()
if not text:
    sys.exit(1)
open(sys.argv[2], "w").write(text + "\n")
EOF
