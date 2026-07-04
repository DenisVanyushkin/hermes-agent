#!/usr/bin/env bash
set -euo pipefail

# Host-side finalizer for upstream-sync. The cron agent runs its terminal
# inside a Docker sandbox and cannot restart the gateway or run host smoke
# tests; instead it drops finalize-request.json into the shared state dir
# (sandbox /root/.hermes/state/upstream-sync = this dir on the host) and a
# systemd path unit invokes this script.
#
# Request schema: {"action": "rebase"|"finalize"|"rollback",
#                  "upstream_sha": "...", "backup_ref": "..."}
#  - rebase:   clean path — backup, rebase script, smoketest; rollback on fail
#  - finalize: agent already rebased in the sandbox — push+restart via the
#              rebase script (no-op rebase), smoketest; rollback on fail
#  - rollback: explicit rollback to backup_ref
# Result written to finalize-result.json in the same dir.

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STATE_DIR="${HERMES_SYNC_STATE_DIR:-$HERMES_HOME/sandboxes/docker/default/home/.hermes/state/upstream-sync}"
SCRIPTS_DIR="${HERMES_SCRIPTS_DIR:-$HERMES_HOME/scripts}"
REQUEST="$STATE_DIR/finalize-request.json"
PROCESSING="$STATE_DIR/finalize-request.processing.json"
RESULT="$STATE_DIR/finalize-result.json"
LOCK="$STATE_DIR/finalize.lock"

# The sandbox home's parent dirs are root-owned 700 and files written by the
# sandboxed agent arrive root-owned; grant traverse and normalize ownership so
# this script (running as hermes) can process them. hermes has passwordless sudo.
d="$STATE_DIR"
while [ "$d" != "/" ] && [ "$d" != "$HOME" ]; do
  sudo -n setfacl -m "u:$(id -un):--x" "$d" 2>/dev/null || true
  d="$(dirname "$d")"
done
sudo -n chown -R "$(id -un):$(id -gn)" "$STATE_DIR" 2>/dev/null || true

[ -f "$REQUEST" ] || exit 0

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another finalize run is in progress; leaving request in place." >&2
  exit 0
fi

mv "$REQUEST" "$PROCESSING"

json_field() {
  python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or '')" "$PROCESSING" "$1"
}

ACTION="$(json_field action)"
UPSTREAM_SHA="$(json_field upstream_sha)"
BACKUP_REF="$(json_field backup_ref)"

write_result() {
  python3 - "$RESULT" "$ACTION" "$1" "$2" "$BACKUP_REF" <<'PY'
import json, sys, datetime
path, action, status, detail, backup = sys.argv[1:6]
json.dump({
    "action": action, "status": status, "detail": detail[-4000:],
    "backup_ref": backup,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path, "w"), ensure_ascii=False, indent=1)
PY
  rm -f "$PROCESSING"
}

run_logged() {
  # Runs a command, appending output to $DETAIL_LOG; returns its exit code.
  "$@" >>"$DETAIL_LOG" 2>&1
}

DETAIL_LOG="$(mktemp)"
trap 'rm -f "$DETAIL_LOG"' EXIT

do_rollback() {
  if [ -n "$BACKUP_REF" ]; then
    run_logged bash "$SCRIPTS_DIR/upstream-sync-rollback.sh" "$BACKUP_REF" || true
  fi
}

case "$ACTION" in
  rebase)
    if [ -z "$BACKUP_REF" ]; then
      BACKUP_REF="backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S)"
      REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
      run_logged git -C "$REPO" branch "$BACKUP_REF" HEAD || true
      run_logged git -C "$REPO" tag "$BACKUP_REF" HEAD || true
    fi
    if run_logged bash "$SCRIPTS_DIR/rebase-local-customizations.sh" && \
       run_logged bash "$SCRIPTS_DIR/upstream-sync-smoketest.sh" "$UPSTREAM_SHA"; then
      write_result ok "$(cat "$DETAIL_LOG")"
    else
      do_rollback
      write_result failed "$(cat "$DETAIL_LOG")"
    fi
    ;;
  finalize)
    if run_logged bash "$SCRIPTS_DIR/rebase-local-customizations.sh" && \
       run_logged bash "$SCRIPTS_DIR/upstream-sync-smoketest.sh" "$UPSTREAM_SHA"; then
      write_result ok "$(cat "$DETAIL_LOG")"
    else
      do_rollback
      write_result failed "$(cat "$DETAIL_LOG")"
    fi
    ;;
  rollback)
    if [ -z "$BACKUP_REF" ]; then
      write_result failed "rollback requested without backup_ref"
    else
      do_rollback
      write_result ok "$(cat "$DETAIL_LOG")"
    fi
    ;;
  *)
    write_result failed "unknown action: $ACTION"
    ;;
esac
