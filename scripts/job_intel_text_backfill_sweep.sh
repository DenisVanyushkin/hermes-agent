#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${JOB_INTEL_SERVICE_USER:=hermes}"
export JOB_INTEL_SERVICE_USER
source "$script_dir/job_intel_service_user.sh"
job_intel_require_service_user

_job_intel_is_package() {
  # A directory *named* job_intel is not enough.  $HERMES_HOME/job_intel holds
  # databases, and python treats any such directory as an implicit namespace
  # package (PEP 420), which shadows the real one and makes `-m job_intel` fail
  # with "is a package and cannot be directly executed".  Probe for the
  # executable package marker instead.
  [[ -f "$1/job_intel/__main__.py" ]]
}

resolve_workdir() {
  local repo_root
  local candidates=(
    "${JOB_INTEL_WORKDIR:-}"
    "$PWD"
  )
  if repo_root="$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || true)"; then
    [[ -n "$repo_root" ]] && candidates+=("$repo_root")
  fi
  if [[ -d "$script_dir/../job_intel" ]]; then
    candidates+=("$(cd -- "$script_dir/.." && pwd)")
  fi
  candidates+=("${HERMES_HOME:-$HOME/.hermes}/hermes-agent")
  local candidate
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if _job_intel_is_package "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_db_path() {
  # Mirror job_intel_health.sh: the live database lives under the state dir.
  # $HOME/.hermes/job_intel/job_intel.sqlite3 was a stale copy frozen in May
  # 2026 (deleted 2026-07-16) and must never be resolved again -- this
  # function never checked existence, so naming it would make sqlite silently
  # recreate an empty database and operate on nothing.
  local state_dir="${JOB_INTEL_STATE_DIR:-/var/lib/job-intel/state}"
  printf '%s\n' "${JOB_INTEL_DB_PATH:-$state_dir/job_intel.sqlite3}"
}

resolve_python() {
  # Unlike job_intel_health.sh, this entrypoint does not drive a browser, so
  # there is no playwright venv preference.  It DOES need the gateway venv's
  # pysqlite3 shim (SQLite 3.53.4) -- the system interpreter links SQLite
  # 3.45.1, which carries the WAL-reset corruption bug (sqlite.org/wal.html
  # #walresetbug, fixed in 3.51.3).  The sweep performs up to 400 write
  # transactions against a 647 MB WAL-mode database with concurrent readers:
  # exactly that bug's scenario.  So this deliberately does NOT fall back to
  # a bare `python3`/`python` the way a "simplified" version of this function
  # might -- doing so would silently trade a loud failure for corruption.
  local workdir="$1"
  local candidates=(
    "${JOB_INTEL_PYTHON:-}"
    "$workdir/venv/bin/python"
    "$workdir/.venv/bin/python"
  )
  local candidate python_path
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate:-}" ]] || continue
    if [[ "$candidate" = */* ]]; then
      if [[ -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    else
      python_path="$(command -v "$candidate" 2>/dev/null || true)"
      if [[ -n "$python_path" ]]; then
        printf '%s\n' "$python_path"
        return 0
      fi
    fi
  done
  return 1
}

require_safe_sqlite() {
  # Verify at runtime rather than trust resolve_python's preference order --
  # a stale/rebuilt venv could still link a vulnerable SQLite.  A silent
  # downgrade here corrupts the production database instead of failing a job.
  #
  # Reuse hermes_cli's own detector instead of re-deriving the vulnerable
  # range here: the fix landed with backports (3.50.7, 3.44.6) that a bare
  # "< 3.51.3" floor check would misclassify as vulnerable.  See
  # hermes_cli/sqlite_runtime.py::is_sqlite_wal_reset_vulnerable.
  local python_bin="$1"
  local output status=0
  output="$("$python_bin" - <<'PY' 2>&1
import sqlite3
import sys

try:
    from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
except ImportError as exc:
    print(f"cannot import hermes_cli.sqlite_runtime: {exc}")
    sys.exit(2)

version = sqlite3.sqlite_version
if is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info):
    print(f"sqlite {version} is vulnerable to the WAL-reset bug "
          "(sqlite.org/wal.html#walresetbug, fixed 3.51.3)")
    sys.exit(1)
print(version)
PY
)" && status=0 || status=$?

  if [[ "$status" -ne 0 ]]; then
    printf 'job_intel_text_backfill_sweep: refusing to run -- %s\n' "$output" >&2
    printf 'job_intel_text_backfill_sweep: interpreter=%s did not pass the sqlite safety check; expected the gateway venv pysqlite3 shim (3.53.4)\n' "$python_bin" >&2
    exit 1
  fi
  printf 'job_intel_text_backfill_sweep: sqlite %s ok (interpreter=%s)\n' "$output" "$python_bin" >&2
}

workdir="$(resolve_workdir)"
db_path="$(resolve_db_path)"
python_bin="$(resolve_python "$workdir")" || {
  printf 'job_intel_text_backfill_sweep: no venv python interpreter found (checked JOB_INTEL_PYTHON, %s/venv/bin/python, %s/.venv/bin/python) -- refusing to fall back to a bare python3/python, see resolve_python() comment\n' "$workdir" "$workdir" >&2
  exit 1
}
require_safe_sqlite "$python_bin"

cd "$workdir"
export JOB_INTEL_WORKDIR="$workdir"
export JOB_INTEL_DB_PATH="$db_path"
export JOB_INTEL_ENVIRONMENT="${JOB_INTEL_ENVIRONMENT:-production}"
export JOB_INTEL_SCRIPTS_DIR="${JOB_INTEL_SCRIPTS_DIR:-$script_dir}"
# Sweep budget (default 400) is read by the python script itself via
# JOB_INTEL_TEXT_BACKFILL_SWEEP_BUDGET -- not set here, this wrapper does not
# choose a budget.
exec "$python_bin" scripts/job_intel_text_backfill_sweep.py
