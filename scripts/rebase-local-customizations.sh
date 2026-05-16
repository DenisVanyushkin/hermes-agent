#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${PWD:-.}/.git" ] && [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="${PWD}"
elif [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="$HOME/.hermes/hermes-agent"
fi

BRANCH="${HERMES_LOCAL_BRANCH:-local/customizations}"
UPSTREAM_REMOTE="${HERMES_UPSTREAM_REMOTE:-origin}"
UPSTREAM_BRANCH="${HERMES_UPSTREAM_BRANCH:-main}"
UPSTREAM_REF="$UPSTREAM_REMOTE/$UPSTREAM_BRANCH"

resolve_hermes_bin() {
  can_run_hermes() {
    [ -n "$1" ] && [ -x "$1" ] || return 1
    "$1" --version >/dev/null 2>&1
  }

  for candidate in "${HERMES_BIN:-}" "$REPO/venv/bin/hermes" "$HOME/.local/bin/hermes" "$(command -v hermes 2>/dev/null || true)"; do
    [ -n "$candidate" ] || continue
    if can_run_hermes "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

abort_rebase_if_needed() {
  git -C "$REPO" rebase --abort >/dev/null 2>&1 || true
}

report_noop() {
  cat <<EOF
Hermes local-branch update: no upstream changes.
Repo: $REPO
Branch: $BRANCH
Base: $UPSTREAM_REF
Current: $1
Gateway restarted: no
EOF
}

report_success() {
  cat <<EOF
Hermes local-branch update succeeded.
Repo: $REPO
Branch: $BRANCH
Base: $UPSTREAM_REF
Before: $1
After: $2
Gateway restarted: yes
EOF
}

if [ ! -d "$REPO/.git" ]; then
  echo "Repo not found or not a git checkout: $REPO" >&2
  exit 1
fi

git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

if [ -d "$REPO/.git/rebase-merge" ] || [ -d "$REPO/.git/rebase-apply" ]; then
  echo "Repo is already mid-rebase; resolve it before running update." >&2
  exit 1
fi

AUTOSTASH_CREATED=0
AUTOSTASH_RESTORE_FAILED=0
cleanup_autostash() {
  local status="$1"
  if [ "$AUTOSTASH_CREATED" -eq 1 ]; then
    if git -C "$REPO" stash pop --index >/dev/null 2>&1; then
      AUTOSTASH_CREATED=0
    else
      AUTOSTASH_RESTORE_FAILED=1
      echo "Warning: autostash could not be restored cleanly; it remains in git stash." >&2
    fi
  fi
  if [ "$status" -eq 0 ] && [ "$AUTOSTASH_RESTORE_FAILED" -eq 1 ]; then
    exit 1
  fi
}

STATUS_BEFORE="$(git -C "$REPO" status --porcelain --untracked-files=all)"
if [ -n "$STATUS_BEFORE" ]; then
  echo "Local changes detected — stashing before update..." >&2
  git -C "$REPO" stash push --include-untracked -m "hermes-local-customizations-autostash-$(date +%Y%m%d-%H%M%S)" >/dev/null
  AUTOSTASH_CREATED=1
  trap 'cleanup_autostash "$?"' EXIT
fi

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  git -C "$REPO" checkout "$BRANCH" >/dev/null
fi

if ! git -C "$REPO" rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  echo "Local branch not found: $BRANCH" >&2
  exit 1
fi

if ! git -C "$REPO" remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  echo "Remote not found: $UPSTREAM_REMOTE" >&2
  exit 1
fi

BEFORE_HEAD="$(git -C "$REPO" rev-parse --short HEAD)"
BASE_BEFORE="$(git -C "$REPO" rev-parse --short "$UPSTREAM_REF" 2>/dev/null || true)"

git -C "$REPO" fetch "$UPSTREAM_REMOTE" --prune >/dev/null

BASE_AFTER="$(git -C "$REPO" rev-parse --short "$UPSTREAM_REF")"
if [ "$BASE_BEFORE" = "$BASE_AFTER" ] && git -C "$REPO" merge-base --is-ancestor "$UPSTREAM_REF" HEAD; then
  report_noop "$BEFORE_HEAD"
  exit 0
fi

REBASE_LOG="$(mktemp)"
if ! git -C "$REPO" rebase "$UPSTREAM_REF" >"$REBASE_LOG" 2>&1; then
  abort_rebase_if_needed
  echo "Hermes local-branch update failed during rebase." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Base: $UPSTREAM_REF" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "Fetched base: $BASE_AFTER" >&2
  echo "Rebase output:" >&2
  cat "$REBASE_LOG" >&2
  rm -f "$REBASE_LOG"
  exit 1
fi
rm -f "$REBASE_LOG"

AFTER_HEAD="$(git -C "$REPO" rev-parse --short HEAD)"
if [ "$AFTER_HEAD" = "$BEFORE_HEAD" ]; then
  report_noop "$BEFORE_HEAD"
  exit 0
fi

HERMES_BIN="$(resolve_hermes_bin || true)"
if [ -z "$HERMES_BIN" ]; then
  echo "Updated repo, but could not find hermes executable to restart gateway." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "After: $AFTER_HEAD" >&2
  exit 1
fi

RESTART_OUTPUT="$($HERMES_BIN gateway restart 2>&1)" || {
  echo "Updated repo, but gateway restart failed." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "After: $AFTER_HEAD" >&2
  echo "Restart output:" >&2
  printf '%s\n' "$RESTART_OUTPUT" >&2
  exit 1
}

report_success "$BEFORE_HEAD" "$AFTER_HEAD"
