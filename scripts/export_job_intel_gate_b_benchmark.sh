#!/usr/bin/env bash
set -euo pipefail

repo="${1:?usage: export_job_intel_gate_b_benchmark.sh REPO COMMIT DEST PYTHON}"
commit="${2:?commit required}"
destination="${3:?destination required}"
python_source="${4:?python executable required}"

[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
  echo "candidate commit must be a full SHA-1" >&2
  exit 64
}
[[ ! -e "$destination" ]] || {
  echo "runtime export destination already exists" >&2
  exit 64
}
git -C "$repo" cat-file -e "$commit^{commit}"
mkdir -p "$destination/runtime" "$destination/python-runtime"
git -C "$repo" archive "$commit" | tar -x -C "$destination/runtime"

python_version="$("$python_source" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
[[ "$python_version" == "3.12.13" ]] || {
  echo "Python 3.12.13 is required" >&2
  exit 64
}
python_prefix="$("$python_source" -c 'import sys; print(sys.prefix)')"
cp -a "$python_prefix" "$destination/python-runtime/cpython"
copied_python="$(find "$destination/python-runtime/cpython/bin" -maxdepth 1 -type f -name 'python3.12' -print -quit)"
[[ -n "$copied_python" && ! -L "$copied_python" ]] || {
  echo "copied Python 3.12.13 interpreter not found" >&2
  exit 64
}

uv venv "$destination/python-runtime/venv" --python "$copied_python"
venv_python_names=(python python3 python3.12)
for venv_python_name in "${venv_python_names[@]}"; do
  venv_python="$destination/python-runtime/venv/bin/$venv_python_name"
  resolved_venv_python="$(readlink -f -- "$venv_python")"
  [[ -L "$venv_python" && "$resolved_venv_python" == "$copied_python" ]] || {
    echo "venv Python target is not the copied interpreter" >&2
    exit 66
  }
done
for venv_python_name in "${venv_python_names[@]}"; do
  venv_python="$destination/python-runtime/venv/bin/$venv_python_name"
  regular_venv_python="$destination/python-runtime/venv/bin/.$venv_python_name.regularizing"
  cp --no-clobber --preserve=mode,timestamps -- "$copied_python" "$regular_venv_python"
  [[ -f "$regular_venv_python" && ! -L "$regular_venv_python" ]] || {
    echo "regular venv Python copy was not created" >&2
    exit 66
  }
  [[ "$(stat -c '%h' -- "$regular_venv_python")" == "1" ]] || {
    echo "regular venv Python copy has an unsafe link count" >&2
    exit 66
  }
  mv -T -- "$regular_venv_python" "$venv_python"
  [[ -f "$venv_python" && ! -L "$venv_python" ]] || {
    echo "venv Python regularization failed" >&2
    exit 66
  }
  cmp --silent -- "$copied_python" "$venv_python" || {
    echo "venv Python content differs from the copied interpreter" >&2
    exit 66
  }
done
UV_PROJECT_ENVIRONMENT="$destination/python-runtime/venv" uv sync \
  --project "$destination/runtime" \
  --frozen \
  --no-install-project \
  --no-dev \
  --python "$destination/python-runtime/venv/bin/python"
uv pip freeze --python "$destination/python-runtime/venv/bin/python" \
  >"$destination/python-runtime/installed-distributions.txt"

(
  cd "$destination/runtime"
  PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$destination/runtime" \
    "$destination/python-runtime/venv/bin/python" \
    -m job_intel.product_search.gate_b_benchmark_v3 \
    export-runtime-manifest "$destination" "$commit"
)
sha256sum "$destination/runtime-manifest.json" \
  | awk '{print $1}' >"$destination/runtime-manifest.sha256"

bytecode_path="$(find "$destination/runtime" \
  \( -type d -name __pycache__ -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
  -print -quit)"
[[ -z "$bytecode_path" ]] || {
  echo "immutable runtime contains Python bytecode: $bytecode_path" >&2
  exit 64
}
chmod -R a-w "$destination/runtime" "$destination/python-runtime"
chmod -R a-w "$destination/runtime-identity" \
  "$destination/runtime-manifest.json" \
  "$destination/runtime-manifest.sha256"
