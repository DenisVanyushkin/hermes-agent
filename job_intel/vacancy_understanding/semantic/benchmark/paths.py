"""Where benchmark artifacts live (Step 5B, Slice 5B-3a).

NOT inside the repository. On 2026-07-20 an upstream-sync/recovery rewrote
`local/customizations` and the untracked `artifacts/` tree went with it,
destroying paid LLM recordings (5A smoke $0.09 + 5B-4 calibration $0.58)
and the pinned eligible-corpus snapshot — none of it reproducible, because
repeated live calls are not byte-identical even at temperature 0.

Benchmark output is expensive, long-lived evidence for a Provider Selection
Review; it must survive any git operation the repo undergoes. It therefore
lives on a plain filesystem path outside the working tree, overridable per
environment via JOB_INTEL_BENCHMARK_ARTIFACTS (absolute paths only — a
relative override would silently reintroduce the repo-relative failure).
"""
from __future__ import annotations

import os
from pathlib import Path

ARTIFACT_ROOT_ENV = "JOB_INTEL_BENCHMARK_ARTIFACTS"
DEFAULT_ARTIFACT_ROOT = Path("/var/lib/job-intel/benchmark-artifacts")


def artifact_root() -> Path:
    raw = os.environ.get(ARTIFACT_ROOT_ENV)
    if not raw:
        return DEFAULT_ARTIFACT_ROOT
    root = Path(raw)
    if not root.is_absolute():
        raise ValueError(
            f"{ARTIFACT_ROOT_ENV} must be an absolute path outside the repo "
            f"(got {raw!r}) — repo-relative artifact roots are destroyed by "
            f"upstream sync/recovery")
    return root
