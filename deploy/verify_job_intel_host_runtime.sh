#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
env_file="${JOB_INTEL_ENV_FILE:-/etc/job-intel/job-intel.env}"
systemd_dir="${JOB_INTEL_SYSTEMD_DIR:-/etc/systemd/system}"
disable_on_failure="${JOB_INTEL_DISABLE_ON_VERIFY_FAILURE:-0}"

usage() {
  cat <<'EOF'
Usage: verify_job_intel_host_runtime.sh [options]

  --repo-root PATH   Checkout root to verify (default: repo containing this script)
  --env-file PATH    Deployed env file to inspect (default: /etc/job-intel/job-intel.env)
  --systemd-dir PATH Systemd unit directory (default: /etc/systemd/system)
  --disable-on-failure Enable fail-closed cleanup when verification fails
  -h, --help         Show this help
EOF
}

fail() {
  printf 'job-intel-verify: %s\n' "$*" >&2
  cleanup_on_failure
  exit 1
}

note() {
  printf 'job-intel-verify: %s\n' "$*" >&2
}

cleanup_on_failure() {
  if [[ "$disable_on_failure" != "1" ]]; then
    return 0
  fi
  local timers=(
    job-intel-daily.timer
    job-intel-alert.timer
    job-intel-health.timer
    job-intel-enrichment.timer
    job-intel-market.timer
    job-intel-strategic.timer
  )
  local services=(
    job-intel-daily.service
    job-intel-alert.service
    job-intel-health.service
    job-intel-enrichment.service
    job-intel-market.service
    job-intel-strategic.service
  )
  if command -v systemctl >/dev/null 2>&1; then
    local unit
    for unit in "${timers[@]}"; do
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
    done
    for unit in "${services[@]}"; do
      systemctl stop "$unit" >/dev/null 2>&1 || true
    done
  fi
}

verify_mode() {
  local path="$1"
  local mode
  mode="$(stat -c '%a' "$path" 2>/dev/null || true)"
  [[ "$mode" == "600" ]] || fail "env file mode must be 0600: $path (actual: ${mode:-missing})"
}

verify_contains() {
  local path="$1"
  local needle="$2"
  grep -Fq "$needle" "$path" || fail "missing expected content in $path: $needle"
}

get_env_value() {
  local file="$1"
  local key="$2"
  local default_value="${3-}"
  if grep -Fq "${key}=" "$file"; then
    grep -E "^${key}=" "$file" | tail -n1 | cut -d= -f2-
  else
    printf '%s' "$default_value"
  fi
}

require_safe_path() {
  local label="$1"
  local value="$2"
  [[ "$value" =~ ^[A-Za-z0-9._/@:+-]+$ ]] || fail "$label contains unsupported characters: $value"
}

require_safe_name() {
  local label="$1"
  local value="$2"
  [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]] || fail "$label contains unsupported characters: $value"
}

while (($#)); do
  case "$1" in
    --repo-root)
      repo_root="${2:?missing value for --repo-root}"
      shift 2
      ;;
    --env-file)
      env_file="${2:?missing value for --env-file}"
      shift 2
      ;;
    --systemd-dir)
      systemd_dir="${2:?missing value for --systemd-dir}"
      shift 2
      ;;
    --disable-on-failure)
      disable_on_failure=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

require_safe_path "repo root" "$repo_root"
require_safe_path "env file" "$env_file"
require_safe_path "systemd dir" "$systemd_dir"

issues=()
add_issue() {
  issues+=("$1")
}

service_user=""
service_group=""
workdir="$repo_root"
if [[ -f "$env_file" ]]; then
  verify_mode "$env_file"
  service_user="$(get_env_value "$env_file" JOB_INTEL_SERVICE_USER hermes)"
  service_group="$(get_env_value "$env_file" JOB_INTEL_SERVICE_GROUP "")"
  workdir="$(get_env_value "$env_file" JOB_INTEL_WORKDIR "$repo_root")"
fi

if [[ -n "$service_user" ]]; then
  require_safe_name "service user" "$service_user"
fi
if [[ -n "$service_group" ]]; then
  require_safe_name "service group" "$service_group"
fi

[[ -d "$repo_root" ]] || add_issue "repo root missing: $repo_root"
[[ -d "$repo_root/job_intel" ]] || add_issue "job_intel package missing from repo root: $repo_root"
[[ -x "$repo_root/scripts/job_intel_host_wrapper.sh" ]] || add_issue "missing host wrapper: $repo_root/scripts/job_intel_host_wrapper.sh"
[[ -x "$script_dir/install_job_intel_host_runtime.sh" ]] || add_issue "missing installer: $script_dir/install_job_intel_host_runtime.sh"
[[ -f "$script_dir/env/job-intel.env.example" ]] || add_issue "missing env example: $script_dir/env/job-intel.env.example"

required_files=(
  job-intel-daily.service
  job-intel-daily.timer
  job-intel-alert.service
  job-intel-alert.timer
  job-intel-health.service
  job-intel-health.timer
  job-intel-enrichment.service
  job-intel-enrichment.timer
  job-intel-market.service
  job-intel-market.timer
  job-intel-strategic.service
  job-intel-strategic.timer
)
for file in "${required_files[@]}"; do
  [[ -f "$script_dir/systemd/$file" ]] || add_issue "missing deploy template: $script_dir/systemd/$file"
done

if [[ -e "$env_file" ]]; then
  verify_mode "$env_file"
  verify_contains "$env_file" "JOB_INTEL_SERVICE_USER="
  verify_contains "$env_file" "JOB_INTEL_SERVICE_GROUP="
  verify_contains "$env_file" "JOB_INTEL_WORKDIR="
  verify_contains "$env_file" "JOB_INTEL_DB_PATH="
  verify_contains "$env_file" "JOB_INTEL_EXPECTED_GIT_COMMIT="
  verify_contains "$env_file" "JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER="
  verify_contains "$env_file" "JOB_INTEL_SLACK_WEBHOOK_URL="
else
  add_issue "env file missing: $env_file"
fi

browser_base_dir="/var/lib/browser-desktop/profiles"
for profile in linkedin hh; do
  [[ -d "$browser_base_dir/$profile" ]] || add_issue "browser profile directory missing: $browser_base_dir/$profile"
done
if [[ -d "$browser_base_dir/company-career" ]]; then
  note "browser profile directory present: $browser_base_dir/company-career"
fi

service_specs=(
  job-intel-daily:daily
  job-intel-alert:alert
  job-intel-health:health
  job-intel-enrichment:enrichment
  job-intel-market:market
  job-intel-strategic:strategic
)
for spec in "${service_specs[@]}"; do
  service_name="${spec%%:*}"
  command_name="${spec##*:}"
  service_path="$systemd_dir/$service_name.service"
  [[ -f "$service_path" ]] || add_issue "installed service unit missing: $service_path"
  if [[ -f "$service_path" ]]; then
    verify_contains "$service_path" "EnvironmentFile=$env_file"
    verify_contains "$service_path" "ExecStart=/usr/bin/env bash $repo_root/scripts/job_intel_host_wrapper.sh $command_name"
    verify_contains "$service_path" "User=$service_user"
    if [[ -n "$service_group" ]]; then
      verify_contains "$service_path" "Group=$service_group"
    fi
    verify_contains "$service_path" "WorkingDirectory=$workdir"
  fi
done

installed_timers=(
  job-intel-daily.timer
  job-intel-alert.timer
  job-intel-health.timer
  job-intel-enrichment.timer
  job-intel-market.timer
  job-intel-strategic.timer
)
for timer in "${installed_timers[@]}"; do
  timer_path="$systemd_dir/$timer"
  [[ -f "$timer_path" ]] || add_issue "installed timer missing: $timer_path"
  if [[ -f "$timer_path" ]]; then
    service_name="${timer%.timer}.service"
    verify_contains "$timer_path" "Unit=$service_name"
  fi
done

expected_commit="${JOB_INTEL_EXPECTED_GIT_COMMIT:-}"
if [[ -n "$expected_commit" ]]; then
  actual_commit="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
  [[ "$expected_commit" == "$actual_commit" ]] || add_issue "git commit mismatch: expected $expected_commit got ${actual_commit:-n/a}"
fi

if (( ${#issues[@]} )); then
  for issue in "${issues[@]}"; do
    note "$issue"
  done
  cleanup_on_failure
  exit 1
fi

note "host runtime verification passed"
