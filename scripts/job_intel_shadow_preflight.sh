#!/usr/bin/env bash
# Prove, from inside the unit namespace, that this run cannot deliver.
#
# Run as ExecStartPre so every assertion is made in the exact environment the
# collection will run in. Checking any of this from a shell outside the unit
# proves nothing about the unit: the namespace, not the repository, decides
# whether a credential file is reachable.
#
# Every failure exits non-zero, which makes systemd fail the unit and record the
# reason in the journal. A silent shadow run that lost its guarantees would be
# worse than no shadow run.
set -euo pipefail

fail() { echo "shadow preflight FAILED: $1" >&2; exit 1; }

[[ "${JOB_INTEL_DELIVERY_DISABLED:-}" == "1" ]] \
  || fail "JOB_INTEL_DELIVERY_DISABLED must be exactly 1, got '${JOB_INTEL_DELIVERY_DISABLED:-<unset>}'"

[[ "${JOB_INTEL_RUN_TYPE:-}" == "shadow" ]] \
  || fail "JOB_INTEL_RUN_TYPE must be shadow, got '${JOB_INTEL_RUN_TYPE:-<unset>}'"

for name in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_HOME_CHANNEL JOB_INTEL_SLACK_WEBHOOK_URL; do
  [[ -z "${!name:-}" ]] || fail "$name must not be set in the shadow environment"
done

# Bitwarden-backed credential resolution would bypass every file barrier below.
for name in BWS_ACCESS_TOKEN BITWARDEN_ACCESS_TOKEN BW_SESSION; do
  [[ -z "${!name:-}" ]] || fail "$name is set; network-backed credential resolution is reachable"
done

# The delivery path can re-read an env file and repopulate credentials, so the
# absence of a variable is not a barrier. Every store that can supply them must
# be unreachable from inside the namespace.
hermes_home="${HERMES_HOME:-/home/hermes/.hermes}"
# hermes_cli/managed_scope.get_managed_dir() resolves the managed store to
# $HERMES_MANAGED_DIR or /etc/hermes — never to $HERMES_HOME/managed. Guarding
# the wrong path would have looked identical in the journal and protected
# nothing.
managed_dir="${HERMES_MANAGED_DIR:-/etc/hermes}"
[[ -z "${HERMES_MANAGED_DIR:-}" ]] || fail "HERMES_MANAGED_DIR is set; the managed store is redirectable"
for path in \
  /etc/job-intel/job-intel.env \
  "$hermes_home/.env" \
  "$hermes_home/.op.env" \
  "$managed_dir/.env" \
  "$hermes_home/config.yaml" \
  "$hermes_home/auth.json"
do
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

# Strict code pin. Presence of the kill-switch helper is not enough: it says
# nothing about the rest of the checkout, which the resident agent rewrites.
# The pin file is written by hand when the owner authorises a specific commit,
# so an unreviewed code change stops the timer instead of running under it.
# The environment must not be able to move the checkout whose commit we verify:
# JOB_INTEL_WORKDIR and JOB_INTEL_SCRIPTS_DIR are carried from the production
# env file, and a redirected workdir would pin a different tree than the one
# that runs. Both are therefore asserted against the expected canonical paths.
expected_workdir="/home/hermes/.hermes/hermes-agent"
workdir="${JOB_INTEL_WORKDIR:-$expected_workdir}"
[[ "$workdir" == "$expected_workdir" ]] \
  || fail "JOB_INTEL_WORKDIR points at '$workdir', expected '$expected_workdir'"
[[ "${JOB_INTEL_SCRIPTS_DIR:-$expected_workdir/scripts}" == "$expected_workdir/scripts" ]] \
  || fail "JOB_INTEL_SCRIPTS_DIR points outside the pinned checkout"
pin_file="${JOB_INTEL_SHADOW_PIN_FILE:-/etc/job-intel/job-intel-shadow.pin}"
[[ -r "$pin_file" ]] || fail "pin file $pin_file is missing; refusing to run unpinned"
pinned="$(tr -d '[:space:]' <"$pin_file")"
[[ -n "$pinned" ]] || fail "pin file $pin_file is empty"
[[ ${#pinned} -eq 40 ]] \
  || fail "pin must be a full 40-character sha, got '${pinned}' (${#pinned} chars)"
actual="$(git -C "$workdir" rev-parse HEAD 2>/dev/null || true)"
[[ -n "$actual" ]] || fail "cannot resolve HEAD of $workdir"
# Exact equality, not a prefix match: an abbreviated pin would accept any commit
# sharing those leading characters.
if [[ "$actual" != "$pinned" ]]; then
  fail "checkout drifted: pinned $pinned, found $actual — re-review and update the pin"
fi
# A matching HEAD says nothing about the working tree, and the resident agent
# edits it in place. Tracked modifications mean the running code is not the
# reviewed code even when the commit matches.
dirty="$(git -C "$workdir" status --porcelain --untracked-files=no 2>/dev/null || true)"
if [[ -n "$dirty" ]]; then
  fail "working tree has tracked modifications; the running code is not the pinned code: $(echo "$dirty" | head -3 | tr '\n' ' ')"
fi

python_bin="$workdir/venv/bin/python"
[[ -x "$python_bin" ]] || fail "interpreter not found at $python_bin"
"$python_bin" - <<'PY' || fail "the pinned checkout does not enforce the delivery kill-switch"
import os, sys
sys.path.insert(0, os.environ.get("JOB_INTEL_WORKDIR", "/home/hermes/.hermes/hermes-agent"))
try:
    from job_intel.runtime import delivery_disabled
except Exception as exc:  # noqa: BLE001 - any import failure is a drift failure
    print(f"import failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if delivery_disabled() else 1)
PY

echo "shadow preflight OK: delivery disabled, credential stores unreachable, checkout pinned to $pinned"
