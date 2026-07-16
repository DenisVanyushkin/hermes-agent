#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${JOB_INTEL_SERVICE_USER:=hermes}"
export JOB_INTEL_SERVICE_USER
source "$script_dir/job_intel_service_user.sh"
job_intel_require_service_user

_job_intel_is_package() {
  # A directory *named* job_intel is not enough.  $HERMES_HOME/job_intel holds
  # databases, and python treats any such directory as an implicit namespace
  # package (PEP 420), which shadows the real one and makes `-m job_intel` fail
  # with "is a package and cannot be directly executed".  Probe for the
  # executable package marker instead.
  [[ -f "$1/job_intel/__main__.py" ]]
}

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
  candidates+=("${HERMES_HOME:-$HOME/.hermes}/hermes-agent")
  local candidate
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if _job_intel_is_package "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_db_path() {
  # Mirror job_intel_host_wrapper.sh: the live database lives under the state
  # dir.  $HOME/.hermes/job_intel/job_intel.sqlite3 was a stale copy frozen in
  # May 2026 (deleted 2026-07-16) and must never be resolved again -- this
  # function never checked existence, so naming it would make sqlite silently
  # recreate an empty database and operate on nothing.
  local state_dir="${JOB_INTEL_STATE_DIR:-/var/lib/job-intel/state}"
  printf '%s\n' "${JOB_INTEL_DB_PATH:-$state_dir/job_intel.sqlite3}"
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
db_path="$(resolve_db_path)"
python_bin="$(resolve_python "$workdir")"
cd "$workdir"
export JOB_INTEL_WORKDIR="$workdir"
export JOB_INTEL_DB_PATH="$db_path"
export JOB_INTEL_ENVIRONMENT="${JOB_INTEL_ENVIRONMENT:-production}"
export JOB_INTEL_SCRIPTS_DIR="${JOB_INTEL_SCRIPTS_DIR:-$script_dir}"
export JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN="${JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN:-/var/lib/browser-desktop/profiles/linkedin}"
export JOB_INTEL_BROWSER_PROFILE_DIR_HH="${JOB_INTEL_BROWSER_PROFILE_DIR_HH:-/var/lib/browser-desktop/profiles/hh}"
exec "$python_bin" -m job_intel market
