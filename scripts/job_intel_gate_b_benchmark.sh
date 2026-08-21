#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly runtime_root="/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/immutable-runtime"
readonly runtime_source="$runtime_root/runtime"
readonly runtime_python="$runtime_root/python-runtime/venv/bin/python"
readonly module="job_intel.product_search.gate_b_benchmark_v3"

[[ -d "$runtime_source" && ! -L "$runtime_source" ]] || {
  echo "immutable Gate B runtime source is unavailable" >&2
  exit 66
}
[[ -x "$runtime_python" && ! -L "$runtime_python" ]] || {
  echo "immutable Gate B Python is unavailable" >&2
  exit 66
}

export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime_source"
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN JOB_INTEL_DB_PATH JOB_INTEL_OUTBOX_PATH
unset BROWSER_PROFILE_DIR PLAYWRIGHT_BROWSERS_PATH XDG_CACHE_HOME

case "${1:-}" in
  prepare-output-root)
    [[ "$#" -eq 1 && "$EUID" -eq 0 ]] || {
      echo "output-root preparation requires root and no arguments" >&2
      exit 77
    }
    cd "$runtime_source"
    exec "$runtime_python" -m "$module" prepare-output-root
    ;;
  consume-launch-receipt)
    [[ "$#" -eq 1 && "$EUID" -eq 0 ]] || {
      echo "receipt consumption requires root and no arguments" >&2
      exit 77
    }
    cd "$runtime_source"
    exec "$runtime_python" -m "$module" consume-launch-receipt
    ;;
  run-at-most-once)
    [[ "$#" -eq 1 && "$(id -un)" == "hermes" ]] || {
      echo "benchmark execution requires the hermes service user and no arguments" >&2
      exit 77
    }
    cd "$runtime_source"
    exec "$runtime_python" -m "$module" run-at-most-once
    ;;
  *)
    echo "usage: job_intel_gate_b_benchmark.sh prepare-output-root|consume-launch-receipt|run-at-most-once" >&2
    exit 64
    ;;
esac
