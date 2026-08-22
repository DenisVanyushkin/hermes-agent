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
#  - apply-merge: land a merge an external party built in <state>/<scratch_repo>
#  - apply-decisions: build AND land the merge for a decided pending.json —
#              clone, mechanical + model resolution, commit, gate, publish.
#              No sandbox, no agent: the host owns the whole state machine.
#  - apply-triage-fixes: the operator answered "apply fix" to a proposal this
#              script made after a red test gate — patch the test files in the
#              preserved clone, amend the merge, and try to land ONCE.
# Result written to finalize-result.json in the same dir.

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STATE_DIR="${HERMES_SYNC_STATE_DIR:-$HERMES_HOME/state/upstream-sync}"
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
  [ "$ACTION" = sync ] || [ "$ACTION" = rebase ] || [ "$ACTION" = apply-merge ] || \
    [ "$ACTION" = apply-decisions ] || [ "$ACTION" = apply-triage-fixes ]
}
UPSTREAM_SHA="$(json_field upstream_sha)"
BACKUP_REF="$(json_field backup_ref)"

# Which stage of the apply pipeline died, if any. The apply used to be one
# `if rebase && smoketest` with a single status, so a rebase failure was
# reported to the operator as a failed smoketest (2026-07-27) — the log tail
# they were shown started well after the real cause.
FAILED_STAGE=""
BREAK_GLASS_NOTICE=""

write_result() {
  # Statuses: ok | failed | awaiting_decision (apply-decisions stopped to ask
  # the operator about a new security-path conflict — not a failure, the gate
  # stays armed and pending.json carries the question).
  # Keep the complete log next to the result: the JSON detail is truncated
  # to its tail, which has already hidden the actual failure cause once
  # (2026-07-16: a pre-report error line was cut off, leaving a causeless
  # rollback). One file, overwritten per run.
  cp -f "$DETAIL_LOG" "$STATE_DIR/finalize-detail.log" 2>/dev/null || true
  # The detail reaches python as a FILE, never as an argv element. Linux caps a
  # single argument at 128 KiB and two full fork-test runs sail past that, so
  # this exec died with "Argument list too long" *after* a merge had landed,
  # been pushed and smoke-tested — no result written, the decision left
  # unarchived and the memory unrecorded (2026-08-15). The value only ever
  # existed to be truncated to its tail, which python still does below.
  detail_file="$(mktemp)"
  printf '%s' "$2" >"$detail_file"
  python3 - "$RESULT" "$ACTION" "$1" "$detail_file" "$BACKUP_REF" "$FAILED_STAGE" <<'PY'
import json, sys, datetime
path, action, status, detail_path, backup, stage = sys.argv[1:7]
with open(detail_path, encoding="utf-8", errors="replace") as fh:
    detail = fh.read()
json.dump({
    "action": action, "status": status, "detail": detail[-4000:],
    "detail_log": "finalize-detail.log",
    "backup_ref": backup,
    "failed_stage": stage or None,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path, "w"), ensure_ascii=False, indent=1)
PY
  rm -f "$detail_file"
  rm -f "$PROCESSING"
  # The triage proposal is answered exactly once. "applied" closes it; a gate
  # that is still red after the fix ends "exhausted", which is what stops the
  # next run from proposing another patch on top of a failed one.
  if [ "$ACTION" = apply-triage-fixes ] && [ -f "$STATE_DIR/gate-triage.json" ]; then
    python3 - "$STATE_DIR/gate-triage.json" "$1" <<'PY' || true
import json, sys
path, status = sys.argv[1:3]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)
data["status"] = "applied" if status == "ok" else "exhausted"
json.dump(data, open(path, "w"), ensure_ascii=False, indent=1)
PY
  fi
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
        ARCHIVED_PENDING="$archived"
        record_decisions_from "$archived"
        cp -f "$DETAIL_LOG" "$STATE_DIR/finalize-detail.log" 2>/dev/null || true
      else
        rm -f "$STATE_DIR/pending.json"
      fi
    fi
  fi
  # After the archive: the thread report reads pending.json or its archive.
  report_to_thread "$1"
}

# The operator-facing summary, threaded under the conflict report when
# pending.json (or its archive) knows the thread. Composed by the Slack helper
# from apply-prepare.json + finalize-result.json; best effort — a failed post
# never changes the outcome. notify_slack keeps its one-liner as the fallback
# channel message.
report_to_thread() {
  local status="$1" py helper
  helper="$SCRIPTS_DIR/upstream_sync_slack.py"
  [ -f "$helper" ] || helper="$REPO/scripts/upstream_sync_slack.py"
  [ -f "$helper" ] || return 0
  py="${HERMES_PYTHON:-$REPO/venv/bin/python}"
  [ -x "$py" ] || py="$(command -v python3)"
  "$py" - "$helper" "$STATE_DIR" "$RESULT" "$status" "$ACTION" "$SCRATCH_FOR_REPORT" "$ARCHIVED_PENDING" >>"$DETAIL_LOG" 2>&1 <<'PY' || echo "warning: thread report not posted (see above)" >>"$DETAIL_LOG"
import glob, importlib.util, json, os, sys
helper, state, result_path, status, action, scratch, archived_pending = sys.argv[1:8]
spec = importlib.util.spec_from_file_location("upstream_sync_slack", helper)
slack = importlib.util.module_from_spec(spec); spec.loader.exec_module(slack)
def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
pending = load(os.path.join(state, "pending.json"))
if not pending and archived_pending:
    # The archive this very run created. On success pending.json is renamed
    # before the report runs, so the caller hands us the path rather than
    # letting us re-derive it.
    pending = load(archived_pending)
if not pending:
    # Last resort: newest by mtime. NEVER by name — a hand-made archive
    # (pending.json.applied-manual-20260724) sorts above every dated one
    # because "m" outranks any digit, and it carries no channel, so the
    # report would exit 0 having posted nowhere (silent since 2026-07-24).
    archived = glob.glob(os.path.join(state, "pending.json.applied-*"))
    pending = load(max(archived, key=os.path.getmtime)) if archived else {}
channel = pending.get("slack_channel") or os.environ.get("HERMES_SYNC_SLACK_CHANNEL") or ""
thread = pending.get("slack_thread_ts") or None
if not channel:
    sys.exit(0)
prep = load(os.path.join(state, "apply-prepare.json"))
result = load(result_path)
triage = load(os.path.join(state, "gate-triage.json"))
if status == "ok" and action in ("apply-decisions", "apply-merge", "apply-triage-fixes"):
    text = slack.applied_text(prep, result)
elif status == "awaiting_decision":
    text = slack.report_text(pending)
elif status == "failed" and action in ("apply-decisions", "apply-merge", "apply-triage-fixes"):
    text = slack.failed_text(prep, result, scratch=scratch, triage=triage)
else:
    sys.exit(0)
slack.post(channel, text, thread_ts=thread)
PY
}
SCRATCH_FOR_REPORT=""
ARCHIVED_PENDING=""
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
  # Keep both runs. They used to be mktemp'd and deleted, which left a blocked
  # merge with no evidence beyond a truncated log tail in the result JSON —
  # and no way to diagnose WHICH change broke WHICH test without redoing the
  # whole thing by hand. The triage reads these.
  cp -f "$baseline" "$STATE_DIR/gate-baseline.log" 2>/dev/null || true
  cp -f "$post" "$STATE_DIR/gate-post.log" 2>/dev/null || true
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
    # The list travels in a FILE, not a pipe: `python3 - <<PY` already claims
    # stdin for the script itself, so a piped payload arrives empty and the
    # evidence file records zero failures for a gate that just blocked.
    local failures_file
    failures_file="$(mktemp)"
    printf '%s\n' "$new_failures" >"$failures_file"
    python3 - "$STATE_DIR/gate-failures.json" "$after" "$before" "$failures_file" <<'PY' || true
import datetime, json, sys
path, merge_sha, before, failures_path = sys.argv[1:5]
with open(failures_path, encoding="utf-8") as fh:
    failures = [l.strip() for l in fh.read().splitlines() if l.strip()]
json.dump({"merge_sha": merge_sha, "before": before, "new_failures": failures,
           "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
          open(path, "w"), ensure_ascii=False, indent=1)
PY
    rm -f "$failures_file"
    return 1
  fi
  rm -f "$STATE_DIR/gate-failures.json"
  return 0
}

# Diagnose a red gate and PROPOSE a test patch — never apply one. Best effort by
# construction: the gate has already decided the merge does not land, and a
# triage that falls over must not turn that into a different outcome. Skipped
# when we are already applying a proposal: one attempt, no proposal on top of a
# failed proposal (that loop is how automation ends up rewriting tests until
# they pass).
run_gate_triage() {
  [ "$ACTION" = apply-triage-fixes ] && return 0
  [ -f "$STATE_DIR/gate-failures.json" ] || return 0
  local triage py
  triage="$SCRIPTS_DIR/upstream_sync_triage.py"
  [ -f "$triage" ] || triage="$REPO/scripts/upstream_sync_triage.py"
  [ -f "$triage" ] || { echo "warning: upstream_sync_triage.py not found; no triage" >>"$DETAIL_LOG"; return 0; }
  py="${HERMES_PYTHON:-$REPO/venv/bin/python}"
  [ -x "$py" ] || py="$(command -v python3)"
  "$py" "$triage" --state "$STATE_DIR" --repo "$REPO" >>"$DETAIL_LOG" 2>&1 || \
    echo "warning: gate triage failed (see above); the gate outcome stands" >>"$DETAIL_LOG"
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

# Land a merge commit that sits in $SCRATCH: fetch it, prove it is parented on
# our HEAD and the gated upstream point, run the fork tests, back up, fast-
# forward, publish, smoke-test. Shared by apply-merge (a merge someone else
# built) and apply-decisions (a merge we built ourselves). Uses MERGE_SHA,
# UPSTREAM_SHA, SCRATCH, SCRATCH_NAME, BACKUP_REF from the caller. Every early
# return follows a write_result, so exiting there is terminal by design.
land_merge() {
    if [ -z "$BREAK_GLASS_NOTICE" ] &&        grep -q '"invariants_break_glass"' "$STATE_DIR/apply-prepare.json" 2>/dev/null; then
      BREAK_GLASS_NOTICE="BREAK_GLASS: structural gate was not executed; merge continued only via explicit manual emergency bypass."
    fi
    # A merge that is already the branch tip is a duplicate hand-off, not a
    # mismatch: reporting it as a parent mismatch overwrote the real outcome of
    # the run that had just landed it, telling the operator the apply had failed
    # while it was live (2026-08-15). This has to come BEFORE any work on the
    # clone, because a successful apply deletes the clone — so the duplicate
    # would otherwise die on an unfetchable scratch repo, i.e. for the wrong
    # reason entirely.
    if [ "$MERGE_SHA" = "$(git -C "$REPO" rev-parse HEAD 2>>"$DETAIL_LOG")" ]; then
      write_result ok "already applied — $MERGE_SHA is the branch tip; nothing to do (duplicate request)."
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
      # Before the report, not after: the operator's message IS the triage.
      run_gate_triage
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
    if [ -n "$BREAK_GLASS_NOTICE" ]; then
      echo "$BREAK_GLASS_NOTICE" >>"$DETAIL_LOG"
    fi
    run_post_update_pipeline "$HEAD_SHA"
    # The clone is a full working copy; keep it only when it may still be
    # needed for diagnosis.
    if [ "$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['status'])" "$RESULT" 2>/dev/null)" = ok ]; then
      rm -rf "$SCRATCH"
    fi
}

# apply-decisions — the host-owned path. pending.json carries every decision
# (policy / memory / operator); nothing runs in a sandbox and no agent holds
# state. Each step writes its outcome to the state dir, so a failed run leaves
# a precise report and a preserved clone rather than a half-applied branch,
# and a re-request after a manual fix resumes instead of starting over.
apply_decisions() {
  local py apply prep_status
  py="${HERMES_PYTHON:-$REPO/venv/bin/python}"
  [ -x "$py" ] || py="$(command -v python3)"
  apply="$SCRIPTS_DIR/upstream_sync_apply.py"
  [ -f "$apply" ] || apply="$REPO/scripts/upstream_sync_apply.py"
  SCRATCH_NAME="scratch"
  SCRATCH="$STATE_DIR/$SCRATCH_NAME"
  SCRATCH_FOR_REPORT="$SCRATCH"
  if [ ! -f "$STATE_DIR/pending.json" ]; then
    write_result failed "apply-decisions: no pending.json — nothing decided to apply; repo untouched."
    exit 0
  fi
  UPSTREAM_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"upstream_head\") or \"\")" "$STATE_DIR/pending.json" 2>/dev/null || true)"
  if [ -z "$UPSTREAM_SHA" ]; then
    write_result failed "apply-decisions: pending.json has no upstream_head; repo untouched."
    exit 0
  fi

  # Resume: a preserved clone with no conflict markers left is someone's hand
  # work — take it as is. Otherwise (re)build it.
  prep_status="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"status\") or \"\")" "$STATE_DIR/apply-prepare.json" 2>/dev/null || true)"
  if [ -d "$SCRATCH/.git" ] && [ "$prep_status" = ready ] && \
     [ -z "$(git -C "$SCRATCH" ls-files -u 2>/dev/null)" ] && \
     [ "$(git -C "$SCRATCH" rev-parse HEAD 2>/dev/null)" = "$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" -o -n "$(git -C "$SCRATCH" rev-parse -q --verify MERGE_HEAD 2>/dev/null)" ]; then
    echo "apply-decisions: resuming from the preserved clone (no unmerged paths)" >>"$DETAIL_LOG"
  else
    rm -rf "$SCRATCH"
    set +e
    "$py" "$apply" prepare --state "$STATE_DIR" --live "$REPO" --scratch "$SCRATCH_NAME" --auto-policy --in-flight-ok >>"$DETAIL_LOG" 2>&1
    rc=$?
    set -e
    case "$rc" in
      0) ;;
      4)
        # A new security-path conflict: the policy does not decide those. Ask
        # (report_to_thread posts the question) and keep everything armed.
        write_result awaiting_decision "apply-decisions: a new conflict on a security-sensitive path needs the operator's decision; nothing applied. $(cat "$DETAIL_LOG")"
        exit 0
        ;;
      *)
        FAILED_STAGE=prepare
        write_result failed "apply-decisions: prepare failed (rc=$rc); repo untouched. $(cat "$DETAIL_LOG")"
        exit 0
        ;;
    esac
    set +e
    "$py" "$apply" resolve-llm --state "$STATE_DIR" --live "$REPO" --scratch "$SCRATCH_NAME" >>"$DETAIL_LOG" 2>&1
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      FAILED_STAGE=resolve
      write_result failed "apply-decisions: the model could not resolve every hunk; the clone is preserved at $SCRATCH with markers in place, decision kept armed. $(cat "$DETAIL_LOG")"
      exit 0
    fi
  fi
  set +e
  "$py" "$apply" commit --state "$STATE_DIR" --live "$REPO" --scratch "$SCRATCH_NAME" >>"$DETAIL_LOG" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    FAILED_STAGE=commit
    # A structural refusal is its own outcome, not a generic commit failure, and
    # it must not read like a red test gate. That one can legitimately mean the
    # tests went stale, which is what the triage flow offers to patch. Here the
    # merge itself lost something: patching tests would bury the finding along
    # with the code it points at (2026-08-19).
    if grep -Eq "invariants_failed|invariant_origin_incomplete" "$DETAIL_LOG"; then
      FAILED_STAGE=invariants
      findings="$(python3 "$SCRIPTS_DIR/upstream_sync_findings.py" "$DETAIL_LOG" 2>/dev/null || true)"
      write_result failed "apply-decisions: the resolved merge failed its structural checks — nothing was committed, the clone is preserved at $SCRATCH.
${findings:-(see finalize-detail.log)}

Not a stale-test failure — do not answer with the triage vocabulary. Either the resolution dropped code that has to come back, or every finding is intended, in which case repair the resolution, or answer each listed soft finding with its exact `ack INV-...` receipt; hard findings cannot be acknowledged. When the invariant state is blocked, receipt interception stays disabled until every hard finding is repaired."
      exit 0
    fi
    write_result failed "apply-decisions: could not commit the merge (rc=$rc — unresolved paths, or the live branch moved); clone preserved. $(cat "$DETAIL_LOG")"
    exit 0
  fi
  MERGE_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"merge_sha\") or \"\")" "$STATE_DIR/apply-prepare.json" 2>/dev/null || true)"
  if grep -q '"invariants_break_glass"' "$STATE_DIR/apply-prepare.json" 2>/dev/null; then
    BREAK_GLASS_NOTICE="BREAK_GLASS: structural gate was not executed; merge continued only via explicit manual emergency bypass."
  fi
  if [ -z "$MERGE_SHA" ]; then
    write_result failed "apply-decisions: commit reported no merge_sha; clone preserved."
    exit 0
  fi
  land_merge
}

# ack-invariant — the operator acknowledged one exact soft finding. The
# Python gate re-reads the merge record and all receipts; it commits only when
# every soft finding still has a matching fingerprint and hard findings are gone.
resume_invariant_ack() {
  local py apply rc
  py="${HERMES_PYTHON:-$REPO/venv/bin/python}"
  [ -x "$py" ] || py="$(command -v python3)"
  apply="$SCRIPTS_DIR/upstream_sync_apply.py"
  [ -f "$apply" ] || apply="$REPO/scripts/upstream_sync_apply.py"
  SCRATCH_NAME="scratch"
  SCRATCH="$STATE_DIR/$SCRATCH_NAME"
  SCRATCH_FOR_REPORT="$SCRATCH"
  if [ ! -f "$STATE_DIR/invariants-pending.json" ] || [ ! -d "$SCRATCH/.git" ]; then
    write_result failed "ack-invariant: no armed invariant state or preserved clone; repo untouched."
    exit 0
  fi
  set +e
  "$py" "$apply" commit --state "$STATE_DIR" --live "$REPO" --scratch "$SCRATCH_NAME" >>"$DETAIL_LOG" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    if grep -q 'invariants_failed' "$DETAIL_LOG"; then
      write_result awaiting_decision "ack-invariant: receipt recorded, but other findings remain or a fingerprint changed; repo untouched. $(cat "$DETAIL_LOG")"
    else
      write_result failed "ack-invariant: commit refused (rc=$rc); clone preserved, repo untouched. $(cat "$DETAIL_LOG")"
    fi
    exit 0
  fi
  UPSTREAM_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('upstream_head') or '')" "$STATE_DIR/pending.json" 2>/dev/null || true)"
  MERGE_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('merge_sha') or '')" "$STATE_DIR/apply-prepare.json" 2>/dev/null || true)"
  if [ -z "$MERGE_SHA" ] || [ -z "$UPSTREAM_SHA" ]; then
    write_result failed "ack-invariant: committed merge or gated upstream point is unknown; clone preserved."
    exit 0
  fi
  land_merge
}

# apply-triage-fixes — the operator answered "apply fix" to a proposal this
# script made after a red test gate. Patch the test files in the preserved
# clone, fold the patch INTO the merge commit (its parents are what the host
# will accept), and try to land once. A gate that is still red ends here: the
# clone is kept and the human takes over.
apply_triage_fixes() {
  local py apply rc
  py="${HERMES_PYTHON:-$REPO/venv/bin/python}"
  [ -x "$py" ] || py="$(command -v python3)"
  apply="$SCRIPTS_DIR/upstream_sync_apply.py"
  [ -f "$apply" ] || apply="$REPO/scripts/upstream_sync_apply.py"
  SCRATCH_NAME="scratch"
  SCRATCH="$STATE_DIR/$SCRATCH_NAME"
  SCRATCH_FOR_REPORT="$SCRATCH"
  if [ ! -f "$STATE_DIR/gate-triage.json" ]; then
    write_result failed "apply-triage-fixes: no gate-triage.json — nothing was proposed; repo untouched."
    exit 0
  fi
  if [ ! -d "$SCRATCH/.git" ]; then
    write_result failed "apply-triage-fixes: the merge clone is gone from $SCRATCH — re-run the sync; repo untouched."
    exit 0
  fi
  # Re-validate every path here rather than trusting the state file: it is
  # plain JSON on disk, and the one thing this action must never do is write
  # outside tests/.
  set +e
  "$py" - "$STATE_DIR/gate-triage.json" "$SCRATCH" >>"$DETAIL_LOG" 2>&1 <<'PY'
import json, pathlib, subprocess, sys
triage_path, scratch = sys.argv[1], pathlib.Path(sys.argv[2])
data = json.load(open(triage_path, encoding="utf-8"))
applied = []
for prop in data.get("proposals") or []:
    patch = prop.get("patch") or ""
    rel = str(prop.get("test_file") or "")
    if not patch:
        continue
    parts = pathlib.PurePosixPath(rel).parts
    if not rel.endswith(".py") or not parts or parts[0] != "tests" or ".." in parts or rel.startswith("/"):
        print(f"refusing {rel!r}: not a test file under tests/ — the triage only ever patches tests")
        sys.exit(3)
    target = scratch / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patch, encoding="utf-8")
    applied.append(rel)
if not applied:
    print("no patch to apply")
    sys.exit(4)
subprocess.run(["git", "-C", str(scratch), "add", "--", *applied], check=True)
print("patched: " + ", ".join(applied))
PY
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    FAILED_STAGE=triage-apply
    write_result failed "apply-triage-fixes: the proposed patch was refused (rc=$rc); repo untouched, clone preserved. $(cat "$DETAIL_LOG")"
    exit 0
  fi
  set +e
  "$py" "$apply" commit --state "$STATE_DIR" --live "$REPO" --scratch "$SCRATCH_NAME" --amend >>"$DETAIL_LOG" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    FAILED_STAGE=commit
    write_result failed "apply-triage-fixes: could not amend the merge with the patch (rc=$rc); clone preserved. $(cat "$DETAIL_LOG")"
    exit 0
  fi
  UPSTREAM_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"upstream_head\") or \"\")" "$STATE_DIR/pending.json" 2>/dev/null || true)"
  MERGE_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(\"merge_sha\") or \"\")" "$STATE_DIR/apply-prepare.json" 2>/dev/null || true)"
  if [ -z "$MERGE_SHA" ] || [ -z "$UPSTREAM_SHA" ]; then
    write_result failed "apply-triage-fixes: the amended merge or the gated upstream point is unknown; clone preserved."
    exit 0
  fi
  land_merge
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
    land_merge
    ;;
  apply-decisions)
    apply_decisions
    ;;
  apply-triage-fixes)
    apply_triage_fixes
    ;;
  ack-invariant)
    resume_invariant_ack
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
