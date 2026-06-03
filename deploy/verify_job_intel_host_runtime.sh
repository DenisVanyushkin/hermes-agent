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

stat_line() {
  local path="$1"
  stat -c '%U:%G %a %n' "$path"
}

verify_access_as_service_user() {
  local path="$1"
  local label="$2"
  local perms command
  perms="$(stat_line "$path" 2>/dev/null || true)"
  [[ -n "$perms" ]] || fail "$label missing: $path"
  note "$label ownership/perms: $perms"
  printf -v command 'test -r %q && test -w %q && test -x %q' "$path" "$path" "$path"
  su -s /bin/bash "$service_user" -c "$command" || fail "$label is not readable+writable+executable by service user '$service_user': $path"
}

resolve_runtime_python() {
  local candidates=(
    "$(get_env_value "$env_file" JOB_INTEL_BROWSER_PYTHON "")"
    "$(get_env_value "$env_file" JOB_INTEL_PYTHON "")"
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

probe_runtime_python() {
  local runtime_python="$1"
  local probe
  probe="$($runtime_python - "$workdir" <<'PY'
import importlib
import json
import sys
from pathlib import Path

workdir = Path(sys.argv[1]).resolve()
required_modules = [
    "job_intel",
    "job_intel.runtime",
    "job_intel.store",
    "job_intel.browser_sourcing",
    "job_intel.cli",
]
module_locations = {}
for name in required_modules:
    module = importlib.import_module(name)
    origin = Path(module.__file__).resolve()
    try:
        origin.relative_to(workdir)
    except ValueError as exc:
        raise SystemExit(f"module outside workdir: {name} -> {origin}") from exc
    module_locations[name] = str(origin)

print(json.dumps({
    "python_executable": sys.executable,
    "python_version": sys.version.split()[0],
    "module_locations": module_locations,
}, ensure_ascii=False))
PY
)"
  note "runtime python: $(printf '%s' "$probe" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["python_executable"] + " " + data["python_version"])')"
  note "runtime python module locations: $(printf '%s' "$probe" | python3 -c 'import json,sys; data=json.load(sys.stdin); print("; ".join(f"{k}={v}" for k,v in sorted(data["module_locations"].items())))')"
}

latest_health_run_json() {
  local runtime_python="$1"
  local db_file="$2"
  local after_id="${3:-0}"
  [[ -f "$db_file" ]] || return 1
  "$runtime_python" - "$db_file" "$after_id" <<'PY'
import json
import sqlite3
import sys

db_file = sys.argv[1]
after_id = int(sys.argv[2])
conn = sqlite3.connect(db_file)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM runs WHERE mode = 'health' AND id > ? ORDER BY id DESC LIMIT 1", (after_id,)).fetchone()
if row is None:
    raise SystemExit(2)
print(json.dumps(dict(row), ensure_ascii=False))
PY
}

extract_run_id() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
print(payload["id"])
PY
}

verify_run_provenance() {
  local payload="$1"
  local workdir_value="$2"
  python3 - "$payload" "$workdir_value" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(sys.argv[1])
workdir = Path(sys.argv[2]).resolve()
provenance_raw = payload.get("provenance_json") or "{}"
provenance = json.loads(provenance_raw)
contract = provenance.get("runtime_contract") or {}
if contract.get("status") != "healthy":
    raise SystemExit(f"runtime contract status is not healthy: {contract.get('status')!r}")
module_locations = provenance.get("imported_module_locations") or {}
required = [
    "job_intel.runtime",
    "job_intel.store",
    "job_intel.browser_sourcing",
    "job_intel.cli",
]
for name in required:
    origin = module_locations.get(name)
    if not origin:
        raise SystemExit(f"missing imported module location: {name}")
    origin_path = Path(origin).resolve()
    try:
        origin_path.relative_to(workdir)
    except ValueError as exc:
        raise SystemExit(f"module outside workdir: {name} -> {origin_path}") from exc
print(json.dumps({"id": payload.get("id"), "status": payload.get("status"), "provenance": provenance}, ensure_ascii=False))
PY
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
    job-intel-metrics-exporter.service
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
  if [[ ! -r "$file" ]]; then
    printf '%s' "$default_value"
    return 0
  fi
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
db_path="/var/lib/job-intel/state/job_intel.sqlite3"
if [[ -f "$env_file" ]]; then
  verify_mode "$env_file"
  service_user="$(get_env_value "$env_file" JOB_INTEL_SERVICE_USER hermes)"
  service_group="$(get_env_value "$env_file" JOB_INTEL_SERVICE_GROUP "")"
  workdir="$(get_env_value "$env_file" JOB_INTEL_WORKDIR "$repo_root")"
  db_path="$(get_env_value "$env_file" JOB_INTEL_DB_PATH "/var/lib/job-intel/state/job_intel.sqlite3")"
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
  job-intel-metrics-exporter.service
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

browser_base_dir="$(get_env_value "$env_file" JOB_INTEL_BROWSER_PROFILE_DIR "/var/lib/browser-desktop/profiles")"
browser_profile_linkedin="$(get_env_value "$env_file" JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN "$browser_base_dir/linkedin")"
browser_profile_hh="$(get_env_value "$env_file" JOB_INTEL_BROWSER_PROFILE_DIR_HH "$browser_base_dir/hh")"
browser_profile_company_career="$(get_env_value "$env_file" JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER "$browser_base_dir/company-career")"
for profile in "$browser_profile_linkedin" "$browser_profile_hh" "$browser_profile_company_career"; do
  [[ -d "$profile" ]] || add_issue "browser profile directory missing: $profile"
done
if [[ -d "$browser_profile_company_career" ]]; then
  note "browser profile directory present: $browser_profile_company_career"
fi

if [[ -n "$service_user" ]]; then
  verify_access_as_service_user "$browser_profile_linkedin" "browser profile directory (linkedin)"
  verify_access_as_service_user "$browser_profile_hh" "browser profile directory (hh)"
  verify_access_as_service_user "$browser_profile_company_career" "browser profile directory (company-career)"
fi

service_specs=(
  job-intel-daily:daily
  job-intel-alert:alert
  job-intel-health:health
  job-intel-enrichment:enrichment
  job-intel-market:market
  job-intel-strategic:strategic
  job-intel-metrics-exporter:metrics-exporter
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

systemd_units=()
for unit in "$systemd_dir"/job-intel-*.service "$systemd_dir"/job-intel-*.timer; do
  [[ -e "$unit" ]] || continue
  systemd_units+=("$unit")
done
if ((${#systemd_units[@]})); then
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "${systemd_units[@]}"
  else
    add_issue "systemd-analyze missing; cannot validate installed units"
  fi
fi

expected_commit="$(get_env_value "$env_file" JOB_INTEL_EXPECTED_GIT_COMMIT "")"
if [[ -n "$expected_commit" ]]; then
  actual_commit="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
  [[ "$expected_commit" == "$actual_commit" ]] || add_issue "git commit mismatch: expected $expected_commit got ${actual_commit:-n/a}"
fi

runtime_python="$(resolve_runtime_python)"
probe_runtime_python "$runtime_python"

before_run_json="$(latest_health_run_json "$runtime_python" "$db_path" 0 2>/dev/null || true)"
before_run_id=0
if [[ -n "$before_run_json" ]]; then
  before_run_id="$(extract_run_id "$before_run_json")"
fi

health_unit="$systemd_dir/job-intel-health.service"
[[ -f "$health_unit" ]] || add_issue "installed service unit missing: $health_unit"
if [[ -f "$health_unit" ]]; then
  note "starting runtime verification service: $health_unit"
  systemctl start --wait job-intel-health.service || fail "failed to start job-intel-health.service"
  systemd_result="$(systemctl show -p Result --value job-intel-health.service 2>/dev/null || true)"
  [[ "$systemd_result" == "success" ]] || fail "job-intel-health.service did not complete successfully (Result=${systemd_result:-n/a})"
  note "job-intel-health.service Result=$systemd_result"
  note "journalctl output for job-intel-health.service:"
  journalctl -u job-intel-health.service --no-pager -o cat -n 200 >&2 || true
  latest_run_payload="$(latest_health_run_json "$runtime_python" "$db_path" "$before_run_id")"
  latest_run_id="$(extract_run_id "$latest_run_payload")"
  [[ "$latest_run_id" -gt "$before_run_id" ]] || fail "no fresh run row appeared in canonical DB (before=$before_run_id after=$latest_run_id)"
  verified_row="$(verify_run_provenance "$latest_run_payload" "$workdir")"
  note "latest canonical run row: $verified_row"
fi

if (( ${#issues[@]} )); then
  for issue in "${issues[@]}"; do
    note "$issue"
  done
  cleanup_on_failure
  exit 1
fi

note "host runtime verification passed"
