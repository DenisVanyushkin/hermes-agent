#!/usr/bin/env bash
# Non-destructive restore rehearsal for the NAS offsite (age) backups.
# Usage: offsite-restore-rehearsal.sh <private-key-file> [<age-file-on-nas>]
# Pulls newest assistant-*.db.age from the NAS mount, decrypts to a temp copy
# with the supplied private key, runs maint.verify_backup. The live
# assistant.db is never touched. The private key lives OFF the VM (with Denis)
# and is only passed in transiently for the rehearsal.
set -euo pipefail
KEY="${1:?private key file required}"
NAS=/mnt/nas-hermes
AGEF="${2:-$(ls -1 $NAS/assistant-*.db.age 2>/dev/null | tail -1)}"
[ -n "$AGEF" ] || { echo "FAIL: no assistant-*.db.age on NAS"; exit 1; }
TMP="$(mktemp -d)"; trap "rm -rf \"$TMP\"" EXIT
age -d -i "$KEY" -o "$TMP/restored.db" "$AGEF"
cd "$(dirname "$0")/.."
python3 - "$TMP/restored.db" <<"PY"
import sys
from fam import maint
ok, info = maint.verify_backup(sys.argv[1])
print(("PASS" if ok else "FAIL"), info)
sys.exit(0 if ok else 1)
PY
echo "rehearsed $AGEF"
