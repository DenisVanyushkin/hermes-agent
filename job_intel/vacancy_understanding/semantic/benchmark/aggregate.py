"""Run-level aggregation (Step 5B, Slice 5B-2).

provider_benchmark_summary.json is derived exclusively from the persisted
manifest + per-case result rows on disk. There is no code path that feeds
in-memory counters into the summary: a resumed/partially-cached run
re-reads the same rows and therefore cannot double-count cases, tokens,
cost, or latency (step5b task §Resume).

State discipline (contract §6): every aggregate number is a NumericValue.
An `unknown` on any contributing case poisons the corresponding total to
`unknown` — a total that silently drops unmeasured cases would understate
cost, which is exactly the failure mode the states exist to prevent.

Percentiles use the nearest-rank method (index = ceil(p/100 * n) on the
sorted series): deterministic, interpolation-free, and defined for every
n >= 1. This choice is part of the metric identity — changing it after
results exist requires a new benchmark_id (contract §2).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional

from .models import (
    RUNNER_VERSION,
    BenchmarkCaseResult,
    BenchmarkManifest,
    BenchmarkSummary,
    CaseStatus,
    LatencyMode,
    LatencySeriesSummary,
    NumericState,
    NumericValue,
)

SUMMARY_FILENAME = "provider_benchmark_summary.json"


class AggregateError(RuntimeError):
    pass


def _nv(value: Optional[float], state: Optional[NumericState] = None) -> NumericValue:
    if state is not None:
        return NumericValue(state=state, value=value if state in (
            NumericState.known_zero, NumericState.known_value) else None)
    if value == 0:
        return NumericValue(state=NumericState.known_zero, value=0.0)
    return NumericValue(state=NumericState.known_value, value=float(value))


def _unknown() -> NumericValue:
    return NumericValue(state=NumericState.unknown)


def _na() -> NumericValue:
    return NumericValue(state=NumericState.not_applicable)


def _percentile_nearest_rank(sorted_values: list[float], p: float) -> float:
    rank = math.ceil(p / 100 * len(sorted_values))
    return sorted_values[max(rank, 1) - 1]


def _sum_token_field(rows: list[BenchmarkCaseResult], attr: str) -> NumericValue:
    if not rows:
        return _na()
    values = [getattr(r, attr) for r in rows]
    if any(v is None for v in values):
        return _unknown()  # a case that should have reported usage did not
    return _nv(sum(values))


def _sum_cost(rows: list[BenchmarkCaseResult]) -> NumericValue:
    contributing = [r for r in rows if r.cost_state != NumericState.not_applicable]
    if not contributing:
        return _na()
    if any(r.cost_state == NumericState.unknown for r in contributing):
        return _unknown()
    return _nv(sum(r.cost_usd for r in contributing))


def _divide(total: NumericValue, denominator: int) -> NumericValue:
    if denominator <= 0:
        return _na()
    if total.state in (NumericState.unknown, NumericState.not_applicable):
        return NumericValue(state=total.state)
    return _nv(total.value / denominator)


def _latency_series(values: list[float], accepted: int, *,
                    poisoned: bool = False) -> LatencySeriesSummary:
    if poisoned:
        return LatencySeriesSummary(
            case_count=len(values), latency_total_ms=_unknown(),
            latency_p50_ms=_unknown(), latency_p90_ms=_unknown(),
            latency_p95_ms=_unknown(), latency_p99_ms=_unknown(),
            latency_max_ms=_unknown(),
            latency_per_accepted_observation_ms=_unknown())
    if not values:
        return LatencySeriesSummary(
            case_count=0, latency_total_ms=_na(),
            latency_p50_ms=_na(), latency_p90_ms=_na(),
            latency_p95_ms=_na(), latency_p99_ms=_na(),
            latency_max_ms=_na(),
            latency_per_accepted_observation_ms=_na())
    ordered = sorted(values)
    total = sum(ordered)
    return LatencySeriesSummary(
        case_count=len(values),
        latency_total_ms=_nv(total),
        latency_p50_ms=_nv(_percentile_nearest_rank(ordered, 50)),
        latency_p90_ms=_nv(_percentile_nearest_rank(ordered, 90)),
        latency_p95_ms=_nv(_percentile_nearest_rank(ordered, 95)),
        latency_p99_ms=_nv(_percentile_nearest_rank(ordered, 99)),
        latency_max_ms=_nv(ordered[-1]),
        latency_per_accepted_observation_ms=(
            _nv(total / accepted) if accepted > 0 else _na()),
    )


def _load_rows(cases_dir: Path) -> list[BenchmarkCaseResult]:
    rows: list[BenchmarkCaseResult] = []
    if not cases_dir.exists():
        return rows
    for path in sorted(cases_dir.glob("*.result.json")):
        try:
            rows.append(BenchmarkCaseResult.model_validate(json.loads(path.read_text())))
        except Exception as exc:
            # A corrupt row EXPLICITLY blocks aggregation — silently skipping
            # it would publish a summary that claims to cover the run.
            raise AggregateError(f"corrupt case result {path.name}: {exc}") from exc
    return rows


def aggregate_run(out_dir: Path) -> BenchmarkSummary:
    manifest_path = Path(out_dir) / "manifest.json"
    if not manifest_path.exists():
        raise AggregateError(f"manifest not found: {manifest_path}")
    manifest = BenchmarkManifest.model_validate(json.loads(manifest_path.read_text()))
    rows = _load_rows(Path(out_dir) / "cases")

    ok_rows = [r for r in rows if r.status == CaseStatus.ok]

    # Wall-clock series, one per mode — never merged (contract §7).
    latency_by_mode: dict[str, LatencySeriesSummary] = {}
    for mode in (LatencyMode.live, LatencyMode.replay, LatencyMode.deterministic):
        mode_rows = [r for r in rows if r.latency_mode == mode and r.latency_ms is not None]
        values = [r.latency_ms for r in mode_rows]
        accepted = sum(r.observations_accepted for r in mode_rows)
        # The live series additionally carries recorded live-call latency
        # surfaced from replay-mode rows (their wall-clock stays in "replay").
        poisoned = False
        if mode == LatencyMode.live:
            extra_rows = [r for r in rows if r.latency_mode != LatencyMode.live
                          and r.live_latency_state != NumericState.not_applicable]
            poisoned = any(r.live_latency_state == NumericState.unknown
                           for r in extra_rows)
            known = [r for r in extra_rows
                     if r.live_latency_state in (NumericState.known_value,
                                                 NumericState.known_zero)]
            values = values + [r.live_latency_ms for r in known]
            accepted += sum(r.observations_accepted for r in known)
        latency_by_mode[mode.value] = _latency_series(values, accepted, poisoned=poisoned)

    cost_total = _sum_cost(rows)
    accepted_total = sum(r.observations_accepted for r in rows)

    summary = BenchmarkSummary(
        benchmark_id=manifest.benchmark_id,
        run_id=manifest.run_id,
        provider_id=manifest.provider_id,
        runner_version=RUNNER_VERSION,
        cases_total=len(rows),
        cases_succeeded=len(ok_rows),
        cases_failed=len(rows) - len(ok_rows),
        observations_emitted=sum(r.observations_emitted for r in rows),
        observations_accepted=accepted_total,
        observations_rejected=sum(r.observations_rejected for r in rows),
        zero_observation_cases=sum(1 for r in ok_rows if r.observations_emitted == 0),
        input_tokens_total=_sum_token_field(rows, "input_tokens"),
        output_tokens_total=_sum_token_field(rows, "output_tokens"),
        cost_usd_total=cost_total,
        cost_per_case=_divide(cost_total, len(rows)),
        cost_per_accepted_observation=_divide(cost_total, accepted_total),
        latency_by_mode=latency_by_mode,
    )

    summary_path = Path(out_dir) / SUMMARY_FILENAME
    tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary.model_dump(mode="json"), indent=2,
                              ensure_ascii=False, sort_keys=True))
    os.replace(tmp, summary_path)
    return summary
