#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ensure_playwright="${script_dir}/browser-desktop-ensure-playwright.sh"
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
