#!/usr/bin/env bash
set -u
SCRIPT="$(dirname "$0")/transcribe_remote.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $1"; exit 1; }

# checks that the transcript contains a recognizable word from the fixture
# text ("гермес", case-insensitive) -- a mere non-empty check would also
# pass for garbage output.
contains_word() {
  grep -qi "гермес" "$1"
}

# 1. Голосовое-фикстура (сгенерировать заранее: сказать фразу в WhatsApp себе
#    или ffmpeg-синтез); путь: custom/stt/fixture_ru.ogg
[ -f "$(dirname "$0")/fixture_ru.ogg" ] || fail "нет фикстуры fixture_ru.ogg"

# 2. Happy path: транскрипт не пустой и содержит узнаваемое слово
"$SCRIPT" "$(dirname "$0")/fixture_ru.ogg" "$TMP/out.txt" || fail "exit!=0 на happy path"
[ -s "$TMP/out.txt" ] || fail "пустой транскрипт"
contains_word "$TMP/out.txt" || fail "транскрипт не содержит 'гермес' (happy path): $(cat "$TMP/out.txt")"

# 3. Фолбэк: с недоступным transcriber (подменяем URL через env)
TRANSCRIBER_URL="http://127.0.0.1:9" "$SCRIPT" "$(dirname "$0")/fixture_ru.ogg" "$TMP/out2.txt" \
  || fail "exit!=0 при недоступном transcriber (фолбэк не сработал)"
[ -s "$TMP/out2.txt" ] || fail "пустой транскрипт от фолбэка"
contains_word "$TMP/out2.txt" || fail "транскрипт не содержит 'гермес' (fallback): $(cat "$TMP/out2.txt")"
echo "ALL PASS"
