#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
env_template="$script_dir/env/job-intel.env.example"
systemd_dir="${JOB_INTEL_SYSTEMD_DIR:-/etc/systemd/system}"
env_file="${JOB_INTEL_ENV_FILE:-/etc/job-intel/job-intel.env}"
service_user="${JOB_INTEL_SERVICE_USER:-hermes}"
enable_now=0
force=0
dry_run=0

usage() {
  cat <<'EOF'
Usage: install_job_intel_host_runtime.sh [options]

  --repo-root PATH          Checkout root to deploy (default: repo containing this script)
  --service-user USER       System user that runs the timer units (default: hermes)
  --env-file PATH           Host env file path (default: /etc/job-intel/job-intel.env)
  --systemd-dir PATH        Systemd unit directory (default: /etc/systemd/system)
  --enable-now              Enable timers after installation
  --force                   Overwrite an existing env file with the template
  --dry-run                 Print intended actions without writing
  -h, --help                Show this help
EOF
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

fail() {
  printf 'job-intel-install: %s\n' "$*" >&2
  disable_timers_on_failure
  exit 1
}

render_template() {
  local template="$1"
  local destination="$2"
  local content tmp
  content="$(<"$template")"
  content="${content//__JOB_INTEL_REPO_ROOT__/$repo_root}"
  content="${content//__JOB_INTEL_ENV_FILE__/$env_file}"
  content="${content//__JOB_INTEL_SERVICE_USER__/$service_user}"
  content="${content//__JOB_INTEL_SERVICE_GROUP__/$service_group}"
  if (( dry_run )); then
    printf 'would install %s -> %s\n' "$template" "$destination"
    return 0
  fi
  tmp="$(mktemp)"
  printf '%s\n' "$content" >"$tmp"
  install -D -m 0644 "$tmp" "$destination"
  rm -f "$tmp"
}

disable_timers_on_failure() {
  local units=(
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
  local unit
  for unit in "${units[@]}"; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
  done
  for unit in "${services[@]}"; do
    systemctl stop "$unit" >/dev/null 2>&1 || true
  done
}

verify_installed_contract() {
  JOB_INTEL_DISABLE_ON_VERIFY_FAILURE=1 "$script_dir/verify_job_intel_host_runtime.sh" \
    --repo-root "$repo_root" \
    --env-file "$env_file" \
    --systemd-dir "$systemd_dir"
}

while (($#)); do
  case "$1" in
    --repo-root)
      repo_root="${2:?missing value for --repo-root}"
      shift 2
      ;;
    --service-user)
      service_user="${2:?missing value for --service-user}"
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
    --enable-now)
      enable_now=1
      shift
      ;;
    --force)
      force=1
      shift
      ;;
    --dry-run)
      dry_run=1
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
require_safe_name "service user" "$service_user"

[[ -d "$repo_root" ]] || fail "repo root not found: $repo_root"
[[ -d "$repo_root/job_intel" ]] || fail "job_intel package missing from repo root: $repo_root"
[[ -x "$repo_root/scripts/job_intel_host_wrapper.sh" ]] || fail "missing host wrapper: $repo_root/scripts/job_intel_host_wrapper.sh"
[[ -x "$script_dir/verify_job_intel_host_runtime.sh" ]] || fail "missing verifier: $script_dir/verify_job_intel_host_runtime.sh"

service_group="$(id -gn "$service_user" 2>/dev/null || true)"
[[ -n "$service_group" ]] || fail "unable to determine primary group for service user: $service_user"
if ! getent passwd "$service_user" >/dev/null 2>&1; then
  fail "configured service user does not exist: $service_user"
fi
if ! getent group "$service_group" >/dev/null 2>&1; then
  fail "configured service group does not exist: $service_group"
fi

if (( dry_run )); then
  printf 'would ensure directories: %s %s %s %s %s %s\n' \
    /etc/job-intel /var/lib/job-intel/state /var/log/job-intel \
    /var/lib/browser-desktop/profiles/linkedin /var/lib/browser-desktop/profiles/hh /var/lib/browser-desktop/profiles/company-career
  printf 'would install env file: %s\n' "$env_file"
  printf 'would render systemd units into: %s\n' "$systemd_dir"
  if (( enable_now )); then
    printf 'would enable timers after verification: job-intel-daily.timer job-intel-alert.timer job-intel-health.timer job-intel-enrichment.timer job-intel-market.timer job-intel-strategic.timer\n'
  fi
  exit 0
fi

[[ $EUID -eq 0 ]] || fail "run as root so the installer can write /etc/systemd and /etc/job-intel"

browser_profile_base="/var/lib/browser-desktop/profiles"
state_dir="/var/lib/job-intel/state"
log_dir="/var/log/job-intel"
mkdir -p /etc/job-intel "$state_dir" "$log_dir" "$browser_profile_base/linkedin" "$browser_profile_base/hh" "$browser_profile_base/company-career"

expected_commit="$(git -C "$repo_root" rev-parse HEAD)"
if [[ -z "$expected_commit" ]]; then
  fail "unable to determine expected git commit from repo root: $repo_root"
fi

if [[ -e "$env_file" && $force -eq 0 ]]; then
  chmod 0600 "$env_file" || true
else
  env_tmp="$(mktemp)"
  cat >"$env_tmp" <<EOF
JOB_INTEL_SERVICE_USER=$service_user
JOB_INTEL_SERVICE_GROUP=$service_group
JOB_INTEL_ENVIRONMENT=production
JOB_INTEL_WORKDIR=$repo_root
JOB_INTEL_STATE_DIR=$state_dir
JOB_INTEL_DB_PATH=$state_dir/job_intel.sqlite3
JOB_INTEL_BROWSER_RUNTIME_DIR=/var/lib/browser-desktop
JOB_INTEL_BROWSER_PROFILE_DIR=$browser_profile_base
JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN=$browser_profile_base/linkedin
JOB_INTEL_BROWSER_PROFILE_DIR_HH=$browser_profile_base/hh
JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER=$browser_profile_base/company-career
JOB_INTEL_SCRIPTS_DIR=$repo_root/scripts
JOB_INTEL_EXPECTED_GIT_COMMIT=$expected_commit
JOB_INTEL_TARGET_COMPANY_BROWSER=0
JOB_INTEL_TARGET_HTTP_TIMEOUT_SECONDS=8
JOB_INTEL_ENRICHMENT_MIN_INTERVAL_DAYS=14
JOB_INTEL_SLACK_WEBHOOK_URL=
EOF
  install -D -m 0600 "$env_tmp" "$env_file"
  rm -f "$env_tmp"
fi

service_templates=(
  job-intel-daily.service
  job-intel-alert.service
  job-intel-health.service
  job-intel-enrichment.service
  job-intel-market.service
  job-intel-strategic.service
)
timer_templates=(
  job-intel-daily.timer
  job-intel-alert.timer
  job-intel-health.timer
  job-intel-enrichment.timer
  job-intel-market.timer
  job-intel-strategic.timer
)
for template in "${service_templates[@]}" "${timer_templates[@]}"; do
  render_template "$script_dir/systemd/$template" "$systemd_dir/$template"
done

systemctl daemon-reload

timers=(
  job-intel-daily.timer
  job-intel-alert.timer
  job-intel-health.timer
  job-intel-enrichment.timer
  job-intel-market.timer
  job-intel-strategic.timer
)
if ! verify_installed_contract; then
  fail "verification failed"
fi

if (( enable_now )); then
  if ! systemctl enable --now "${timers[@]}"; then
    fail "failed to enable timers"
  fi
fi

printf 'job-intel deployment installed successfully\n'
