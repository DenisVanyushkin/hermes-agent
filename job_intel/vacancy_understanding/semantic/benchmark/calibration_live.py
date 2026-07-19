"""Budgeted LLM calibration run (Step 5B, Slice 5B-4).

Orchestrates the common runner in bounded chunks (max_new_cases) and stops
hard on either spend-gate condition from the approved report:

- known cost across all datasets exceeds the cap;
- N consecutive failed cases (transport/parse trouble — do not burn budget
  on a broken pipe).

The budget guard sums KNOWN per-case costs (known_value/known_zero rows).
A case with unknown cost cannot be counted, so it also increments the
failure accounting path — the run never silently spends past the cap
because usage went missing.

Resume-safe by construction: the runner skips persisted rows, so an aborted
run re-invoked with the same arguments continues where it stopped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .aggregate import SUMMARY_FILENAME, aggregate_run
from .models import CaseStatus, NumericState
from .runner import run_benchmark

DEFAULT_CAP_USD = 3.0


class CalibrationAborted(RuntimeError):
    pass


def _known_cost(results) -> float:
    return sum(r.cost_usd for r in results
               if r.cost_state in (NumericState.known_value, NumericState.known_zero))


def _max_consecutive_failures(results) -> int:
    worst = streak = 0
    for r in results:
        streak = streak + 1 if r.status == CaseStatus.failed else 0
        worst = max(worst, streak)
    return worst


def run_llm_calibration(
    *, out_root: Path, provider_spec: dict[str, Any],
    dataset_specs: list[tuple[str, list[dict[str, Any]]]],
    cap_usd: float = DEFAULT_CAP_USD, chunk_size: int = 25,
    consecutive_failure_limit: int = 3, run_id: str = "r1",
    benchmark_id: str = "5b4-llm-calibration",
) -> dict[str, Any]:
    out_root = Path(out_root)
    spent_known = 0.0
    outcome: dict[str, Any] = {"datasets": {}, "cap_usd": cap_usd}

    for name, cases in dataset_specs:
        out_dir = out_root / name
        while True:
            _, results = run_benchmark(
                benchmark_id=f"{benchmark_id}-{name}", run_id=run_id,
                provider_spec=provider_spec, dataset_id=name, cases=cases,
                out_dir=out_dir, max_new_cases=chunk_size)
            dataset_cost = _known_cost(results)
            total_known = spent_known + dataset_cost
            if total_known > cap_usd:
                raise CalibrationAborted(
                    f"cap exceeded: known cost ${total_known:.4f} > cap ${cap_usd:.2f} "
                    f"(dataset {name}, {len(results)}/{len(cases)} cases persisted)")
            streak = _max_consecutive_failures(results)
            if streak >= consecutive_failure_limit:
                raise CalibrationAborted(
                    f"{streak} consecutive failed cases in dataset {name} "
                    f"(limit {consecutive_failure_limit}) — stopping per spend gate")
            if len(results) >= len(cases):
                break
        spent_known += dataset_cost
        summary = json.loads((out_dir / SUMMARY_FILENAME).read_text())
        outcome["datasets"][name] = {
            "cases_total": summary["cases_total"],
            "cases_failed": summary["cases_failed"],
            "observations_accepted": summary["observations_accepted"],
            "cost_usd_total": summary["cost_usd_total"],
            "summary_path": str(out_dir / SUMMARY_FILENAME),
        }

    outcome["known_cost_usd"] = spent_known
    return outcome
