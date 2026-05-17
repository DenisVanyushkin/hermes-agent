#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/var/lib/browser-desktop"
USER_NAME="browser"
PROFILE="all"

usage() {
  cat <<'EOF'
Usage: sudo bash scripts/browser-desktop-stop.sh [profile|options]

Stops the browser desktop processes for one profile or all managed profiles.

Arguments:
  PROFILE          Stop one profile by name (linkedin, hh, or custom profile)

Options:
  --profile NAME   Stop only one profile (same as positional PROFILE)
  --all            Stop all browser desktop processes (default)
  -h, --help       Show this help

Examples:
  sudo bash scripts/browser-desktop-stop.sh linkedin
  sudo bash scripts/browser-desktop-stop.sh hh
  sudo bash scripts/browser-desktop-stop.sh --profile linkedin
  sudo bash scripts/browser-desktop-stop.sh --profile hh
  sudo bash scripts/browser-desktop-stop.sh --all
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    linkedin|hh)
      PROFILE="$1"
      shift
      ;;
    --profile)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for --profile" >&2
        exit 1
      fi
      PROFILE="$2"
      shift 2
      ;;
    --all)
      PROFILE="all"
      shift
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

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo/root so it can stop browser desktop processes." >&2
  exit 1
fi

validate_profile_name() {
  local profile="$1"
  if [[ "$profile" != "all" && ! "$profile" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Invalid profile name: ${profile}. Use only letters, numbers, underscore, and dash." >&2
    exit 1
  fi
}

select_ports() {
  local profile="$1"
  case "$profile" in
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
      offset="$(( ($(printf '%s' "$profile" | cksum | awk '{print $1}') % 50) + 1 ))"
      DISPLAY_NUM="$((100 + offset))"
      VNC_PORT="$((5900 + offset))"
      NOVNC_PORT="$((6080 + offset))"
      CDP_PORT="$((9222 + offset))"
      ;;
  esac
}

pids_for_pattern() {
  local pattern="$1"
  pgrep -u "$USER_NAME" -f "$pattern" 2>/dev/null || true
}

kill_pids() {
  local signal="$1"
  shift
  local pids=("$@")
  if [[ ${#pids[@]} -eq 0 ]]; then
    return 0
  fi
  kill "-${signal}" "${pids[@]}" 2>/dev/null || true
}

kill_pattern() {
  local pattern="$1"
  local label="$2"
  local -a pids=()
  mapfile -t pids < <(pids_for_pattern "$pattern")
  if [[ ${#pids[@]} -eq 0 ]]; then
    return 0
  fi

  echo "Stopping ${label}: ${pids[*]}"
  kill_pids TERM "${pids[@]}"
  local tries=20
  while (( tries-- > 0 )); do
    if ! pgrep -u "$USER_NAME" -f "$pattern" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  kill_pids KILL "${pids[@]}"
}

stop_profile() {
  local profile="$1"
  select_ports "$profile"

  kill_pattern "--user-data-dir=${BASE_DIR}/profiles/${profile}" "Chromium (${profile})"
  kill_pattern "websockify .*127.0.0.1:${NOVNC_PORT} .*127.0.0.1:${VNC_PORT}" "noVNC (${profile})"
  kill_pattern "x11vnc .* -display :${DISPLAY_NUM} .* -rfbport ${VNC_PORT}" "x11vnc (${profile})"
  kill_pattern "DISPLAY=:${DISPLAY_NUM} .*dbus-run-session -- startxfce4" "XFCE (${profile})"
  kill_pattern "Xvfb :${DISPLAY_NUM}" "Xvfb (${profile})"

  rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
}

if [[ "$PROFILE" == "all" ]]; then
  echo "Stopping all browser desktop processes..."
  kill_pattern "remote-debugging-port=[0-9]+" "Chromium"
  kill_pattern "websockify .*127.0.0.1:[0-9]+ .*127.0.0.1:[0-9]+" "noVNC"
  kill_pattern "x11vnc .* -display :[0-9]+ .* -rfbport [0-9]+" "x11vnc"
  kill_pattern "DISPLAY=:[0-9]+ .*dbus-run-session -- startxfce4" "XFCE"
  kill_pattern "Xvfb :[0-9]+" "Xvfb"
  rm -f /tmp/.X*-lock /tmp/.X11-unix/X* 2>/dev/null || true
else
  validate_profile_name "$PROFILE"
  stop_profile "$PROFILE"
fi

if pgrep -u "$USER_NAME" >/dev/null 2>&1; then
  echo "Remaining browser-user processes:" >&2
  pgrep -u "$USER_NAME" -af || true
fi

echo "Browser desktop stopped."
