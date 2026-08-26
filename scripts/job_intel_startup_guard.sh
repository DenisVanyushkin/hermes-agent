#!/usr/bin/env bash
# Gate the target interpreter behind a verified startup tree.
#
# The order is the guarantee, so it lives in one small script that can be
# executed by a test: verify the manifest with a trusted interpreter, and only
# then run the target one. Inlining this in the preflight left the ordering
# provable only by reading the shell, which is how it was got wrong before.
#
# Usage: job_intel_startup_guard.sh <manifest> <venv-root> <checker> <expected-uid> [command...]
# With no command it verifies and exits; with one it runs it only on success.
#
# The expected manifest owner is a positional argument, not an environment
# variable: the caller (the unit) decides it, and the collection environment —
# which is generated from the production file — must not be able to.
set -euo pipefail

manifest="${1:?manifest path required}"
venv_root="${2:?venv root required}"
checker="${3:?checker path required}"
expected_uid="${4:?expected manifest owner uid required}"
shift 4

system_python="${JOB_INTEL_SYSTEM_PYTHON:-/usr/bin/python3.12}"

fail() { echo "startup guard FAILED: $1" >&2; exit 1; }

[[ -x "$system_python" ]] || fail "trusted system interpreter missing at $system_python"

# The manifest is authority: if hermes can rewrite it, it blesses whatever the
# tree happens to contain. Readability is not enough.
[[ -f "$manifest" && ! -L "$manifest" ]] || fail "manifest $manifest is not a regular file"
read -r m_uid m_mode < <(stat -c '%u %a' "$manifest")
[[ "$m_uid" == "$expected_uid" ]] \
  || fail "manifest $manifest must be owned by uid $expected_uid, is $m_uid"
[[ "${m_mode: -2}" =~ ^[0-4][0-4]$ ]] \
  || fail "manifest $manifest must not be group- or world-writable, mode is $m_mode"

"$system_python" -I -S "$checker" verify "$manifest" "$venv_root" \
  || fail "startup tree does not match the manifest"

(($# == 0)) && exit 0
exec "$@"
