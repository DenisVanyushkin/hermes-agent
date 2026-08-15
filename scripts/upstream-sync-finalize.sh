#!/usr/bin/env bash
set -euo pipefail

# Host-side finalizer for upstream-sync. The cron agent runs its terminal
# inside a Docker sandbox and cannot restart the gateway or run host smoke
# tests; instead it drops finalize-request.json into the shared state dir
# (sandbox /root/.hermes/state/upstream-sync = this dir on the host) and a
# systemd path unit invokes this script.
#
# Request schema: {"action": "sync"|"finalize"|"rollback",
#                  "upstream_sha": "...", "backup_ref": "..."}
#  - sync:     clean path — backup, sync script, smoketest; rollback on fail
#              ("rebase" is accepted as a legacy alias for this action)
#  - finalize: agent already merged in the sandbox — push+restart via the
#              sync script (no-op merge), smoketest; rollback on fail
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
  # Only ever walk a state dir that lives under our own home. The production
  # handoff dir always does; an overridden HERMES_SYNC_STATE_DIR pointing
  # elsewhere is a test or dev setup, and walking THAT climbs into shared
  # parents — on 2026-07-20 it reached /tmp and blocked writes there for every
  # user on the box. Being triggered rarely is not the same as being safe when
  # triggered.
  case "$STATE_DIR/" in
    "$HOME"/*)
      d="$STATE_DIR"
      while [ "$d" != "/" ] && [ "$d" != "$HOME" ]; do
        sudo -n setfacl -m "u:$(id -un):--x" "$d" 2>/dev/null || true
        d="$(dirname "$d")"
      done
      sudo -n chown -R "$(id -un):$(id -gn)" "$STATE_DIR" 2>/dev/null || true
      ;;
    *)
      echo "state dir $STATE_DIR is outside $HOME — skipping the access self-heal rather than touching shared parents." >&2
      ;;
  esac
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

# `sync` — каноническое имя действия; `rebase` принимается от скилла,
# который ещё не обновился до нового контракта. Оба означают одно:
# применить обновление.
is_apply_action() {
  [ "$ACTION" = sync ] || [ "$ACTION" = rebase ] || [ "$ACTION" = apply-merge ]
}
UPSTREAM_SHA="$(json_field upstream_sha)"
BACKUP_REF="$(json_field backup_ref)"

# Which stage of the apply pipeline died, if any. The apply used to be one
# `if rebase && smoketest` with a single status, so a rebase failure was
# reported to the operator as a failed smoketest (2026-07-27) — the log tail
# they were shown started well after the real cause.
FAILED_STAGE=""

write_result() {
  # Keep the complete log next to the result: the JSON detail is truncated
  # to its tail, which has already hidden the actual failure cause once
  # (2026-07-16: a pre-report error line was cut off, leaving a causeless
  # rollback). One file, overwritten per run.
  cp -f "$DETAIL_LOG" "$STATE_DIR/finalize-detail.log" 2>/dev/null || true
  python3 - "$RESULT" "$ACTION" "$1" "$2" "$BACKUP_REF" "$FAILED_STAGE" <<'PY'
import json, sys, datetime
path, action, status, detail, backup, stage = sys.argv[1:7]
json.dump({
    "action": action, "status": status, "detail": detail[-4000:],
    "detail_log": "finalize-detail.log",
    "backup_ref": backup,
    "failed_stage": stage or None,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path, "w"), ensure_ascii=False, indent=1)
PY
  rm -f "$PROCESSING"
  notify_slack "$1"
  # On a successful apply (rebase/finalize), clear the consumed decision so a
  # stray reply or the next scheduled sync does not re-trigger against
  # already-applied state. Keep it on rollback/failure so the operator can retry.
  if { is_apply_action || [ "$ACTION" = finalize ]; } && [ "$1" = ok ]; then
    # Archive rather than delete. Recording the operator's decision into
    # decision-memory.json is the LAST step of Mode B and reads this file, so
    # deleting it here destroyed the step's own input (2026-08-12: the decision
    # was lost and had to be rebuilt by hand). Renaming still disarms it: only
    # a file named exactly pending.json is treated as outstanding.
    if [ -f "$STATE_DIR/pending.json" ]; then
      archived="$STATE_DIR/pending.json.applied-$(date +%Y%m%d-%H%M%S)"
      if mv -f "$STATE_DIR/pending.json" "$archived" 2>/dev/null; then
        record_decisions_from "$archived"
        cp -f "$DETAIL_LOG" "$STATE_DIR/finalize-detail.log" 2>/dev/null || true
      else
        rm -f "$STATE_DIR/pending.json"
      fi
    fi
  fi
}

# Recording the operator's decisions used to be the LAST step of Mode B, run by
# the agent — but its session dies with the gateway restart the smoketest
# triggers, so on 2026-08-12 the record never ran and the memory had to be
# rebuilt by hand. We hold the archived file and outlive the restart: record
# here, best-effort (a memory that failed to update is worth a warning, not a
# rollback of a merge that is already live).
record_decisions_from() {
  local archived="$1" helper
  helper="$SCRIPTS_DIR/upstream_sync_decisions.py"
  [ -f "$helper" ] || helper="$REPO/scripts/upstream_sync_decisions.py"
  if [ ! -f "$helper" ]; then
    echo "warning: upstream_sync_decisions.py not found; decision memory not updated" >>"$DETAIL_LOG"
    return 0
  fi
  python3 "$helper" record --pending "$archived" \
      --memory "$STATE_DIR/decision-memory.json" \
      --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$DETAIL_LOG" 2>&1 || \
    echo "warning: decision memory not updated (see above)" >>"$DETAIL_LOG"
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
  text="upstream-sync finalizer: action=$ACTION status=$status${FAILED_STAGE:+ failed_stage=$FAILED_STAGE} backup_ref=${BACKUP_REF:-none} (details: finalize-result.json / finalize-detail.log)"
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

# Apply the sync: rebase, then smoke-test. Each stage reports under its own
# name so the operator-facing summary never has to guess which one died.
run_apply_pipeline() {
  if ! run_logged bash "$SCRIPTS_DIR/sync-local-customizations.sh"; then
    FAILED_STAGE=rebase
    do_rollback
    write_result failed "rebase stage failed — the smoketest never ran. $(cat "$DETAIL_LOG")"
    return
  fi
  if ! run_logged bash "$SCRIPTS_DIR/upstream-sync-smoketest.sh" "$UPSTREAM_SHA"; then
    FAILED_STAGE=smoketest
    do_rollback
    write_result failed "smoketest stage failed after a successful rebase. $(cat "$DETAIL_LOG")"
    return
  fi
  write_result ok "$(cat "$DETAIL_LOG")"
}

# The scratch clone is made INSIDE the sandbox: its files arrive root-owned
# (git then refuses it for us as "dubious ownership") and its
# objects/info/alternates names the sandbox mount /workspace/live-hermes,
# which does not exist here — on 2026-08-15 either would have killed the fetch
# below. Make the clone ours and point it at our object store before reading
# it. Idempotent: the sandbox re-clones on every attempt and never needs this
# copy again, and objects are content-addressed, so swapping the alternate
# cannot change what a SHA means.
adopt_scratch_clone() {
  local scratch="$1"
  sudo -n chown -R "$(id -un):$(id -gn)" "$scratch" >>"$DETAIL_LOG" 2>&1 || \
    echo "warning: could not chown $scratch — git may refuse it as dubious" >>"$DETAIL_LOG"
  local alternates="$scratch/.git/objects/info/alternates"
  if [ -f "$alternates" ]; then
    printf '%s\n' "$REPO/.git/objects" >"$alternates"
  fi
}

# Gate the agent's merge on the fork's own tests before it becomes the live
# branch — the same before/after comparison the sync script applies to an
# automatic merge. Baseline is our HEAD, post is the merge; only NEW failures
# block, and an unreadable run (killed, no summary line) blocks too.
merge_passes_fork_tests() {
  local before="$1" after="$2"
  local test_cmd="${HERMES_SYNC_TEST_CMD:-$SCRIPTS_DIR/run-fork-tests.sh}"
  [ -x "$test_cmd" ] || test_cmd="$REPO/scripts/run-fork-tests.sh"
  local gate="$SCRIPTS_DIR/upstream_sync_gate.py"
  [ -f "$gate" ] || gate="$REPO/scripts/upstream_sync_gate.py"
  local py="${HERMES_PYTHON:-$REPO/venv/bin/python}"
  [ -x "$py" ] || py="$(command -v python3)"
  local wt baseline post rc new_failures
  wt="$(mktemp -d -t hermes-apply-merge-XXXXXX)"
  baseline="$(mktemp)"
  post="$(mktemp)"
  if ! git -C "$REPO" worktree add --detach "$wt" "$before" >>"$DETAIL_LOG" 2>&1; then
    rm -rf "$wt" "$baseline" "$post"
    echo "could not create a worktree for the test gate" >>"$DETAIL_LOG"
    return 1
  fi
  "$test_cmd" "$wt" >"$baseline" 2>&1 || true
  if ! git -C "$wt" checkout -q --detach "$after" >>"$DETAIL_LOG" 2>&1; then
    git -C "$REPO" worktree remove --force "$wt" >/dev/null 2>&1 || true
    rm -rf "$wt" "$baseline" "$post"
    echo "could not check out the merge in the test-gate worktree" >>"$DETAIL_LOG"
    return 1
  fi
  "$test_cmd" "$wt" >"$post" 2>&1 || true
  git -C "$REPO" worktree remove --force "$wt" >/dev/null 2>&1 || true
  rm -rf "$wt"
  # set +e, not `|| true`: rc 2 (could not compare) must stay distinct from
  # "no new failures" — the same idiom the sync script uses for this gate.
  set +e
  new_failures="$("$py" "$gate" new-failures --baseline "$baseline" --post "$post")"
  rc=$?
  set -e
  if [ "$rc" -eq 2 ]; then
    {
      echo "could not compare the two test runs; refusing to land the merge."
      echo "baseline tail:"; tail -n 5 "$baseline"
      echo "post-merge tail:"; tail -n 5 "$post"
    } >>"$DETAIL_LOG"
    rm -f "$baseline" "$post"
    return 1
  fi
  rm -f "$baseline" "$post"
  if [ -n "$new_failures" ]; then
    {
      echo "the merge introduces test failures:"
      printf '%s\n' "$new_failures" | sed 's/^/  /'
    } >>"$DETAIL_LOG"
    return 1
  fi
  return 0
}

# What follows a merge we fast-forwarded ourselves: the sync script's
# post-update tail (parse check, runtime scripts, push, restart), then the
# smoketest. Not the full sync — that re-fetches upstream and takes its
# conflict branch whenever the newer tip conflicts, which exits successfully
# without pushing and without syncing the runtime scripts (2026-08-15).
run_post_update_pipeline() {
  local before="$1"
  if ! run_logged bash "$SCRIPTS_DIR/sync-local-customizations.sh" --post-update-only "$before"; then
    FAILED_STAGE=post-update
    do_rollback
    write_result failed "post-update stage (scripts sync / push / restart) failed after the merge was fast-forwarded — rolled back. $(cat "$DETAIL_LOG")"
    return
  fi
  if ! run_logged bash "$SCRIPTS_DIR/upstream-sync-smoketest.sh" "$UPSTREAM_SHA"; then
    FAILED_STAGE=smoketest
    do_rollback
    write_result failed "smoketest stage failed after the merge was fast-forwarded — rolled back. $(cat "$DETAIL_LOG")"
    return
  fi
  write_result ok "$(cat "$DETAIL_LOG")"
}

case "$ACTION" in
  sync|rebase)
    if [ -z "$BACKUP_REF" ]; then
      BACKUP_REF="backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S)"
      REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
      run_logged git -C "$REPO" branch "$BACKUP_REF" HEAD || true
      run_logged git -C "$REPO" tag "$BACKUP_REF" HEAD || true
    fi
    run_apply_pipeline
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
    run_apply_pipeline
    ;;
  apply-merge)
    # The live checkout is bind-mounted :ro into sandboxes, so the Mode B agent
    # cannot create a backup ref or commit a merge in it (2026-08-12: an apply
    # died on exactly that, having been told to do both). It merges in a
    # writable `git clone --shared` under the state dir instead and hands us the
    # SHA. Trust is re-derived HERE from the commit own parents rather than
    # taken on the agent word: a merge that is not parented exactly on our
    # current HEAD and on the operator-approved upstream point is refused.
    MERGE_SHA="$(json_field merge_sha)"
    SCRATCH_NAME="$(json_field scratch_repo)"
    # A bare directory name resolved from OUR state dir — never a path carried
    # in the request, so there is no traversal to validate away.
    case "$SCRATCH_NAME" in
      "" | . | .. | */*)
        write_result failed "invalid scratch_repo [$SCRATCH_NAME] — must be a bare directory name under the state dir; repo untouched, no rollback."
        exit 0
        ;;
    esac
    SCRATCH="$STATE_DIR/$SCRATCH_NAME"
    if [ -z "$MERGE_SHA" ] || [ -z "$UPSTREAM_SHA" ]; then
      write_result failed "apply-merge needs both merge_sha and upstream_sha; repo untouched, no rollback."
      exit 0
    fi
    if [ ! -d "$SCRATCH/.git" ]; then
      write_result failed "scratch_repo [$SCRATCH_NAME] is not a git repository under the state dir — repo untouched, no rollback."
      exit 0
    fi
    adopt_scratch_clone "$SCRATCH"
    # Fetch the scratch clone HEAD, then insist it is the commit we were
    # promised — a mismatch means the clone moved between the agent writing
    # the request and this running.
    if ! run_logged git -C "$REPO" fetch --no-tags "$SCRATCH" HEAD; then
      write_result failed "could not fetch the merge from scratch_repo [$SCRATCH_NAME] — repo untouched, no rollback. $(cat "$DETAIL_LOG")"
      exit 0
    fi
    FETCHED="$(git -C "$REPO" rev-parse FETCH_HEAD 2>>"$DETAIL_LOG")"
    if [ "$FETCHED" != "$MERGE_SHA" ]; then
      write_result failed "scratch_repo HEAD $FETCHED is not the promised merge_sha $MERGE_SHA — repo untouched, no rollback."
      exit 0
    fi
    # pending.json is the gate-time record, written before the operator
    # answered; the request field is only the agent's claim. Where they differ,
    # the merge joins a point nobody decided about — upstream keeps moving
    # while the gate waits, and later commits can change the conflict set the
    # decision was made against.
    PENDING_HEAD="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"upstream_head\") or \"\")" "$STATE_DIR/pending.json" 2>/dev/null || true)"
    if [ -n "$PENDING_HEAD" ] && [ "$PENDING_HEAD" != "$UPSTREAM_SHA" ]; then
      write_result failed "upstream_sha $UPSTREAM_SHA is not the point the operator was gated on (pending.json records $PENDING_HEAD) — refusing; repo untouched, no rollback, decision kept for a re-gate."
      exit 0
    fi
    HEAD_SHA="$(git -C "$REPO" rev-parse HEAD 2>>"$DETAIL_LOG")"
    UPSTREAM_FULL="$(git -C "$REPO" rev-parse "$UPSTREAM_SHA" 2>>"$DETAIL_LOG")"
    MERGE_PARENTS="$(git -C "$REPO" rev-list --parents -n1 "$MERGE_SHA" 2>>"$DETAIL_LOG" | cut -d" " -f2-)"
    PARENT_LOCAL="$(printf %s "$MERGE_PARENTS" | cut -d" " -f1)"
    PARENT_UPSTREAM="$(printf %s "$MERGE_PARENTS" | cut -d" " -f2)"
    if [ "$PARENT_LOCAL" != "$HEAD_SHA" ] || [ "$PARENT_UPSTREAM" != "$UPSTREAM_FULL" ]; then
      write_result failed "merge_sha parent mismatch: parents ($MERGE_PARENTS) are not (HEAD $HEAD_SHA, approved upstream $UPSTREAM_FULL) — refusing; repo untouched, no rollback."
      exit 0
    fi
    if ! merge_passes_fork_tests "$HEAD_SHA" "$MERGE_SHA"; then
      FAILED_STAGE=test-gate
      write_result failed "the agent merge does not pass the fork's tests — not landed; repo untouched, no rollback, decision kept for a rework. $(cat "$DETAIL_LOG")"
      exit 0
    fi
    # Invariant 1 (back up before touching the branch) is the host job now:
    # the agent has no write access to make a backup ref.
    if [ -z "$BACKUP_REF" ]; then
      BACKUP_REF="backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S)"
    fi
    run_logged git -C "$REPO" branch -f "$BACKUP_REF" HEAD || true
    # --ff-only, not merge: the parent check already proved this commit sits
    # directly on our HEAD, so anything that still needs a real merge here is a
    # race we must lose rather than paper over.
    if ! run_logged git -C "$REPO" merge --ff-only "$MERGE_SHA"; then
      FAILED_STAGE=fast-forward
      write_result failed "fast-forward to the agent merge failed — repo untouched, no rollback. $(cat "$DETAIL_LOG")"
      exit 0
    fi
    run_post_update_pipeline "$HEAD_SHA"
    # The clone is a full working copy; keep it only when it may still be
    # needed for diagnosis.
    if [ "$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['status'])" "$RESULT" 2>/dev/null)" = ok ]; then
      rm -rf "$SCRATCH"
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
