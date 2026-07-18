#!/usr/bin/env bash
# TTS для Гермеса: homelab-transcriber /tts (MP3) -> ffmpeg -> OGG/Opus.
# Вход: $1 = файл с текстом (или "-" = stdin). Выход: путь к .ogg на stdout.
# Любая ошибка -> ненулевой exit (вызывающий код деградирует в текст).
set -u
IN="${1:--}"
URL="${TRANSCRIBER_URL:-http://192.168.1.20:5001}"
CACHE="${AUDIO_CACHE_DIR:-$HOME/.hermes/audio_cache}"
mkdir -p "$CACHE"

if [ "$IN" = "-" ]; then TEXT=$(cat); else TEXT=$(cat "$IN"); fi
[ -n "$TEXT" ] || { echo "empty text" >&2; exit 1; }

TS=$(date +%Y%m%d-%H%M%S)-$$
MP3="$CACHE/tts-$TS.mp3"
OGG="$CACHE/tts-$TS.ogg"
trap 'rm -f "$MP3"' EXIT

BODY=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$TEXT") || exit 1
HTTP=$(curl -s --max-time 60 -o "$MP3" -w "%{http_code} %{content_type}" \
  -X POST -H "Content-Type: application/json" -d "$BODY" "$URL/tts") || {
  echo "tts request failed: curl error" >&2; exit 1; }
case "$HTTP" in
  "200 audio/"*) ;;
  *) echo "tts request failed: $HTTP $(head -c 200 "$MP3")" >&2; exit 1 ;;
esac

ffmpeg -v error -y -i "$MP3" -c:a libopus -b:a 32k -ar 48000 -ac 1 "$OGG" || {
  echo "ffmpeg conversion failed" >&2; rm -f "$OGG"; exit 1; }
printf "%s\n" "$OGG"
