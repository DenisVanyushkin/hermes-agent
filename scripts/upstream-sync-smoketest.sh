#!/usr/bin/env bash
set -euo pipefail

# Post-merge smoke test for upstream-sync.
# Usage: upstream-sync-smoketest.sh [<upstream-sha-to-record>]
# Exit 0 = pass. On pass, records the synced upstream SHA into
# ~/.hermes/state/upstream-sync/last-synced.json (when a SHA is given).

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${PWD:-.}/.git" ] && [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="${PWD}"
elif [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
fi

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STATE_DIR="$HERMES_HOME/state/upstream-sync"
GATEWAY_LOG="$HERMES_HOME/logs/gateway.log"
RECORD_SHA="${1:-}"

fail() {
  echo "SMOKETEST FAIL: $*" >&2
  exit 1
}

resolve_python() {
  for candidate in "$REPO/venv/bin/python" "$REPO/venv/bin/python3" /usr/local/bin/python3 /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

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

echo "== upstream-sync smoketest =="
echo "Repo: $REPO"

# 1. Import check: core packages must at least import under the runtime python.
PYTHON_BIN="$(resolve_python || true)"
[ -n "$PYTHON_BIN" ] || fail "no python interpreter found"
echo "-- import check ($PYTHON_BIN)"
if ! (cd "$REPO" && "$PYTHON_BIN" -c "import agent, gateway, hermes_cli" 2>&1); then
  fail "core package import failed"
fi
echo "imports: ok"

# 2. Gateway restart.
HERMES_BIN="$(resolve_hermes_bin || true)"
[ -n "$HERMES_BIN" ] || fail "hermes executable not found"
LOG_OFFSET=0
if [ -r "$GATEWAY_LOG" ]; then
  LOG_OFFSET="$(wc -c <"$GATEWAY_LOG")"
fi
echo "-- gateway restart ($HERMES_BIN)"
if ! "$HERMES_BIN" gateway restart >/dev/null 2>&1; then
  fail "gateway restart command failed"
fi

# 3. Process alive after grace period.
echo "-- waiting for gateway to come up"
ALIVE=0
for _ in $(seq 1 12); do
  sleep 5
  if pgrep -f "hermes_cli.main gateway" >/dev/null 2>&1; then
    ALIVE=1
    break
  fi
done
[ "$ALIVE" -eq 1 ] || fail "gateway process not running 60s after restart"
echo "process: alive"

# 4. Log health: no traceback in bytes written after the restart.
if [ -r "$GATEWAY_LOG" ]; then
  sleep 10
  CUR_SIZE="$(wc -c <"$GATEWAY_LOG")"
  if [ "$CUR_SIZE" -lt "$LOG_OFFSET" ]; then
    LOG_OFFSET=0 # log rotated during restart
  fi
  if tail -c "+$((LOG_OFFSET + 1))" "$GATEWAY_LOG" | grep -q "Traceback"; then
    fail "traceback in gateway.log right after restart"
  fi
  echo "log: no fresh traceback"
else
  echo "log: $GATEWAY_LOG not readable, skipping log check" >&2
fi

# 5. Record success.
if [ -n "$RECORD_SHA" ]; then
  mkdir -p "$STATE_DIR"
  LOCAL_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
  printf '{"upstream_sha": "%s", "local_sha": "%s", "synced_at": "%s", "result": "%s"}\n' \
    "$RECORD_SHA" "$LOCAL_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${UPSTREAM_SYNC_RESULT:-clean}" \
    >"$STATE_DIR/last-synced.json"
  echo "state: recorded $RECORD_SHA in $STATE_DIR/last-synced.json"
fi

echo "SMOKETEST PASS"
