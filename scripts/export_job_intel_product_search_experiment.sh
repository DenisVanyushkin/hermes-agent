#!/usr/bin/env bash
set -euo pipefail

repo="${1:?usage: export_job_intel_product_search_experiment.sh REPO COMMIT DEST PYTHON}"
commit="${2:?commit required}"
destination="${3:?destination required}"
python_source="${4:?python executable required}"

case "$repo" in
  */.worktrees/*) echo "refusing to export from a feature worktree" >&2; exit 64 ;;
esac
git -C "$repo" diff --quiet || { echo "canonical checkout is dirty" >&2; exit 64; }
git -C "$repo" cat-file -e "$commit^{commit}"
mkdir -p "$destination/runtime" "$destination/python-runtime" "$destination/raw-evidence" \
  "$destination/logs" "$destination/locks" "$destination/browser-profile" \
  "$destination/cache" "$destination/tmp"
git -C "$repo" archive "$commit" | tar -x -C "$destination/runtime"

python_prefix="$("$python_source" -c 'import sys; print(sys.prefix)')"
# The interpreter's own versioned name (python3.14 since the v0.22.5 port): the lock only resolves
# for the version it was built for, so the copy must be exactly the source interpreter.
python_name="$("$python_source" -c 'import sys; print("python%d.%d" % sys.version_info[:2])')"
cp -a "$python_prefix" "$destination/python-runtime/cpython"
copied_python="$(find "$destination/python-runtime/cpython/bin" -maxdepth 1 -type f -name "$python_name" -print -quit)"
[[ -n "$copied_python" ]] || { echo "copied $python_name interpreter not found" >&2; exit 64; }
uv venv "$destination/python-runtime/venv" --python "$copied_python"
UV_PROJECT_ENVIRONMENT="$destination/python-runtime/venv" uv sync \
  --project "$destination/runtime" --frozen --no-install-project --no-dev \
  --extra product-search-browser \
  --python "$destination/python-runtime/venv/bin/python"
uv pip freeze --python "$destination/python-runtime/venv/bin/python" \
  >"$destination/python-runtime/installed-distributions.txt"
(cd "$destination/runtime" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$destination/runtime" \
  "$destination/python-runtime/venv/bin/python" -m job_intel.product_search.acquisition_probe \
  write-manifest "$destination" "$commit")
bytecode_path="$(find "$destination/runtime" \
  \( -type d -name __pycache__ -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
  -print -quit)"
[[ -z "$bytecode_path" ]] || {
  echo "immutable runtime contains Python bytecode: $bytecode_path" >&2
  exit 64
}
chmod -R a-w "$destination/runtime" "$destination/python-runtime/venv"
