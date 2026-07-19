"""Benchmark manifest and case-result schemas (Step 5B, Slice 5B-1).

Result/manifest schema only — no metric formulas here (Slice 5B-2). Every
numeric field that can be legitimately absent carries an explicit state
(NumericState) so "not measured" is never confused with "measured as zero"
(step5b-benchmark-contract.md §6).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

RUNNER_VERSION = "5b2.0.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericState(str, Enum):
    known_zero = "known_zero"
    known_value = "known_value"
    unknown = "unknown"
    not_applicable = "not_applicable"


class NumericValue(_Strict):
    """A number that can never be silently absent: `value` is meaningful only
    for the known_* states, and known_zero is structurally pinned to 0 so a
    "measured as zero" can never drift into carrying a nonzero number
    (step5b-benchmark-contract.md §6, состояния значений)."""

    state: NumericState
    value: Optional[float] = None

    @model_validator(mode="after")
    def _state_value_consistency(self) -> "NumericValue":
        if self.state == NumericState.known_zero:
            if self.value is None:
                self.value = 0.0
            elif self.value != 0.0:
                raise ValueError("known_zero requires value == 0")
        elif self.state == NumericState.known_value:
            if self.value is None:
                raise ValueError("known_value requires a value")
        elif self.value is not None:
            raise ValueError(f"{self.state.value} forbids a value")
        return self


class LatencyMode(str, Enum):
    live = "live"
    replay = "replay"
    deterministic = "deterministic"  # not LLM-timed at all; own bucket, never mixed with live/replay


class CaseStatus(str, Enum):
    ok = "ok"
    failed = "failed"


class BenchmarkManifest(_Strict):
    """Written atomically before the first case executes. Immutable once
    written: a resume that finds a mismatched field on any of the four
    identity axes (dataset / provider / metric contract / decision matrix)
    must block, never silently continue (step5b task §Resume and idempotency)."""

    benchmark_id: str
    run_id: str
    created_at: str
    git_commit: str

    provider_id: str
    provider_version: str
    provider_config_hash: str
    prompt_version: str
    model_requested: Optional[str] = None
    model_actual: Optional[str] = None
    transport: Optional[str] = None

    dataset_id: str
    dataset_hash: str
    dataset_size: int

    semantic_contract_version: str
    runtime_version: str
    decision_sot_version: str
    runner_version: str
    recording_format_version: Optional[str] = None

    temperature: Optional[float] = None
    retry_policy: str
    fallback_policy: str

    metric_contract_path: str
    metric_contract_hash: str
    decision_matrix_path: str
    decision_matrix_hash: str

    # Pricing is fixed per run and published here, never hardcoded — it may
    # legitimately differ between runs (e.g. 5B-4 vs 5B-7, contract §6).
    # It also participates in provider_config_hash, so a resume under a
    # different price is identity-blocked rather than silently mixed.
    price_input_usd_per_mtok: Optional[float] = None
    price_output_usd_per_mtok: Optional[float] = None
    pricing_source: Optional[str] = None


class BenchmarkCaseResult(_Strict):
    benchmark_id: str
    run_id: str
    case_id: str
    vacancy_id: Optional[str] = None
    vacancy_key: str

    provider_id: str
    status: CaseStatus

    observations_emitted: int
    observations_accepted: int
    observations_rejected: int
    rejection_codes: list[str] = Field(default_factory=list)

    semantic_hash: Optional[str] = None
    semantic_dump_path: Optional[str] = None
    decision_output_path: Optional[str] = None

    latency_ms: Optional[float] = None
    latency_mode: LatencyMode
    # Recorded live-call latency surfaced from the recording during replay.
    # Kept in its own field so the wall-clock replay timing above and the
    # original live timing can never end up in one percentile series
    # (contract §7). not_applicable for providers with no transport.
    live_latency_ms: Optional[float] = None
    live_latency_state: NumericState = NumericState.not_applicable

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_state: NumericState

    recording_path: Optional[str] = None
    error_code: Optional[str] = None

    started_at: str
    completed_at: str


class LatencySeriesSummary(_Strict):
    """Aggregate latency for ONE latency mode. Modes are separate series by
    construction — the aggregate never merges values across modes."""

    case_count: int
    latency_total_ms: NumericValue
    latency_p50_ms: NumericValue
    latency_p90_ms: NumericValue
    latency_p95_ms: NumericValue
    latency_p99_ms: NumericValue
    latency_max_ms: NumericValue
    latency_per_accepted_observation_ms: NumericValue


class BenchmarkSummary(_Strict):
    """provider_benchmark_summary.json — computed ONLY from persisted case
    rows (never from in-memory counters), so a resumed run re-derives the
    same numbers instead of double-counting. Deliberately timestamp-free:
    re-aggregating unchanged rows must be byte-identical."""

    benchmark_id: str
    run_id: str
    provider_id: str
    runner_version: str

    cases_total: int
    cases_succeeded: int
    cases_failed: int

    observations_emitted: int
    observations_accepted: int
    observations_rejected: int
    zero_observation_cases: int

    input_tokens_total: NumericValue
    output_tokens_total: NumericValue
    cost_usd_total: NumericValue
    cost_per_case: NumericValue
    cost_per_accepted_observation: NumericValue

    latency_by_mode: dict[str, LatencySeriesSummary]
