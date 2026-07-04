#!/usr/bin/env bash
set -euo pipefail

# Roll the repo back to a pre-upstream-sync backup ref and restart the gateway.
# Usage: upstream-sync-rollback.sh <backup-ref>
# Backup refs follow the convention backup/pre-upstream-sync-YYYYmmdd-HHMMSS.

# Same root-ownership guard as rebase-local-customizations.sh: sandbox
# containers leave root-owned files in the repo; git refuses mixed ownership.
_default_repo="${HOME:-/home/hermes}/.hermes/hermes-agent"
if [ "$(id -u)" -ne 0 ] && \
   [ -n "$(find "$_default_repo" -maxdepth 6 -user root -print -quit 2>/dev/null)" ]; then
  exec sudo -n env HOME="$HOME" "$0" "$@"
fi
unset _default_repo

BACKUP_REF="${1:-}"
if [ -z "$BACKUP_REF" ]; then
  echo "Usage: $0 <backup-ref>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${PWD:-.}/.git" ] && [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="${PWD}"
elif [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
fi

BRANCH="${HERMES_LOCAL_BRANCH:-local/customizations}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STATE_DIR="${HERMES_SYNC_STATE_DIR:-$HERMES_HOME/sandboxes/docker/default/home/.hermes/state/upstream-sync}"

# If root, repair ownership and re-exec as the repo owner (mirror of the
# rebase script's guard).
REPO_UID="$(stat -c '%u' "$REPO")"
REPO_GID="$(stat -c '%g' "$REPO")"
if [ "$(id -u)" -eq 0 ] && [ "$REPO_UID" != "0" ]; then
  echo "Repairing root-owned files and re-running as repo owner..." >&2
  find "$REPO/.git" \( -user root -o -group root \) -exec chown -h "$REPO_UID:$REPO_GID" {} +
  find "$REPO" -path "$REPO/.git" -prune -o \( -user root -o -group root \) -exec chown -h "$REPO_UID:$REPO_GID" {} +
  REPO_USER="$(getent passwd "$REPO_UID" | cut -d: -f1 || true)"
  [ -n "$REPO_USER" ] || { echo "Cannot resolve repo owner UID $REPO_UID" >&2; exit 1; }
  printf -v REEXEC_CMD 'cd %q && exec bash %q %q' "$REPO" "$REPO/scripts/upstream-sync-rollback.sh" "$BACKUP_REF"
  exec su -s /bin/bash "$REPO_USER" -c "$REEXEC_CMD"
fi

git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

if ! git -C "$REPO" rev-parse --verify --quiet "$BACKUP_REF" >/dev/null; then
  echo "Backup ref not found: $BACKUP_REF" >&2
  exit 1
fi

echo "== upstream-sync rollback to $BACKUP_REF =="
git -C "$REPO" rebase --abort >/dev/null 2>&1 || true
git -C "$REPO" merge --abort >/dev/null 2>&1 || true

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current || true)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  git -C "$REPO" checkout -f "$BRANCH" >/dev/null
fi
git -C "$REPO" reset --hard "$BACKUP_REF"

SYNC_HELPER="$REPO/scripts/sync-runtime-scripts.sh"
if [ -x "$SYNC_HELPER" ]; then
  "$SYNC_HELPER" >/dev/null || echo "Warning: runtime script sync failed" >&2
fi

resolve_hermes_bin() {
  for candidate in "${HERMES_BIN:-}" "$REPO/venv/bin/hermes" "$HOME/.local/bin/hermes" "$(command -v hermes 2>/dev/null || true)"; do
    [ -n "$candidate" ] || continue
    if [ -x "$candidate" ] && "$candidate" --version >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

RESTARTED="no"
HERMES_BIN="$(resolve_hermes_bin || true)"
if [ -n "$HERMES_BIN" ] && "$HERMES_BIN" gateway restart >/dev/null 2>&1; then
  RESTARTED="yes"
else
  echo "Warning: gateway restart failed or hermes binary missing" >&2
fi

mkdir -p "$STATE_DIR"
printf '{"rolled_back_to": "%s", "at": "%s"}\n' \
  "$BACKUP_REF" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$STATE_DIR/last-rollback.json"

echo "ROLLBACK DONE"
echo "HEAD: $(git -C "$REPO" rev-parse --short HEAD)"
echo "Branch: $(git -C "$REPO" branch --show-current)"
echo "Gateway restarted: $RESTARTED"
