#!/usr/bin/env bash
set -euo pipefail
umask 077

[[ "$#" -eq 0 && "$EUID" -eq 0 ]] || {
  echo "Gate B unit installation requires root and no arguments" >&2
  exit 77
}

readonly runtime_root="/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/immutable-runtime"
readonly runtime_source="$runtime_root/runtime"
readonly runner="$runtime_source/scripts/job_intel_gate_b_benchmark.sh"
readonly unit_source="$runtime_source/deploy/systemd/experiments/job-intel-gate-b-benchmark.service"
readonly unit_destination="/etc/systemd/system/job-intel-gate-b-benchmark.service"

[[ -x "$runner" && ! -L "$runner" ]] || {
  echo "immutable Gate B runner is unavailable" >&2
  exit 66
}
[[ -f "$unit_source" && ! -L "$unit_source" ]] || {
  echo "immutable Gate B unit is unavailable" >&2
  exit 66
}

# systemd resolves ReadWritePaths before any ExecStartPre command.  This fixed,
# root-only preflight must therefore run outside the unit namespace.
"$runner" prepare-output-root
/usr/bin/install -o root -g root -m 0644 "$unit_source" "$unit_destination"
/usr/bin/systemctl daemon-reload

[[ "$(/usr/bin/systemctl show job-intel-gate-b-benchmark.service --property=User --value)" == "hermes" ]]
[[ "$(/usr/bin/systemctl show job-intel-gate-b-benchmark.service --property=Group --value)" == "hermes" ]]
[[ "$(/usr/bin/systemctl show job-intel-gate-b-benchmark.service --property=Type --value)" == "oneshot" ]]
[[ "$(/usr/bin/systemctl show job-intel-gate-b-benchmark.service --property=Restart --value)" == "no" ]]
