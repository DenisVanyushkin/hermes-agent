"""Closed, additive Gate B at-most-once benchmark vocabulary (v3)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Self

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic import model_validator

from job_intel.product_search.contracts import SHA256_PATTERN


DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/gate_b_benchmark.v3.yaml"
)
_ORDERED_CALL_CAP = 48
_PER_CALL_MAXIMUM_USD = Decimal("0.01")
_AGGREGATE_MAXIMUM_USD = Decimal("0.48")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GateBCallStateV3(str, Enum):
    PENDING = "pending"
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    SUCCESS = "success"
    TERMINAL_FAILURE = "terminal_failure"
    TERMINAL_UNKNOWN = "terminal_unknown"


class GateBTerminalKindV3(str, Enum):
    TERMINAL_FAILURE = "terminal_failure"
    TERMINAL_UNKNOWN = "terminal_unknown"


_TRANSITION_ACTORS = {
    (GateBCallStateV3.PENDING, GateBCallStateV3.RESERVED): frozenset({"runner"}),
    (GateBCallStateV3.RESERVED, GateBCallStateV3.PENDING): frozenset(
        {"owner_recovery"}
    ),
    (GateBCallStateV3.RESERVED, GateBCallStateV3.DISPATCHED): frozenset({"runner"}),
    (GateBCallStateV3.DISPATCHED, GateBCallStateV3.SUCCESS): frozenset({"runner"}),
    (
        GateBCallStateV3.DISPATCHED,
        GateBCallStateV3.TERMINAL_FAILURE,
    ): frozenset({"runner"}),
    (
        GateBCallStateV3.DISPATCHED,
        GateBCallStateV3.TERMINAL_UNKNOWN,
    ): frozenset({"runner"}),
}


def transition_allowed(
    source: GateBCallStateV3 | str,
    target: GateBCallStateV3 | str,
    *,
    actor: str,
) -> bool:
    """Return whether the closed v3 transition matrix authorizes this actor."""
    try:
        transition = (GateBCallStateV3(source), GateBCallStateV3(target))
    except ValueError:
        return False
    return actor in _TRANSITION_ACTORS.get(transition, frozenset())


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _require_utc(timestamp: AwareDatetime) -> AwareDatetime:
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC-aware")
    return timestamp


class GateBBenchmarkPolicyV3(_StrictFrozenModel):
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

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class GateBLaunchIdentityV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    run_id: str = Field(min_length=1)
    issued_at: AwareDatetime
    package_manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("issued_at")
    @classmethod
    def validate_issued_at(cls, value: AwareDatetime) -> AwareDatetime:
        return _require_utc(value)

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class GateBPackageManifestV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    package_id: str = Field(min_length=1)
    created_at: AwareDatetime
    ordered_input_sha256s: tuple[str, ...] = Field(
        min_length=_ORDERED_CALL_CAP,
        max_length=_ORDERED_CALL_CAP,
    )
    authority_sha256s: tuple[str, ...] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: AwareDatetime) -> AwareDatetime:
        return _require_utc(value)

    @field_validator("ordered_input_sha256s", "authority_sha256s")
    @classmethod
    def validate_sha256s(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not re.fullmatch(SHA256_PATTERN, value):
                raise ValueError("hashes must be lowercase SHA-256 values")
        return values

    @model_validator(mode="after")
    def validate_full_ordered_contract(self) -> Self:
        if len(set(self.ordered_input_sha256s)) != _ORDERED_CALL_CAP:
            raise ValueError("ordered input hashes must be unique")
        if self.authority_sha256s != tuple(sorted(set(self.authority_sha256s))):
            raise ValueError("authority hashes must be sorted and unique")
        return self

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


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
