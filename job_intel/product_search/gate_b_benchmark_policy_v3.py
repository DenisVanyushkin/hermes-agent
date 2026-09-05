"""Closed Gate B benchmark policy contract shared by evidence and runner code."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator


DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/gate_b_benchmark.v3.yaml"
)
_PER_CALL_MAXIMUM_USD = Decimal("0.01")
_AGGREGATE_MAXIMUM_USD = Decimal("0.48")
_COMPANY_FACT_DENY_PATTERNS = (
    r"\bwe\b",
    r"\bour\b",
    r"\bour company\b",
    r"\bthe company\b",
    r"\bglobal leader\b",
    r"\bmarket leader\b",
    r"\bplatform\b",
    r"\bcustomers?\b",
    r"\bclients?\b",
    r"\brevenue\b",
    r"\bmerchant volume\b",
    r"\bmarket share\b",
    r"\bfunding\b",
    r"\bseries [a-z]\b",
    r"\bemployees?\b",
    r"\boffices?\b",
    r"\bexpansion\b",
    r"\bfastest[- ]growing\b",
)


class GateBBenchmarkPolicyV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["3.0.0"]
    benchmark_kind: Literal["gate_b_at_most_once"]
    ordered_call_cap: Literal[48]
    per_call_maximum_usd: Decimal
    aggregate_maximum_usd: Decimal
    automatic_restart: Literal[False]
    post_dispatch_retry: Literal[False]
    ambiguous_post_dispatch_cost: Literal["conservative_maximum"]
    minimum_deliverable_results: Literal[43]
    maximum_terminal_unknown: Literal[5]
    minimum_manual_triage_accuracy: Literal["0.80"]
    company_authority_when_missing: Literal["unavailable"]
    company_claims_when_unavailable: Literal["forbidden"]
    company_fact_deny_patterns: tuple[str, ...]
    description_claim_admission: Literal["reviewed_hash_allowlist_only"]

    @field_validator("company_fact_deny_patterns", mode="before")
    @classmethod
    def normalize_yaml_deny_patterns(cls, value: object) -> tuple[str, ...] | object:
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return tuple(value)
        return value

    @field_validator("per_call_maximum_usd")
    @classmethod
    def validate_per_call_cost(cls, value: Decimal) -> Decimal:
        if value != _PER_CALL_MAXIMUM_USD:
            raise ValueError("per_call_maximum_usd must be exactly 0.01")
        return value

    @field_validator("aggregate_maximum_usd")
    @classmethod
    def validate_aggregate_cost(cls, value: Decimal) -> Decimal:
        if value != _AGGREGATE_MAXIMUM_USD:
            raise ValueError("aggregate_maximum_usd must be exactly 0.48")
        return value

    @field_validator("company_fact_deny_patterns")
    @classmethod
    def validate_company_fact_deny_patterns(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if value != _COMPANY_FACT_DENY_PATTERNS:
            raise ValueError(
                "company_fact_deny_patterns must match the reviewed v3 policy"
            )
        return value

    @property
    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def load_gate_b_benchmark_policy_v3(
    path_or_payload: Path | str | Mapping[str, Any] = (
        DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH
    ),
) -> GateBBenchmarkPolicyV3:
    if isinstance(path_or_payload, Mapping):
        payload = dict(path_or_payload)
    else:
        payload = yaml.safe_load(Path(path_or_payload).read_bytes())
    if not isinstance(payload, Mapping):
        raise ValueError("Gate B benchmark policy v3 must be a mapping")
    normalized = dict(payload)
    for field_name, expected in (
        ("per_call_maximum_usd", _PER_CALL_MAXIMUM_USD),
        ("aggregate_maximum_usd", _AGGREGATE_MAXIMUM_USD),
    ):
        if normalized.get(field_name) == str(expected):
            normalized[field_name] = expected
    return GateBBenchmarkPolicyV3.model_validate(normalized)
