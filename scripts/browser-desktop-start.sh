#!/usr/bin/env bash
set -euo pipefail

SCRIPT="/home/hermes/.hermes/hermes-agent/scripts/browser-desktop-bootstrap.sh"

if [[ ! -x "$SCRIPT" ]]; then
  echo "Missing or non-executable bootstrap script: $SCRIPT" >&2
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
      echo "Usage: sudo $0 {linkedin|hh|custom-profile} [url]" >&2
      echo "Examples:" >&2
      echo "  sudo $0 linkedin" >&2
      echo "  sudo $0 hh" >&2
      echo "  sudo $0 custom-profile https://example.com/" >&2
      exit 1
    fi
    ;;
esac

if [[ "${EUID}" -eq 0 ]]; then
  exec bash "$SCRIPT" --profile "$PROFILE" --url "$URL"
else
  exec sudo bash "$SCRIPT" --profile "$PROFILE" --url "$URL"
fi
