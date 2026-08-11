#!/usr/bin/env bash
set -euo pipefail

manifest="${1:?usage: job_intel_product_search_probe.sh MANIFEST}"
python_path="${PRODUCT_SEARCH_PYTHON:?pinned PRODUCT_SEARCH_PYTHON is required}"
runtime_root="${PRODUCT_SEARCH_RUNTIME_ROOT:?pinned PRODUCT_SEARCH_RUNTIME_ROOT is required}"
manifest_python="$("$python_path" - "$manifest" <<'PY'
from pathlib import Path
import sys, yaml
print(yaml.safe_load(Path(sys.argv[1]).read_text())["python"]["executable_path"])
PY
)"
manifest_runtime="$("$python_path" - "$manifest" <<'PY'
from pathlib import Path
import sys, yaml
print(yaml.safe_load(Path(sys.argv[1]).read_text())["environment"]["import_root"])
PY
)"
[[ "$python_path" == "$manifest_python" ]] || { echo "pinned Python does not match manifest" >&2; exit 64; }
[[ "$runtime_root" == "$manifest_runtime" ]] || { echo "runtime root does not match manifest" >&2; exit 64; }

export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime_root"
"$python_path" -m job_intel.product_search.acquisition_probe validate-manifest "$manifest"
exec "$python_path" -m job_intel.product_search.acquisition_probe run-manifest "$manifest"
