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

# Shared helpers keep their subdirectory: the synced scripts source them as
# "$SCRIPT_DIR/lib/...", so flattening them by basename would leave the runtime
# copy sourcing a path that does not exist -- invisible until the next cron run.
for src in "$REPO"/scripts/lib/*.sh; do
  install -D -m 755 "$src" "$TARGET_DIR/lib/$(basename "$src")"
  copied=$((copied + 1))
done

# The bounded idea-signal collector resolves its registry beside the synced
# script at runtime. Keep config and code in the same atomic sync operation.
IDEA_REGISTRY_SRC="$REPO/config/idea_sources.yaml"
if [ -f "$IDEA_REGISTRY_SRC" ]; then
  install -m 644 "$IDEA_REGISTRY_SRC" "$TARGET_DIR/idea_sources.yaml"
  printf 'Synced idea-signal registry to %s\n' "$TARGET_DIR/idea_sources.yaml"
fi

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
