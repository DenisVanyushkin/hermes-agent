#!/usr/bin/env bash
set -euo pipefail

# T22 preparation: prove the contract-set publication in a disposable target.
# This script is deliberately dry-run only.  The live publication, path-unit
# stop/start, and post-publication service acceptance remain an operator step.

if [ "${1:-}" != "--dry-run" ] || [ "${2:-}" != "" ]; then
  echo "usage: publish-runtime.sh --dry-run" >&2
  exit 2
fi

SOURCE_ROOT="${HERMES_RUNTIME_SOURCE_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}"
TARGET_ROOT="${HERMES_RUNTIME_TARGET_ROOT:-$(mktemp -d -t hermes-runtime-publish.XXXXXX)}"
SOURCE_SCRIPTS="$SOURCE_ROOT/scripts"
TARGET_SCRIPTS="$TARGET_ROOT/scripts"
TARGET_STATE="${HERMES_RUNTIME_TARGET_STATE_DIR:-$TARGET_ROOT/state}"

RUNTIME_FILES=(
  upstream-sync-finalize.sh
  run-fork-tests.sh
  upstream_sync_gate.py
  sync-local-customizations.sh
  upstream-sync-smoketest.sh
  upstream_sync_triage.py
  upstream_sync_slack.py
  upstream_sync_apply.py
)

mkdir -p "$TARGET_SCRIPTS" "$TARGET_STATE"

for name in "${RUNTIME_FILES[@]}"; do
  src="$SOURCE_SCRIPTS/$name"
  dst="$TARGET_SCRIPTS/$name"
  [ -f "$src" ] || {
    echo "T22: missing runtime source: $src" >&2
    exit 1
  }
  install -m 755 "$src" "$dst"
done

if [ -e "$TARGET_STATE/finalize-request.json" ]; then
  echo "T22: finalize-request.json exists in dry-run target" >&2
  exit 1
fi

for name in "${RUNTIME_FILES[@]}"; do
  diff -u "$SOURCE_SCRIPTS/$name" "$TARGET_SCRIPTS/$name" >/dev/null || {
    echo "T22: published runtime differs: $name" >&2
    exit 1
  }
done

printf 'dry_run=1\n'
printf 'files=%s\n' "${#RUNTIME_FILES[@]}"
printf 'diffs=clean\n'
printf 'finalize_request=absent\n'
printf 'path_unit=not_touched\n'
