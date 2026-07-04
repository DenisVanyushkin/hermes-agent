#!/usr/bin/env bash
# Entry point for the hermes cron job hermes-fallback-refresh-daily-0230.
# Silent when the free-model selection is unchanged; output goes to Slack.
set -euo pipefail
REPO="/home/hermes/.hermes/hermes-agent"
exec "$REPO/venv/bin/python" "$REPO/scripts/update_openrouter_fallback.py" "$@"
