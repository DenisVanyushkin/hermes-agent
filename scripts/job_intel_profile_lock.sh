#!/usr/bin/env bash
set -euo pipefail

default_lock_path="/run/job-intel/linkedin-profile.lock"
lock_path="$default_lock_path"

usage() {
  cat <<'USAGE'
Usage: job_intel_profile_lock.sh [--path PATH]

Acquire and hold an exclusive profile lock in the foreground.

The holder is this long-lived process: it keeps the flock file descriptor open
until it exits, so an ExecStart return cannot leave an active unit without a
lock. The production default is /run/job-intel/linkedin-profile.lock; --path
is available for an experiment-local test path. flock is a kernel advisory
lock, not a lock-file convention. If the holder crashes, the kernel releases
the lock when its descriptor closes. A stale file therefore needs no manual
cleanup: a new holder can acquire it normally.
USAGE
}

while (($#)); do
  case "$1" in
    --path)
      (($# >= 2)) || { echo "--path needs a value" >&2; exit 2; }
      lock_path="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1 (use --help)" >&2
      exit 2
      ;;
  esac
done

[[ "$lock_path" = /* ]] || {
  echo "lock path must be absolute: $lock_path" >&2
  exit 2
}

lock_parent="${lock_path%/*}"
[[ -n "$lock_parent" ]] || lock_parent="/"
mkdir -p "$lock_parent"

exec {lock_fd}>"$lock_path"
if ! flock -n "$lock_fd"; then
  echo "profile lock is already held: $lock_path" >&2
  exit 1
fi

printf 'profile lock acquired: %s holder_pid=%s\n' "$lock_path" "$$"
trap 'exit 0' HUP INT TERM

# Do not return from ExecStart: the descriptor must remain owned by this
# process for the entire lifetime of the profile consumer.
while :; do
  sleep 3600 &
  wait "$!"
done
