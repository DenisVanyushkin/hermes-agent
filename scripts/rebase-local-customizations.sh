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
  if [ -x "$REPO/venv/bin/hermes" ]; then
    printf '%s\n' "$REPO/venv/bin/hermes"
    return 0
  fi
  if command -v hermes >/dev/null 2>&1; then
    command -v hermes
    return 0
  fi
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

STATUS_BEFORE="$(git -C "$REPO" status --porcelain)"
if [ -n "$STATUS_BEFORE" ]; then
  echo "Refusing to update because repo is dirty:" >&2
  printf '%s\n' "$STATUS_BEFORE" >&2
  exit 1
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

trap abort_rebase_if_needed ERR
REBASE_OUTPUT="$(git -C "$REPO" rebase "$UPSTREAM_REF" 2>&1)" || {
  echo "Hermes local-branch update failed during rebase." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Base: $UPSTREAM_REF" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "Fetched base: $BASE_AFTER" >&2
  echo "Rebase output:" >&2
  printf '%s\n' "$REBASE_OUTPUT" >&2
  exit 1
}
trap - ERR

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
