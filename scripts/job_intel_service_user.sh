#!/usr/bin/env bash
set -euo pipefail

_job_intel_service_user_fail() {
  printf 'job-intel: %s\n' "$*" >&2
  return 1
}

job_intel_require_service_user() {
  local service_user="${JOB_INTEL_SERVICE_USER:-hermes}"
  local current_user="$(id -un 2>/dev/null || true)"
  local service_group

  [[ -n "$service_user" ]] || _job_intel_service_user_fail "JOB_INTEL_SERVICE_USER is not set"
  JOB_INTEL_SERVICE_USER="$service_user"
  export JOB_INTEL_SERVICE_USER

  if ! getent passwd "$service_user" >/dev/null 2>&1; then
    _job_intel_service_user_fail "configured service user does not exist: $service_user"
  fi
  service_group="$(id -gn "$service_user" 2>/dev/null || true)"
  [[ -n "$service_group" ]] || _job_intel_service_user_fail "unable to determine primary group for service user: $service_user"
  if ! getent group "$service_group" >/dev/null 2>&1; then
    _job_intel_service_user_fail "configured service group does not exist: $service_group"
  fi
  [[ -n "$current_user" ]] || _job_intel_service_user_fail "unable to determine current user"
  if [[ "$current_user" != "$service_user" ]]; then
    _job_intel_service_user_fail "must run as service user $service_user (current user: $current_user)"
  fi

  export JOB_INTEL_RUNTIME_USER="$service_user"
  export JOB_INTEL_SERVICE_GROUP="$service_group"
}
