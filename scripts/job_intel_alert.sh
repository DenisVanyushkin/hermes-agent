#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${JOB_INTEL_SERVICE_USER:=pn}"
export JOB_INTEL_SERVICE_USER
source "$script_dir/job_intel_service_user.sh"
job_intel_require_service_user

resolve_workdir() {
  local candidates=(
    "${JOB_INTEL_WORKDIR:-}"
    "/home/hermes/.hermes/hermes-agent"
    "/workspace/live-hermes"
    "$PWD"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if [[ -d "$candidate/job_intel" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_db_path() {
  local workdir="$1"
  local candidates=(
    "${JOB_INTEL_DB_PATH:-}"
    "$HOME/.hermes/job_intel/job_intel.sqlite3"
    "$workdir/.hermes/job_intel/job_intel.sqlite3"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    printf '%s\n' "$candidate"
    return 0
  done
  return 1
}

python_has_playwright() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
from importlib.util import find_spec
raise SystemExit(0 if find_spec('playwright.sync_api') is not None else 1)
PY
}

resolve_python() {
  local workdir="$1"
  local candidates=(
    "${JOB_INTEL_BROWSER_PYTHON:-}"
    "/var/lib/browser-desktop/playwright-venv/bin/python"
    "${JOB_INTEL_PYTHON:-}"
    "$workdir/venv/bin/python"
    "$workdir/.venv/bin/python"
    "python3"
    "python"
  )
  local candidate python_path
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if [[ "$candidate" = */* ]]; then
      if [[ -x "$candidate" ]]; then
        python_path="$candidate"
      else
        continue
      fi
    else
      python_path="$(command -v "$candidate" 2>/dev/null || true)"
      [[ -n "$python_path" ]] || continue
    fi
    if python_has_playwright "$python_path"; then
      printf '%s\n' "$python_path"
      return 0
    fi
  done
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if [[ "$candidate" = */* ]]; then
      if [[ -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    else
      if command -v "$candidate" >/dev/null 2>&1; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
workdir="$(resolve_workdir)"
db_path="$(resolve_db_path "$workdir")"
helper="$script_dir/browser-desktop-ensure-playwright.sh"
if [[ -x "$helper" ]]; then
  export JOB_INTEL_BROWSER_PYTHON="$($helper)"
else
  echo "Missing browser Playwright repair helper: $helper" >&2
  exit 1
fi
browser_base_dir="$(cd -- "$(dirname -- "$JOB_INTEL_BROWSER_PYTHON")/../.." && pwd)"
python_bin="$(resolve_python "$workdir")"
cd "$workdir"
export JOB_INTEL_WORKDIR="$workdir"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$browser_base_dir/.cache}"
export JOB_INTEL_DB_PATH="$db_path"
export JOB_INTEL_ENVIRONMENT="${JOB_INTEL_ENVIRONMENT:-production}"
export JOB_INTEL_SCRIPTS_DIR="${JOB_INTEL_SCRIPTS_DIR:-$script_dir}"
export JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN="${JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN:-$browser_base_dir/profiles/linkedin}"
export JOB_INTEL_BROWSER_PROFILE_DIR_HH="${JOB_INTEL_BROWSER_PROFILE_DIR_HH:-$browser_base_dir/profiles/hh}"
exec "$python_bin" -m job_intel alert
