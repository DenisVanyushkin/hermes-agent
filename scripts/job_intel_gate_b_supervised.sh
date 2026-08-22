#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_path="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"
readonly runtime_source="$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)"
readonly artifact_root="$(cd -- "$runtime_source/.." && pwd -P)"
readonly runtime_python="$artifact_root/python-runtime/venv/bin/python"
readonly module="job_intel.product_search.gate_b_evidence_runner_v1"

[[ "$artifact_root" == /var/lib/job-intel-gate-b-artifacts/* ]] || {
  echo "supervised Gate B artifact is outside the approved artifact root" >&2
  exit 66
}
[[ "${artifact_root##*/}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "supervised Gate B artifact is not content-addressed" >&2
  exit 66
}
[[ -x "$runtime_python" && ! -L "$runtime_python" ]] || {
  echo "supervised Gate B artifact Python is unavailable" >&2
  exit 66
}
readonly protected_paths=(
  "/home/hermes/.hermes/state.db"
  "/home/hermes/.hermes/job_intel/job_intel.sqlite3"
  "/home/hermes/.hermes/job_intel/job_intel.sqlite3-wal"
  "/home/hermes/.hermes/job_intel/job_intel.sqlite3-shm"
  "/home/hermes/.cache"
  "/var/lib/browser-desktop/profiles"
)
[[ "${#protected_paths[@]}" -eq 6 ]] || {
  echo "supervised Gate B protected path set is incomplete" >&2
  exit 66
}
namespace_properties=()
for protected_path in "${protected_paths[@]}"; do
  namespace_properties+=(--property="InaccessiblePaths=-${protected_path}")
done
readonly namespace_properties
[[ "$#" -ge 1 ]] || {
  echo "usage: job_intel_gate_b_supervised.sh {init-run|run-supervised} ..." >&2
  exit 64
}

exec sudo -n systemd-run --wait --pipe --uid=hermes \
  --working-directory="$runtime_source" \
  --setenv=PYTHONNOUSERSITE=1 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  --setenv=PYTHONHOME="$artifact_root/python-runtime/venv" \
  --setenv=LD_LIBRARY_PATH="$artifact_root/python-runtime/venv/lib" \
  --setenv=PYTHONPATH="$runtime_source" \
  --setenv=GATE_B_SMOKE_ISOLATION_PROBE="${GATE_B_SMOKE_ISOLATION_PROBE:-}" \
  "${namespace_properties[@]}" \
  -- "$runtime_python" -m "$module" "$@"
