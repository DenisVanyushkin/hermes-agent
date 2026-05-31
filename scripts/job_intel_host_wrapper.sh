#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${JOB_INTEL_ENV_FILE:-/etc/job-intel/job-intel.env}"
: "${JOB_INTEL_SERVICE_USER:=hermes}"
export JOB_INTEL_SERVICE_USER
source "$script_dir/job_intel_service_user.sh"
job_intel_require_service_user

fail() {
  printf 'job-intel-host-wrapper: %s\n' "$*" >&2
  exit 1
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
  local candidate
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if [[ -d "$candidate/job_intel" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  fail "unable to resolve job-intel workdir"
}

resolve_python() {
  local workdir="$1"
  local candidates=(
    "${JOB_INTEL_BROWSER_PYTHON:-}"
    "${JOB_INTEL_PYTHON:-}"
    "/var/lib/browser-desktop/playwright-venv/bin/python"
    "$workdir/venv/bin/python"
    "$workdir/.venv/bin/python"
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
  fail "unable to resolve python interpreter"
}

maybe_source_env_file() {
  if [[ -r "$env_file" ]]; then
    # shellcheck disable=SC1090
    set -a
    . "$env_file"
    set +a
  fi
}

ensure_dir() {
  local path="$1"
  mkdir -p "$path"
}

ensure_writable_dir() {
  local path="$1"
  ensure_dir "$path"
  [[ -w "$path" ]] || fail "directory not writable: $path"
}

write_success_marker() {
  local marker="$1"
  local stamp
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\n' "$stamp" > "$marker"
}

should_skip_enrichment() {
  local marker="$1"
  local min_days="${JOB_INTEL_ENRICHMENT_MIN_INTERVAL_DAYS:-14}"
  [[ -f "$marker" ]] || return 1
  local last_stamp last_epoch now_epoch age_days
  last_stamp="$(<"$marker")"
  last_epoch="$(date -u -d "$last_stamp" +%s 2>/dev/null || true)"
  [[ -n "$last_epoch" ]] || return 1
  now_epoch="$(date -u +%s)"
  age_days=$(( (now_epoch - last_epoch) / 86400 ))
  (( age_days < min_days ))
}

command="${1:-}"
shift || true
[[ -n "$command" ]] || fail "usage: $0 <daily|alert|health|enrichment|market|strategic|doctor|browser-health|weekly-kpi|metrics-exporter> [args...]"

maybe_source_env_file
export HERMES_HOME="${HERMES_HOME:-/home/hermes/.hermes}"
workdir="$(resolve_workdir)"
state_dir="${JOB_INTEL_STATE_DIR:-/var/lib/job-intel/state}"
db_path="${JOB_INTEL_DB_PATH:-$state_dir/job_intel.sqlite3}"
browser_profile_dir="${JOB_INTEL_BROWSER_PROFILE_DIR:-/var/lib/browser-desktop/profiles}"

export JOB_INTEL_RUNTIME_USER="${JOB_INTEL_RUNTIME_USER:-$(id -un)}"
export JOB_INTEL_ENVIRONMENT="${JOB_INTEL_ENVIRONMENT:-host-managed}"

# Tag runs so production KPI ignores manual/smoke/backfill executions.
# systemd sets INVOCATION_ID; local/manual invocations typically do not.
if [[ -z "${JOB_INTEL_RUN_TYPE:-}" ]]; then
  if [[ -n "${INVOCATION_ID:-}" ]]; then
    export JOB_INTEL_RUN_TYPE="production"
  else
    export JOB_INTEL_RUN_TYPE="manual"
  fi
fi
export JOB_INTEL_WORKDIR="$workdir"
export JOB_INTEL_STATE_DIR="$state_dir"
export JOB_INTEL_DB_PATH="$db_path"
export JOB_INTEL_BROWSER_PROFILE_DIR="$browser_profile_dir"
export JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN="${JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN:-$browser_profile_dir/linkedin}"
export JOB_INTEL_BROWSER_PROFILE_DIR_HH="${JOB_INTEL_BROWSER_PROFILE_DIR_HH:-$browser_profile_dir/hh}"
export JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER="${JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER:-$browser_profile_dir/company-career}"
export JOB_INTEL_BROWSER_PYTHON="${JOB_INTEL_BROWSER_PYTHON:-/var/lib/browser-desktop/playwright-venv/bin/python}"
export JOB_INTEL_SCRIPTS_DIR="${JOB_INTEL_SCRIPTS_DIR:-$script_dir}"
export JOB_INTEL_BROWSER_RUNTIME_DIR="${JOB_INTEL_BROWSER_RUNTIME_DIR:-/var/lib/browser-desktop}"
export BROWSER_DESKTOP_BASE_DIR="${BROWSER_DESKTOP_BASE_DIR:-$JOB_INTEL_BROWSER_RUNTIME_DIR}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$JOB_INTEL_BROWSER_RUNTIME_DIR/.cache}"
export PYTHONPATH="$workdir${PYTHONPATH:+:$PYTHONPATH}"
export JOB_INTEL_LOG_DIR="${JOB_INTEL_LOG_DIR:-/var/log/job-intel}"

expected_commit="${JOB_INTEL_EXPECTED_GIT_COMMIT:-}"
actual_commit="$(git -C "$workdir" rev-parse HEAD)"
export JOB_INTEL_ACTUAL_GIT_COMMIT="$actual_commit"
if [[ -n "$expected_commit" ]]; then
  export JOB_INTEL_EXPECTED_GIT_COMMIT="$expected_commit"
  if [[ "$actual_commit" != "$expected_commit" ]]; then
    printf 'job-intel-host-wrapper: warning: git commit mismatch: expected %s got %s
' "$expected_commit" "$actual_commit" >&2
  fi
fi

[[ -d "$workdir/job_intel" ]] || fail "job_intel package missing from workdir: $workdir"
ensure_writable_dir "$state_dir"
ensure_writable_dir "$JOB_INTEL_LOG_DIR"
ensure_dir "$(dirname -- "$db_path")"
if [[ "$command" != "metrics-exporter" ]]; then
  [[ -d "$browser_profile_dir" ]] || fail "browser profile base missing: $browser_profile_dir"
  [[ -d "$JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN" ]] || fail "LinkedIn profile dir missing: $JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN"
  [[ -d "$JOB_INTEL_BROWSER_PROFILE_DIR_HH" ]] || fail "HH profile dir missing: $JOB_INTEL_BROWSER_PROFILE_DIR_HH"
fi

python_bin="$(resolve_python "$workdir")"
cd "$workdir"

case "$command" in
  bootstrap|daily|alert|health|market|strategic|doctor|browser-health|weekly-kpi|metrics-exporter)
    ;;
  enrichment)
    enrichment_marker="$state_dir/job_intel_enrichment.last_success"
    if should_skip_enrichment "$enrichment_marker"; then
      exit 0
    fi
    ;;
  *)
    fail "unknown job-intel command: $command"
    ;;
esac

if "$python_bin" -m job_intel "$command" "$@"; then
  if [[ "$command" == "enrichment" ]]; then
    write_success_marker "$enrichment_marker"
  fi
else
  exit $?
fi
