"""Deterministic full baseline (Step 5B, Slice 5B-3).

Runs DeterministicPhraseProvider over the four benchmark corpora through
the common runner — one run directory per dataset, each with its own
manifest, per-case rows, and provider_benchmark_summary.json. $0 by
construction (cost_known_zero); no network anywhere on this path.

The eligible corpus is snapshotted to <out_root>/datasets/eligible.jsonl
before running: the LLM slices (5B-6/5B-7) MUST consume that snapshot, not
a fresh DB scan, or the providers benchmark different datasets.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .aggregate import SUMMARY_FILENAME
from .datasets import (
    control_cases,
    decision_cases,
    eligible_cases,
    export_cases_jsonl,
    golden_cases,
)
from .runner import run_benchmark

BENCHMARK_ID = "5b3-deterministic-baseline"
DEFAULT_OUT_ROOT = Path("artifacts/semantic-benchmark/5b3-deterministic-baseline")
_BUILDERS = {"controls": control_cases, "golden": golden_cases,
             "decision": decision_cases}


def run_deterministic_baseline(
    out_root: Path = DEFAULT_OUT_ROOT,
    datasets: tuple[str, ...] = ("controls", "golden", "decision", "eligible"),
    db_path: Optional[str] = None,
    run_id: str = "r1",
) -> dict[str, dict[str, Any]]:
    out_root = Path(out_root)
    outcome: dict[str, dict[str, Any]] = {}
    for name in datasets:
        if name == "eligible":
            ds_id, cases = (eligible_cases(db_path=db_path) if db_path
                            else eligible_cases())
            export_cases_jsonl(cases, out_root / "datasets" / "eligible.jsonl")
        else:
            ds_id, cases = _BUILDERS[name]()
        manifest, _ = run_benchmark(
            benchmark_id=f"{BENCHMARK_ID}-{name}", run_id=run_id,
            provider_spec={"type": "deterministic"},
            dataset_id=ds_id, cases=cases, out_dir=out_root / name)
        summary = json.loads((out_root / name / SUMMARY_FILENAME).read_text())
        outcome[name] = {
            "dataset_id": ds_id,
            "dataset_hash": manifest.dataset_hash,
            "cases_total": summary["cases_total"],
            "cases_failed": summary["cases_failed"],
            "observations_accepted": summary["observations_accepted"],
            "zero_observation_cases": summary["zero_observation_cases"],
            "summary_path": str(out_root / name / SUMMARY_FILENAME),
        }
    return outcome


if __name__ == "__main__":
    result = run_deterministic_baseline()
    print(json.dumps(result, indent=2, ensure_ascii=False))
