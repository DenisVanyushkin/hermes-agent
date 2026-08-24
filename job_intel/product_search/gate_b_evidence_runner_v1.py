"""Gate B evidence pipeline over the governed structured-call boundary.

This module owns the manifest, foreground dispatch ledger, sealed recording
and decision/adjudication evidence stores, the collection runner, and the
post-collection gate evaluator. Collection publishes per-row Decision v2
evidence; the separate evaluator produces the run-level gate decision.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum
import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol
from dataclasses import dataclass

import yaml

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_intel.product_search.contracts import ImmutableArtifactRef

if TYPE_CHECKING:
    from job_intel.product_search.gate_b_runtime_v1 import (
        AuthorityInputs,
        FrozenRuntime,
        SourceArtifact,
    )

from job_intel.product_search.decision_v2 import (
    DecisionAuthorityInputsV2,
    DecisionImmutableReferencesV2,
    DecisionRequestV2,
    DecisionResultV2,
    LoadedDecisionPolicyV2,
    SelectionEvidenceV2,
    StageEvidenceV2,
    canonical_decision_bytes,
    company_thesis_input_ref,
    load_decision_policy,
    run_decision_v2,
)
from job_intel.product_search.evidence_synthesis import (
    EvidenceSynthesisInputV2,
    EvidenceSynthesisResultV2,
    EvidenceSynthesisStatus,
    synthesis_input_sha256,
)
from job_intel.product_search.company_evidence import CompanyThesisInputV1
from job_intel.product_search.company_evidence import (
    load_company_thesis_input,
)
from job_intel.product_search.gate_b_benchmark_policy_v3 import (
    GateBBenchmarkPolicyV3,
    load_gate_b_benchmark_policy_v3,
)
from job_intel.product_search.gate_b_evidence_v3 import (
    CompanyEvidenceCatalogV3,
    ReviewedFragmentAllowlistV3,
    load_reviewed_fragment_allowlist_v3,
    project_vacancy_evidence_v3,
    validate_reviewed_fragment_allowlist_corpus_v3,
    validate_provider_payload_v3,
    load_company_evidence_catalog_v3,
    resolve_company_authority_v3,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMProviderError,
    RECORDING_FORMAT_VERSION as SEMANTIC_RECORDING_FORMAT_VERSION,
    RecordingStore as SemanticRecordingStore,
    build_live_llm_provider,
)
from job_intel.product_search.gate_b_spend_record_v1 import SpendRecordStore


SHA256_PATTERN = r"^[0-9a-f]{64}$"
EVALUATOR_CONTRACT_VERSION = "gate-b-gate-evaluator-v2"
SUPERVISED_COLLECTION_SPINE_INVARIANT = "supervised_collection_spine_v1"


def _build_committed_budget_reserver(
    manifest: EvidenceManifest,
) -> Callable[[Decimal], object]:
    root_value = os.environ.get("GATE_B_SPEND_RECORD_ROOT")
    if not root_value:
        raise ValueError("committed_budget_record_root_required")
    spend_record = SpendRecordStore.open(
        root=Path(root_value), manifest_sha256=manifest.manifest_sha256
    )
    required_cents = int(
        (manifest.limits.per_call_maximum_usd * Decimal("100")).to_integral_exact()
    )
    if spend_record.remaining_cents < required_cents:
        raise ValueError("committed_budget_exhausted")

    def reserve(amount: Decimal) -> object:
        cents = int((amount * Decimal("100")).to_integral_exact())
        completed = subprocess.run(
            [
                "sudo",
                "-n",
                sys.executable,
                "-m",
                "job_intel.product_search.gate_b_spend_record_v1",
                "reserve",
                "--root",
                root_value,
                "--manifest-sha256",
                manifest.manifest_sha256,
                "--amount-cents",
                str(cents),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            raise ValueError("committed_budget_reservation_failed")
        return completed.stdout.strip()

    return reserve


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


@dataclass(frozen=True)
class _TransportRecordReceipt:
    """Immutable in-memory proof of one sealed generic transport record."""

    input_hash: str
    record_sha256: str
    record_bytes: bytes

    @property
    def record(self) -> dict[str, object]:
        parsed = json.loads(self.record_bytes)
        if not isinstance(parsed, dict):
            raise ValueError("transport_receipt_record_invalid")
        return parsed


def _evaluator_contract_sha256(policy: GateBBenchmarkPolicyV3) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "evaluator_version": EVALUATOR_CONTRACT_VERSION,
                "policy": policy.model_dump(mode="json"),
            }
        )
    )


class RuntimeIdentity(_StrictFrozenModel):
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_tree_sha256: str = Field(pattern=SHA256_PATTERN)
    shim_sha256: str = Field(pattern=SHA256_PATTERN)
    interpreter_sha256: str = Field(pattern=SHA256_PATTERN)
    stdlib_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    installed_distributions_sha256: str = Field(pattern=SHA256_PATTERN)
    installed_files_sha256: str = Field(pattern=SHA256_PATTERN)
    sys_path_sha256: str = Field(pattern=SHA256_PATTERN)
    native_extensions_sha256: str = Field(pattern=SHA256_PATTERN)
    shared_libraries_sha256: str = Field(pattern=SHA256_PATTERN)
    shared_library_provenance: dict[str, str] = Field(default_factory=dict)


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
    decision_clock: datetime
    corpus_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
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
        # created_at is audit chronology, not content identity. decision_clock
        # is the deterministic timestamp used in Decision bytes and is bound.
        body.pop("created_at")
        if self.corpus_sha256 is None:
            body.pop("corpus_sha256")
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


@dataclass(frozen=True)
class DecisionRequestFactoryContextV1:
    """Explicit composition inputs for the production Decision v2 factory."""

    response_payload: dict[str, object]
    projected: EvidenceSynthesisInputV2
    manifest_ref: ManifestRef
    raw: dict[str, object]
    provider_record: dict[str, object]
    validation_status: EvidenceSynthesisStatus | None
    decision_policy: LoadedDecisionPolicyV2
    decision_clock: datetime
    company_thesis_input: CompanyThesisInputV1 | None = None


def _required_provider_value(record: Mapping[str, object], name: str) -> object:
    value = record.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"provider_record_missing:{name}")
    return value


def build_decision_request_v2(
    *,
    response_payload: Mapping[str, object],
    projected: EvidenceSynthesisInputV2,
    provider_input_sha256: str,
    raw: Mapping[str, object],
    provider_record: Mapping[str, object],
    validation_status: EvidenceSynthesisStatus | None,
    decision_policy: LoadedDecisionPolicyV2,
    decision_clock: datetime,
    company_thesis_input: CompanyThesisInputV1 | None = None,
) -> DecisionRequestV2:
    """Build a fully bound DecisionRequest from one collected provider result."""
    # The v3 validator returns None for a valid payload; non-None is a
    # fail-closed status.  Do not turn the normal success path into refusal.
    status = validation_status or EvidenceSynthesisStatus.DELIVERABLE
    deliverable = validation_status is None
    output_sha256 = str(_required_provider_value(provider_record, "output_sha256"))
    synthesis_payload = dict(response_payload)
    synthesis_payload["schema_version"] = str(
        _required_provider_value(provider_record, "schema_version")
    )
    expected_envelope = {
        "status": status.value,
        "deliverable": deliverable,
        "failure_reason": (
            None if deliverable else f"provider_validation:{status.value}"
        ),
        "company_authority_status": (
            projected.assessment_input.company_authority_status.value
        ),
    }
    for field_name, expected_value in expected_envelope.items():
        if field_name in synthesis_payload and synthesis_payload[field_name] != expected_value:
            raise ValueError(f"provider_response_envelope_mismatch:{field_name}")
    synthesis_payload.update(expected_envelope)
    if not deliverable:
        synthesis_payload.update(
            {
                "claims": [],
                "conflicts": [],
                "question_candidates": [],
            }
        )
    synthesis_payload["metadata"] = {
        "provider_id": str(_required_provider_value(provider_record, "provider_id")),
        "provider_version": str(
            _required_provider_value(provider_record, "provider_version")
        ),
        "model_id": str(_required_provider_value(provider_record, "model_id")),
        "semantic_prompt_version": str(
            _required_provider_value(provider_record, "semantic_prompt_version")
        ),
        "prompt_version": str(
            _required_provider_value(provider_record, "prompt_version")
        ),
        "schema_version": synthesis_payload["schema_version"],
        "latency_ms": int(_required_provider_value(provider_record, "latency_ms")),
        "cost_usd": (
            None
            if provider_record.get("cost_usd") is None
            else str(provider_record["cost_usd"])
        ),
        "input_sha256": provider_input_sha256,
        "output_sha256": output_sha256,
    }
    synthesis = EvidenceSynthesisResultV2.model_validate(synthesis_payload)
    authority = projected.company_authority
    bundle = getattr(authority, "company_evidence_bundle", None)
    if bundle is None:
        raise ValueError("company_evidence_bundle_required")
    bundle_ref = ImmutableArtifactRef(
        artifact_id=bundle.bundle_id,
        version=bundle.schema_version,
        sha256=bundle.content_sha256,
    )
    policy_hashes = decision_policy.policy.authority_hashes
    refs = projected.assessment_input.references
    references = DecisionImmutableReferencesV2(
        **policy_hashes.model_dump(),
        decision_contract_sha256=decision_policy.source_sha256,
        semantic_contract_sha256=refs.semantic_contract_ref.sha256,
        evidence_snapshot_sha256=refs.evidence_snapshot_ref.sha256,
        company_evidence_bundle_sha256=bundle_ref.sha256,
        provider_input_sha256=provider_input_sha256,
        provider_output_sha256=output_sha256,
    )
    stages = StageEvidenceV2(
        raw_observed=bool(raw),
        identity_resolved=bool(raw.get("company")) and authority.status == "available",
        duplicates_consolidated=True,
        freshness_confirmed=bool(raw.get("posted_at")),
        role_identified=bool(raw.get("title")),
        company_identified=bool(raw.get("company")) and authority.status == "available",
        location_and_work_format_identified=bool(raw.get("location")),
        material_responsibilities_identified=bool(
            projected.assessment_input.dimensions.mandate_fit.evidence_refs
        ),
        known_feasibility_constraints_identified=bool(
            projected.assessment_input.dimensions.feasibility.evidence_refs
        ),
    )
    return DecisionRequestV2(
        schema_version="2.0.0",
        assessment_id=projected.assessment_input.assessment_id,
        stages=stages,
        references=references,
        authority_inputs=DecisionAuthorityInputsV2(
            assessment_references=refs,
            company_evidence_bundle_ref=bundle_ref,
            company_thesis_input_ref=(
                company_thesis_input_ref(company_thesis_input)
                if company_thesis_input is not None
                else None
            ),
        ),
        synthesis=synthesis,
        selection=SelectionEvidenceV2(),
        company_action=None,
        urgency_evidence=None,
        daily_digest_at=decision_clock,
        assessed_at=decision_clock,
        evaluated_at=decision_clock,
    )


def build_decision_request_from_context_v2(
    context: DecisionRequestFactoryContextV1,
) -> DecisionRequestV2:
    """Adapt the positional runtime factory contract to the V2 builder."""
    return build_decision_request_v2(
        response_payload=context.response_payload,
        projected=context.projected,
        provider_input_sha256=context.manifest_ref.input_sha256,
        raw=context.raw,
        provider_record=context.provider_record,
        validation_status=context.validation_status,
        decision_policy=context.decision_policy,
        decision_clock=context.decision_clock,
        company_thesis_input=context.company_thesis_input,
    )


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


class NoDurableAccounting:
    """Explicit test-only opt-out from the production spend record."""

    def __call__(self, amount: Decimal) -> None:
        del amount


class ForegroundDispatchLedger:
    """Process-local dispatch ledger for the supervised run.

    This deliberately has no file, reopen, recovery, or replay path.  It
    keeps the canonical provider-record anchor and the no-duplicate/cap
    invariants alive for one foreground process; restart durability belongs to
    the retired unattended design and is not promised here.
    """

    def __init__(
        self,
        manifest: EvidenceManifest,
        *,
        committed_budget_reserver: Callable[[Decimal], object],
    ) -> None:
        self.manifest = manifest
        self._entries: dict[int, JournalEntry] = {}
        self._committed_budget_reserver = committed_budget_reserver

    def _validate_ref(self, ref: ManifestRef) -> None:
        if ref != self.manifest.row_ref(ref.ordinal):
            raise ValueError("dispatch manifest reference mismatch")

    def state(self, ordinal: int) -> JournalState:
        entry = self._entries.get(ordinal)
        return entry.state if entry else JournalState.PENDING

    def append_pre_dispatch(self, ref: ManifestRef) -> DispatchReceipt:
        self._validate_ref(ref)
        if len(self._entries) >= self.manifest.limits.ordered_call_cap:
            raise ValueError("call_cap_exhausted")
        if self.state(ref.ordinal) is not JournalState.PENDING:
            raise ValueError("dispatch row is already claimed")
        conservative_cost = self.manifest.limits.per_call_maximum_usd
        reserved = sum(entry.conservative_cost_usd for entry in self._entries.values())
        if reserved + conservative_cost > self.manifest.limits.aggregate_maximum_usd:
            raise ValueError("spend_cap_exhausted")
        self._committed_budget_reserver(conservative_cost)
        sequence = len(self._entries)
        entry = JournalEntry(
            manifest_ref=ref,
            sequence=sequence,
            state=JournalState.DISPATCHED,
            conservative_cost_usd=conservative_cost,
        )
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
        current = self._entries.get(receipt.manifest_ref.ordinal)
        if current is None or current.state is JournalState.PENDING:
            raise ValueError("dispatch terminal commit requires dispatched state")
        entry = JournalEntry(
            manifest_ref=receipt.manifest_ref,
            sequence=receipt.sequence,
            state=JournalState(outcome.value),
            recording_sha256=recording_sha256,
            measured_cost_usd=measured_cost_usd,
            conservative_cost_usd=conservative_cost_usd,
        )
        if current.state is not JournalState.DISPATCHED:
            if current == entry:
                return
            raise ValueError("terminal commit conflict")
        if measured_cost_usd is not None and measured_cost_usd > conservative_cost_usd:
            raise ValueError("measured cost exceeds conservative cost")
        other_cost = sum(
            item.conservative_cost_usd
            for ordinal, item in self._entries.items()
            if ordinal != receipt.manifest_ref.ordinal
        )
        if other_cost + conservative_cost_usd > self.manifest.limits.aggregate_maximum_usd:
            raise ValueError("spend_cap_exhausted")
        self._entries[receipt.manifest_ref.ordinal] = entry

    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries[index] for index in sorted(self._entries))

class RecordingRef(_StrictFrozenModel):
    manifest_ref: ManifestRef
    recording_sha256: str = Field(pattern=SHA256_PATTERN)


class DecisionEvidenceRef(_StrictFrozenModel):
    manifest_ref: ManifestRef
    decision_sha256: str = Field(pattern=SHA256_PATTERN)


class DecisionEvidenceStore:
    """Create-once canonical Decision v2 bytes for offline adjudication."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, manifest_ref: ManifestRef, decision_sha256: str) -> Path:
        storage_identity = {
            "manifest_ref": manifest_ref.model_dump(mode="json"),
            "decision_sha256": decision_sha256,
        }
        storage_key = hashlib.sha256(_canonical_bytes(storage_identity)).hexdigest()
        return self.root / f"{storage_key}.json"

    def save_exclusive(
        self,
        manifest_ref: ManifestRef,
        decision_bytes: bytes,
    ) -> DecisionEvidenceRef:
        decision_sha256 = _sha256(decision_bytes)
        payload = {
            "schema_version": "gate-b-decision-evidence-v1",
            "manifest_ref": manifest_ref.model_dump(mode="json"),
            "decision_sha256": decision_sha256,
            "decision_b64": base64.b64encode(decision_bytes).decode("ascii"),
        }
        encoded = _canonical_bytes(payload)
        path = self._path(manifest_ref, decision_sha256)
        try:
            with path.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError("decision evidence collision with different bytes")
        return DecisionEvidenceRef(
            manifest_ref=manifest_ref,
            decision_sha256=decision_sha256,
        )

    def bytes_for(self, ref: DecisionEvidenceRef) -> bytes:
        try:
            encoded = self._path(ref.manifest_ref, ref.decision_sha256).read_bytes()
        except FileNotFoundError as exc:
            raise ValueError("decision evidence missing") from exc
        try:
            payload = json.loads(encoded)
            decision_bytes = base64.b64decode(payload["decision_b64"], validate=True)
            loaded_ref = ManifestRef.model_validate(payload["manifest_ref"])
        except Exception as exc:
            raise ValueError("decision evidence is invalid") from exc
        if (
            _canonical_bytes(payload) != encoded
            or payload.get("schema_version") != "gate-b-decision-evidence-v1"
            or loaded_ref != ref.manifest_ref
            or payload.get("decision_sha256") != ref.decision_sha256
            or _sha256(decision_bytes) != ref.decision_sha256
        ):
            raise ValueError("decision evidence binding mismatch")
        return decision_bytes

    def find_for_manifest_ref(self, manifest_ref: ManifestRef) -> DecisionEvidenceRef:
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_bytes())
                loaded_ref = ManifestRef.model_validate(payload["manifest_ref"])
            except Exception:
                continue
            if loaded_ref != manifest_ref:
                continue
            try:
                decision_bytes = base64.b64decode(payload["decision_b64"], validate=True)
            except Exception:
                continue
            ref = DecisionEvidenceRef(
                manifest_ref=manifest_ref,
                decision_sha256=_sha256(decision_bytes),
            )
            self.bytes_for(ref)
            return ref
        raise ValueError("decision evidence for manifest row missing")

    def verify(self, ref: DecisionEvidenceRef, manifest: EvidenceManifest) -> None:
        if ref.manifest_ref != manifest.row_ref(ref.manifest_ref.ordinal):
            raise ValueError("decision evidence manifest reference mismatch")
        self.bytes_for(ref)


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
    metadata: dict[str, str]


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

    def find_for_manifest_ref(self, manifest_ref: ManifestRef) -> RecordingRef:
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_bytes())
                loaded_ref = ManifestRef.model_validate(payload["manifest_ref"])
            except Exception:
                continue
            if loaded_ref != manifest_ref:
                continue
            ref = RecordingRef(
                manifest_ref=manifest_ref,
                recording_sha256=path.stem,
            )
            self._payload(ref)
            return ref
        raise ValueError("recording for manifest row missing")

    def _payload(self, ref: RecordingRef) -> dict[str, object]:
        encoded = self.bytes_for(ref)
        try:
            payload = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("recording payload is invalid") from exc
        if not isinstance(payload, dict) or _canonical_bytes(payload) != encoded:
            raise ValueError("recording payload is not canonical")
        if set(payload) != {
            "schema_version", "manifest_ref", "request_b64", "response_b64",
            "outcome", "metadata",
        } or payload["schema_version"] != "gate-b-sealed-recording-v1":
            raise ValueError("recording payload contract mismatch")
        loaded_ref = ManifestRef.model_validate(payload["manifest_ref"])
        if loaded_ref != ref.manifest_ref:
            raise ValueError("recording manifest reference mismatch")
        return payload

    def _verify_dispatch_anchor(
        self,
        ref: RecordingRef,
        manifest: EvidenceManifest,
        dispatch_entry: JournalEntry,
        metadata: Mapping[str, object],
    ) -> None:
        if dispatch_entry.manifest_ref != ref.manifest_ref:
            raise ValueError("recording dispatch reference mismatch")
        if dispatch_entry.state not in {
            JournalState.SUCCESS,
            JournalState.TERMINAL_FAILURE,
            JournalState.TERMINAL_UNKNOWN,
        }:
            raise ValueError("recording dispatch entry is not terminal")
        if dispatch_entry.recording_sha256 is None:
            raise ValueError("recording provider anchor missing")
        if metadata.get("provider_record_sha256") != dispatch_entry.recording_sha256:
            raise ValueError("recording provider anchor mismatch")

    def verify(
        self,
        ref: RecordingRef,
        manifest: EvidenceManifest,
        dispatch_entry: JournalEntry,
    ) -> None:
        payload = self._payload(ref)
        expected_ref = manifest.row_ref(ref.manifest_ref.ordinal)
        if ref.manifest_ref != expected_ref:
            raise ValueError("recording manifest reference mismatch")
        metadata = payload["metadata"]
        if not isinstance(metadata, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise ValueError("recording metadata invalid")
        try:
            request_bytes = base64.b64decode(payload["request_b64"], validate=True)
            response_bytes = base64.b64decode(payload["response_b64"], validate=True)
        except Exception as exc:
            raise ValueError("recording request/response invalid") from exc
        if _sha256(request_bytes) != expected_ref.input_sha256:
            raise ValueError("recording request hash mismatch")
        if metadata.get("input_sha256") != expected_ref.input_sha256:
            raise ValueError("recording input hash mismatch")
        if metadata.get("projection_sha256") != expected_ref.projection_sha256:
            raise ValueError("recording projection hash mismatch")
        if metadata.get("response_sha256") != _sha256(response_bytes):
            raise ValueError("recording response hash mismatch")
        self._verify_dispatch_anchor(ref, manifest, dispatch_entry, metadata)
        if TerminalOutcome(payload["outcome"]) is TerminalOutcome.TERMINAL_UNKNOWN:
            if response_bytes:
                raise ValueError("terminal unknown must have empty response")
            try:
                cost = Decimal(str(metadata["conservative_cost_usd"]))
            except Exception as exc:
                raise ValueError("terminal unknown cost missing") from exc
            if not cost.is_finite() or cost < 0:
                raise ValueError("terminal unknown cost invalid")

    def replay(
        self,
        ref: RecordingRef,
        manifest: EvidenceManifest,
        dispatch_entry: JournalEntry,
    ) -> ReplayObservation:
        self.verify(ref, manifest, dispatch_entry)
        payload = self._payload(ref)
        metadata = payload["metadata"]
        if not isinstance(metadata, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise ValueError("recording metadata invalid")
        try:
            request_bytes = base64.b64decode(payload["request_b64"], validate=True)
            response_bytes = base64.b64decode(payload["response_b64"], validate=True)
            outcome = TerminalOutcome(payload["outcome"])
        except Exception as exc:
            raise ValueError("recording payload invalid") from exc
        if outcome is TerminalOutcome.TERMINAL_UNKNOWN and response_bytes:
            raise ValueError("terminal unknown must have empty response")
        return ReplayObservation(
            manifest_ref=ManifestRef.model_validate(payload["manifest_ref"]),
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            outcome=outcome,
            metadata=dict(metadata),
        )


class AdjudicationVerdict(_StrictFrozenModel):
    """One human judgment bound to the exact Decision v2 artifact reviewed."""

    manifest_ref: ManifestRef
    decision_sha256: str = Field(pattern=SHA256_PATTERN)
    correct: bool


class AdjudicationSet(_StrictFrozenModel):
    schema_version: Literal["gate-b-adjudication-v1"]
    verdicts: tuple[AdjudicationVerdict, ...]
    adjudication_sha256: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def from_verdicts(
        cls, verdicts: tuple[AdjudicationVerdict, ...]
    ) -> AdjudicationSet:
        body = {
            "schema_version": "gate-b-adjudication-v1",
            "verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
        }
        return cls(
            schema_version="gate-b-adjudication-v1",
            verdicts=verdicts,
            adjudication_sha256=_sha256(_canonical_bytes(body)),
        )

    @model_validator(mode="after")
    def validate_set_identity(self) -> AdjudicationSet:
        ordinals = tuple(verdict.manifest_ref.ordinal for verdict in self.verdicts)
        if ordinals != tuple(sorted(set(ordinals))):
            raise ValueError("adjudication verdict ordinals must be sorted and unique")
        body = self.model_dump(mode="json", exclude={"adjudication_sha256"})
        if _sha256(_canonical_bytes(body)) != self.adjudication_sha256:
            raise ValueError("adjudication_sha256 does not match verdict bytes")
        return self

    @property
    def audited_count(self) -> int:
        return len(self.verdicts)

    @property
    def denominator(self) -> int:
        return len(self.verdicts)

    @property
    def correct_count(self) -> int:
        return sum(verdict.correct for verdict in self.verdicts)

    @property
    def audited_ordinals(self) -> tuple[int, ...]:
        return tuple(verdict.manifest_ref.ordinal for verdict in self.verdicts)


class AdjudicationSetRef(_StrictFrozenModel):
    run_id: str = Field(pattern=r"^gate-b-evidence-v1-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    adjudication_sha256: str = Field(pattern=SHA256_PATTERN)


class AdjudicationSetStore:
    """Create-once, manifest-bound human adjudication evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, adjudication_sha256: str) -> Path:
        return self.root / f"{adjudication_sha256}.json"

    @staticmethod
    def _validate_bindings(
        adjudication: AdjudicationSet,
        manifest: EvidenceManifest,
        finalized_decision_sha256s: tuple[str, ...],
    ) -> None:
        if len(finalized_decision_sha256s) != manifest.row_count:
            raise ValueError("finalized_decision_hashes_required")
        for verdict in adjudication.verdicts:
            ordinal = verdict.manifest_ref.ordinal
            if verdict.manifest_ref != manifest.row_ref(ordinal):
                raise ValueError("adjudication manifest reference mismatch")
            if verdict.decision_sha256 != finalized_decision_sha256s[ordinal]:
                raise ValueError("adjudication decision hash mismatch")

    def save_exclusive(
        self,
        adjudication: AdjudicationSet,
        manifest: EvidenceManifest,
        finalized_decision_sha256s: tuple[str, ...],
    ) -> AdjudicationSetRef:
        self._validate_bindings(adjudication, manifest, finalized_decision_sha256s)
        encoded = _canonical_bytes(adjudication.model_dump(mode="json"))
        path = self._path(adjudication.adjudication_sha256)
        try:
            with path.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError("adjudication hash collision with different bytes")
        return AdjudicationSetRef(
            run_id=manifest.run_id,
            manifest_sha256=manifest.manifest_sha256,
            adjudication_sha256=adjudication.adjudication_sha256,
        )

    def load(
        self,
        ref: AdjudicationSetRef,
        manifest: EvidenceManifest,
        finalized_decision_sha256s: tuple[str, ...],
    ) -> AdjudicationSet:
        if ref.run_id != manifest.run_id or ref.manifest_sha256 != manifest.manifest_sha256:
            raise ValueError("adjudication set manifest binding mismatch")
        encoded = self._path(ref.adjudication_sha256).read_bytes()
        try:
            payload = json.loads(encoded)
            adjudication = AdjudicationSet.model_validate(payload)
        except Exception as exc:
            raise ValueError("adjudication set is invalid") from exc
        if _canonical_bytes(payload) != encoded:
            raise ValueError("adjudication set is not canonical")
        if adjudication.adjudication_sha256 != ref.adjudication_sha256:
            raise ValueError("adjudication set hash mismatch")
        self._validate_bindings(adjudication, manifest, finalized_decision_sha256s)
        return adjudication


class MeasurementReport(_StrictFrozenModel):
    run_id: str = Field(pattern=r"^gate-b-evidence-v1-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_row_count: int = Field(ge=1, le=48)
    observed_row_count: int = Field(ge=0, le=48)
    deliverable_count: int = Field(ge=0, le=48)
    terminal_unknown_count: int = Field(ge=0, le=48)
    adjudicated_count: int = Field(ge=0, le=48)
    adjudication_denominator: int = Field(ge=0, le=48)
    adjudicated_correct: int = Field(ge=0, le=48)
    recording_sha256s: tuple[str, ...] = ()
    decision_sha256s: tuple[str, ...] = ()
    audited_ordinals: tuple[int, ...] = ()
    adjudication_set_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_evidence_hashes(self) -> MeasurementReport:
        for name, values in (
            ("recording_sha256s", self.recording_sha256s),
            ("decision_sha256s", self.decision_sha256s),
        ):
            if any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in values
            ):
                raise ValueError(f"{name} must contain lowercase SHA-256 values")
        return self


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


class GateEvaluationReport(_StrictFrozenModel):
    """Published metrics plus the separate run-level gate decision."""

    metrics: MeasurementReport
    gate_decision: GateDecision


class GateEvaluator:
    @staticmethod
    def evaluate_report(
        manifest: EvidenceManifest,
        measurements: MeasurementReport,
        adjudication: AdjudicationSet,
        *,
        policy: GateBBenchmarkPolicyV3,
    ) -> GateEvaluationReport:
        """Evaluate a finalized run while preserving all immutable metrics."""
        if (
            len(measurements.recording_sha256s) != manifest.row_count
            or len(measurements.decision_sha256s) != manifest.row_count
        ):
            raise ValueError("finalized_evidence_hashes_required")
        finalized_metrics = measurements.model_copy(
            update={
                "audited_ordinals": adjudication.audited_ordinals,
                "adjudication_set_sha256": adjudication.adjudication_sha256,
                "adjudicated_count": adjudication.audited_count,
                "adjudication_denominator": adjudication.denominator,
                "adjudicated_correct": adjudication.correct_count,
            }
        )
        return GateEvaluationReport(
            metrics=finalized_metrics,
            gate_decision=GateEvaluator.evaluate(
                manifest,
                finalized_metrics,
                adjudication,
                policy=policy,
            ),
        )

    @staticmethod
    def evaluate(
        manifest: EvidenceManifest,
        measurements: MeasurementReport,
        adjudication: AdjudicationSet,
        *,
        policy: GateBBenchmarkPolicyV3,
    ) -> GateDecision:
        evaluator_contract_sha256 = _evaluator_contract_sha256(policy)
        if (
            measurements.run_id != manifest.run_id
            or measurements.manifest_sha256 != manifest.manifest_sha256
        ):
            return GateDecision(
                run_id=manifest.run_id,
                manifest_sha256=manifest.manifest_sha256,
                evaluator_contract_sha256=evaluator_contract_sha256,
                measurement_status="incomplete",
                decision=GateDecisionKind.REVISE,
                violated_rules=("measurement_report_manifest_binding_mismatch",),
            )
        if measurements.expected_row_count != manifest.row_count:
            return GateDecision(
                run_id=manifest.run_id,
                manifest_sha256=manifest.manifest_sha256,
                evaluator_contract_sha256=evaluator_contract_sha256,
                measurement_status="incomplete",
                decision=GateDecisionKind.REVISE,
                violated_rules=("measurement_cardinality_mismatch",),
            )
        if measurements.observed_row_count != measurements.expected_row_count:
            return GateDecision(
                run_id=manifest.run_id,
                manifest_sha256=manifest.manifest_sha256,
                evaluator_contract_sha256=evaluator_contract_sha256,
                measurement_status="incomplete",
                decision=GateDecisionKind.REVISE,
                violated_rules=("collection_incomplete",),
            )
        if (
            adjudication.audited_count != measurements.adjudicated_count
            or adjudication.denominator != measurements.adjudication_denominator
            or adjudication.correct_count != measurements.adjudicated_correct
            or adjudication.denominator != manifest.row_count
            or adjudication.audited_ordinals != tuple(range(manifest.row_count))
            or len(measurements.decision_sha256s) != manifest.row_count
            or any(
                verdict.manifest_ref != manifest.row_ref(verdict.manifest_ref.ordinal)
                for verdict in adjudication.verdicts
            )
            or any(
                verdict.decision_sha256 != measurements.decision_sha256s[verdict.manifest_ref.ordinal]
                for verdict in adjudication.verdicts
            )
        ):
            return GateDecision(
                run_id=manifest.run_id,
                manifest_sha256=manifest.manifest_sha256,
                evaluator_contract_sha256=evaluator_contract_sha256,
                measurement_status="incomplete",
                decision=GateDecisionKind.REVISE,
                violated_rules=("adjudication_incomplete",),
            )
        violated: list[str] = []
        if measurements.deliverable_count < policy.minimum_deliverable_results:
            violated.append("minimum_deliverable_results")
        if measurements.terminal_unknown_count > policy.maximum_terminal_unknown:
            violated.append("maximum_terminal_unknown")
        if (
            adjudication.denominator == 0
            or Decimal(adjudication.correct_count) / Decimal(adjudication.denominator)
            < Decimal(policy.minimum_manual_triage_accuracy)
        ):
            violated.append("minimum_manual_triage_accuracy")
        return GateDecision(
            run_id=manifest.run_id,
            manifest_sha256=manifest.manifest_sha256,
            evaluator_contract_sha256=evaluator_contract_sha256,
            measurement_status="complete",
            decision=(GateDecisionKind.REFUSE if violated else GateDecisionKind.PROCEED_TO_SHADOW),
            violated_rules=tuple(sorted(violated)),
        )


class GovernedProvider(Protocol):
    """One production-shaped dispatch seam shared by real and fake providers."""

    store: object
    pricing: object

    def dispatch(
        self,
        payload: dict[str, object],
        *,
        input_hash: str,
        capability: object,
    ) -> object: ...


class GovernedStructuredProviderAdapter:
    """Production-shaped adapter over the semantic governed-call runtime."""

    def __init__(
        self,
        *,
        provider: object,
        request_factory: Callable[[dict[str, object], str], object],
        pricing: object,
        authority_identity: Mapping[str, str],
    ) -> None:
        governed_call = getattr(provider, "governed_structured_call", None)
        store = getattr(provider, "store", None)
        if not callable(governed_call) or store is None:
            raise ValueError("governed_structured_provider_required")
        self._provider = provider
        self._request_factory = request_factory
        self.store = store
        self.pricing = pricing
        self.authority_identity = dict(authority_identity)

    def dispatch(
        self,
        payload: dict[str, object],
        *,
        input_hash: str,
        capability: object,
    ) -> object:
        request = self._request_factory(payload, input_hash)
        return self._provider.governed_structured_call(
            request=request,
            capability=capability,
        )


class _LiveGateBProvider:
    """Production provider implementing the same seam as the smoke fake."""

    def __init__(self, semantic_provider: object) -> None:
        from job_intel.product_search.evidence_synthesis import (
            RecordedEvidenceSynthesisProviderV2,
            load_evidence_synthesis_policy,
            provider_output_schema_v2_sha256,
            task10_prompt_v2_sha256,
        )
        from job_intel.product_search.gate_b import governed_pricing_schedule

        manifest_sha256 = os.environ.get("GATE_B_MANIFEST_SHA256", "")
        if not manifest_sha256 or not os.environ.get("GATE_B_PROVIDER_STORE_DIR"):
            raise ValueError("live_provider_context_missing")
        policy = load_evidence_synthesis_policy()
        pricing = governed_pricing_schedule()
        self._semantic_provider = semantic_provider
        self._policy = policy
        self._adapter = None
        inner_store = getattr(semantic_provider, "store", None)
        if inner_store is None:
            raise ValueError("live_provider_store_missing")
        self.pricing = pricing
        self._v2_record_verifier: Callable[[dict[str, object]], None] | None = None
        self.authority_identity = {
            "provider_sha256": _sha256(
                Path(
                    os.environ.get("GATE_B_PROVIDER_AUTHORITY_PATH", "")
                ).read_bytes()
                if os.environ.get("GATE_B_PROVIDER_AUTHORITY_PATH")
                else str(getattr(semantic_provider, "provider_id", "")).encode("utf-8")
            ),
            "model_sha256": _sha256(policy.model_id.encode("utf-8")),
            "prompt_sha256": task10_prompt_v2_sha256(policy),
            "response_schema_sha256": provider_output_schema_v2_sha256(),
            "pricing_sha256": pricing.identity_sha256,
        }
        # The generic semantic record is its own sealed artifact. Keep the
        # replay/cache seam on the raw store; authority belongs to the V2
        # envelope and must not be appended after generic sealing.
        self._semantic_store = inner_store
        semantic_provider.store = inner_store
        self.store = SemanticRecordingStore(
            Path(os.environ["GATE_B_PROVIDER_STORE_DIR"]) / "v2"
        )

    def verify_provider_record(self, record: dict[str, object]) -> None:
        verifier = self._v2_record_verifier
        if not callable(verifier):
            raise ValueError("v2_provider_record_verifier_required")
        verifier(record)

    def _publish_v2_provider_record(
        self,
        *,
        dispatch_input_hash: str,
        input_payload: dict[str, object],
        result: EvidenceSynthesisResultV2,
        capability: object,
    ) -> dict[str, object]:
        metadata = result.metadata.model_dump(mode="json")
        semantic_input_sha256 = str(metadata["input_sha256"])
        call_metadata = dict(getattr(self._adapter, "last_call_metadata", {}) or {})
        post_dispatch_outcome = call_metadata.get("post_dispatch_outcome_v3")
        conservative_cost = call_metadata.get("conservative_cost_usd")
        transport_record_sha256 = call_metadata.get("transport_record_sha256")
        if not isinstance(post_dispatch_outcome, str) or not post_dispatch_outcome:
            raise ValueError("v2_provider_record_post_dispatch_missing")
        if not isinstance(conservative_cost, str) or not conservative_cost:
            raise ValueError("v2_provider_record_conservative_cost_missing")
        if not isinstance(transport_record_sha256, str) or not transport_record_sha256:
            raise ValueError("v2_provider_record_transport_receipt_missing")
        raw_response_payload = getattr(self._adapter, "last_response_payload", None)
        if raw_response_payload is None:
            if result.deliverable:
                raise ValueError("v2_provider_record_response_missing")
            raw_response_text = ""
        elif isinstance(raw_response_payload, dict):
            raw_response_text = _canonical_bytes(raw_response_payload).decode("utf-8")
        else:
            raise ValueError("v2_provider_record_response_invalid")
        v2_record = {
            "recording_format_version": SEMANTIC_RECORDING_FORMAT_VERSION,
            "input_hash": dispatch_input_hash,
            "semantic_input_sha256": semantic_input_sha256,
            "provider_input_sha256": semantic_input_sha256,
            "input": input_payload,
            "input_payload_sha256": _sha256(_canonical_bytes(input_payload)),
            "provider_id": metadata["provider_id"],
            "provider_version": metadata["provider_version"],
            "model_id": metadata["model_id"],
            "requested_model": metadata["model_id"],
            "response_model": metadata["model_id"],
            "semantic_prompt_version": metadata["semantic_prompt_version"],
            "prompt_version": metadata["prompt_version"],
            "schema_version": result.schema_version,
            "output_sha256": metadata["output_sha256"],
            "raw_response_text": raw_response_text,
            "response_hash": _sha256(raw_response_text.encode("utf-8")),
            "usage": call_metadata.get("usage"),
            "cost_usd": call_metadata.get("cost_usd", metadata.get("cost_usd")),
            "measured_cost_usd": call_metadata.get("measured_cost_usd"),
            "conservative_cost_usd": conservative_cost,
            "latency_ms": metadata["latency_ms"],
            "retry_count": call_metadata.get("retry_count", 0),
            "post_dispatch_outcome_v3": post_dispatch_outcome,
            "status": result.status.value,
            "failure_code": None if result.deliverable else result.failure_reason,
            "failure_diagnostic": call_metadata.get("failure_diagnostic"),
            "provider_record_kind": "gate-b-evidence-synthesis-v2",
            "provider_sha256": self.authority_identity["provider_sha256"],
            "model_sha256": self.authority_identity["model_sha256"],
            "prompt_sha256": self.authority_identity["prompt_sha256"],
            "response_schema_sha256": self.authority_identity[
                "response_schema_sha256"
            ],
            "provider_authority_identity": dict(self.authority_identity),
            "pricing": self.pricing.as_record(),
            "pricing_sha256": self.pricing.identity_sha256,
            "max_output_tokens": self.pricing.max_output_tokens,
            "semantic_transport_record_sha256": transport_record_sha256,
        }
        seal_record = getattr(capability, "seal_record", None)
        if not callable(seal_record):
            raise ValueError("v2_provider_record_sealer_required")
        seal_record(v2_record)
        self.store.save_exclusive(v2_record)
        return v2_record

    def dispatch(
        self,
        payload: dict[str, object],
        *,
        input_hash: str,
        capability: object,
    ) -> object:
        if self._adapter is None:
            from job_intel.product_search.evidence_synthesis import (
                RecordedEvidenceSynthesisProviderV2,
            )
            self._adapter = RecordedEvidenceSynthesisProviderV2(
                semantic_provider=self._semantic_provider,
                policy=self._policy,
                pricing=self.pricing,
                record_capability=capability,
                run_identity_sha256=os.environ["GATE_B_MANIFEST_SHA256"],
            )
        else:
            self._adapter.record_capability = capability
        verifier = getattr(capability, "verify_record", None)
        self._v2_record_verifier = verifier if callable(verifier) else None
        provider_input_sha256 = synthesis_input_sha256(
            payload, provider=self._adapter
        )
        binder = getattr(capability, "bind_record_identity", None)
        if not callable(binder):
            raise ValueError("transport_receipt_binder_required")
        binder(input_hash, provider_input_sha256)
        try:
            from job_intel.product_search.evidence_synthesis import (
                run_evidence_synthesis_v2,
            )
            result = run_evidence_synthesis_v2(
                synthesis_input=EvidenceSynthesisInputV2.model_validate(payload),
                provider=self._adapter,
                policy=self._policy,
            )
            v2_record = self._publish_v2_provider_record(
                dispatch_input_hash=input_hash,
                input_payload=payload,
                result=result,
                capability=capability,
            )
            raw_response_text = v2_record["raw_response_text"]
            if not isinstance(raw_response_text, str) or not raw_response_text:
                return {}
            try:
                response_payload = json.loads(raw_response_text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise LLMProviderError(
                    "schema_invalid", "structured JSON invalid"
                ) from exc
            if not isinstance(response_payload, dict):
                raise LLMProviderError("schema_invalid", "structured JSON object required")
            return response_payload
        finally:
            self._adapter.record_capability = None


class _AuthorityRecordingStore:
    """Persist provider authority fields alongside every canonical record."""

    def __init__(self, inner: object, identity: Mapping[str, str]) -> None:
        self._inner = inner
        self._identity = dict(identity)

    def _with_identity(self, record: dict[str, object]) -> dict[str, object]:
        enriched = dict(record)
        enriched.update(self._identity)
        return enriched

    def save(self, record: dict[str, object]) -> object:
        return self._inner.save(self._with_identity(record))

    def save_exclusive(self, record: dict[str, object]) -> object:
        return self._inner.save_exclusive(self._with_identity(record))

    def load(self, input_hash: str) -> dict[str, object]:
        return self._with_identity(self._inner.load(input_hash))

    def exists(self, input_hash: str) -> bool:
        return bool(self._inner.exists(input_hash))

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def build_live_provider_factory() -> GovernedProvider:
    """Build the real provider only through the explicit approval gate."""
    if os.environ.get("JOB_INTEL_LLM_LIVE_APPROVED") != "1":
        raise LLMProviderError("live_calls_not_approved", "explicit approval required")
    store_dir = os.environ.get("GATE_B_PROVIDER_STORE_DIR")
    if not store_dir:
        raise ValueError("live_provider_store_dir_required")
    return _LiveGateBProvider(build_live_llm_provider(store_dir=store_dir))


class OneRowResult(_StrictFrozenModel):
    manifest_ref: ManifestRef
    validation_status: EvidenceSynthesisStatus | None
    recording_ref: RecordingRef
    recording_bytes: bytes
    decision: DecisionResultV2
    decision_ref: DecisionEvidenceRef
    decision_bytes: bytes


class CorpusRow(_StrictFrozenModel):
    """One immutable corpus input before the v3 projector runs."""

    ordinal: int = Field(ge=0, lt=48)
    record: dict[str, object]
    raw: dict[str, object]


class CollectionRowResult(_StrictFrozenModel):
    """Durable collection evidence: per-row Decision v2 output, but no gate decision."""

    manifest_ref: ManifestRef
    validation_status: EvidenceSynthesisStatus | None
    recording_ref: RecordingRef
    recording_bytes: bytes
    outcome: TerminalOutcome
    decision: DecisionResultV2
    decision_ref: DecisionEvidenceRef
    decision_bytes: bytes


class CollectionMetrics(_StrictFrozenModel):
    """Immutable collection counts consumed later by the separate evaluator."""

    expected_row_count: int = Field(ge=1, le=48)
    observed_row_count: int = Field(ge=0, le=48)
    deliverable_count: int = Field(ge=0, le=48)
    terminal_unknown_count: int = Field(ge=0, le=48)


class CollectionReport(_StrictFrozenModel):
    """Per-row evidence and metrics; gate thresholds are Task 8."""

    run_id: str = Field(pattern=r"^gate-b-evidence-v1-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    rows: tuple[CollectionRowResult, ...]
    metrics: CollectionMetrics


class CollectionConfig(_StrictFrozenModel):
    """External paths and anchored callables for one collection attempt."""

    manifest_path: Path
    corpus_rows_path: Path | None = None
    corpus_package_root: Path | None = None
    gate_a_root: Path | None = None
    run_manifest_path: Path | None = None
    run_manifest_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    corpus_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    reviewed_allowlist_path: Path
    decision_policy_path: Path
    company_evidence_root: Path
    provider_factory: str = (
        "job_intel.product_search.gate_b_evidence_runner_v1:build_live_provider_factory"
    )
    decision_request_factory: str
    authority_paths: dict[str, Path]

    @model_validator(mode="after")
    def validate_corpus_source(self) -> CollectionConfig:
        direct = self.corpus_rows_path is not None
        package = (
            self.corpus_package_root is not None
            and self.gate_a_root is not None
            and self.run_manifest_path is not None
            and self.run_manifest_sha256 is not None
            and self.corpus_sha256 is not None
        )
        if direct == package:
            raise ValueError("configure exactly one corpus source")
        return self


def _load_collection_config(path: Path) -> CollectionConfig:
    try:
        payload = json.loads(path.read_bytes())
        config = CollectionConfig.model_validate(payload)
    except Exception as exc:
        raise ValueError("collection config is invalid") from exc
    base = path.parent.resolve()
    relative_fields = (
        "manifest_path",
        "corpus_rows_path",
        "corpus_package_root",
        "gate_a_root",
        "run_manifest_path",
        "reviewed_allowlist_path",
        "decision_policy_path",
        "company_evidence_root",
    )
    updates = {
        name: value if value is None or value.is_absolute() else (base / value).resolve()
        for name in relative_fields
        for value in [getattr(config, name)]
    }
    updates["authority_paths"] = {
        key: value if value.is_absolute() else (base / value).resolve()
        for key, value in config.authority_paths.items()
    }
    return config.model_copy(update=updates)


def _load_company_authority_inputs(
    root: Path,
    manifest: EvidenceManifest,
) -> tuple[CompanyEvidenceCatalogV3, dict[str, CompanyThesisInputV1]]:
    """Load the immutable company bundles and only the theses that validate."""
    if root is None:
        raise ValueError("company_evidence_root_required")
    contract_sha256 = manifest.authorities.source_authority_sha256s.get(
        "company_evidence_contract"
    )
    if contract_sha256 is None:
        raise ValueError("company_evidence_contract_authority_missing")
    catalog = load_company_evidence_catalog_v3(
        root,
        company_evidence_contract_sha256=contract_sha256,
    )
    bundles = {
        bundle.company_identity.company_id: bundle for bundle in catalog.bundles
    }
    theses: dict[str, CompanyThesisInputV1] = {}
    for path in sorted(root.rglob("company-thesis-input.v1.yaml")):
        raw_payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_payload, Mapping):
            raise ValueError("company thesis input is invalid")
        thesis_company_id = raw_payload.get("company_id")
        if not isinstance(thesis_company_id, str) or thesis_company_id not in bundles:
            raise ValueError("company thesis company identity is unknown")
        thesis = load_company_thesis_input(
            path,
            evidence_bundle=bundles[thesis_company_id],
        )
        theses[thesis.company_id] = thesis
    return catalog, theses


def _load_artifact_callable(specification: str, artifact_root: Path) -> Callable[..., Any]:
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("artifact callable must use module:attribute syntax")
    try:
        module_spec = importlib.util.find_spec(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("artifact callable module is unavailable") from exc
    if module_spec is None or module_spec.origin is None:
        raise ValueError("artifact callable module has no file origin")
    origin = Path(module_spec.origin).resolve()
    root = artifact_root.resolve()
    if origin != root and root not in origin.parents:
        raise ValueError("artifact callable is outside the anchored artifact")
    module = importlib.import_module(module_name)
    value = getattr(module, attribute, None)
    if not callable(value):
        raise ValueError("artifact callable attribute is not callable")
    return value


def _load_manifest(path: Path, expected_sha256: str) -> EvidenceManifest:
    encoded = path.read_bytes()
    if _sha256(encoded) != expected_sha256:
        raise ValueError("evidence manifest hash mismatch")
    try:
        payload = json.loads(encoded)
        return EvidenceManifest.model_validate(payload)
    except Exception as exc:
        raise ValueError("evidence manifest is invalid") from exc


def _artifact_binding_context(
    manifest: EvidenceManifest,
    artifact_root: Path,
    authority_paths: dict[str, Path],
) -> tuple[object, object, object]:
    """Derive binding values from the published artifact, never from a factory."""
    from job_intel.product_search.gate_b_runtime_v1 import (
        AuthorityInputs,
        FrozenRuntime,
        RuntimeParity,
        SourceArtifact,
        _site_packages,
    )

    runtime_manifest_path = artifact_root / "runtime-manifest.json"
    payload = json.loads(runtime_manifest_path.read_bytes())
    if payload.get("artifact_tree_sha256") != artifact_root.name:
        raise ValueError("artifact tree anchor mismatch")
    python_executable = artifact_root / "python-runtime/venv/bin/python"
    if python_executable.is_symlink() or not python_executable.is_file():
        raise ValueError("artifact interpreter is not a regular file")
    shim = _site_packages(
        python_executable,
        python_home=artifact_root / "python-runtime/venv",
    ) / "00-pysqlite3-shim.pth"
    if not shim.is_file() or shim.is_symlink():
        raise ValueError("artifact sqlite shim is unavailable")
    if _sha256(python_executable.read_bytes()) != manifest.runtime.interpreter_sha256:
        raise ValueError("artifact interpreter hash mismatch")
    if _sha256(shim.read_bytes()) != manifest.runtime.shim_sha256:
        raise ValueError("artifact shim hash mismatch")
    runtime_fields = {
        "artifact_sha256": "artifact_sha256",
        "artifact_tree_sha256": "artifact_tree_sha256",
        "shim_sha256": "shim_sha256",
        "interpreter_sha256": "python_executable_sha256",
        "stdlib_inventory_sha256": "stdlib_tree_sha256",
        "installed_distributions_sha256": "installed_distributions_sha256",
        "installed_files_sha256": "installed_files_sha256",
        "sys_path_sha256": "sys_path_sha256",
        "native_extensions_sha256": "native_extensions_sha256",
        "shared_libraries_sha256": "shared_libraries_sha256",
        "shared_library_provenance": "shared_library_provenance",
    }
    missing = [key for key in runtime_fields.values() if key not in payload]
    if missing:
        raise ValueError(f"runtime identity fields missing: {','.join(missing)}")
    if payload["artifact_tree_sha256"] != artifact_root.name:
        raise ValueError("artifact tree anchor mismatch")
    known_manifest_fields = {
        key: payload[source_key] for key, source_key in runtime_fields.items()
    }
    if manifest.runtime.model_dump(mode="json") != manifest.runtime.model_validate(
        known_manifest_fields
    ).model_dump(mode="json"):
        raise ValueError("artifact runtime identity mismatch")
    runtime = FrozenRuntime(
        root=artifact_root / "python-runtime/venv",
        python_executable=python_executable,
        # The CLI module is executed as ``__main__`` by ``python -m`` while
        # runtime_v1 imports its qualified sibling name.  Pass the canonical
        # value across that module boundary instead of leaking a duplicate
        # Pydantic class identity into FrozenRuntime validation.
        runtime_identity=manifest.runtime.model_dump(mode="json"),
        parity=RuntimeParity(
            python_version=str(payload["python_version"]),
            sqlite_module="pysqlite3",
            sqlite_version="unknown",
        ),
        shim_sha256=manifest.runtime.shim_sha256,
        reproducibility="frozen_non_editable",
    )
    source_artifact = SourceArtifact(
        commit=str(payload["candidate_commit"]),
        source_root=artifact_root / "runtime",
        archive_sha256="0" * 64,
        artifact_sha256=manifest.runtime.artifact_sha256,
    )
    names = (
        "model_bytes",
        "prompt_bytes",
        "response_schema_bytes",
        "profile_bytes",
        "policy_bytes",
        "decision_v2_bytes",
        "pricing_bytes",
    )
    missing = [name for name in names if name not in authority_paths]
    if missing:
        raise ValueError(f"authority paths missing: {','.join(missing)}")
    authorities = AuthorityInputs(
        **{
            name: authority_paths[name].read_bytes()
            for name in names
        },
        source_authority_bytes={
            key.removeprefix("source:"): path.read_bytes()
            for key, path in authority_paths.items()
            if key.startswith("source:")
        },
    )
    return source_artifact, runtime, authorities


def _write_measurement_report(
    report: CollectionReport,
    state_directory: Path,
) -> Path:
    measurement = MeasurementReport(
        run_id=report.run_id,
        manifest_sha256=report.manifest_sha256,
        expected_row_count=report.metrics.expected_row_count,
        observed_row_count=report.metrics.observed_row_count,
        deliverable_count=report.metrics.deliverable_count,
        terminal_unknown_count=report.metrics.terminal_unknown_count,
        adjudicated_count=0,
        adjudication_denominator=0,
        adjudicated_correct=0,
        recording_sha256s=tuple(
            row.recording_ref.recording_sha256 for row in report.rows
        ),
        decision_sha256s=tuple(row.decision_ref.decision_sha256 for row in report.rows),
    )
    destination = state_directory / "measurement-report.json"
    destination.write_bytes(_canonical_bytes(measurement.model_dump(mode="json")))
    return destination


def _load_measurement_report(
    path: Path,
    *,
    expected_sha256: str,
    expected_run_id: str,
    expected_manifest_sha256: str,
) -> MeasurementReport:
    try:
        encoded = path.read_bytes()
        if _sha256(encoded) != expected_sha256:
            raise ValueError("measurement report hash mismatch")
        payload = json.loads(encoded)
        if _canonical_bytes(payload) != encoded:
            raise ValueError("measurement report is not canonical")
        report = MeasurementReport.model_validate(payload)
        if (
            report.run_id != expected_run_id
            or report.manifest_sha256 != expected_manifest_sha256
        ):
            raise ValueError("measurement report manifest binding mismatch")
        return report
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("measurement report is invalid") from exc


def _load_adjudication_file(path: Path, expected_sha256: str) -> AdjudicationSet:
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise ValueError("adjudication set is unavailable") from exc
    try:
        payload = json.loads(encoded)
        if _canonical_bytes(payload) != encoded:
            raise ValueError("adjudication set is not canonical")
        adjudication = AdjudicationSet.model_validate(payload)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("adjudication set is invalid") from exc
    if adjudication.adjudication_sha256 != expected_sha256:
        raise ValueError("adjudication set hash mismatch")
    return adjudication


def _load_corpus_rows_file(path: Path, manifest: EvidenceManifest) -> tuple[CorpusRow, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("corpus rows file is unavailable")
    try:
        payload = json.loads(path.read_bytes())
        if not isinstance(payload, list):
            raise ValueError("corpus rows must be a list")
        rows = tuple(CorpusRow.model_validate(item) for item in payload)
    except Exception as exc:
        raise ValueError("corpus rows file is invalid") from exc
    if tuple(row.ordinal for row in rows) != tuple(range(manifest.row_count)):
        raise ValueError("corpus rows are not in manifest order")
    for row in rows:
        expected = manifest.row(row.ordinal).raw_sha256
        if _sha256(_canonical_bytes(row.raw)) != expected:
            raise ValueError("corpus raw hash does not match manifest row")
    return rows


def _load_manifest_bound_decision_policy(
    path: Path, manifest: EvidenceManifest
) -> LoadedDecisionPolicyV2:
    policy_bytes = path.read_bytes()
    if _sha256(policy_bytes) != manifest.authorities.decision_v2_sha256:
        raise ValueError("decision_policy_authority_mismatch")
    return load_decision_policy(path)


def build_collection_preflight(
    *,
    config: CollectionConfig,
    manifest: EvidenceManifest,
) -> dict[str, Any]:
    """Validate exact collection inputs without constructing a provider."""
    if config.company_evidence_root is None:
        raise ValueError("company_evidence_root_required")
    reviewed_allowlist = load_reviewed_fragment_allowlist_v3(
        config.reviewed_allowlist_path
    )
    if manifest.corpus_sha256 is None:
        raise ValueError("manifest_corpus_sha256_required")
    validate_reviewed_fragment_allowlist_corpus_v3(
        reviewed_allowlist,
        corpus_sha256=manifest.corpus_sha256,
    )
    failures: list[dict[str, str]] = []
    try:
        _load_manifest_bound_decision_policy(config.decision_policy_path, manifest)
    except (OSError, ValueError) as exc:
        failures.append(
            {"check": "decision_policy", "error": str(exc)}
        )
    catalog, theses = _load_company_authority_inputs(
        config.company_evidence_root, manifest
    )
    if config.corpus_rows_path is not None:
        corpus_rows = _load_corpus_rows_file(config.corpus_rows_path, manifest)
    else:
        if (
            config.corpus_package_root is None
            or config.gate_a_root is None
            or config.run_manifest_path is None
            or config.run_manifest_sha256 is None
            or config.corpus_sha256 is None
        ):
            raise ValueError("collection corpus source is incomplete")
        corpus_rows = load_gate_b_corpus_rows(
            package_root=config.corpus_package_root,
            gate_a_root=config.gate_a_root,
            run_manifest_path=config.run_manifest_path,
            expected_sha256=config.run_manifest_sha256,
            expected_corpus_sha256=config.corpus_sha256,
        )
    authority_counts: dict[str, int] = {}
    sufficiency_counts: dict[str, int] = {}
    company_rows: dict[str, dict[str, Any]] = {}
    derived_rows = _derive_binding_rows(
        corpus_rows,
        reviewed_allowlist,
        company_evidence_catalog=catalog,
    )
    for corpus_row, derived in zip(corpus_rows, derived_rows, strict=True):
        projected = project_vacancy_evidence_v3(
            corpus_row.record,
            corpus_row.raw,
            reviewed_allowlist,
            company_evidence_catalog=catalog,
        )
        expected_authority = resolve_company_authority_v3(corpus_row.raw, catalog)
        actual_authority = projected.company_authority
        if expected_authority.status == "available" and actual_authority.status != "available":
            raise ValueError("company_evidence_not_connected")
        if actual_authority.status == "unavailable":
            reason = actual_authority.reason.value
            authority_counts[reason] = authority_counts.get(reason, 0) + 1
            company_id = str(corpus_row.raw.get("company", "<unknown>"))
            company_rows.setdefault(
                company_id,
                {"status": "unavailable", "rows": 0, "sufficiency": None},
            )["rows"] += 1
        else:
            authority_counts["available"] = authority_counts.get("available", 0) + 1
            bundle = actual_authority.company_evidence_bundle
            sufficiency = bundle.sufficiency_state.value
            sufficiency_counts[sufficiency] = sufficiency_counts.get(sufficiency, 0) + 1
            company_id = bundle.company_identity.company_id
            entry = company_rows.setdefault(
                company_id,
                {"status": "available", "rows": 0, "sufficiency": sufficiency},
            )
            entry["rows"] += 1
            if entry["sufficiency"] != sufficiency:
                raise ValueError("company_sufficiency_inconsistent")
        expected = manifest.row(corpus_row.ordinal)
        if derived.raw_sha256 != expected.raw_sha256:
            raise ValueError("corpus_raw_hash_does_not_match_manifest")
        if derived.input_sha256 != expected.input_sha256:
            raise ValueError("provider_input_hash_does_not_match_manifest")
        if derived.projection_sha256 != expected.projection_sha256:
            raise ValueError("projection_hash_does_not_match_manifest")
    return {
        "status": "ready" if not failures else "blocked",
        "failures": failures,
        "manifest_sha256": manifest.manifest_sha256,
        "corpus_sha256": manifest.corpus_sha256,
        "row_count": len(corpus_rows),
        "company_authority": authority_counts,
        "sufficiency": sufficiency_counts,
        "companies": company_rows,
        "thesis_company_ids": sorted(theses),
        "provider_constructed": False,
        "network_called": False,
        "spend_usd": "0.00",
        "provider_factory": config.provider_factory,
        "decision_request_factory": config.decision_request_factory,
        "company_evidence_root": str(config.company_evidence_root),
    }



def _authority_paths_for_manifest(
    manifest: EvidenceManifest, authority_root: Path
) -> dict[str, Path]:
    """Resolve authority files from the manifest source-authority contract."""
    fixed_names = (
        "model_bytes",
        "prompt_bytes",
        "response_schema_bytes",
        "profile_bytes",
        "policy_bytes",
        "decision_v2_bytes",
        "pricing_bytes",
    )
    keys = (*fixed_names, *(f"source:{name}" for name in sorted(
        manifest.authorities.source_authority_sha256s
    )))
    return {
        key: authority_root / (key.replace(":", "-") + ".bin")
        for key in keys
    }


def _run_supervised_collection(args: argparse.Namespace) -> int:
    """Run one foreground collection under the canonical supervised wrapper."""
    manifest = _load_manifest(args.manifest, args.manifest_sha256)
    artifact_root = Path(__file__).resolve().parents[3]
    state_directory = args.output.resolve()
    state_directory.mkdir(parents=True, exist_ok=True)
    os.environ["GATE_B_MANIFEST_SHA256"] = manifest.manifest_sha256
    os.environ["GATE_B_PROVIDER_STORE_DIR"] = str(state_directory / "provider-records")
    spend_root_value = os.environ.get("GATE_B_SPEND_RECORD_ROOT")
    if not spend_root_value:
        raise ValueError("committed_budget_record_root_required")
    committed_budget_reserver = _build_committed_budget_reserver(manifest)
    os.environ["GATE_B_PROVIDER_AUTHORITY_PATH"] = str(
        args.authority_root.resolve() / "source-provider.bin"
    )
    provider_factory = _load_artifact_callable(args.provider_factory, artifact_root)
    decision_request_factory = _load_artifact_callable(
        args.decision_request_factory, artifact_root
    )
    reviewed_allowlist = load_reviewed_fragment_allowlist_v3(
        args.reviewed_allowlist
    )
    decision_policy = _load_manifest_bound_decision_policy(
        args.decision_policy, manifest
    )
    company_evidence_catalog, company_thesis_inputs = _load_company_authority_inputs(
        args.company_evidence_root, manifest
    )
    authority_root = args.authority_root.resolve()
    authority_paths = _authority_paths_for_manifest(manifest, authority_root)
    source_artifact, runtime, authorities = _artifact_binding_context(
        manifest, artifact_root, authority_paths
    )
    ledger = ForegroundDispatchLedger(
        manifest, committed_budget_reserver=committed_budget_reserver
    )
    recordings = RecordingStore(state_directory / "recordings")
    decision_evidence = DecisionEvidenceStore(state_directory / "decisions")
    corpus_rows = _load_corpus_rows_file(args.corpus, manifest)
    report = run_collection(
        manifest=manifest,
        corpus_rows=corpus_rows,
        reviewed_allowlist=reviewed_allowlist,
        provider_factory=provider_factory,
        ledger=ledger,
        recordings=recordings,
        decision_evidence=decision_evidence,
        decision_policy=decision_policy,
        decision_request_factory=decision_request_factory,
        company_evidence_catalog=company_evidence_catalog,
        company_thesis_inputs=company_thesis_inputs,
        source_artifact=source_artifact,
        runtime=runtime,
        authorities=authorities,
    )
    report_path = _write_measurement_report(report, state_directory)
    if not report_path.is_file():
        raise RuntimeError("collection evidence publication incomplete")
    return 0


def _run_evaluate_run(args: argparse.Namespace) -> int:
    """Evaluate finalized collection evidence in a separate foreground step."""
    manifest = _load_manifest(args.manifest, args.manifest_sha256)
    measurements = _load_measurement_report(
        args.measurement_report,
        expected_sha256=args.measurement_report_sha256,
        expected_run_id=manifest.run_id,
        expected_manifest_sha256=manifest.manifest_sha256,
    )
    adjudication = _load_adjudication_file(
        args.adjudication,
        args.adjudication_sha256,
    )
    policy = load_gate_b_benchmark_policy_v3(args.gate_policy)
    measurements = measurements.model_copy(
        update={
            "adjudicated_count": adjudication.audited_count,
            "adjudication_denominator": adjudication.denominator,
            "adjudicated_correct": adjudication.correct_count,
        }
    )
    evaluation = GateEvaluator.evaluate_report(
        manifest,
        measurements,
        adjudication,
        policy=policy,
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    evaluation_bytes = _canonical_bytes(evaluation.model_dump(mode="json"))
    decision_bytes = _canonical_bytes(
        evaluation.gate_decision.model_dump(mode="json")
    )
    (output / "gate-evaluation-report.json").write_bytes(evaluation_bytes)
    (output / "gate-decision.json").write_bytes(decision_bytes)
    print(decision_bytes.decode("utf-8"))
    return 0

def _main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gate_b_evidence_runner_v1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    supervised = subparsers.add_parser("run-supervised")
    supervised.add_argument("--corpus", type=Path, required=True)
    supervised.add_argument("--manifest", type=Path, required=True)
    supervised.add_argument("--manifest-sha256", required=True)
    supervised.add_argument("--output", type=Path, required=True)
    supervised.add_argument("--reviewed-allowlist", type=Path, required=True)
    supervised.add_argument("--decision-policy", type=Path, required=True)
    supervised.add_argument("--authority-root", type=Path, required=True)
    supervised.add_argument("--company-evidence-root", type=Path, required=True)
    supervised.add_argument("--provider-factory", required=True)
    supervised.add_argument("--decision-request-factory", required=True)

    evaluate = subparsers.add_parser("evaluate-run")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--manifest-sha256", required=True)
    evaluate.add_argument("--measurement-report", type=Path, required=True)
    evaluate.add_argument("--measurement-report-sha256", required=True)
    evaluate.add_argument("--adjudication", type=Path, required=True)
    evaluate.add_argument("--adjudication-sha256", required=True)
    evaluate.add_argument("--gate-policy", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)

    preflight = subparsers.add_parser("preflight-collection")
    preflight.add_argument("--config", type=Path, required=True)
    preflight.add_argument("--manifest-sha256", required=True)
    preflight.add_argument("--output", type=Path)

    run = subparsers.add_parser("run-collection")
    run.add_argument(
        "--manifest",
        type=Path,
        default=os.environ.get("GATE_B_EVIDENCE_MANIFEST"),
    )
    run.add_argument(
        "--state-directory",
        type=Path,
        default=os.environ.get("STATE_DIRECTORY"),
    )
    run.add_argument(
        "--config",
        type=Path,
        default=os.environ.get("GATE_B_COLLECTION_CONFIG"),
    )
    run.add_argument(
        "--manifest-sha256",
        default=os.environ.get("GATE_B_MANIFEST_SHA256"),
    )
    args = parser.parse_args(arguments)
    if args.command == "preflight-collection":
        config = _load_collection_config(args.config)
        manifest = _load_manifest(config.manifest_path, args.manifest_sha256)
        result = build_collection_preflight(config=config, manifest=manifest)
        encoded = _canonical_bytes(result)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encoded)
        print(encoded.decode("utf-8"))
        return 0 if result["status"] == "ready" else 2
    if args.command == "run-supervised":
        return _run_supervised_collection(args)
    if args.command == "evaluate-run":
        return _run_evaluate_run(args)
    if args.command == "run-collection":
        if args.manifest is None or args.state_directory is None or args.config is None:
            parser.error(
                "run-collection requires config, manifest and STATE_DIRECTORY"
            )
        if args.manifest_sha256 is None:
            parser.error("run-collection requires an external manifest SHA-256")
        config = _load_collection_config(args.config)
        if config.manifest_path.resolve() != Path(args.manifest).resolve():
            parser.error("manifest path disagrees with collection config")
        manifest = _load_manifest(args.manifest, args.manifest_sha256)
        artifact_root = Path(__file__).resolve().parents[3]
        state_directory = Path(args.state_directory).resolve()
        state_directory.mkdir(parents=True, exist_ok=True)
        spend_root_value = os.environ.get("GATE_B_SPEND_RECORD_ROOT")
        if not spend_root_value:
            parser.error("run-collection requires GATE_B_SPEND_RECORD_ROOT")
        committed_budget_reserver = _build_committed_budget_reserver(manifest)
        os.environ["GATE_B_MANIFEST_SHA256"] = manifest.manifest_sha256
        os.environ["GATE_B_PROVIDER_STORE_DIR"] = str(state_directory / "provider-records")
        provider_authority_path = config.authority_paths.get("source:provider")
        if provider_authority_path is not None:
            os.environ["GATE_B_PROVIDER_AUTHORITY_PATH"] = str(provider_authority_path)
        if os.environ.get("JOB_INTEL_LLM_LIVE_APPROVED") == "1" and (
            config.provider_factory
            != "job_intel.product_search.gate_b_evidence_runner_v1:build_live_provider_factory"
        ):
            parser.error("live approval requires live provider factory")
        provider_factory = _load_artifact_callable(config.provider_factory, artifact_root)
        decision_request_factory = _load_artifact_callable(
            config.decision_request_factory, artifact_root
        )
        reviewed_allowlist = load_reviewed_fragment_allowlist_v3(
            config.reviewed_allowlist_path
        )
        decision_policy = _load_manifest_bound_decision_policy(
            config.decision_policy_path, manifest
        )
        company_evidence_catalog, company_thesis_inputs = _load_company_authority_inputs(
            config.company_evidence_root, manifest
        )
        source_artifact, runtime, authorities = _artifact_binding_context(
            manifest, artifact_root, config.authority_paths
        )
        ledger = ForegroundDispatchLedger(
            manifest, committed_budget_reserver=committed_budget_reserver
        )
        recordings = RecordingStore(state_directory / "recordings")
        decision_evidence = DecisionEvidenceStore(state_directory / "decisions")
        if config.corpus_rows_path is not None:
            corpus_rows = _load_corpus_rows_file(config.corpus_rows_path, manifest)
        else:
            corpus_rows = load_gate_b_corpus_rows(
                package_root=config.corpus_package_root,
                gate_a_root=config.gate_a_root,
                run_manifest_path=config.run_manifest_path,
                expected_sha256=config.run_manifest_sha256,
                expected_corpus_sha256=config.corpus_sha256,
            )
        report = run_collection(
            manifest=manifest,
            corpus_rows=corpus_rows,
            reviewed_allowlist=reviewed_allowlist,
            provider_factory=provider_factory,
            ledger=ledger,
            recordings=recordings,
            decision_evidence=decision_evidence,
            decision_policy=decision_policy,
            decision_request_factory=decision_request_factory,
            company_evidence_catalog=company_evidence_catalog,
            company_thesis_inputs=company_thesis_inputs,
            source_artifact=source_artifact,
            runtime=runtime,
            authorities=authorities,
        )
        report_path = _write_measurement_report(report, state_directory)
        if not report_path.is_file():
            raise RuntimeError("collection evidence publication incomplete")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def load_gate_b_corpus_rows(
    *,
    package_root: Path,
    gate_a_root: Path,
    run_manifest_path: Path,
    expected_sha256: str,
    expected_corpus_sha256: str,
) -> tuple[CorpusRow, ...]:
    """Load and validate the pinned corpus without opening the live database."""
    from job_intel.product_search import gate_b

    payload = gate_b.load_gate_b_run_manifest(
        run_manifest_path,
        expected_sha256=expected_sha256,
        expected_corpus_sha256=expected_corpus_sha256,
    )
    records = payload["records"]
    if not isinstance(records, list):
        raise ValueError("corpus manifest records are invalid")
    loaded: list[CorpusRow] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("corpus manifest row is invalid")
        raw_reference = record.get("raw_reference")
        if not isinstance(raw_reference, str):
            raise ValueError("corpus raw reference is invalid")
        raw_bytes = gate_b.read_contained_nofollow(gate_a_root, raw_reference)
        try:
            raw = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("corpus raw artifact is invalid") from exc
        if not isinstance(raw, dict):
            raise ValueError("corpus raw artifact must be an object")
        # This validates the existing package projection and its content hashes;
        # the actual provider input below is still produced by the v3 projector.
        gate_b.load_gate_b_task10_input(
            package_root=package_root,
            record=record,
            gate_a_root=gate_a_root,
        )
        loaded.append(
            CorpusRow(
                ordinal=record.get("ordinal"),
                record=record,
                raw=raw,
            )
        )
    if tuple(row.ordinal for row in loaded) != tuple(range(48)):
        raise ValueError("corpus order does not match the 48-row manifest")
    return tuple(loaded)


def load_gate_b_corpus_rows_from_corpus_manifest(
    *,
    gate_a_root: Path,
    corpus_manifest_path: Path,
    expected_corpus_sha256: str,
) -> tuple[CorpusRow, ...]:
    """Load rows from the content-addressed corpus manifest itself.

    The run manifest is intentionally not consulted here: it is a derived
    projection artifact and may be rebuilt after this allowlist is generated.
    """
    encoded = corpus_manifest_path.read_bytes()
    observed_sha256 = _sha256(encoded)
    if observed_sha256 != expected_corpus_sha256:
        raise ValueError("corpus_manifest_sha256_mismatch")
    try:
        payload = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("corpus manifest is invalid") from exc
    if not isinstance(payload, dict) or payload.get("gate") != "gate-b":
        raise ValueError("corpus manifest is invalid")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("corpus manifest records are invalid")
    from job_intel.product_search import gate_b

    loaded: list[CorpusRow] = []
    for ordinal, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("corpus manifest row is invalid")
        raw_reference = record.get("raw_reference")
        if not isinstance(raw_reference, str):
            raise ValueError("corpus raw reference is invalid")
        raw_bytes = gate_b.read_contained_nofollow(gate_a_root, raw_reference)
        try:
            raw = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("corpus raw artifact is invalid") from exc
        if not isinstance(raw, dict):
            raise ValueError("corpus raw artifact must be an object")
        loaded.append(
            CorpusRow(
                ordinal=ordinal,
                record=record,
                raw=raw,
            )
        )
    if tuple(row.ordinal for row in loaded) != tuple(range(48)):
        raise ValueError("corpus order does not match the 48-row contract")
    return tuple(loaded)


def _derive_binding_rows(
    rows: Sequence[CorpusRow],
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
    *,
    company_evidence_catalog: CompanyEvidenceCatalogV3 | None = None,
) -> tuple[EvidenceManifestRow, ...]:
    """Derive binding identities from the loaded corpus, never from the manifest."""
    derived: list[EvidenceManifestRow] = []
    for corpus_row in rows:
        if company_evidence_catalog is None:
            projected = project_vacancy_evidence_v3(
                corpus_row.record, corpus_row.raw, reviewed_allowlist
            )
        else:
            projected = project_vacancy_evidence_v3(
                corpus_row.record,
                corpus_row.raw,
                reviewed_allowlist,
                company_evidence_catalog=company_evidence_catalog,
            )
        derived.append(
            EvidenceManifestRow(
                ordinal=corpus_row.ordinal,
                corpus_key=f"loaded-corpus-row-{corpus_row.ordinal}",
                raw_sha256=_sha256(_canonical_bytes(corpus_row.raw)),
                input_sha256=_sha256(
                    _canonical_bytes(projected.provider_payload())
                ),
                projection_sha256=_sha256(
                    _canonical_bytes(projected.model_dump(mode="json"))
                ),
            )
        )
    return tuple(derived)


def _default_binding_verifier(
    manifest: EvidenceManifest,
    *,
    source_artifact: SourceArtifact,
    runtime: FrozenRuntime,
    rows: tuple[EvidenceManifestRow, ...],
    authorities: AuthorityInputs,
) -> None:
    # Imported lazily because Task 4's runtime module imports these contracts.
    from job_intel.product_search.gate_b_runtime_v1 import verify_manifest_binding

    verify_manifest_binding(
        manifest,
        source_artifact=source_artifact,
        runtime=runtime,
        rows=rows,
        authorities=authorities,
    )


def _provider_record(provider: GovernedProvider, input_hash: str) -> dict[str, object]:
    store = getattr(provider, "store", None)
    loader = getattr(store, "load", None)
    if not callable(loader):
        raise ValueError("provider_record_store_required")
    record = loader(input_hash)
    if not isinstance(record, dict):
        raise ValueError("provider_record_invalid")
    if record.get("provider_record_kind") == "gate-b-evidence-synthesis-v2":
        verifier = getattr(provider, "verify_provider_record", None)
        if not callable(verifier):
            raise ValueError("v2_provider_record_verifier_required")
        verifier(record)
    return record


def _validate_decision_request_binding(
    request: DecisionRequestV2,
    *,
    row: EvidenceManifestRow,
    provider_record: Mapping[str, object],
) -> None:
    expected_output_sha256 = str(
        _required_provider_value(provider_record, "output_sha256")
    )
    if request.references.provider_input_sha256 != row.input_sha256:
        raise ValueError("decision_request_input_binding_mismatch")
    if request.synthesis.metadata.input_sha256 != row.input_sha256:
        raise ValueError("decision_request_input_binding_mismatch")
    if request.references.provider_output_sha256 != expected_output_sha256:
        raise ValueError("decision_request_output_binding_mismatch")
    if request.synthesis.metadata.output_sha256 != expected_output_sha256:
        raise ValueError("decision_request_output_binding_mismatch")


def _reservation_input_hash(ref: ManifestRef) -> str:
    """Namespace provider-runtime records by the complete row identity."""
    return _sha256(_canonical_bytes(ref.model_dump(mode="json")))


def _provider_authority_identity(provider: GovernedProvider) -> Mapping[str, str]:
    supplied = getattr(provider, "authority_identity", None)
    identity = supplied() if callable(supplied) else supplied
    if not isinstance(identity, Mapping):
        raise ValueError("provider_authority_identity_required")
    names = ("provider_sha256", "model_sha256", "prompt_sha256", "response_schema_sha256", "pricing_sha256")
    if any(not isinstance(identity.get(name), str) for name in names):
        raise ValueError("provider_authority_identity_incomplete")
    return {name: str(identity[name]) for name in names}


def _assert_provider_authority(
    manifest: EvidenceManifest, provider: GovernedProvider
) -> None:
    observed = _provider_authority_identity(provider)
    expected = {
        "provider_sha256": manifest.authorities.source_authority_sha256s.get(
            "provider"
        ),
        "model_sha256": manifest.authorities.model_sha256,
        "prompt_sha256": manifest.authorities.prompt_sha256,
        "response_schema_sha256": manifest.authorities.response_schema_sha256,
        "pricing_sha256": manifest.authorities.pricing_sha256,
    }
    if any(observed.get(name) != expected.get(name) for name in expected):
        raise ValueError("provider_authority_mismatch")


def _assert_provider_record_authority(
    manifest: EvidenceManifest, record: Mapping[str, object]
) -> None:
    provider_id = record.get("provider_id")
    model_id = record.get("model_id")
    observed = {
        "provider_sha256": record.get(
            "provider_sha256",
            _sha256(str(provider_id).encode("utf-8")) if provider_id else None,
        ),
        "model_sha256": record.get(
            "model_sha256",
            _sha256(str(model_id).encode("utf-8")) if model_id else None,
        ),
        "prompt_sha256": record.get(
            "structured_prompt_sha256",
            record.get("prompt_sha256", record.get("semantic_prompt_sha256")),
        ),
        "response_schema_sha256": record.get("response_schema_sha256"),
        "pricing_sha256": record.get("pricing_sha256"),
    }
    expected = {
        "provider_sha256": manifest.authorities.source_authority_sha256s.get(
            "provider"
        ),
        "model_sha256": manifest.authorities.model_sha256,
        "prompt_sha256": manifest.authorities.prompt_sha256,
        "response_schema_sha256": manifest.authorities.response_schema_sha256,
        "pricing_sha256": manifest.authorities.pricing_sha256,
    }
    if any(observed.get(name) != expected.get(name) for name in expected):
        raise ValueError("provider_record_authority_mismatch")


def _provider_dispatch_result(
    provider: GovernedProvider, input_hash: str, result: object
) -> tuple[dict[str, object], str, str, bytes, Decimal | None, Decimal]:
    record = _provider_record(provider, input_hash)
    outcome = str(record.get("post_dispatch_outcome_v3", ""))
    try:
        terminal = TerminalOutcome(outcome)
    except ValueError as exc:
        raise ValueError("provider_record_outcome_invalid") from exc
    provider_record_sha256 = _sha256(_canonical_bytes(record))
    raw_response = record.get("raw_response_text", "")
    if not isinstance(raw_response, str):
        raise ValueError("provider_record_response_invalid")
    try:
        measured = (
            None
            if record.get("measured_cost_usd") is None
            else Decimal(str(record["measured_cost_usd"]))
        )
        conservative = Decimal(str(record["conservative_cost_usd"]))
    except Exception as exc:
        raise ValueError("provider_record_cost_invalid") from exc
    if not conservative.is_finite() or conservative < 0:
        raise ValueError("provider_record_cost_invalid")
    if terminal is TerminalOutcome.TERMINAL_UNKNOWN and raw_response:
        raise ValueError("provider_record_unknown_response_not_empty")
    if terminal is TerminalOutcome.TERMINAL_UNKNOWN:
        payload: dict[str, object] = {}
    else:
        try:
            parsed = json.loads(raw_response) if raw_response else {}
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("provider_record_response_invalid") from exc
        if not isinstance(parsed, dict):
            raise ValueError("provider_record_response_invalid")
        payload = parsed
    return record, provider_record_sha256, outcome, raw_response.encode("utf-8"), measured, conservative


def _issue_collection_capability(
    *,
    manifest: EvidenceManifest,
    provider: GovernedProvider,
    ledger: ForegroundDispatchLedger,
) -> object:
    try:
        from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
            _issue_structured_call_capability,
        )
    except ImportError as exc:
        raise ValueError("governed_provider_runtime_unavailable") from exc
    pricing = getattr(provider, "pricing", None)
    pricing_identity = getattr(pricing, "identity_sha256", None)
    if not isinstance(pricing_identity, str):
        raise ValueError("provider_pricing_identity_required")
    if pricing_identity != manifest.authorities.pricing_sha256:
        raise ValueError("pricing_identity_mismatch")
    if getattr(pricing, "reservation_cost_usd", None) != manifest.limits.per_call_maximum_usd:
        raise ValueError("pricing_reservation_mismatch")
    # Step 1 scope: this capability and its counters live for one foreground
    # process only. A restart is not promised to preserve the cap; reopening a
    # run is an explicit later recovery concern, not an implicit retry path.
    reservations: dict[str, str] = {}
    reservation_refs: dict[str, ManifestRef] = {}
    for row in manifest.rows:
        ref = manifest.row_ref(row.ordinal)
        dispatch_key = _reservation_input_hash(ref)
        if dispatch_key in reservation_refs:
            raise ValueError("reservation_identity_collision")
        reservation_refs[dispatch_key] = ref
    receipts: dict[str, DispatchReceipt] = {}
    transport_receipts: dict[str, _TransportRecordReceipt] = {}
    record_identity_bindings: dict[str, str] = {}
    provider_input_to_dispatch: dict[str, str] = {}

    def capture_record(record: dict[str, object]) -> _TransportRecordReceipt | None:
        if record.get("provider_record_kind") == "gate-b-evidence-synthesis-v2":
            return None
        input_hash = record.get("input_hash")
        if not isinstance(input_hash, str) or not input_hash:
            raise ValueError("transport_receipt_input_hash_missing")
        record_bytes = _canonical_bytes(record)
        receipt = _TransportRecordReceipt(
            input_hash=input_hash,
            record_sha256=_sha256(record_bytes),
            record_bytes=record_bytes,
        )
        previous = transport_receipts.get(input_hash)
        if previous is not None and previous.record_sha256 != receipt.record_sha256:
            raise ValueError("transport_receipt_identity_conflict")
        transport_receipts[input_hash] = receipt
        return receipt

    def bind_record_identity(
        dispatch_input_hash: str, provider_input_hash: str
    ) -> None:
        if dispatch_input_hash not in reservation_refs:
            raise ValueError("transport_receipt_dispatch_unknown")
        previous = record_identity_bindings.get(dispatch_input_hash)
        if previous is not None and previous != provider_input_hash:
            raise ValueError("transport_receipt_identity_conflict")
        record_identity_bindings[dispatch_input_hash] = provider_input_hash
        previous_dispatch = provider_input_to_dispatch.get(provider_input_hash)
        if previous_dispatch is not None and previous_dispatch != dispatch_input_hash:
            raise ValueError("transport_receipt_identity_conflict")
        provider_input_to_dispatch[provider_input_hash] = dispatch_input_hash

    def reserve(dispatch_key: str, _amount: Decimal) -> str:
        dispatch_key = provider_input_to_dispatch.get(dispatch_key, dispatch_key)
        if dispatch_key not in reservation_refs:
            raise ValueError("reservation_manifest_ref_missing")
        reservation_id = f"gate-b:{dispatch_key}"
        reservations[reservation_id] = dispatch_key
        return reservation_id

    def mark_dispatching(reservation_id: str) -> None:
        dispatch_key = reservations.get(reservation_id)
        if dispatch_key is None:
            raise ValueError("reservation_unknown")
        ref = reservation_refs.get(dispatch_key)
        if ref is None:
            raise ValueError("reservation_manifest_ref_missing")
        receipts[reservation_id] = ledger.append_pre_dispatch(ref)

    def reconcile(
        reservation_id: str, actual_cost: Decimal, outcome: str
    ) -> None:
        dispatch_key = reservations.get(reservation_id)
        receipt = receipts.get(reservation_id)
        if dispatch_key is None or receipt is None:
            raise ValueError("reservation_unknown")
        provider_input_hash = record_identity_bindings.pop(dispatch_key, None)
        if provider_input_hash is None:
            raise ValueError("transport_receipt_binding_missing")
        transport_receipt = transport_receipts.pop(provider_input_hash, None)
        if transport_receipt is None:
            raise ValueError("transport_receipt_missing")
        if transport_receipt.input_hash != provider_input_hash:
            raise ValueError("transport_receipt_identity_mismatch")
        record = transport_receipt.record
        if record.get("post_dispatch_outcome_v3") != outcome:
            raise ValueError("transport_receipt_outcome_mismatch")
        # The final V2 authority check runs after V2 publication. The
        # pre-reconcile proof is the immutable transport receipt.
        transport_record_sha256 = transport_receipt.record_sha256
        measured = (
            None
            if record.get("measured_cost_usd") is None
            else Decimal(str(record["measured_cost_usd"]))
        )
        conservative = Decimal(str(record.get("conservative_cost_usd")))
        ledger.commit_terminal(
            receipt,
            TerminalOutcome(outcome),
            transport_record_sha256,
            measured,
            conservative,
        )

    metadata_seal_key = hashlib.sha256(
        ("gate-b-provider-record:" + manifest.manifest_sha256).encode("ascii")
    ).digest()
    return _issue_structured_call_capability(
        run_identity_sha256=manifest.manifest_sha256,
        pricing=pricing,
        exact_call_cap=manifest.limits.ordered_call_cap,
        exact_spend_cap_usd=manifest.limits.aggregate_maximum_usd,
        metadata_seal_key=metadata_seal_key,
        reserve=reserve,
        mark_dispatching=mark_dispatching,
        reconcile=reconcile,
        capture_record=capture_record,
        bind_record_identity=bind_record_identity,
    )


def run_collection(
    *,
    manifest: EvidenceManifest,
    corpus_rows: Sequence[CorpusRow],
    reviewed_allowlist: ReviewedFragmentAllowlistV3 | None = None,
    company_evidence_catalog: CompanyEvidenceCatalogV3 | None = None,
    company_thesis_inputs: Mapping[str, CompanyThesisInputV1] | None = None,
    provider_factory: Callable[[], GovernedProvider],
    ledger: ForegroundDispatchLedger | None = None,
    recordings: RecordingStore | None = None,
    decision_evidence: DecisionEvidenceStore | None = None,
    decision_policy: LoadedDecisionPolicyV2,
    decision_request_factory: Callable[
        [DecisionRequestFactoryContextV1], DecisionRequestV2
    ] | None = None,
    source_artifact: SourceArtifact,
    runtime: FrozenRuntime,
    authorities: AuthorityInputs,
    binding_verifier: Callable[..., None] | None = None,
) -> CollectionReport:
    """Collect all supplied rows, then publish evidence metrics only.

    Binding is checked immediately before provider construction and again after
    the final recording is durable.  The returned object intentionally has no
    GateDecision field; per-row Decision v2 is evidence and adjudication and
    thresholds are a separate deterministic Task 8 operation.
    """
    rows = tuple(corpus_rows)
    if len(rows) != manifest.row_count:
        raise ValueError("collection row count does not match manifest")
    if tuple(row.ordinal for row in rows) != tuple(range(manifest.row_count)):
        raise ValueError("collection rows are not in manifest order")
    if reviewed_allowlist is None:
        raise ValueError("reviewed_allowlist_required")
    if isinstance(manifest.corpus_sha256, str):
        validate_reviewed_fragment_allowlist_corpus_v3(
            reviewed_allowlist,
            corpus_sha256=manifest.corpus_sha256,
        )
    elif isinstance(manifest, EvidenceManifest):
        raise ValueError("manifest_corpus_sha256_required")
    if ledger is None or recordings is None or decision_evidence is None:
        raise ValueError("dispatch_ledger_recordings_and_decision_evidence_required")
    if decision_policy is None or decision_request_factory is None:
        raise ValueError("decision_policy_and_request_factory_required")
    verifier = binding_verifier or _default_binding_verifier
    binding_rows = _derive_binding_rows(
        rows,
        reviewed_allowlist,
        company_evidence_catalog=company_evidence_catalog,
    )
    verifier(
        manifest,
        source_artifact=source_artifact,
        runtime=runtime,
        rows=binding_rows,
        authorities=authorities,
    )
    provider = provider_factory()
    _assert_provider_authority(manifest, provider)
    capability = _issue_collection_capability(
        manifest=manifest, provider=provider, ledger=ledger
    )
    results: list[CollectionRowResult] = []
    for corpus_row in rows:
        row = manifest.row(corpus_row.ordinal)
        if company_evidence_catalog is None:
            projected = project_vacancy_evidence_v3(
                corpus_row.record, corpus_row.raw, reviewed_allowlist
            )
        else:
            projected = project_vacancy_evidence_v3(
                corpus_row.record,
                corpus_row.raw,
                reviewed_allowlist,
                company_evidence_catalog=company_evidence_catalog,
            )
        projection_sha256 = _sha256(
            _canonical_bytes(projected.model_dump(mode="json"))
        )
        if projection_sha256 != row.projection_sha256:
            raise ValueError("projection hash does not match manifest row")
        request_payload = projected.provider_payload()
        request_bytes = _canonical_bytes(request_payload)
        if _sha256(request_bytes) != row.input_sha256:
            raise ValueError("provider input hash does not match manifest row")
        ref = manifest.row_ref(corpus_row.ordinal)
        dispatch_input_hash = _reservation_input_hash(ref)
        try:
            dispatch_result = provider.dispatch(
                request_payload,
                input_hash=dispatch_input_hash,
                capability=capability,
            )
        except LLMProviderError:
            # The governed provider persists its canonical terminal-failure
            # record before raising.  Consume that record as the row result;
            # an absent record is a fail-closed provider contract violation.
            try:
                _provider_record(provider, dispatch_input_hash)
            except Exception as exc:
                raise ValueError("provider_failure_record_missing") from exc
            dispatch_result = None
        (
            provider_record,
            provider_record_sha256,
            provider_outcome,
            response_bytes,
            measured_cost,
            conservative_cost,
        ) = _provider_dispatch_result(provider, dispatch_input_hash, dispatch_result)
        _assert_provider_record_authority(manifest, provider_record)
        outcome = TerminalOutcome(provider_outcome)
        response_payload = json.loads(response_bytes) if response_bytes else {}
        canonical_response_bytes = _canonical_bytes(response_payload)
        sealed_response_bytes = (
            b"" if outcome is TerminalOutcome.TERMINAL_UNKNOWN else canonical_response_bytes
        )
        validation_status = validate_provider_payload_v3(
            response_payload,
            synthesis_input=projected,
            reviewed_allowlist=reviewed_allowlist,
        )
        company_thesis_input = None
        if company_thesis_inputs is not None and getattr(
            projected.company_authority, "status", None
        ) == "available":
            company_id = projected.company_authority.company_evidence_bundle.company_identity.company_id
            company_thesis_input = company_thesis_inputs.get(company_id)
        decision_request = decision_request_factory(
            DecisionRequestFactoryContextV1(
                response_payload=response_payload,
                projected=projected,
                manifest_ref=ref,
                raw=dict(corpus_row.raw),
                provider_record=dict(provider_record),
                validation_status=validation_status,
                decision_policy=decision_policy,
                decision_clock=manifest.decision_clock,
                company_thesis_input=company_thesis_input,
            )
        )
        _validate_decision_request_binding(
            decision_request,
            row=row,
            provider_record=provider_record,
        )
        decision = run_decision_v2(decision_request, policy=decision_policy)
        decision_bytes = canonical_decision_bytes(decision)
        decision_ref = decision_evidence.save_exclusive(ref, decision_bytes)
        recording_ref = recordings.save_exclusive(
            SealedRecording(
                manifest_ref=ref,
                request_bytes=request_bytes,
                response_bytes=sealed_response_bytes,
                outcome=outcome,
                metadata={
                    "input_sha256": row.input_sha256,
                    "projection_sha256": row.projection_sha256,
                    "response_sha256": _sha256(sealed_response_bytes),
                    "provider_record_sha256": provider_record_sha256,
                    "provider_id": str(provider_record.get("provider_id", "")),
                    "model_id": str(provider_record.get("model_id", "")),
                    "provider_sha256": str(provider_record.get("provider_sha256", "")),
                    "model_sha256": str(provider_record.get("model_sha256", "")),
                    "prompt_sha256": str(
                        provider_record.get(
                            "structured_prompt_sha256",
                            provider_record.get(
                                "prompt_sha256",
                                provider_record.get("semantic_prompt_sha256", ""),
                            ),
                        )
                    ),
                    "response_schema_sha256": str(
                        provider_record.get("response_schema_sha256", "")
                    ),
                    "pricing_sha256": str(provider_record.get("pricing_sha256", "")),
                    "conservative_cost_usd": str(conservative_cost),
                    "validator": "gate_b_evidence_v3",
                },
            )
        )
        dispatch_entry = next(
            entry
            for entry in ledger.entries()
            if entry.manifest_ref.ordinal == ref.ordinal
        )
        recordings.verify(recording_ref, manifest, dispatch_entry)
        results.append(
            CollectionRowResult(
                manifest_ref=ref,
                validation_status=validation_status,
                recording_ref=recording_ref,
                recording_bytes=recordings.bytes_for(recording_ref),
                outcome=outcome,
                decision=decision,
                decision_ref=decision_ref,
                decision_bytes=decision_evidence.bytes_for(decision_ref),
            )
        )
    verifier(
        manifest,
        source_artifact=source_artifact,
        runtime=runtime,
        rows=binding_rows,
        authorities=authorities,
    )
    deliverable_count = sum(
        result.outcome is TerminalOutcome.SUCCESS
        and result.validation_status is None
        for result in results
    )
    terminal_unknown_count = sum(
        result.outcome is TerminalOutcome.TERMINAL_UNKNOWN for result in results
    )
    return CollectionReport(
        run_id=manifest.run_id,
        manifest_sha256=manifest.manifest_sha256,
        rows=tuple(results),
        metrics=CollectionMetrics(
            expected_row_count=manifest.row_count,
            observed_row_count=len(results),
            deliverable_count=deliverable_count,
            terminal_unknown_count=terminal_unknown_count,
        ),
    )


def run_one_row(
    *,
    manifest: EvidenceManifest,
    ordinal: int,
    record: Mapping[str, object],
    raw: Mapping[str, object],
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
    company_evidence_catalog: CompanyEvidenceCatalogV3 | None = None,
    provider: GovernedProvider,
    ledger: ForegroundDispatchLedger,
    recordings: RecordingStore,
    decision_evidence: DecisionEvidenceStore,
    decision_request_factory: Callable[
        [DecisionRequestFactoryContextV1], DecisionRequestV2
    ],
    decision_policy: LoadedDecisionPolicyV2 | None = None,
    decision_clock: datetime,
    provider_record: Mapping[str, object] | None = None,
) -> OneRowResult:
    """Task 3 compatibility skeleton with per-row Decision v2 evidence only."""
    row = manifest.row(ordinal)
    if company_evidence_catalog is None:
        projected = project_vacancy_evidence_v3(record, raw, reviewed_allowlist)
    else:
        projected = project_vacancy_evidence_v3(
            record,
            raw,
            reviewed_allowlist,
            company_evidence_catalog=company_evidence_catalog,
        )
    projection_sha256 = _sha256(_canonical_bytes(projected.model_dump(mode="json")))
    if projection_sha256 != row.projection_sha256:
        raise ValueError("projection hash does not match manifest row")
    request_payload = projected.provider_payload()
    request_bytes = _canonical_bytes(request_payload)
    if _sha256(request_bytes) != row.input_sha256:
        raise ValueError("provider input hash does not match manifest row")
    ref = manifest.row_ref(ordinal)

    # This append/fsync is intentionally before the fake transport call.
    receipt = ledger.append_pre_dispatch(ref)
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
    provider_record_sha256 = getattr(provider, "provider_record_sha256", None)
    if not isinstance(provider_record_sha256, str):
        raise ValueError("provider record anchor required")
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
                "provider_record_sha256": provider_record_sha256,
                "validator": "gate_b_evidence_v3",
            },
        ),
    )
    ledger.commit_terminal(
        receipt,
        outcome,
        provider_record_sha256,
        Decimal("0"),
        Decimal("0"),
    )
    if validation_status is not None:
        raise ValueError(f"provider payload rejected: {validation_status.value}")

    decision_request = decision_request_factory(
        DecisionRequestFactoryContextV1(
            response_payload=response_payload,
            projected=projected,
            manifest_ref=ref,
            raw=dict(raw),
            provider_record=dict(provider_record or {}),
            validation_status=validation_status,
            decision_policy=decision_policy
            if decision_policy is not None
            else load_decision_policy(),
            decision_clock=decision_clock,
        )
    )
    decision = run_decision_v2(
        decision_request,
        policy=decision_policy,
    )
    decision_bytes = canonical_decision_bytes(decision)
    decision_ref = decision_evidence.save_exclusive(ref, decision_bytes)
    return OneRowResult(
        manifest_ref=ref,
        validation_status=validation_status,
        recording_ref=recording_ref,
        recording_bytes=recordings.bytes_for(recording_ref),
        decision=decision,
        decision_ref=decision_ref,
        decision_bytes=decision_evidence.bytes_for(decision_ref),
    )


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
