#!/usr/bin/env bash
set -euo pipefail

# T21 acceptance: exercise the real gate-only entrypoint with scripts from this
# worktree, while keeping the repository and state under a disposable clone.
# This deliberately does not stop or start any systemd unit.

ACCEPTANCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd -- "$ACCEPTANCE_DIR/.." && pwd)"
SOURCE_REPO="${HERMES_REHEARSAL_SOURCE_REPO:-$(cd -- "$SCRIPTS_DIR/.." && pwd)}"
PYTHON_BIN="${HERMES_PYTHON:-/home/hermes/.hermes/hermes-agent/venv/bin/python}"

command -v git >/dev/null
[ -x "$PYTHON_BIN" ] || {
  echo "T21: Python interpreter is unavailable: $PYTHON_BIN" >&2
  exit 2
}

tmp_root="$(mktemp -d -t hermes-gate-rehearsal.XXXXXX)"
trap 'rm -rf -- "$tmp_root"' EXIT

repo="$tmp_root/repo"
state="$tmp_root/state"
mkdir -p "$state/scratch"

after="${HERMES_REHEARSAL_AFTER:-f941342b6ba7a5b235ae70766339d49f1e852ade}"
if [ -z "$after" ]; then
  after="$(git -C "$SOURCE_REPO" log --format=%H --merges -1)"
fi
before="$(git -C "$SOURCE_REPO" rev-parse "$after^1")"
boundary="$(git -C "$SOURCE_REPO" rev-parse "$after^2")"

[ "$before" != "$after" ] || {
  echo "T21: before and after must differ" >&2
  exit 2
}

git clone --shared --no-checkout "$SOURCE_REPO" "$repo" >/dev/null
git -C "$repo" checkout --detach -q "$before"
live_before="$(git -C "$repo" rev-parse HEAD)"
[ "$live_before" = "$before" ] || {
  echo "T21: isolated live HEAD is not before" >&2
  exit 1
}

# These are the state objects gate-only is forbidden to change.
printf '%s\n' '{"status":"pending","local_head":"fixture","upstream_head":"fixture"}' \
  >"$state/pending.json"
printf '%s\n' 'pre-existing scratch evidence' >"$state/scratch/evidence.txt"
cp -a "$state/pending.json" "$tmp_root/pending.before"
cp -a "$state/scratch" "$tmp_root/scratch.before"

attempt_id="t21-rehearsal-$(date +%s)"
printf '%s\n' "{\"action\":\"gate-only\",\"before\":\"$before\",\"after\":\"$after\",\"boundary\":\"$boundary\",\"attempt_id\":\"$attempt_id\"}" \
  >"$tmp_root/request.json"
mv -- "$tmp_root/request.json" "$state/finalize-request.json"

set +e
HERMES_HOME="$tmp_root/home" \
HERMES_REPO="$repo" \
HERMES_SYNC_STATE_DIR="$state" \
HERMES_SCRIPTS_DIR="$SCRIPTS_DIR" \
HERMES_PYTHON="$PYTHON_BIN" \
  bash "$SCRIPTS_DIR/upstream-sync-finalize.sh" >"$tmp_root/finalizer.log" 2>&1
finalizer_rc=$?
set -e

if [ "$finalizer_rc" -ne 0 ]; then
  echo "T21: finalizer exited $finalizer_rc" >&2
  tail -n 80 "$tmp_root/finalizer.log" >&2 || true
  exit 1
fi

result_count="$(find "$state/attempts" -type f -name attempt-result.json -print 2>/dev/null | wc -l | tr -d ' ')"
[ "$result_count" = 1 ] || {
  echo "T21: expected one gate-only attempt result, found $result_count" >&2
  tail -n 80 "$tmp_root/finalizer.log" >&2 || true
  exit 1
}
result="$(find "$state/attempts" -type f -name attempt-result.json -print -quit)"
verdict="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1]))["gate_verdict"])' "$result")"
[ "$verdict" = block ] || {
  echo "T21: expected measured gate_verdict=block, got $verdict" >&2
  tail -n 80 "$tmp_root/finalizer.log" >&2 || true
  exit 1
}

live_after="$(git -C "$repo" rev-parse HEAD)"
[ "$live_after" = "$live_before" ] || {
  echo "T21: gate-only changed isolated live HEAD" >&2
  exit 1
}
cmp -s "$tmp_root/pending.before" "$state/pending.json" || {
  echo "T21: gate-only changed pending.json" >&2
  exit 1
}
diff -ru "$tmp_root/scratch.before" "$state/scratch" >/dev/null || {
  echo "T21: gate-only changed scratch/" >&2
  exit 1
}

printf 'gate_verdict=%s\n' "$verdict"
printf 'before=%s\n' "$live_before"
printf 'after=%s\n' "$after"
printf 'pending_and_scratch=unchanged\n'
