#!/usr/bin/env bash
set -euo pipefail

repo="${1:?usage: export_job_intel_gate_b_benchmark.sh REPO COMMIT DEST PYTHON}"
commit="${2:?commit required}"
destination="${3:?destination required}"
python_source="${4:?python executable required}"

[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
  echo "candidate commit must be a full SHA-1" >&2
  exit 64
}
[[ ! -e "$destination" ]] || {
  echo "runtime export destination already exists" >&2
  exit 64
}
[[ -x "$python_source" ]] || {
  echo "gateway Python executable is unavailable" >&2
  exit 64
}
git -C "$repo" cat-file -e "$commit^{commit}"

gateway_venv="$(cd "$(dirname "$python_source")/.." && pwd -P)"
exec "$python_source" -m job_intel.product_search.gate_b_runtime_v1 \
  build-artifact "$repo" "$commit" "$gateway_venv" "$destination" "$python_source"
