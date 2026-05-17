#!/usr/bin/env bash
set -euo pipefail

# Lightweight persistent browser desktop bootstrap for VPS use.
#
# Installs and starts:
#   - XFCE desktop
#   - Chromium
#   - Xvfb
#   - x11vnc with password auth
#   - noVNC/websockify
#
# It binds services to localhost only. Use SSH port forwarding to connect.
#
# Usage:
#   sudo bash scripts/browser-desktop-bootstrap.sh
#   sudo bash scripts/browser-desktop-bootstrap.sh --profile linkedin --url https://www.linkedin.com/
#   sudo bash scripts/browser-desktop-bootstrap.sh --profile hh --url https://hh.ru/
#
# Connect from your workstation:
#   ssh -L 6080:127.0.0.1:6080 -L 9222:127.0.0.1:9222 user@vps
#   open http://127.0.0.1:6080/vnc.html
#
# After authenticating once, Chromium sessions persist under /var/lib/browser-desktop/profiles/<profile>.

PROFILE="linkedin"
URL="https://www.linkedin.com/"
DISPLAY_NUM="99"
VNC_PORT="5901"
NOVNC_PORT="6080"
CDP_PORT="9222"
BASE_DIR="/var/lib/browser-desktop"
USER_NAME="browser"
USER_HOME="${BASE_DIR}"
VNC_DIR="${BASE_DIR}/.vnc"
LOG_DIR="${BASE_DIR}/logs"
RUNTIME_DIR=""
CHROMIUM_PKG="chromium"

validate_profile_name() {
  if [[ ! "${PROFILE}" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Invalid profile name: ${PROFILE}. Use only letters, numbers, underscore, and dash." >&2
    exit 1
  fi
}

select_ports() {
  case "${PROFILE}" in
    linkedin)
      DISPLAY_NUM="99"
      VNC_PORT="5901"
      NOVNC_PORT="6080"
      CDP_PORT="9222"
      ;;
    hh)
      DISPLAY_NUM="100"
      VNC_PORT="5902"
      NOVNC_PORT="6081"
      CDP_PORT="9223"
      ;;
    *)
      local offset
      offset="$(( ($(printf '%s' "${PROFILE}" | cksum | awk '{print $1}') % 50) + 1 ))"
      DISPLAY_NUM="$((100 + offset))"
      VNC_PORT="$((5900 + offset))"
      NOVNC_PORT="$((6080 + offset))"
      CDP_PORT="$((9222 + offset))"
      ;;
  esac
}

usage() {
  cat <<'EOF'
Usage: sudo bash scripts/browser-desktop-bootstrap.sh [options]

Options:
  --profile NAME   Chromium profile name (default: linkedin)
  --url URL        Start Chromium at URL (default: LinkedIn)
  -h, --help       Show this help

Examples:
  sudo bash scripts/browser-desktop-bootstrap.sh --profile linkedin --url https://www.linkedin.com/
  sudo bash scripts/browser-desktop-bootstrap.sh --profile hh --url https://hh.ru/
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for --profile" >&2
        exit 1
      fi
      PROFILE="$2"
      shift 2
      ;;
    --url)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for --url" >&2
        exit 1
      fi
      URL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

validate_profile_name
select_ports

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo/root so it can install packages and configure the service user." >&2
  exit 1
fi

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This step requires root." >&2
    exit 1
  fi
}

ensure_pkg() {
  local missing=()
  for pkg in "$@"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      missing+=("$pkg")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    need_root
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y "${missing[@]}"
  fi
}

ensure_user() {
  if ! id -u "${USER_NAME}" >/dev/null 2>&1; then
    need_root
    useradd -m -d "${USER_HOME}" -s /bin/bash "${USER_NAME}"
  fi
}

ensure_base_dir_safety() {
  if [[ -L "${BASE_DIR}" ]]; then
    echo "${BASE_DIR} is a symlink; refusing to use it." >&2
    exit 1
  fi
  if [[ -e "${BASE_DIR}" && ! -f "${BASE_DIR}/.browser-desktop-managed" ]]; then
    echo "${BASE_DIR} already exists and is not marked as browser-desktop-managed. Refusing to modify it." >&2
    exit 1
  fi
}

mkdirs() {
  need_root
  install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 0750 \
    "${BASE_DIR}" \
    "${BASE_DIR}/profiles" \
    "${LOG_DIR}" \
    "${BASE_DIR}/downloads" \
    "${BASE_DIR}/Desktop"
  RUNTIME_DIR="/run/user/$(id -u "${USER_NAME}")"
  install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 0700 "${RUNTIME_DIR}"
  install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 0700 \
    "${VNC_DIR}" \
    "${BASE_DIR}/.config" \
    "${BASE_DIR}/.cache" \
    "${BASE_DIR}/profiles/${PROFILE}"
  touch \
    "${BASE_DIR}/.browser-desktop-managed" \
    "${LOG_DIR}/xvfb.log" \
    "${LOG_DIR}/xfce.log" \
    "${LOG_DIR}/x11vnc.log" \
    "${LOG_DIR}/websockify.log" \
    "${LOG_DIR}/chromium-${PROFILE}.log"
  chown "${USER_NAME}:${USER_NAME}" \
    "${BASE_DIR}/.browser-desktop-managed" \
    "${LOG_DIR}/xvfb.log" \
    "${LOG_DIR}/xfce.log" \
    "${LOG_DIR}/x11vnc.log" \
    "${LOG_DIR}/websockify.log" \
    "${LOG_DIR}/chromium-${PROFILE}.log"
  chmod 0640 \
    "${BASE_DIR}/.browser-desktop-managed" \
    "${LOG_DIR}/xvfb.log" \
    "${LOG_DIR}/xfce.log" \
    "${LOG_DIR}/x11vnc.log" \
    "${LOG_DIR}/websockify.log" \
    "${LOG_DIR}/chromium-${PROFILE}.log"
}

is_snap_path() {
  local path="$1"
  local resolved
  resolved="$(readlink -f "$path" 2>/dev/null || printf '%s' "$path")"
  [[ "${resolved}" == /snap/* || "${resolved}" == /var/lib/snapd/* ]]
}

resolve_non_snap_chromium_bin() {
  local candidate resolved
  for candidate in /usr/bin/chromium /usr/bin/chromium-browser; do
    if [[ -x "${candidate}" ]]; then
      if ! is_snap_path "${candidate}"; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    fi
  done

  for candidate in chromium chromium-browser; do
    if candidate="$(command -v "${candidate}" 2>/dev/null || true)" && [[ -n "${candidate}" ]]; then
      if [[ -x "${candidate}" ]]; then
        if ! is_snap_path "${candidate}"; then
          printf '%s\n' "${candidate}"
          return 0
        fi
      fi
    fi
  done

  return 1
}

create_desktop_shortcuts() {
  need_root
  local desktop_dir="${BASE_DIR}/Desktop"
  local linked_in="${desktop_dir}/Chromium LinkedIn.desktop"
  local hh="${desktop_dir}/Chromium HH.desktop"
  local chromium_bin
  chromium_bin="$(resolve_non_snap_chromium_bin || true)"
  if [[ -z "${chromium_bin}" ]]; then
    echo "No non-snap Chromium binary was found while creating desktop shortcuts." >&2
    echo "If the VPS only has /snap/bin/chromium, remove it and rerun:" >&2
    echo "  sudo snap remove chromium" >&2
    exit 1
  fi

  cat > "${linked_in}" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Chromium LinkedIn
Comment=Open LinkedIn in the persistent Chromium profile
Exec=${chromium_bin} --user-data-dir=${BASE_DIR}/profiles/linkedin --profile-directory=Default --new-window https://www.linkedin.com/
Icon=chromium
Terminal=false
Categories=Network;WebBrowser;
EOF

  cat > "${hh}" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Chromium HH
Comment=Open HH in the persistent Chromium profile
Exec=${chromium_bin} --user-data-dir=${BASE_DIR}/profiles/hh --profile-directory=Default --new-window https://hh.ru/
Icon=chromium
Terminal=false
Categories=Network;WebBrowser;
EOF

  chown "${USER_NAME}:${USER_NAME}" "${linked_in}" "${hh}"
  chmod 0755 "${linked_in}" "${hh}"
}

install_chromium_pkg() {
  if dpkg -s chromium >/dev/null 2>&1; then
    CHROMIUM_PKG="chromium"
    return 0
  fi
  if dpkg -s chromium-browser >/dev/null 2>&1; then
    CHROMIUM_PKG="chromium-browser"
    return 0
  fi

  export DEBIAN_FRONTEND=noninteractive
  if apt-get install -y chromium >/dev/null 2>&1; then
    CHROMIUM_PKG="chromium"
    return 0
  fi
  if apt-get install -y chromium-browser >/dev/null 2>&1; then
    CHROMIUM_PKG="chromium-browser"
    return 0
  fi

  echo "Could not install Chromium (tried chromium and chromium-browser)." >&2
  exit 1
}

get_password() {
  local pw_file="${VNC_DIR}/password.txt"
  local auth_file="${VNC_DIR}/passwd"
  if [[ -f "${pw_file}" && -f "${auth_file}" ]]; then
    cat "${pw_file}"
    return 0
  fi

  local password
  password="$(openssl rand -hex 12)"
  printf '%s\n' "${password}" > "${pw_file}"
  chmod 0600 "${pw_file}"
  chown "${USER_NAME}:${USER_NAME}" "${pw_file}"
  runuser -u "${USER_NAME}" -- x11vnc -storepasswd "${password}" "${auth_file}" >/dev/null
  chmod 0600 "${auth_file}"
  chown "${USER_NAME}:${USER_NAME}" "${auth_file}"
  printf '%s\n' "${password}"
}

start_as_browser() {
  local log_file="$1"
  shift
  nohup runuser -u "${USER_NAME}" -- env \
    "DISPLAY=:${DISPLAY_NUM}" \
    "HOME=${USER_HOME}" \
    "USER=${USER_NAME}" \
    "LOGNAME=${USER_NAME}" \
    "XDG_RUNTIME_DIR=${RUNTIME_DIR}" \
    "XDG_CONFIG_HOME=${BASE_DIR}/.config" \
    "XDG_CACHE_HOME=${BASE_DIR}/.cache" \
    "$@" >>"${log_file}" 2>&1 &
}

port_listening() {
  local port="$1"
  ss -ltn | awk -v p=":${port}" '$4 ~ p"$" {found=1} END {exit !found}'
}

process_matches() {
  local pattern="$1"
  pgrep -u "${USER_NAME}" -f "${pattern}" >/dev/null 2>&1
}

ensure_display_free() {
  if [[ -e "/tmp/.X${DISPLAY_NUM}-lock" || -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ]]; then
    if ! process_matches "Xvfb :${DISPLAY_NUM}"; then
      echo "Display :${DISPLAY_NUM} already appears to be in use. Stop the existing X server or choose another display number." >&2
      exit 1
    fi
  fi
}

ensure_port_free_or_owned() {
  local port="$1"
  local pattern="$2"
  local label="$3"
  if port_listening "${port}" && ! process_matches "${pattern}"; then
    echo "${label} port ${port} is already in use by another process." >&2
    exit 1
  fi
}

wait_for_display() {
  local tries=100
  while (( tries-- > 0 )); do
    if runuser -u "${USER_NAME}" -- env "DISPLAY=:${DISPLAY_NUM}" xdpyinfo >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  echo "Timed out waiting for Xvfb display :${DISPLAY_NUM}" >&2
  exit 1
}

validate_profile_name
apt-get update >/dev/null
install_chromium_pkg
ensure_pkg "${CHROMIUM_PKG}" xfce4 dbus-x11 x11vnc novnc websockify xvfb x11-utils xauth openssl iproute2 curl procps dbus-user-session
ensure_base_dir_safety
ensure_user
mkdirs
create_desktop_shortcuts
PASSWORD="$(get_password)"

ensure_display_free
if ! process_matches "Xvfb :${DISPLAY_NUM}"; then
  start_as_browser "${LOG_DIR}/xvfb.log" Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -nolisten tcp
fi
wait_for_display

if ! process_matches "dbus-run-session -- startxfce4"; then
  start_as_browser "${LOG_DIR}/xfce.log" dbus-run-session -- startxfce4
fi

ensure_port_free_or_owned "${VNC_PORT}" "x11vnc .* -display :${DISPLAY_NUM} .* -rfbport ${VNC_PORT}" "VNC"
if ! process_matches "x11vnc .* -display :${DISPLAY_NUM}"; then
  start_as_browser "${LOG_DIR}/x11vnc.log" x11vnc \
    -display ":${DISPLAY_NUM}" \
    -localhost \
    -rfbauth "${VNC_DIR}/passwd" \
    -rfbport "${VNC_PORT}" \
    -forever \
    -shared \
    -xkb \
    -o "${LOG_DIR}/x11vnc.log"
fi

ensure_port_free_or_owned "${NOVNC_PORT}" "websockify .*127.0.0.1:${NOVNC_PORT} .*127.0.0.1:${VNC_PORT}" "noVNC"
if ! process_matches "websockify .*127.0.0.1:${NOVNC_PORT} .*127.0.0.1:${VNC_PORT}"; then
  start_as_browser "${LOG_DIR}/websockify.log" websockify --web=/usr/share/novnc "127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}"
fi

CHROMIUM_BIN="$(resolve_non_snap_chromium_bin || true)"
if [[ -z "${CHROMIUM_BIN}" ]]; then
  echo "No non-snap Chromium binary was found." >&2
  echo "If the VPS only has /snap/bin/chromium, remove it and rerun:" >&2
  echo "  sudo snap remove chromium" >&2
  echo "Then rerun this bootstrap script so it can use /usr/bin/chromium." >&2
  exit 1
fi

ensure_port_free_or_owned "${CDP_PORT}" "remote-debugging-port=${CDP_PORT}" "Chromium CDP"
if ! process_matches "remote-debugging-port=${CDP_PORT}"; then
  start_as_browser "${LOG_DIR}/chromium-${PROFILE}.log" dbus-run-session -- "${CHROMIUM_BIN}" \
    --user-data-dir="${BASE_DIR}/profiles/${PROFILE}" \
    --profile-directory=Default \
    --no-first-run \
    --disable-dev-shm-usage \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="${CDP_PORT}" \
    --new-window "${URL}"
fi

sleep 2
if ! curl -fsS "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
  echo "Chromium CDP endpoint is not responding yet; check ${LOG_DIR}/chromium-${PROFILE}.log" >&2
fi

cat <<EOF
Browser desktop bootstrap complete.

Connect securely via SSH tunnel:
  ssh -L ${NOVNC_PORT}:127.0.0.1:${NOVNC_PORT} -L ${CDP_PORT}:127.0.0.1:${CDP_PORT} user@YOUR_VPS

Open noVNC:
  http://127.0.0.1:${NOVNC_PORT}/vnc.html

Use this VNC password:
  ${PASSWORD}

Chrome DevTools / Playwright CDP:
  http://127.0.0.1:${CDP_PORT}/json/version

Persistent profile directory:
  ${BASE_DIR}/profiles/${PROFILE}

Stop this desktop:
  sudo bash scripts/browser-desktop-stop.sh ${PROFILE}

Stop all managed desktops:
  sudo bash scripts/browser-desktop-stop.sh

Logs:
  ${LOG_DIR}
EOF
