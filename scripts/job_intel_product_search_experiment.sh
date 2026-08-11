#!/usr/bin/env bash
set -euo pipefail

command="${1:?usage: job_intel_product_search_experiment.sh preflight|run MANIFEST}"
manifest="${2:?manifest path required}"

for name in SLACK_BOT_TOKEN SLACK_APP_TOKEN JOB_INTEL_SLACK_WEBHOOK_URL; do
  if [[ -n "${!name:-}" ]]; then
    echo "Slack credentials are forbidden in Product Search experiment: $name" >&2
    exit 64
  fi
done

python3 -m job_intel.product_search.acquisition_probe validate-manifest "$manifest"
[[ "$command" == "preflight" ]] && exit 0
[[ "$command" == "run" ]] || { echo "unknown command: $command" >&2; exit 64; }

lock_path="$(python3 - "$manifest" <<'PY'
from pathlib import Path
import sys, yaml
print(Path(yaml.safe_load(Path(sys.argv[1]).read_text())["paths"]["locks"]) / "scheduled-run.lock")
PY
)"
mkdir -p "$(dirname "$lock_path")"
exec 9>"$lock_path"
if ! flock -n 9; then
  echo "overlap attempt skipped: lock busy" >&2
  exit 75
fi

exec "$(dirname "$0")/job_intel_product_search_probe.sh" "$manifest"
