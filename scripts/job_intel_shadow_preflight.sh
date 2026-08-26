#!/usr/bin/env bash
# Prove, from inside the unit namespace, that this run cannot deliver.
#
# Run as ExecStartPre so the assertions are made in the exact environment the
# collection will run in. Checking any of this from a shell outside the unit
# proves nothing about the unit: the namespace, not the repository, is what
# decides whether a credential file is reachable.
set -euo pipefail

fail() { echo "shadow preflight: $1" >&2; exit 1; }

[[ "${JOB_INTEL_DELIVERY_DISABLED:-}" == "1" ]] \
  || fail "JOB_INTEL_DELIVERY_DISABLED must be exactly 1, got '${JOB_INTEL_DELIVERY_DISABLED:-<unset>}'"

[[ "${JOB_INTEL_RUN_TYPE:-}" == "shadow" ]] \
  || fail "JOB_INTEL_RUN_TYPE must be shadow, got '${JOB_INTEL_RUN_TYPE:-<unset>}'"

for name in SLACK_BOT_TOKEN SLACK_APP_TOKEN JOB_INTEL_SLACK_WEBHOOK_URL; do
  [[ -z "${!name:-}" ]] || fail "$name must not be set in the shadow environment"
done

# The delivery path can re-read an env file and repopulate credentials, so the
# absence of a variable is not by itself a barrier. These files must be
# unreachable from inside the namespace.
for path in /etc/job-intel/job-intel.env "${HERMES_HOME:-/home/hermes/.hermes}/.env"; do
  if [[ -r "$path" ]]; then
    fail "$path is readable from inside the unit namespace; credential isolation is not in effect"
  fi
done

env_file="${JOB_INTEL_ENV_FILE:-}"
[[ -n "$env_file" && -r "$env_file" ]] \
  || fail "JOB_INTEL_ENV_FILE must point at a readable shadow env file, got '${env_file:-<unset>}'"
if grep -Eq '^(SLACK_BOT_TOKEN|SLACK_APP_TOKEN|SLACK_HOME_CHANNEL|JOB_INTEL_SLACK_WEBHOOK_URL)=' "$env_file"; then
  fail "$env_file declares a delivery credential"
fi

# Code drift: the checkout must carry the kill-switch this unit depends on.
python_bin="${JOB_INTEL_WORKDIR:-/home/hermes/.hermes/hermes-agent}/venv/bin/python"
[[ -x "$python_bin" ]] || fail "interpreter not found at $python_bin"
"$python_bin" - <<'PY' || fail "the checkout does not enforce the delivery kill-switch"
import os, sys
sys.path.insert(0, os.environ.get("JOB_INTEL_WORKDIR", "/home/hermes/.hermes/hermes-agent"))
try:
    from job_intel.runtime import delivery_disabled
except Exception as exc:  # noqa: BLE001 - any import failure is a drift failure
    print(f"import failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if delivery_disabled() else 1)
PY

echo "shadow preflight: delivery is disabled, credentials are unreachable, kill-switch present"
