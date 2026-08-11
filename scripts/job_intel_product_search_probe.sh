#!/usr/bin/env bash
set -euo pipefail

manifest="${1:?usage: job_intel_product_search_probe.sh MANIFEST}"
python_path="$(python3 - "$manifest" <<'PY'
from pathlib import Path
import sys, yaml
print(yaml.safe_load(Path(sys.argv[1]).read_text())["python"]["executable_path"])
PY
)"
runtime_root="$(python3 - "$manifest" <<'PY'
from pathlib import Path
import sys, yaml
print(yaml.safe_load(Path(sys.argv[1]).read_text())["environment"]["import_root"])
PY
)"

export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime_root"
"$python_path" -m job_intel.product_search.acquisition_probe validate-manifest "$manifest"
exec "$python_path" -m job_intel.product_search.acquisition_probe run-manifest "$manifest"
