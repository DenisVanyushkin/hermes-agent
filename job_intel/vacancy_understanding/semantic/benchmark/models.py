"""Benchmark manifest and case-result schemas (Step 5B, Slice 5B-1).

Result/manifest schema only — no metric formulas here (Slice 5B-2). Every
numeric field that can be legitimately absent carries an explicit state
(NumericState) so "not measured" is never confused with "measured as zero"
(step5b-benchmark-contract.md §6).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

RUNNER_VERSION = "5b1.0.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericState(str, Enum):
    known_zero = "known_zero"
    known_value = "known_value"
    unknown = "unknown"
    not_applicable = "not_applicable"


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

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_state: NumericState

    recording_path: Optional[str] = None
    error_code: Optional[str] = None

    started_at: str
    completed_at: str
