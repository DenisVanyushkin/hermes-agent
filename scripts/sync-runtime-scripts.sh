#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${SCRIPT_DIR}/../agent" ] && [ -d "${SCRIPT_DIR}/../gateway" ]; then
  REPO="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
elif [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="$(cd -- "${PWD}" && pwd)"
else
  REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
fi

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET_DIR="$HERMES_HOME/scripts"
mkdir -p "$TARGET_DIR"

shopt -s nullglob
runtime_sources=(
  "$REPO/scripts/*.sh"
  "$REPO/scripts/*.py"
)

copied=0
for pattern in "${runtime_sources[@]}"; do
  for src in $pattern; do
    base="$(basename "$src")"
    dst="$TARGET_DIR/$base"
    install -m 755 "$src" "$dst"
    copied=$((copied + 1))
  done
done

printf 'Synced %s runtime script(s) to %s
' "$copied" "$TARGET_DIR"

# Sync the upstream-sync skill into the runtime skills dir.
SKILL_SRC="$REPO/skills/devops/upstream-sync"
SKILL_DST="$HERMES_HOME/skills/devops/upstream-sync"
if [ -d "$SKILL_SRC" ]; then
  mkdir -p "$SKILL_DST"
  install -m 644 "$SKILL_SRC/SKILL.md" "$SKILL_DST/SKILL.md"
  printf 'Synced upstream-sync skill to %s\n' "$SKILL_DST"
fi
