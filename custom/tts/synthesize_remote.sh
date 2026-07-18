#!/usr/bin/env bash
# TTS command-provider для Гермеса: transcriber /tts (MP3) -> ffmpeg -> OGG/Opus.
# Вызов (tts.providers.transcriber в config.yaml):
#   synthesize_remote.sh {input_path} {output_path}
# {input_path} — UTF-8 текст от Hermes, {output_path} — куда писать ogg.
# Exit != 0 -> Hermes шлёт ответ текстом (auto-TTS деградирует молча).
#
# Политика (не ошибка, тихий exit 1): длинный или структурный текст — голосом
# не отправляем. Порог — tts_max_voice_chars из fam-config (дефолт 500).
# Реальный сбой транскрайбера — алерт Денису в Telegram, не чаще раза в 30 мин.
set -u
IN="$1"; OUT="$2"
URL="${TRANSCRIBER_URL:-http://192.168.1.20:5001}"
FAM_CONFIG="${FAM_CONFIG:-$HOME/.hermes/private/amina/fam-config.json}"
THROTTLE_STAMP="${TMPDIR:-/tmp}/hermes-tts-alert.stamp"

TEXT=$(cat "$IN")
[ -n "$TEXT" ] || { echo "empty text" >&2; exit 1; }

MAX=$(python3 -c '
import json,sys
try: print(int(json.load(open(sys.argv[1])).get("tts_max_voice_chars", 500)))
except Exception: print(500)' "$FAM_CONFIG")

python3 - "$MAX" "$IN" <<'EOF' || { echo "policy: text not voice-suitable, falling back to text" >&2; exit 1; }
import re, sys
text = open(sys.argv[2], encoding="utf-8").read().strip()
max_chars = int(sys.argv[1])
if len(text) > max_chars:
    sys.exit(1)
# Структура (списки, нумерация, таблицы, многострочность) — на слух не работает
lines = text.splitlines()
if len(lines) > 3:
    sys.exit(1)
if any(re.match(r"\s*([-*•]|\d+[.)])\s", l) for l in lines):
    sys.exit(1)
if "|" in text:
    sys.exit(1)
EOF

alert() {
  # Троттлинг: не чаще одного алерта в 30 минут
  if [ -f "$THROTTLE_STAMP" ] && [ -n "$(find "$THROTTLE_STAMP" -mmin -30 2>/dev/null)" ]; then
    return 0
  fi
  touch "$THROTTLE_STAMP"
  TOKEN=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$HOME/.hermes/.env" 2>/dev/null | cut -d= -f2-)
  [ -n "$TOKEN" ] || return 0
  curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -d chat_id=79564752 --data-urlencode "text=⚠️ Hermes TTS: transcriber /tts недоступен ($1). Ответы уходят текстом." \
    >/dev/null 2>&1 || true
}

MP3="${OUT%.*}.tmp.mp3"
trap 'rm -f "$MP3"' EXIT

BODY=$(python3 -c 'import json,sys; print(json.dumps({"text": open(sys.argv[1]).read().strip()}))' "$IN") || exit 1
HTTP=$(curl -s --max-time 60 -o "$MP3" -w "%{http_code} %{content_type}" \
  -X POST -H "Content-Type: application/json" -d "$BODY" "$URL/tts") || {
  echo "tts request failed: curl error" >&2; alert "curl error"; exit 1; }
case "$HTTP" in
  "200 audio/"*) ;;
  *) echo "tts request failed: $HTTP" >&2; alert "HTTP $HTTP"; exit 1 ;;
esac

ffmpeg -v error -y -i "$MP3" -c:a libopus -b:a 32k -ar 48000 -ac 1 -f ogg "$OUT" || {
  echo "ffmpeg conversion failed" >&2; rm -f "$OUT"; exit 1; }
exit 0
