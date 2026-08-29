#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: check_p0_scope.sh [--authorization PATH] [--ref REF]

Checks the closed path table from the authorization artifact. Every commit in
authorized_base..REF is inspected, including intermediate commits that a final
diff would hide. Merge commits are rejected fail-closed; rebase or fast-forward
the work onto a linear history and run this checker again.
USAGE
}

fail() {
  echo "p0 scope check: $*" >&2
  exit 1
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || fail "not inside a git worktree"
initial_cwd="$PWD"
authorization_arg="$repo_root/docs/evidence/product-search-gate-a/2026-08-26-p0-authorization.md"
ref="HEAD"

while (($#)); do
  case "$1" in
    --authorization)
      (($# >= 2)) || fail "--authorization needs a path"
      authorization_arg="$2"
      shift 2
      ;;
    --ref)
      (($# >= 2)) || fail "--ref needs a revision"
      ref="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1 (use --help)"
      ;;
  esac
done

if [[ "$authorization_arg" = /* ]]; then
  authorization_file="$authorization_arg"
else
  authorization_file="$initial_cwd/$authorization_arg"
fi
[[ -f "$authorization_file" ]] \
  || fail "missing authorization file: $authorization_file"

case "$authorization_file" in
  "$repo_root"/*) authorization_rel="${authorization_file#"$repo_root/"}" ;;
  *) fail "authorization file is outside repository: $authorization_file" ;;
esac

cd "$repo_root"
ref_commit="$(git rev-parse --verify "${ref}^{commit}" 2>/dev/null)" \
  || fail "cannot resolve ref: $ref"

metadata_file="$(mktemp)"
trap 'rm -f "$metadata_file"' EXIT
if ! python3 - "$authorization_file" >"$metadata_file" <<'PY'
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if not text.startswith("---\n"):
    raise SystemExit("authorization artifact has no frontmatter")

frontmatter_end = text.find("\n---\n", 4)
if frontmatter_end < 0:
    raise SystemExit("authorization artifact has unterminated frontmatter")

metadata: dict[str, str] = {}
for line in text[4:frontmatter_end].splitlines():
    if ":" in line:
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')

required = (
    "authorized_base",
    "scope_table_sha256",
    "scope_table_path_count",
)
missing = [key for key in required if not metadata.get(key)]
if missing:
    raise SystemExit(f"authorization artifact missing metadata: {', '.join(missing)}")

section = re.search(r"(?m)^## 4\.[^\n]*\n", text)
if section is None:
    raise SystemExit("authorization artifact missing section 4 path table")
table_end = re.search(r"(?m)^## ", text[section.end():])
table_text = text[section.end():]
if table_end is not None:
    table_text = table_text[:table_end.start()]

paths = []
for line in table_text.splitlines():
    if not line.startswith("|"):
        continue
    match = re.search(r"\x60([^\x60\n]+)\x60", line)
    if match:
        paths.append(match.group(1))

if not paths:
    raise SystemExit("authorization section 4 contains no backticked paths")
unique_paths = sorted(set(paths))
if len(paths) != len(unique_paths):
    raise SystemExit("authorization section 4 contains duplicate paths")
if any(not path or path.startswith("/") for path in unique_paths):
    raise SystemExit("authorization section 4 contains an invalid path")

canonical = "".join(f"{path}\n" for path in unique_paths).encode("utf-8")
actual_hash = hashlib.sha256(canonical).hexdigest()
expected_hash = metadata["scope_table_sha256"]
if actual_hash != expected_hash:
    raise SystemExit(
        f"scope table hash mismatch: expected {expected_hash}, actual {actual_hash}"
    )

try:
    expected_count = int(metadata["scope_table_path_count"])
except ValueError as exc:
    raise SystemExit("scope_table_path_count is not an integer") from exc
if expected_count != len(unique_paths):
    raise SystemExit(
        "scope table path count mismatch: "
        f"expected {expected_count}, actual {len(unique_paths)}"
    )

print(f"BASE\t{metadata['authorized_base']}")
for value in unique_paths:
    print(f"PATH\t{value}")
PY
then
  fail "invalid authorization artifact: $authorization_file"
fi

declare -A allowed_paths=()
authorized_base=""
while IFS=$'\t' read -r kind value; do
  case "$kind" in
    BASE) authorized_base="$value" ;;
    PATH) allowed_paths["$value"]=1 ;;
  esac
done <"$metadata_file"

[[ -n "$authorized_base" ]] || fail "authorization artifact has no authorized_base"
git cat-file -e "${authorized_base}^{commit}" 2>/dev/null \
  || fail "authorized_base is not a commit: $authorized_base"
git merge-base --is-ancestor "$authorized_base" "$ref_commit" \
  || fail "authorized_base is not an ancestor of ref: $authorized_base..$ref_commit"
git cat-file -e "$ref_commit:$authorization_rel" 2>/dev/null \
  || fail "authorization artifact is not present at ref: $authorization_rel"

authorization_commit="$(
  git log --reverse --format=%H --diff-filter=A "$ref_commit" -- "$authorization_rel" |
    head -n 1
)"
[[ -n "$authorization_commit" ]] \
  || fail "cannot find the first commit that added authorization artifact: $authorization_rel"
if git merge-base --is-ancestor "$authorization_commit" "$authorized_base"; then
  fail "authorization artifact commit $authorization_commit is not after authorized_base $authorized_base"
fi

check_path() {
  local path="$1"
  local origin="$2"
  [[ -n "${allowed_paths[$path]+present}" ]] \
    || fail "unauthorized path=$path origin=$origin"
}

# These five tracked fixtures have a repository-wide CRLF/LF checkout defect.
# Only an EOL-only working-tree difference for these exact paths is ignored;
# all commits and all other paths remain subject to the closed path table.
readonly -a WORKING_TREE_EOL_NOISE_PATHS=(
  "tests/fixtures/legal_research/code_act.html"
  "tests/fixtures/legal_research/info_page.html"
  "tests/fixtures/legal_research/rules_act.html"
  "tests/fixtures/legal_research/small_act.html"
  "tests/fixtures/legal_research/zero_search.html"
)

is_named_working_tree_noise_path() {
  local candidate="$1"
  local noise_path
  for noise_path in "${WORKING_TREE_EOL_NOISE_PATHS[@]}"; do
    [[ "$candidate" == "$noise_path" ]] && return 0
  done
  return 1
}

check_working_path() {
  local path="$1"
  # The named fixtures may differ only by the known CRLF/LF checkout defect;
  # an eol-only comparison is safe only after the exact path has been matched.
  if is_named_working_tree_noise_path "$path" \
    && git diff --ignore-space-at-eol --quiet "$ref_commit" -- "$path"; then
    return
  fi
  check_path "$path" "working-tree"
}

check_commit_paths() {
  local commit="$1"
  local status first second
  while IFS=$'\t' read -r status first second; do
    [[ -z "$status" ]] && continue
    if [[ "$status" == R* || "$status" == C* ]]; then
      check_path "$first" "commit=$commit"
      check_path "$second" "commit=$commit"
    else
      check_path "$first" "commit=$commit"
    fi
  done < <(
    git diff-tree --root -r --no-commit-id --name-status -M "$commit"
  )
}

mapfile -t commits < <(git rev-list --reverse "$authorized_base..$ref_commit")
for commit in "${commits[@]}"; do
  parent_count="$(git rev-list --parents -n 1 "$commit" | awk '{print NF - 1}')"
  if ((parent_count > 1)); then
    fail "merge commit rejected: commit=$commit; return to a linear history by rebasing or fast-forwarding"
  fi
  git merge-base --is-ancestor "$authorization_commit" "$commit" \
    || fail "authorization order violation: authorization_commit=$authorization_commit is not an ancestor of commit=$commit"
  check_commit_paths "$commit"
done

# This is the final git diff --name-only check, using name-status to preserve
# both sides of renames. Untracked files are checked separately below.
while IFS=$'\t' read -r status first second; do
  [[ -z "$status" ]] && continue
  if [[ "$status" == R* || "$status" == C* ]]; then
    check_working_path "$first"
    check_working_path "$second"
  else
    check_working_path "$first"
  fi
done < <(git diff --name-status -M "$ref_commit")

while IFS= read -r -d '' path; do
  check_path "$path" "untracked-working-tree"
done < <(git ls-files --others --exclude-standard -z)

echo "scope check passed: base=$authorized_base ref=$ref_commit paths=${#allowed_paths[@]} authorization_commit=$authorization_commit"
