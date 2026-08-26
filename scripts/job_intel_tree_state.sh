#!/usr/bin/env bash
# Report whether a checkout is safe to run pinned code from.
#
# Extracted from the preflight so the behaviour is testable against a throwaway
# repository. The preflight always calls it with the canonical checkout; the
# directory is an argument here only so a test can build the failing states,
# never so the environment can choose them.
#
# Exit codes: 0 clean; 2 git failed; 3 tracked modifications; 4 untracked
# auto-loading code.
set -uo pipefail

dir="${1:?usage: job_intel_tree_state.sh <checkout>}"

if ! status="$(git -C "$dir" status --porcelain --untracked-files=normal 2>&1)"; then
  echo "git status failed in $dir: $(printf '%s' "$status" | head -1)" >&2
  exit 2
fi

tracked="$(printf '%s\n' "$status" | grep -v '^??' || true)"
if [[ -n "$tracked" ]]; then
  echo "tracked modifications: $(printf '%s' "$tracked" | head -3 | tr '\n' ' ')" >&2
  exit 3
fi

# Python loads sitecustomize.py and .pth files without any import statement, so
# an untracked file can execute while every tracked file matches the pin.
untracked="$(printf '%s\n' "$status" | grep '^??' | grep -E '(sitecustomize\.py|\.pth|conftest\.py|/__init__\.py)$' || true)"
if [[ -n "$untracked" ]]; then
  echo "untracked auto-loading code: $(printf '%s' "$untracked" | head -3 | tr '\n' ' ')" >&2
  exit 4
fi

echo "clean"
