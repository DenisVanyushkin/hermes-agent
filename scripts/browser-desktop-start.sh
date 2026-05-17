#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_SCRIPT="${SCRIPT_DIR}/browser-desktop-bootstrap.sh"

if [[ ! -x "${BOOTSTRAP_SCRIPT}" ]]; then
  BOOTSTRAP_SCRIPT="/home/hermes/.hermes/hermes-agent/scripts/browser-desktop-bootstrap.sh"
fi

if [[ ! -x "${BOOTSTRAP_SCRIPT}" ]]; then
  echo "Missing or non-executable bootstrap script: ${BOOTSTRAP_SCRIPT}" >&2
  exit 1
fi

PROFILE="${1:-linkedin}"
URL="${2:-}"

case "$PROFILE" in
  linkedin)
    URL="${URL:-https://www.linkedin.com/}"
    ;;
  hh)
    URL="${URL:-https://hh.ru/}"
    ;;
  *)
    if [[ -z "$URL" ]]; then
      echo "Usage: $0 {linkedin|hh|custom-profile} [url]" >&2
      echo "Examples:" >&2
      echo "  $0 linkedin" >&2
      echo "  $0 hh" >&2
      echo "  $0 custom-profile https://example.com/" >&2
      exit 1
    fi
    ;;
esac

if [[ "${EUID}" -eq 0 ]]; then
  exec bash "${BOOTSTRAP_SCRIPT}" --profile "$PROFILE" --url "$URL"
else
  exec sudo bash "${BOOTSTRAP_SCRIPT}" --profile "$PROFILE" --url "$URL"
fi
