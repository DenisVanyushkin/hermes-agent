#!/usr/bin/env bash
# Phase 6a DoD: restore the latest assistant.db backup into a scratch copy,
# prove integrity + schema, and prove fam can open it. PASS/FAIL, non-destructive.
set -euo pipefail
REPO="/home/denis/.hermes/hermes-agent"
BACKUP_DIR="${1:-/home/denis/.hermes/private/amina/backups}"
LATEST="$(ls -1t "$BACKUP_DIR"/assistant-*.db 2>/dev/null | head -1 || true)"
[ -n "$LATEST" ] || { echo "FAIL: no assistant backup in $BACKUP_DIR"; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cp "$LATEST" "$TMP/assistant.db"
# 1. integrity + schema via maint.verify_backup
PYTHONPATH="$REPO/custom/fam" "$REPO/venv/bin/python" - "$TMP/assistant.db" <<'PY'
import sys
from fam import maint
ok, detail = maint.verify_backup(sys.argv[1])
print("verify:", detail)
sys.exit(0 if ok else 1)
PY
# 2. the app opens the restored DB
FAM_DB="$TMP/assistant.db" "$REPO/custom/fam/bin/fam" people list >/dev/null
echo "PASS: restored $LATEST — integrity ok, schema ok, fam opened it"
