#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_path="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"
readonly runtime_source="$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)"
readonly artifact_root="$(cd -- "$runtime_source/.." && pwd -P)"
readonly runtime_python="$artifact_root/python-runtime/venv/bin/python"
readonly module="job_intel.product_search.gate_b_evidence_runner_v1"
readonly artifact_parent="/var/lib/job-intel-gate-b-artifacts"

[[ "$artifact_root" == "$artifact_parent"/* ]] || {
  echo "Gate B artifact is outside the approved artifact root" >&2
  exit 66
}
[[ "${artifact_root##*/}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "Gate B artifact directory is not content-addressed" >&2
  exit 66
}
[[ -d "$artifact_root" && ! -L "$artifact_root" ]] || {
  echo "Gate B artifact root is unavailable" >&2
  exit 66
}
[[ -d "$runtime_source" && ! -L "$runtime_source" ]] || {
  echo "Gate B artifact runtime source is unavailable" >&2
  exit 66
}
[[ -x "$runtime_python" && ! -L "$runtime_python" ]] || {
  echo "Gate B artifact Python is unavailable" >&2
  exit 66
}

export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime_source"
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN JOB_INTEL_DB_PATH JOB_INTEL_OUTBOX_PATH
unset BROWSER_PROFILE_DIR PLAYWRIGHT_BROWSERS_PATH XDG_CACHE_HOME

case "${1:-}" in
  run-description-evidence)
    [[ "$#" -eq 1 && "$(id -un)" == "hermes" ]] || {
      echo "description evidence collection requires the hermes service user and no arguments" >&2
      exit 77
    }
    [[ -n "${STATE_DIRECTORY:-}" && -d "$STATE_DIRECTORY" ]] || {
      echo "systemd StateDirectory is unavailable" >&2
      exit 66
    }
    cd "$runtime_source"
    exec "$runtime_python" -m "$module" run-collection
    ;;
  *)
    echo "usage: job_intel_gate_b_benchmark.sh run-description-evidence" >&2
    exit 64
    ;;
esac
