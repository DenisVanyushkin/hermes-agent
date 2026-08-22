#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly artifact_parent="/var/lib/job-intel-gate-b-artifacts"
readonly unit_destination="/etc/systemd/system/job-intel-gate-b-benchmark@.service"

[[ "$#" -eq 2 && "$EUID" -eq 0 ]] || {
  echo "Gate B artifact installation requires root, ARTIFACT_ROOT, and ARTIFACT_TREE_SHA256" >&2
  exit 77
}

readonly source_input="${1}"
[[ -d "$source_input" && ! -L "$source_input" ]] || {
  echo "artifact source is unavailable" >&2
  exit 66
}
readonly source_root="$(realpath -- "$source_input")"
# This is the external provenance anchor: never derive it from the artifact.
readonly artifact_tree_sha256="${2}"
readonly destination="$artifact_parent/$artifact_tree_sha256"
readonly runtime_source="$source_root/runtime"
readonly unit_source="$runtime_source/deploy/systemd/experiments/job-intel-gate-b-benchmark@.service"
readonly runtime_manifest="$source_root/runtime-manifest.json"
readonly runtime_manifest_sha256="$source_root/runtime-manifest.sha256"

[[ "$artifact_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "artifact tree SHA-256 must be a lowercase 64-hex digest" >&2
  exit 64
}
[[ -d "$runtime_source" && ! -L "$runtime_source" ]] || {
  echo "artifact runtime source is unavailable" >&2
  exit 66
}
[[ -f "$unit_source" && ! -L "$unit_source" ]] || {
  echo "artifact unit is unavailable" >&2
  exit 66
}
[[ -f "$runtime_manifest" && ! -L "$runtime_manifest" ]] || {
  echo "artifact runtime manifest is unavailable" >&2
  exit 66
}
[[ -f "$runtime_manifest_sha256" && ! -L "$runtime_manifest_sha256" ]] || {
  echo "artifact runtime manifest checksum is unavailable" >&2
  exit 66
}

# The source hash is diagnostic component identity, not the publication anchor.
readonly artifact_sha256="$(/usr/bin/python3 - "$runtime_manifest" <<'PY'
import json
from pathlib import Path
import re
import sys

payload = json.loads(Path(sys.argv[1]).read_bytes())
value = payload.get("artifact_sha256")
if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
    raise SystemExit("artifact manifest has no valid artifact_sha256")
print(value)
PY
)"

verify_runtime_manifest_hash() {
  /usr/bin/python3 - "$1" <<'PY'
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
manifest = root / "runtime-manifest.json"
checksum = root / "runtime-manifest.sha256"
expected = hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n"
if checksum.read_text(encoding="ascii") != expected:
    raise SystemExit("runtime manifest checksum mismatch")
PY
}

verify_source_artifact_hash() {
  /usr/bin/python3 - "$1" "$2" <<'PY'
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
expected = sys.argv[2]
if not root.is_dir() or root.is_symlink():
    raise SystemExit("artifact runtime directory is unavailable")
digest = hashlib.sha256()
for path in sorted(root.rglob("*")):
    if path.is_symlink():
        raise SystemExit(f"artifact runtime contains symlink: {path}")
    if path.is_file():
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
observed = digest.hexdigest()
if observed != expected:
    raise SystemExit(
        f"artifact runtime hash mismatch: expected {expected}, observed {observed}"
    )
PY
}

verify_artifact_hash() {
  /usr/bin/python3 - "$1" "$2" <<'PY'
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
expected = sys.argv[2]
excluded = {"runtime-manifest.json", "runtime-manifest.sha256"}
if not root.is_dir() or root.is_symlink():
    raise SystemExit("artifact root is unavailable")
digest = hashlib.sha256()
paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
for path in paths:
    reference = path.relative_to(root).as_posix()
    if reference in excluded:
        continue
    reference_bytes = reference.encode("utf-8")
    if path.is_symlink():
        digest.update(b"L\0" + reference_bytes + b"\0")
        digest.update(path.readlink().as_posix().encode("utf-8"))
        digest.update(b"\0")
    elif path.is_dir():
        digest.update(b"D\0" + reference_bytes + b"\0")
    elif path.is_file():
        digest.update(b"F\0" + reference_bytes + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    else:
        raise SystemExit(f"artifact contains unsupported entry: {path}")
observed = digest.hexdigest()
if observed != expected:
    raise SystemExit(
        f"artifact tree hash mismatch: expected {expected}, observed {observed}"
    )
PY
}

verify_source_artifact_hash "$runtime_source" "$artifact_sha256" || exit 66
verify_runtime_manifest_hash "$source_root" || exit 66
verify_artifact_hash "$source_root" "$artifact_tree_sha256" || exit 66
/usr/bin/install -d -o root -g hermes -m 0750 "$artifact_parent"

if [[ -e "$destination" || -L "$destination" ]]; then
  [[ -d "$destination" && ! -L "$destination" ]] || {
    echo "existing artifact path is not a directory" >&2
    exit 66
  }
  verify_runtime_manifest_hash "$destination" || exit 66
  verify_artifact_hash "$destination" "$artifact_tree_sha256" || exit 66
else
  temporary="$artifact_parent/.${artifact_tree_sha256}.install.$$"
  trap 'rm -rf -- "$temporary"' EXIT
  (umask 077; mkdir -- "$temporary"; cp -a --no-preserve=ownership "$source_root/." "$temporary/")
  verify_runtime_manifest_hash "$temporary" || exit 66
  verify_artifact_hash "$temporary" "$artifact_tree_sha256" || exit 66
  chown -R root:hermes "$temporary"
  find "$temporary" -type d -exec chmod u=rwx,g=rx,o= {} +
  find "$temporary" -type f -exec chmod u=rwX,g=rX,o= {} +
  find "$temporary" -type f -perm /111 -exec chmod u=rwx,g=rx,o= {} +
  mv -T -n -- "$temporary" "$destination"
  [[ -d "$destination" ]] || {
    echo "artifact publication raced with another installer" >&2
    exit 66
  }
  verify_artifact_hash "$destination" "$artifact_tree_sha256" || exit 66
  trap - EXIT
fi

/usr/bin/install -o root -g root -m 0644 "$unit_source" "$unit_destination"
/usr/bin/systemctl daemon-reload

[[ "$(/usr/bin/systemctl show "job-intel-gate-b-benchmark@${artifact_tree_sha256}.service" --property=User --value)" == "hermes" ]]
[[ "$(/usr/bin/systemctl show "job-intel-gate-b-benchmark@${artifact_tree_sha256}.service" --property=Group --value)" == "hermes" ]]
[[ "$(/usr/bin/systemctl show "job-intel-gate-b-benchmark@${artifact_tree_sha256}.service" --property=Type --value)" == "oneshot" ]]
[[ "$(/usr/bin/systemctl show "job-intel-gate-b-benchmark@${artifact_tree_sha256}.service" --property=Restart --value)" == "no" ]]
