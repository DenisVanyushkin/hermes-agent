#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: install_job_intel_watchdogs.sh --repo-root PATH [--systemd-dir PATH]
       [--backup-dir PATH] [--enable-now] [--dry-run]
EOF
}

repo_root=""
systemd_dir=""
backup_dir="/etc/job-intel/shadow-plan-backups"
enable_now=0
dry_run=0

while (($#)); do
  case "$1" in
    --repo-root) repo_root="${2:?missing value for --repo-root}"; shift 2 ;;
    --systemd-dir) systemd_dir="${2:?missing value for --systemd-dir}"; shift 2 ;;
    --backup-dir) backup_dir="${2:?missing value for --backup-dir}"; shift 2 ;;
    --enable-now) enable_now=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$(id -u)" == 0 ]] || { echo "must run as root" >&2; exit 1; }
repo_root="$(cd -- "${repo_root:?--repo-root is required}" && pwd)"
systemd_dir="${systemd_dir:-$repo_root/deploy/systemd}"

if systemctl is-active --quiet job-intel-shadow-collection.service; then
  echo "refusing to mutate while job-intel-shadow-collection.service is active" >&2
  exit 1
fi

units=(
  job-intel-linkedin-ddns-resolver.service
  job-intel-linkedin-ddns-watchdog.service
  job-intel-linkedin-ddns-watchdog.timer
  job-intel-linkedin-browser-supervisor.service
  job-intel-source-alert.service
  job-intel-source-alert.timer
)
for unit in "${units[@]}"; do
  [[ -f "$systemd_dir/$unit" ]] || { echo "missing unit template: $systemd_dir/$unit" >&2; exit 1; }
done
[[ -f "$repo_root/scripts/job_intel_ddns_watchdog.py" ]] || { echo "missing DDNS script" >&2; exit 1; }
[[ -f "$repo_root/scripts/job_intel_source_alert.py" ]] || { echo "missing source alert script" >&2; exit 1; }
[[ -f "$repo_root/scripts/job_intel_source_alert.sh" ]] || { echo "missing source alert wrapper" >&2; exit 1; }

if ((dry_run)); then
  printf 'dry-run: repo_root=%s backup_dir=%s units=%s\n' "$repo_root" "$backup_dir" "${#units[@]}"
  exit 0
fi

install -d -o root -g root -m 0700 "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
for unit in "${units[@]}"; do
  destination="/etc/systemd/system/$unit"
  if [[ -e "$destination" ]]; then
    backup="$backup_dir/$unit.$timestamp.before-watchdogs"
    cp --no-dereference --preserve=mode,ownership "$destination" "$backup"
    chmod 0600 "$backup"
    printf 'backup %s sha256=%s\n' "$backup" "$(sha256sum "$backup" | awk '{print $1}')"
  else
    printf 'backup none (new file) %s\n' "$destination"
  fi
done

temporary_dir="$(mktemp -d /tmp/job-intel-watchdogs.XXXXXX)"
cleanup() { rm -rf -- "$temporary_dir"; }
trap cleanup EXIT
for unit in "${units[@]}"; do
  sed "s|__JOB_INTEL_REPO_ROOT__|$repo_root|g" "$systemd_dir/$unit" > "$temporary_dir/$unit"
  install -o root -g root -m 0644 "$temporary_dir/$unit" "/etc/systemd/system/$unit"
done

for unit in "${units[@]}"; do
  systemd-analyze verify "/etc/systemd/system/$unit"
done
systemctl daemon-reload
printf 'installed %s units; daemon-reload complete\n' "${#units[@]}"

if ((enable_now)); then
  systemctl enable --now job-intel-linkedin-ddns-watchdog.timer job-intel-source-alert.timer job-intel-linkedin-browser-supervisor.service
  printf 'enabled and started DDNS timer, source-alert timer, and browser supervisor\n'
fi
