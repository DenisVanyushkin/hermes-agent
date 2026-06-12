#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${PWD:-.}/.git" ] && [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="${PWD}"
elif [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="${HERMES_HOME:-$HOME/.hermes}/hermes-agent"
fi

BRANCH="${HERMES_LOCAL_BRANCH:-local/customizations}"
UPSTREAM_REMOTE="${HERMES_UPSTREAM_REMOTE:-origin}"
UPSTREAM_BRANCH="${HERMES_UPSTREAM_BRANCH:-main}"
UPSTREAM_REF="$UPSTREAM_REMOTE/$UPSTREAM_BRANCH"
UPSTREAM_FETCH_URL="${HERMES_UPSTREAM_FETCH_URL:-https://github.com/NousResearch/hermes-agent.git}"

if [ ! -d "$REPO/.git" ]; then
  echo "Repo not found or not a git checkout: $REPO" >&2
  exit 1
fi

git -C "$REPO" config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

status_file="$tmpdir/status.txt"
upstream_commits_file="$tmpdir/upstream_commits.txt"
upstream_files_file="$tmpdir/upstream_files.txt"
local_commits_file="$tmpdir/local_commits.txt"
local_files_file="$tmpdir/local_files.txt"

# Refresh upstream before comparing so the report describes the latest remote state.
git -C "$REPO" fetch --prune "$UPSTREAM_FETCH_URL" "+refs/heads/$UPSTREAM_BRANCH:refs/remotes/$UPSTREAM_REMOTE/$UPSTREAM_BRANCH" >/dev/null

HEAD="$(git -C "$REPO" rev-parse HEAD)"
UPSTREAM_HEAD="$(git -C "$REPO" rev-parse "$UPSTREAM_REF")"
BASE="$(git -C "$REPO" merge-base HEAD "$UPSTREAM_REF")"
UPSTREAM_AHEAD="$(git -C "$REPO" rev-list --count "$BASE..$UPSTREAM_REF")"
LOCAL_AHEAD="$(git -C "$REPO" rev-list --count "$BASE..HEAD")"

if [ -n "${REPORT_BRANCH_ONLY:-}" ]; then
  git -C "$REPO" status --porcelain=v1 --untracked-files=all >"$status_file"
else
  git -C "$REPO" status --porcelain=v1 --untracked-files=all >"$status_file"
fi

git -C "$REPO" log --no-merges --format='%h %s' "$BASE..$UPSTREAM_REF" >"$upstream_commits_file" || true
git -C "$REPO" diff --name-only "$BASE..$UPSTREAM_REF" >"$upstream_files_file" || true
git -C "$REPO" log --no-merges --format='%h %s' "$BASE..HEAD" >"$local_commits_file" || true
git -C "$REPO" diff --name-only "$BASE..HEAD" >"$local_files_file" || true

resolve_python() {
  for candidate in \
    /usr/local/bin/python3 \
    /usr/bin/python3 \
    /bin/python3 \
    "$(command -v python3 2>/dev/null || true)" \
    "$(command -v python 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(resolve_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "Neither python3 nor python found (checked common absolute paths and PATH); cannot run preflight report." >&2
  exit 127
fi

"$PYTHON_BIN" - "$REPO" "$BRANCH" "$UPSTREAM_REF" "$HEAD" "$UPSTREAM_HEAD" "$BASE" "$UPSTREAM_AHEAD" "$LOCAL_AHEAD" "$status_file" "$upstream_commits_file" "$upstream_files_file" "$local_commits_file" "$local_files_file" <<'PY'
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

repo, branch, upstream_ref, head, upstream_head, base, upstream_ahead, local_ahead, status_file, upstream_commits_file, upstream_files_file, local_commits_file, local_files_file = sys.argv[1:14]
upstream_ahead = int(upstream_ahead)
local_ahead = int(local_ahead)


def read_lines(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [line.rstrip("\n") for line in p.read_text().splitlines() if line.strip()]


def parse_status(lines: list[str]) -> list[str]:
    files: list[str] = []
    for line in lines:
        if not line:
            continue
        # porcelain v1: XY path or XY old -> new
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return files


def split_subject(line: str) -> tuple[str, str]:
    if " " not in line:
        return line, ""
    short, subject = line.split(" ", 1)
    return short, subject


def classify(path: str) -> str:
    if re.match(r"^(agent/|gateway/|hermes_cli/|cli\.py$|run_agent\.py$|model_tools\.py$|toolsets\.py$|tools/|hermes_state\.py$)", path):
        return "core runtime"
    if re.match(r"^(deploy/|docker-compose\.yml$|scripts/|job_intel/)", path):
        return "deployment / job-intel"
    if re.match(r"^(tests/|docs/|\.github/|website/|docs/reports/)", path):
        return "docs / tests / release"
    if re.search(r"(security|auth|secret|pairing|allowlist|file-safety|control-plane|HMAC|hmac|insecure)", path, re.I):
        return "security-sensitive"
    return "other"


status_paths = parse_status(read_lines(status_file))
upstream_commits = read_lines(upstream_commits_file)
upstream_files = read_lines(upstream_files_file)
local_commits = read_lines(local_commits_file)
local_files = read_lines(local_files_file)

# Overlap detection between local worktree/local branch and upstream changes.
upstream_set = set(upstream_files)
local_set = set(local_files)
status_set = set(status_paths)
conflict_overlap = sorted((local_set | status_set) & upstream_set)

upstream_counts = Counter(classify(p) for p in upstream_files)
local_counts = Counter(classify(p) for p in local_files)
status_counts = Counter(classify(p) for p in status_paths)

critical_patterns = {
    "gateway / delivery": re.compile(r"^(gateway/|docker-compose\.yml$|hermes_cli/config\.py$|cli\.py$)"),
    "job-intel / deploy": re.compile(r"^(job_intel/|deploy/|scripts/|docs/job-intel|docs/.*job-intel)"),
    "agent / tool plumbing": re.compile(r"^(agent/|toolsets\.py$|tools/|run_agent\.py$|model_tools\.py$|hermes_state\.py$)"),
    "security / auth": re.compile(r"(security|auth|secret|pairing|allowlist|file-safety|control-plane|HMAC|hmac|insecure)", re.I),
}

risk_flags: list[str] = []
if status_paths:
    risk_flags.append(f"local uncommitted changes present ({len(status_paths)} files)")
if conflict_overlap:
    risk_flags.append(f"direct overlap between local changes and upstream files ({len(conflict_overlap)} paths)")

critical_hits = []
for label, pat in critical_patterns.items():
    hits = [p for p in upstream_files if pat.search(p)]
    if hits:
        critical_hits.append((label, hits))

if critical_hits:
    risk_flags.append("upstream touched runtime-sensitive surfaces")

# Print report.
print("## Local customizations update preflight")
print(f"Repo: {repo}")
print(f"Branch: {branch}")
print(f"HEAD: {head[:12]}")
print(f"Upstream: {upstream_ref} @ {upstream_head[:12]}")
print(f"Merge-base: {base[:12]}")
print(f"Ahead/behind vs upstream: local +{local_ahead}, upstream +{upstream_ahead}")
print()

if upstream_ahead == 0:
    print("### Upstream delta")
    print("No new upstream commits are ahead of the local merge-base right now.")
else:
    print("### Upstream delta")
    print(f"Upstream commits ahead of local: {upstream_ahead}")
    if upstream_commits:
        preview = upstream_commits[:20]
        for line in preview:
            print(f"- {line}")
        if len(upstream_commits) > len(preview):
            print(f"- … {len(upstream_commits) - len(preview)} more upstream commits")
    else:
        print("- (no commit subjects available)")
    print()
    print(f"Changed upstream files: {len(upstream_files)}")
    for category in ["core runtime", "deployment / job-intel", "security-sensitive", "docs / tests / release", "other"]:
        count = upstream_counts.get(category, 0)
        if count:
            print(f"- {category}: {count}")

print()
print("### Local state that may affect the update")
if status_paths:
    print(f"Uncommitted worktree files: {len(status_paths)}")
    for p in status_paths[:20]:
        print(f"- {p}")
    if len(status_paths) > 20:
        print(f"- … {len(status_paths) - 20} more")
else:
    print("Uncommitted worktree files: none")

if local_ahead:
    print(f"Local-only commits since merge-base: {local_ahead}")
    for line in local_commits[:10]:
        print(f"- {line}")
    if len(local_commits) > 10:
        print(f"- … {len(local_commits) - 10} more")

print()
print("### Conflict / breaking-change analysis")
if conflict_overlap:
    print("Direct overlap between local changes and upstream files:")
    for p in conflict_overlap[:25]:
        print(f"- {p}")
    if len(conflict_overlap) > 25:
        print(f"- … {len(conflict_overlap) - 25} more")
else:
    print("No direct file-path overlap between local changes and upstream changes.")

for label, hits in critical_hits:
    print(f"- {label}: {len(hits)} touched upstream files")
    for p in hits[:10]:
        print(f"  - {p}")
    if len(hits) > 10:
        print(f"  - … {len(hits) - 10} more")

if not risk_flags:
    print()
    print("Risk level: low")
    print("Reason: no upstream delta and no local dirty-state issues detected.")
else:
    print()
    if conflict_overlap or status_paths:
        print("Risk level: medium/high")
    else:
        print("Risk level: medium")
    for flag in risk_flags:
        print(f"- {flag}")

print()
print("### Approval gate")
print("Reply with approval only after you are satisfied with this report. Do not run the update until then.")
print(f"Planned apply command: scripts/rebase-local-customizations.sh")
PY
