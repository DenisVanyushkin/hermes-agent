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

# --untracked-files=all, not normal: normal collapses an untracked directory to
# a single "?? dir/" entry, hiding executable files inside it.
if ! status="$(git -C "$dir" status --porcelain --untracked-files=all 2>&1)"; then
  echo "git status failed in $dir: $(printf '%s' "$status" | head -1)" >&2
  exit 2
fi

tracked="$(printf '%s\n' "$status" | grep -v '^??' || true)"
if [[ -n "$tracked" ]]; then
  echo "tracked modifications: $(printf '%s' "$tracked" | head -3 | tr '\n' ' ')" >&2
  exit 3
fi

# Only what this check can actually see and what actually auto-loads from the
# checkout root: Python imports sitecustomize.py from sys.path[0]. Files inside
# the virtualenv are ignored by git and are covered by the separate site
# integrity manifest instead — claiming them here would be a guarantee this
# check cannot deliver.
untracked="$(printf '%s\n' "$status" | grep '^?? sitecustomize\.py$' || true)"
if [[ -n "$untracked" ]]; then
  echo "untracked auto-loading code at the checkout root: sitecustomize.py" >&2
  exit 4
fi

echo "clean"
