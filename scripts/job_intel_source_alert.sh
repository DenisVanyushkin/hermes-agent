#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
workdir="${JOB_INTEL_WORKDIR:-$repo_root}"
python_bin="${JOB_INTEL_BROWSER_PYTHON:-/var/lib/browser-desktop/playwright-venv/bin/python}"

if [[ -f "$script_dir/job_intel_service_user.sh" ]]; then
  # The production unit runs this wrapper as hermes; the check prevents an
  # accidental invocation with a different identity from writing state.
  # shellcheck disable=SC1091
  source "$script_dir/job_intel_service_user.sh"
  job_intel_require_service_user
fi

cd -- "$workdir"
export PYTHONPATH="$workdir${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" "$script_dir/job_intel_source_alert.py" "$@"
