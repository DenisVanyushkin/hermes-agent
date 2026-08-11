#!/usr/bin/env bash
set -euo pipefail

expected_branch="codex/job-intel-product-search"
repo_root="${PRODUCT_SEARCH_REPO_ROOT:-/home/hermes/.hermes/hermes-agent/.worktrees/job-intel-product-search}"
baseline="${PRODUCT_SEARCH_SCOPE_BASELINE:-$repo_root/docs/product-search-scope-baseline.yaml}"

fail() {
  echo "product-search scope guard: $*" >&2
  exit 1
}

actual_root="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "not inside a git worktree"
[[ "$PWD" == "$repo_root" ]] || fail "expected execution root $repo_root, got $PWD"
[[ "$actual_root" == "$repo_root" ]] || fail "git root mismatch: $actual_root"

actual_branch="$(git branch --show-current)"
[[ "$actual_branch" == "$expected_branch" ]] || fail "expected branch $expected_branch, got $actual_branch"
[[ -f "$baseline" ]] || fail "missing scope baseline: $baseline"

mapfile -t baseline_values < <(
  python3 - "$baseline" <<'PY'
from pathlib import Path
import sys
import yaml

data = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(data["base_branch"])
print(data["base_commit"])
for path in data["protected_paths"]:
    print(f"protected\t{path}\t{data['protected_paths'][path]}")
for path in data.get("production_source_config_paths", []):
    expected = data.get("production_source_config_hashes", {}).get(path, "")
    print(f"config\t{path}\t{expected}")
PY
)

base_branch="${baseline_values[0]}"
base_commit="${baseline_values[1]}"
merge_base="$(git merge-base HEAD "$base_branch")" || fail "cannot resolve merge-base with $base_branch"
[[ "$merge_base" == "$base_commit" ]] || fail "scope baseline base_commit does not match merge-base: $base_commit != $merge_base"

declare -A guarded_paths=()
for record in "${baseline_values[@]:2}"; do
  IFS=$'\t' read -r kind path expected_hash <<<"$record"
  guarded_paths["$path"]=1
  [[ -n "$expected_hash" ]] || continue
  actual_hash="$(git show "$base_commit:$path" | sha256sum | awk '{print $1}')" || fail "cannot hash baseline path: $path"
  [[ "$actual_hash" == "$expected_hash" ]] || fail "baseline hash mismatch for $path"
done

while IFS= read -r changed; do
  [[ -z "$changed" ]] && continue
  if [[ -n "${guarded_paths[$changed]:-}" ]]; then
    fail "protected Product Search path changed: $changed"
  fi
done < <(git diff --name-only "$merge_base")

echo "product-search scope guard passed: branch=$actual_branch base=$merge_base"
