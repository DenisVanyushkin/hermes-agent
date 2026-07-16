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

resolve_python() {
  local workdir="$1"
  local candidates=(
    "${JOB_INTEL_BROWSER_PYTHON:-}"
    "${JOB_INTEL_PYTHON:-}"
    "$workdir/venv/bin/python"
    "$workdir/.venv/bin/python"
    "${BROWSER_DESKTOP_BASE_DIR:-/var/lib/browser-desktop}/playwright-venv/bin/python"
    "python3"
    "python"
  )
  local candidate python_path
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if [[ "$candidate" = */* ]]; then
      [[ -x "$candidate" ]] || continue
      python_path="$candidate"
    else
      python_path="$(command -v "$candidate" 2>/dev/null || true)"
      [[ -n "$python_path" ]] || continue
    fi
    printf '%s\n' "$python_path"
    return 0
  done
  return 1
}

workdir="$(resolve_workdir)"
if [[ -x "$ensure_playwright" ]]; then
  if browser_python="$("$ensure_playwright")"; then
    export JOB_INTEL_BROWSER_PYTHON="$browser_python"
  else
    printf 'job-intel-browser-health: Playwright runtime repair/check failed; continuing with diagnostics\n' >&2
  fi
else
  printf 'job-intel-browser-health: helper missing or not executable: %s\n' "$ensure_playwright" >&2
fi
python_bin="$(resolve_python "$workdir")"
cd "$workdir"
export JOB_INTEL_WORKDIR="$workdir"
if [[ -n "${JOB_INTEL_BROWSER_PYTHON:-}" ]]; then
  browser_base_dir="$(cd -- "$(dirname -- "${JOB_INTEL_BROWSER_PYTHON}")/../.." && pwd)"
else
  browser_base_dir="${BROWSER_DESKTOP_BASE_DIR:-/var/lib/browser-desktop}"
fi
export BROWSER_DESKTOP_BASE_DIR="${BROWSER_DESKTOP_BASE_DIR:-$browser_base_dir}"
export HOME="${HOME:-$browser_base_dir}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$browser_base_dir/.cache}"
exec "$python_bin" -m job_intel browser-health
