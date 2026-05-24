#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${JOB_INTEL_SERVICE_USER:=hermes}"
export JOB_INTEL_SERVICE_USER
source "$script_dir/job_intel_service_user.sh"
job_intel_require_service_user

resolve_workdir() {
  local repo_root
  local candidates=(
    "${JOB_INTEL_WORKDIR:-}"
    "$PWD"
  )
  if repo_root="$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || true)"; then
    [[ -n "$repo_root" ]] && candidates+=("$repo_root")
  fi
  if [[ -d "$script_dir/../job_intel" ]]; then
    candidates+=("$(cd -- "$script_dir/.." && pwd)")
  fi
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
  local candidate
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

workdir="$(resolve_workdir)"
db_path="$(resolve_db_path "$workdir")"
python_bin="$(resolve_python "$workdir")"
cd "$workdir"
export JOB_INTEL_WORKDIR="$workdir"
export JOB_INTEL_DB_PATH="$db_path"
export JOB_INTEL_ENVIRONMENT="${JOB_INTEL_ENVIRONMENT:-production}"
export JOB_INTEL_SCRIPTS_DIR="${JOB_INTEL_SCRIPTS_DIR:-$script_dir}"
export JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN="${JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN:-/var/lib/browser-desktop/profiles/linkedin}"
export JOB_INTEL_BROWSER_PROFILE_DIR_HH="${JOB_INTEL_BROWSER_PROFILE_DIR_HH:-/var/lib/browser-desktop/profiles/hh}"
exec "$python_bin" -m job_intel market
