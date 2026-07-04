#!/usr/bin/env bash
# Refresh sha256 hashes in the career-facts SoT manifest after editing
# career_facts.json or preferences.yaml. Run as the hermes user.
set -euo pipefail

DIR="${HERMES_HOME:-$HOME/.hermes}/job_intel/career_facts"
MANIFEST="$DIR/manifest.yaml"

[ -f "$MANIFEST" ] || { echo "manifest not found: $MANIFEST" >&2; exit 1; }

for f in career_facts.json preferences.yaml; do
  [ -f "$DIR/$f" ] || { echo "missing file: $DIR/$f" >&2; exit 1; }
  hash=$(sha256sum "$DIR/$f" | awk '{print $1}')
  python3 - "$MANIFEST" "$f" "$hash" <<'PY'
import sys
manifest, name, digest = sys.argv[1:4]
lines = open(manifest).read().splitlines(keepends=True)
out, in_entry = [], False
for line in lines:
    if "path:" in line:
        in_entry = name in line
    if in_entry and line.strip().startswith("sha256:"):
        line = line.split("sha256:")[0] + f"sha256: {digest}\n"
        in_entry = False
    out.append(line)
open(manifest, "w").writelines(out)
PY
  echo "updated $f -> $hash"
done

python3 - "$MANIFEST" <<'PY'
import datetime, sys
manifest = sys.argv[1]
today = datetime.date.today().isoformat()
lines = open(manifest).read().splitlines(keepends=True)
out = [
    (line.split("updated_at:")[0] + f'updated_at: "{today}"\n') if line.strip().startswith("updated_at:") else line
    for line in lines
]
open(manifest, "w").writelines(out)
PY
echo "manifest refreshed: $MANIFEST"
