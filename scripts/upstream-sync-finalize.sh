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
REPO="${HERMES_REPO:-$HERMES_HOME/hermes-agent}"
HERMES_ENV_FILE="${HERMES_ENV_FILE:-$HERMES_HOME/.env}"
SYNC_SLACK_CHANNEL="${HERMES_SYNC_SLACK_CHANNEL:-C0B3X1E5SJZ}"
REQUEST="$STATE_DIR/finalize-request.json"
PROCESSING="$STATE_DIR/finalize-request.processing.json"
RESULT="$STATE_DIR/finalize-result.json"
LOCK="$STATE_DIR/finalize.lock"

# The sandbox home's parent dirs are root-owned 700 and files written by the
# sandboxed agent arrive root-owned; grant traverse and normalize ownership so
# this script (running as hermes) can process them. hermes has passwordless sudo.
# Only self-heal when access is actually broken: the setfacl walk must never
# touch shared parents like /tmp on an overridden STATE_DIR (2026-07-20: a
# test run planted u:hermes:--x on /tmp, blocking all hermes writes there).
if [ ! -w "$STATE_DIR" ] || ! ls "$STATE_DIR" >/dev/null 2>&1; then
  d="$STATE_DIR"
  while [ "$d" != "/" ] && [ "$d" != "$HOME" ]; do
    sudo -n setfacl -m "u:$(id -un):--x" "$d" 2>/dev/null || true
    d="$(dirname "$d")"
  done
  sudo -n chown -R "$(id -un):$(id -gn)" "$STATE_DIR" 2>/dev/null || true
fi

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
  # Keep the complete log next to the result: the JSON detail is truncated
  # to its tail, which has already hidden the actual failure cause once
  # (2026-07-16: a pre-report error line was cut off, leaving a causeless
  # rollback). One file, overwritten per run.
  cp -f "$DETAIL_LOG" "$STATE_DIR/finalize-detail.log" 2>/dev/null || true
  python3 - "$RESULT" "$ACTION" "$1" "$2" "$BACKUP_REF" <<'PY'
import json, sys, datetime
path, action, status, detail, backup = sys.argv[1:6]
json.dump({
    "action": action, "status": status, "detail": detail[-4000:],
    "detail_log": "finalize-detail.log",
    "backup_ref": backup,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path, "w"), ensure_ascii=False, indent=1)
PY
  rm -f "$PROCESSING"
  notify_slack "$1"
  # On a successful apply (rebase/finalize), clear the consumed decision so a
  # stray reply or the next scheduled sync does not re-trigger against
  # already-applied state. Keep it on rollback/failure so the operator can retry.
  if { [ "$ACTION" = rebase ] || [ "$ACTION" = finalize ]; } && [ "$1" = ok ]; then
    rm -f "$STATE_DIR/pending.json"
  fi
}

notify_slack() {
  # The requesting agent session often dies with the gateway restart this
  # script triggers (2026-07-20: operator saw silence + dead approval
  # buttons twice). Deliver the outcome out-of-band, straight to Slack.
  status="$1"
  token="${SLACK_BOT_TOKEN:-}"
  if [ -z "$token" ] && [ -r "$HERMES_ENV_FILE" ]; then
    token="$(grep -E '^ *(export +)?SLACK_BOT_TOKEN=' "$HERMES_ENV_FILE" | tail -1 | sed -e 's/^ *export *//' -e 's/^SLACK_BOT_TOKEN=//' -e 's/^"//' -e 's/"$//')"
  fi
  [ -n "$token" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  text="upstream-sync finalizer: action=$ACTION status=$status backup_ref=${BACKUP_REF:-none} (details: finalize-result.json / finalize-detail.log)"
  curl -sS -m 15 -X POST "https://slack.com/api/chat.postMessage" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2]}))' "$SYNC_SLACK_CHANNEL" "$text")" \
    >/dev/null 2>&1 || true
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
    # finalize means "the sandbox agent already rebased the repo". Verify that
    # claim before running push/restart: on 2026-07-20 the agent requested
    # finalize after its own rebase had ABORTED, and the pipeline replayed
    # hundreds of commits into conflicts, then rolled back and restarted the
    # gateway for nothing. Fail fast and leave the repo untouched instead.
    if [ -n "$UPSTREAM_SHA" ] && \
       ! git -C "$REPO" merge-base --is-ancestor "$UPSTREAM_SHA" HEAD 2>>"$DETAIL_LOG"; then
      echo "HEAD $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?') is not a descendant of upstream_sha $UPSTREAM_SHA" >>"$DETAIL_LOG"
      write_result failed "finalize requested but the repo is not rebased onto upstream_sha — refusing to run; repo untouched, no rollback. $(cat "$DETAIL_LOG")"
      exit 0
    fi
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
