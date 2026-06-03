#!/usr/bin/env bash
set -euo pipefail

SYSTEM_BASE_DIR="${BROWSER_DESKTOP_BASE_DIR:-/var/lib/browser-desktop}"
BASE_DIR="$SYSTEM_BASE_DIR"
USER_NAME="${BROWSER_DESKTOP_USER:-browser}"
PLAYWRIGHT_VENV="${BASE_DIR}/playwright-venv"
CACHE_DIR="${BASE_DIR}/.cache"
MANAGED_MARKER="${BASE_DIR}/.browser-desktop-managed"

refresh_paths() {
  PLAYWRIGHT_VENV="${BASE_DIR}/playwright-venv"
  CACHE_DIR="${BASE_DIR}/.cache"
  MANAGED_MARKER="${BASE_DIR}/.browser-desktop-managed"
}

log() { printf 'browser-desktop-ensure-playwright: %s\n' "$*" >&2; }

python_has_playwright() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
from importlib.util import find_spec
raise SystemExit(0 if find_spec('playwright.sync_api') is not None else 1)
PY
}

python_has_job_intel_deps() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
from importlib.util import find_spec
required = ('pydantic', 'requests', 'yaml')
raise SystemExit(0 if all(find_spec(name) is not None for name in required) else 1)
PY
}

playwright_chromium_smoke() {
  local python_bin="$1"
  local runner=("$python_bin")
  if [[ "${EUID}" -eq 0 ]] && id -u "${USER_NAME}" >/dev/null 2>&1; then
    runner=(runuser -u "${USER_NAME}" -- env HOME="${BASE_DIR}" XDG_CACHE_HOME="${CACHE_DIR}" "$python_bin")
  else
    runner=(env HOME="${BASE_DIR}" XDG_CACHE_HOME="${CACHE_DIR}" "$python_bin")
  fi
  "${runner[@]}" - <<'PY' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('data:text/html,<title>job-intel-playwright-smoke</title>')
    assert page.title() == 'job-intel-playwright-smoke'
    browser.close()
PY
}

runtime_healthy() {
  [[ -x "${PLAYWRIGHT_VENV}/bin/python" ]] \
    && python_has_playwright "${PLAYWRIGHT_VENV}/bin/python" \
    && python_has_job_intel_deps "${PLAYWRIGHT_VENV}/bin/python" \
    && playwright_chromium_smoke "${PLAYWRIGHT_VENV}/bin/python"
}

select_repair_base() {
  if runtime_healthy; then
    return 0
  fi
  if [[ "${EUID}" -ne 0 && -z "${BROWSER_DESKTOP_BASE_DIR:-}" ]]; then
    BASE_DIR="${HOME}/.hermes/browser-desktop"
    USER_NAME="$(id -un)"
    refresh_paths
  fi
}

ensure_pkg_root() {
  local missing=()
  for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    log "installing missing packages: ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update >/dev/null
    apt-get install -y "${missing[@]}" >/dev/null
  fi
}

ensure_base_dir() {
  if [[ -L "${BASE_DIR}" ]]; then
    echo "${BASE_DIR} is a symlink; refusing to repair Playwright runtime." >&2
    exit 1
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    if ! id -u "${USER_NAME}" >/dev/null 2>&1; then
      log "creating service user ${USER_NAME}"
      useradd -m -d "${BASE_DIR}" -s /bin/bash "${USER_NAME}"
    fi
    install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 0750 "${BASE_DIR}" "${BASE_DIR}/profiles" "${BASE_DIR}/logs"
    install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 0700 "${CACHE_DIR}" "${BASE_DIR}/profiles/linkedin" "${BASE_DIR}/profiles/hh"
    if id -u hermes >/dev/null 2>&1; then
      setfacl -m u:hermes:r-x,m:r-x "${BASE_DIR}" "${BASE_DIR}/profiles"
      setfacl -m u:hermes:rwx,m:rwx "${BASE_DIR}/profiles/linkedin" "${BASE_DIR}/profiles/hh"
      setfacl -d -m u:hermes:rwx,m:rwx "${BASE_DIR}/profiles/linkedin" "${BASE_DIR}/profiles/hh"
    fi
    : > "${MANAGED_MARKER}"
    chown "${USER_NAME}:${USER_NAME}" "${MANAGED_MARKER}"
    chmod 0640 "${MANAGED_MARKER}"
  else
    install -d -m 0750 "${BASE_DIR}" "${BASE_DIR}/profiles" "${BASE_DIR}/logs"
    install -d -m 0700 "${CACHE_DIR}" "${BASE_DIR}/profiles/linkedin" "${BASE_DIR}/profiles/hh"
    if command -v setfacl >/dev/null 2>&1 && id -u hermes >/dev/null 2>&1; then
      setfacl -m u:hermes:r-x,m:r-x "${BASE_DIR}" "${BASE_DIR}/profiles" || true
      setfacl -m u:hermes:rwx,m:rwx "${BASE_DIR}/profiles/linkedin" "${BASE_DIR}/profiles/hh" || true
      setfacl -d -m u:hermes:rwx,m:rwx "${BASE_DIR}/profiles/linkedin" "${BASE_DIR}/profiles/hh" || true
    fi
    : > "${MANAGED_MARKER}"
    chmod 0640 "${MANAGED_MARKER}"
  fi
}

venv_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    echo "python3 is required to create ${PLAYWRIGHT_VENV}" >&2
    exit 1
  fi
}

run_in_runtime_env() {
  if [[ "${EUID}" -eq 0 ]] && id -u "${USER_NAME}" >/dev/null 2>&1; then
    runuser -u "${USER_NAME}" -- env HOME="${BASE_DIR}" XDG_CACHE_HOME="${CACHE_DIR}" "$@"
  else
    env HOME="${BASE_DIR}" XDG_CACHE_HOME="${CACHE_DIR}" "$@"
  fi
}

install_playwright() {
  rm -rf "${PLAYWRIGHT_VENV}"
  "$(venv_python)" -m venv "${PLAYWRIGHT_VENV}"
  if [[ "${EUID}" -eq 0 ]] && id -u "${USER_NAME}" >/dev/null 2>&1; then
    chown -R "${USER_NAME}:${USER_NAME}" "${PLAYWRIGHT_VENV}"
  fi
  run_in_runtime_env "${PLAYWRIGHT_VENV}/bin/pip" install --upgrade \
    pip setuptools wheel playwright \
    pydantic==2.12.5 requests==2.33.0 pyyaml==6.0.3 >/dev/null
  run_in_runtime_env "${PLAYWRIGHT_VENV}/bin/python" -m playwright install chromium >/dev/null
  if [[ "${EUID}" -eq 0 ]]; then
    "${PLAYWRIGHT_VENV}/bin/python" -m playwright install-deps chromium >/dev/null
  fi
}

repair_playwright_venv() {
  select_repair_base
  if runtime_healthy; then
    printf '%s\n' "${PLAYWRIGHT_VENV}/bin/python"
    return 0
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    ensure_pkg_root python3 python3-venv python3-pip curl ca-certificates
  fi
  ensure_base_dir
  log "repairing ${PLAYWRIGHT_VENV}"
  install_playwright
  if ! runtime_healthy; then
    echo "Playwright runtime repair completed, but import/dependency/Chromium smoke checks still fail for ${PLAYWRIGHT_VENV}/bin/python." >&2
    exit 1
  fi
  printf '%s\n' "${PLAYWRIGHT_VENV}/bin/python"
}

repair_playwright_venv
