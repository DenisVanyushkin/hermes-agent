#!/usr/bin/env bash
# Counts smart-approval verdicts from the agent log into node_exporter textfile
# format. Counts are "since the last agent.log rotation", not lifetime — a
# rotation resets them to 0, which Prometheus handles as a normal counter reset.
set -euo pipefail
LOG=/home/hermes/.hermes/logs/agent.log
OUT=/var/lib/hermes-metrics/smart_approval.prom
# Same directory as $OUT so the final mv is a same-filesystem atomic rename —
# node_exporter must never read a half-written file.
TMP=$(mktemp "${OUT}.XXXXXX")
trap 'rm -f "$TMP"' EXIT
{
  echo '# HELP hermes_smart_approval_verdicts_total Smart approval verdicts seen in agent.log since its last rotation'
  echo '# TYPE hermes_smart_approval_verdicts_total counter'
  for v in APPROVE DENY ESCALATE; do
    c=$(grep -c "approval: smart ${v}" "$LOG" || true)
    echo "hermes_smart_approval_verdicts_total{verdict=\"${v,,}\"} ${c}"
  done
  echo '# HELP hermes_smart_approval_reviewer_failures_total Auxiliary reviewer calls that failed and fell back to ESCALATE (blocks)'
  echo '# TYPE hermes_smart_approval_reviewer_failures_total counter'
  c=$(grep -c "Smart approval: reviewer call failed" "$LOG" || true)
  echo "hermes_smart_approval_reviewer_failures_total ${c}"
} > "$TMP"
chmod 644 "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
