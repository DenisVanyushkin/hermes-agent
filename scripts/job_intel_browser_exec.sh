#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="${JOB_INTEL_BROWSER_RUNTIME_DIR:-/var/lib/browser-desktop}"
export HOME="${HOME:-$BASE_DIR}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$BASE_DIR/runtime}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$BASE_DIR/.config}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$BASE_DIR/.cache}"
exec dbus-run-session -- /usr/local/bin/browser-chromium --no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage "$@"
