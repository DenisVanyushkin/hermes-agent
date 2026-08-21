"""Offline one-row Gate B evidence walking skeleton.

This module is deliberately boring: it owns the smallest concrete pieces of
the Task 2 contracts and has no database, socket, credential, subprocess, or
provider-runtime dependency.  The live runner and 48-row binding arrive in
later tasks.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_intel.product_search.decision_v2 import DecisionRequestV2, DecisionResultV2, run_decision_v2
from job_intel.product_search.evidence_synthesis import EvidenceSynthesisStatus
from job_intel.product_search.gate_b_evidence_v3 import (
    ReviewedFragmentAllowlistV3,
    project_vacancy_evidence_v3,
    validate_provider_payload_v3,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
EVALUATOR_CONTRACT_SHA256 = hashlib.sha256(
    b"gate-b-gate-evaluator-v1"
).hexdigest()


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class RuntimeIdentity(_StrictFrozenModel):
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    shim_sha256: str = Field(pattern=SHA256_PATTERN)
    interpreter_sha256: str = Field(pattern=SHA256_PATTERN)
    stdlib_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    installed_distributions_sha256: str = Field(pattern=SHA256_PATTERN)
    installed_files_sha256: str = Field(pattern=SHA256_PATTERN)
    sys_path_sha256: str = Field(pattern=SHA256_PATTERN)
    native_extensions_sha256: str = Field(pattern=SHA256_PATTERN)
    shared_libraries_sha256: str = Field(pattern=SHA256_PATTERN)


class AuthorityIdentity(_StrictFrozenModel):
    model_sha256: str = Field(pattern=SHA256_PATTERN)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    profile_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_v2_sha256: str = Field(pattern=SHA256_PATTERN)
    pricing_sha256: str = Field(pattern=SHA256_PATTERN)
    source_authority_sha256s: dict[str, str]

    @model_validator(mode="after")
    def validate_source_hashes(self) -> AuthorityIdentity:
        if any(
            len(key) == 0 or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for key, value in self.source_authority_sha256s.items()
        ):
            raise ValueError("source_authority_sha256s must contain lowercase SHA-256 values")
        return self


class Limits(_StrictFrozenModel):
    ordered_call_cap: Literal[48]
    per_call_maximum_usd: Decimal
    aggregate_maximum_usd: Decimal


class EvidenceManifestRow(_StrictFrozenModel):
    ordinal: int = Field(ge=0, lt=48)
    corpus_key: str = Field(min_length=1)
    raw_sha256: str = Field(pattern=SHA256_PATTERN)
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    projection_sha256: str = Field(pattern=SHA256_PATTERN)


class EvidenceManifest(_StrictFrozenModel):
    schema_version: Literal["gate-b-evidence-manifest-v1"]
    run_id: str = Field(pattern=r"^gate-b-evidence-v1-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime
    benchmark_kind: Literal["gate_b_description_evidence"]
    row_count: Literal[48]
    rows: tuple[EvidenceManifestRow, ...]
    runtime: RuntimeIdentity
    authorities: AuthorityIdentity
    limits: Limits

    @model_validator(mode="after")
    def validate_identity(self) -> EvidenceManifest:
        if len(self.rows) != self.row_count:
            raise ValueError("manifest row_count does not match rows")
        if tuple(row.ordinal for row in self.rows) != tuple(range(self.row_count)):
            raise ValueError("manifest ordinals must be contiguous")
        body = self.model_dump(mode="json")
        body.pop("manifest_sha256")
        # created_at is audit chronology, not content identity: identical
        # rebuilds must retain the same manifest hash/run identity.
        body.pop("created_at")
        if _sha256(_canonical_bytes(body)) != self.manifest_sha256:
            raise ValueError("manifest_sha256 does not match canonical identity body")
        return self

    def row(self, ordinal: int) -> EvidenceManifestRow:
        if not 0 <= ordinal < self.row_count:
            raise ValueError("manifest ordinal is outside the 48-row contract")
        return self.rows[ordinal]

    def row_ref(self, ordinal: int) -> ManifestRef:
        row = self.row(ordinal)
        return ManifestRef(
            run_id=self.run_id,
            manifest_sha256=self.manifest_sha256,
            ordinal=row.ordinal,
            input_sha256=row.input_sha256,
            projection_sha256=row.projection_sha256,
        )


class ManifestRef(_StrictFrozenModel):
    run_id: str = Field(pattern=r"^gate-b-evidence-v1-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    ordinal: int = Field(ge=0, lt=48)
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    projection_sha256: str = Field(pattern=SHA256_PATTERN)


class JournalState(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    SUCCESS = "success"
    TERMINAL_FAILURE = "terminal_failure"
    TERMINAL_UNKNOWN = "terminal_unknown"


class TerminalOutcome(str, Enum):
    SUCCESS = "success"
    TERMINAL_FAILURE = "terminal_failure"
    TERMINAL_UNKNOWN = "terminal_unknown"


class DispatchReceipt(_StrictFrozenModel):
    manifest_ref: ManifestRef
    sequence: int = Field(ge=0)


class JournalEntry(_StrictFrozenModel):
    manifest_ref: ManifestRef
    sequence: int = Field(ge=0)
    state: JournalState
    recording_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    measured_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    conservative_cost_usd: Decimal = Field(ge=Decimal("0"))


class AppendOnlyJournal:
    """Small append/fsync journal used by the walking skeleton."""

    def __init__(self, manifest: EvidenceManifest, path: Path) -> None:
        self.manifest = manifest
        self.path = path
        self._entries: dict[int, JournalEntry] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise ValueError("walking-skeleton journal must start empty")
        self.path.touch(mode=0o600, exist_ok=False)

    @classmethod
    def open(cls, manifest: EvidenceManifest, path: Path) -> AppendOnlyJournal:
        return cls(manifest, path)

    def _validate_ref(self, ref: ManifestRef) -> None:
        if ref != self.manifest.row_ref(ref.ordinal):
            raise ValueError("journal manifest reference mismatch")

    def _append(self, payload: dict[str, Any]) -> None:
        encoded = _canonical_bytes(payload) + b"\n"
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            import os

            os.fsync(stream.fileno())

    def state(self, ordinal: int) -> JournalState:
        entry = self._entries.get(ordinal)
        return entry.state if entry else JournalState.PENDING

    def append_pre_dispatch(self, ref: ManifestRef) -> DispatchReceipt:
        self._validate_ref(ref)
        if self.state(ref.ordinal) is not JournalState.PENDING:
            raise ValueError("journal row is already claimed")
        sequence = len(self._entries)
        entry = JournalEntry(
            manifest_ref=ref,
            sequence=sequence,
            state=JournalState.DISPATCHED,
            conservative_cost_usd=Decimal("0"),
        )
        self._append({"event": "dispatch", **entry.model_dump(mode="json")})
        self._entries[ref.ordinal] = entry
        return DispatchReceipt(manifest_ref=ref, sequence=sequence)

    def commit_terminal(
        self,
        receipt: DispatchReceipt,
        outcome: TerminalOutcome,
        recording_sha256: str,
        measured_cost_usd: Decimal | None,
        conservative_cost_usd: Decimal,
    ) -> None:
        self._validate_ref(receipt.manifest_ref)
        if self.state(receipt.manifest_ref.ordinal) is not JournalState.DISPATCHED:
            raise ValueError("journal terminal commit requires dispatched state")
        entry = JournalEntry(
            manifest_ref=receipt.manifest_ref,
            sequence=receipt.sequence,
            state=JournalState(outcome.value),
            recording_sha256=recording_sha256,
            measured_cost_usd=measured_cost_usd,
            conservative_cost_usd=conservative_cost_usd,
        )
        self._append({"event": "terminal", **entry.model_dump(mode="json")})
        self._entries[receipt.manifest_ref.ordinal] = entry

    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries[index] for index in sorted(self._entries))


class RecordingRef(_StrictFrozenModel):
    manifest_ref: ManifestRef
    recording_sha256: str = Field(pattern=SHA256_PATTERN)


class SealedRecording(_StrictFrozenModel):
    manifest_ref: ManifestRef
    request_bytes: bytes
    response_bytes: bytes
    outcome: TerminalOutcome
    metadata: dict[str, str] = {}


class ReplayObservation(_StrictFrozenModel):
    manifest_ref: ManifestRef
    request_bytes: bytes
    response_bytes: bytes
    outcome: TerminalOutcome


class RecordingStore:
    """Create-once sealed bytes with provider-free replay."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, recording_sha256: str) -> Path:
        return self.root / f"{recording_sha256}.json"

    def save_exclusive(
        self,
        record: SealedRecording,
    ) -> RecordingRef:
        payload = {
            "schema_version": "gate-b-sealed-recording-v1",
            "manifest_ref": record.manifest_ref.model_dump(mode="json"),
            "request_b64": base64.b64encode(record.request_bytes).decode("ascii"),
            "response_b64": base64.b64encode(record.response_bytes).decode("ascii"),
            "outcome": record.outcome.value,
            "metadata": record.metadata,
        }
        encoded = _canonical_bytes(payload)
        recording_sha256 = _sha256(encoded)
        path = self._path(recording_sha256)
        try:
            with path.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                import os

                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError("recording hash collision with different bytes")
        return RecordingRef(
            manifest_ref=record.manifest_ref,
            recording_sha256=recording_sha256,
        )

    def bytes_for(self, ref: RecordingRef) -> bytes:
        encoded = self._path(ref.recording_sha256).read_bytes()
        if _sha256(encoded) != ref.recording_sha256:
            raise ValueError("recording bytes do not match recording hash")
        return encoded

    def replay(self, ref: RecordingRef) -> ReplayObservation:
        payload = json.loads(self.bytes_for(ref))
        loaded_ref = ManifestRef.model_validate(payload["manifest_ref"])
        if loaded_ref != ref.manifest_ref:
            raise ValueError("recording manifest reference mismatch")
        return ReplayObservation(
            manifest_ref=loaded_ref,
            request_bytes=base64.b64decode(payload["request_b64"]),
            response_bytes=base64.b64decode(payload["response_b64"]),
            outcome=TerminalOutcome(payload["outcome"]),
        )


class MeasurementReport(_StrictFrozenModel):
    expected_row_count: int = Field(ge=1, le=48)
    observed_row_count: int = Field(ge=0, le=48)
    deliverable_count: int = Field(ge=0, le=48)
    terminal_unknown_count: int = Field(ge=0, le=48)
    adjudicated_count: int = Field(ge=0, le=48)
    adjudication_denominator: int = Field(ge=0, le=48)
    adjudicated_correct: int = Field(ge=0, le=48)


class AdjudicationSet(_StrictFrozenModel):
    audited_count: int = Field(ge=0, le=48)
    denominator: int = Field(ge=0, le=48)
    correct_count: int = Field(ge=0, le=48)


class GateDecisionKind(str, Enum):
    PROCEED_TO_SHADOW = "proceed_to_shadow"
    REVISE = "revise"
    REFUSE = "refuse"


class GateDecision(_StrictFrozenModel):
    run_id: str = Field(pattern=r"^gate-b-evidence-v1-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluator_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    measurement_status: Literal["complete", "incomplete"]
    decision: GateDecisionKind
    violated_rules: tuple[str, ...]


class GateEvaluator:
    @staticmethod
    def evaluate(
        manifest: EvidenceManifest,
        measurements: MeasurementReport,
        adjudication: AdjudicationSet,
    ) -> GateDecision:
        if measurements.observed_row_count != measurements.expected_row_count:
            return GateDecision(
                run_id=manifest.run_id,
                manifest_sha256=manifest.manifest_sha256,
                evaluator_contract_sha256=EVALUATOR_CONTRACT_SHA256,
                measurement_status="incomplete",
                decision=GateDecisionKind.REVISE,
                violated_rules=("collection_incomplete",),
            )
        if (
            adjudication.audited_count != measurements.adjudicated_count
            or adjudication.denominator != measurements.adjudication_denominator
            or adjudication.correct_count != measurements.adjudicated_correct
            or adjudication.denominator != measurements.expected_row_count
        ):
            return GateDecision(
                run_id=manifest.run_id,
                manifest_sha256=manifest.manifest_sha256,
                evaluator_contract_sha256=EVALUATOR_CONTRACT_SHA256,
                measurement_status="incomplete",
                decision=GateDecisionKind.REVISE,
                violated_rules=("adjudication_incomplete",),
            )
        violated: list[str] = []
        if measurements.deliverable_count < 43:
            violated.append("minimum_deliverable_results")
        if measurements.terminal_unknown_count > 5:
            violated.append("maximum_terminal_unknown")
        if (
            adjudication.denominator == 0
            or Decimal(adjudication.correct_count) / Decimal(adjudication.denominator)
            < Decimal("0.80")
        ):
            violated.append("minimum_manual_triage_accuracy")
        return GateDecision(
            run_id=manifest.run_id,
            manifest_sha256=manifest.manifest_sha256,
            evaluator_contract_sha256=EVALUATOR_CONTRACT_SHA256,
            measurement_status="complete",
            decision=(GateDecisionKind.REFUSE if violated else GateDecisionKind.PROCEED_TO_SHADOW),
            violated_rules=tuple(sorted(violated)),
        )


class GovernedProvider(Protocol):
    def dispatch(self, payload: dict[str, object]) -> Mapping[str, object]: ...


class OneRowResult(_StrictFrozenModel):
    manifest_ref: ManifestRef
    validation_status: EvidenceSynthesisStatus | None
    recording_ref: RecordingRef
    recording_bytes: bytes
    decision: DecisionResultV2
    gate_decision: GateDecision


def run_one_row(
    *,
    manifest: EvidenceManifest,
    ordinal: int,
    record: Mapping[str, object],
    raw: Mapping[str, object],
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
    provider: GovernedProvider,
    journal: AppendOnlyJournal,
    recordings: RecordingStore,
    decision_request_factory: Callable[[dict[str, object], ManifestRef], DecisionRequestV2],
) -> OneRowResult:
    """Project, validate, record, replay-reference, and decide one row offline."""
    row = manifest.row(ordinal)
    projected = project_vacancy_evidence_v3(record, raw, reviewed_allowlist)
    projection_sha256 = _sha256(_canonical_bytes(projected.model_dump(mode="json")))
    if projection_sha256 != row.projection_sha256:
        raise ValueError("projection hash does not match manifest row")
    request_payload = projected.provider_payload()
    request_bytes = _canonical_bytes(request_payload)
    if _sha256(request_bytes) != row.input_sha256:
        raise ValueError("provider input hash does not match manifest row")
    ref = manifest.row_ref(ordinal)

    # This append/fsync is intentionally before the fake transport call.
    receipt = journal.append_pre_dispatch(ref)
    response_payload = dict(provider.dispatch(request_payload))
    response_bytes = _canonical_bytes(response_payload)
    validation_status = validate_provider_payload_v3(
        response_payload,
        synthesis_input=projected,
        reviewed_allowlist=reviewed_allowlist,
    )
    if validation_status is not None:
        outcome = TerminalOutcome.TERMINAL_FAILURE
    else:
        outcome = TerminalOutcome.SUCCESS
    recording_ref = recordings.save_exclusive(
        SealedRecording(
            manifest_ref=ref,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            outcome=outcome,
            metadata={
                "input_sha256": row.input_sha256,
                "projection_sha256": row.projection_sha256,
                "response_sha256": _sha256(response_bytes),
                "validator": "gate_b_evidence_v3",
            },
        ),
    )
    journal.commit_terminal(
        receipt,
        outcome,
        recording_ref.recording_sha256,
        Decimal("0"),
        Decimal("0"),
    )
    if validation_status is not None:
        raise ValueError(f"provider payload rejected: {validation_status.value}")

    decision_request = decision_request_factory(response_payload, ref)
    decision = run_decision_v2(decision_request)
    measurements = MeasurementReport(
        expected_row_count=manifest.row_count,
        observed_row_count=1,
        deliverable_count=1,
        terminal_unknown_count=0,
        adjudicated_count=1,
        adjudication_denominator=1,
        adjudicated_correct=1,
    )
    gate_decision = GateEvaluator.evaluate(
        manifest,
        measurements,
        AdjudicationSet(audited_count=1, denominator=1, correct_count=1),
    )
    return OneRowResult(
        manifest_ref=ref,
        validation_status=validation_status,
        recording_ref=recording_ref,
        recording_bytes=recordings.bytes_for(recording_ref),
        decision=decision,
        gate_decision=gate_decision,
    )
