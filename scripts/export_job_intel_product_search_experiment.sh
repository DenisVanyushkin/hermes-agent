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
cp -a "$python_prefix" "$destination/python-runtime/cpython"
copied_python="$(find "$destination/python-runtime/cpython/bin" -maxdepth 1 -type f -name 'python3.12' -print -quit)"
[[ -n "$copied_python" ]] || { echo "copied Python 3.12 interpreter not found" >&2; exit 64; }
uv venv "$destination/python-runtime/venv" --python "$copied_python"
UV_PROJECT_ENVIRONMENT="$destination/python-runtime/venv" uv sync \
  --project "$destination/runtime" --frozen --no-install-project --no-dev \
  --python "$destination/python-runtime/venv/bin/python"
uv pip freeze --python "$destination/python-runtime/venv/bin/python" \
  >"$destination/python-runtime/installed-distributions.txt"
(cd "$destination/runtime" && PYTHONPATH="$destination/runtime" \
  "$destination/python-runtime/venv/bin/python" -m job_intel.product_search.acquisition_probe \
  write-manifest "$destination" "$commit")
chmod -R a-w "$destination/runtime" "$destination/python-runtime/venv"
