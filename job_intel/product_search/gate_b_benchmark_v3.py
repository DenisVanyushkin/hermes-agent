"""Closed, additive Gate B at-most-once benchmark vocabulary (v3)."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import errno
import fcntl
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import sys
import sysconfig
from types import MappingProxyType
from typing import Any, Literal, Self

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from pydantic import model_validator

from job_intel.product_search.contracts import SHA256_PATTERN
from job_intel.product_search.evidence_synthesis import (
    EvidenceSynthesisPolicyV1,
    ProviderEvidencePayloadV2,
    TASK10_PROMPT_VERSION_V2,
    _PRIVATE_MARKERS,
    build_task10_prompt_v2,
    load_evidence_synthesis_policy,
    post_dispatch_outcome_v3,
)
from job_intel.vacancy_understanding.semantic.contract import (
    SemanticFactContract,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    GovernedPricingSchedule,
    GovernedStructuredRequest,
    GovernedStructuredResult,
    GovernedStructuredTerminalUnknown,
    LLMProviderError,
    _issue_structured_call_capability,
    build_live_llm_provider,
    build_prompt_for_version,
)


DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/gate_b_benchmark.v3.yaml"
)
_ORDERED_CALL_CAP = 48
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
    ): frozenset({"runner", "owner_recovery"}),
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


_ZERO_SHA256 = "0" * 64
_PRIVATE_FILE_MODE = 0o600
_PRIVATE_DIRECTORY_MODE = 0o700
_TERMINAL_STATES = frozenset(
    {
        GateBCallStateV3.SUCCESS,
        GateBCallStateV3.TERMINAL_FAILURE,
        GateBCallStateV3.TERMINAL_UNKNOWN,
    }
)
_AT_FDCWD = -100
_AT_SYMLINK_FOLLOW = 0x400
_OWNER_SIGNATURE_DOMAIN = b"gate-b-owner-recovery-v3\0"


class GateBLedgerErrorV3(ValueError):
    """Fail-closed v3 ledger or recovery validation error."""


class GateBLedgerRowV3(_StrictFrozenModel):
    ordinal: int = Field(ge=0, lt=_ORDERED_CALL_CAP)
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    state: GateBCallStateV3
    dispatch_id: str | None = None
    dispatch_marker_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    recording_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    measured_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    conservative_cost_usd: Decimal = Field(ge=Decimal("0"))


class GateBRecoveryTransitionV3(_StrictFrozenModel):
    ordinal: int = Field(ge=0, lt=_ORDERED_CALL_CAP)
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    source_state: GateBCallStateV3
    target_state: GateBCallStateV3


def _recovery_inventory_sha256(
    *,
    run_id: str,
    package_manifest_sha256: str,
    owner_recovery_public_key_sha256: str,
    ledger_head_sha256: str,
    rows: tuple[GateBLedgerRowV3, ...],
) -> str:
    return canonical_json_sha256(
        {
            "schema_version": "3.0.0",
            "run_id": run_id,
            "package_manifest_sha256": package_manifest_sha256,
            "owner_recovery_public_key_sha256": (
                owner_recovery_public_key_sha256
            ),
            "ledger_head_sha256": ledger_head_sha256,
            "rows": [row.model_dump(mode="json") for row in rows],
        }
    )


class GateBRecoveryRequestV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    run_id: str = Field(min_length=1)
    package_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    owner_recovery_public_key_sha256: str = Field(pattern=SHA256_PATTERN)
    ledger_head_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    rows: tuple[GateBLedgerRowV3, ...] = Field(
        min_length=_ORDERED_CALL_CAP,
        max_length=_ORDERED_CALL_CAP,
    )
    conservative_spend_usd: Decimal = Field(ge=Decimal("0"))
    requested_transitions: tuple[GateBRecoveryTransitionV3, ...]

    @model_validator(mode="after")
    def validate_complete_inventory(self) -> Self:
        if tuple(row.ordinal for row in self.rows) != tuple(range(_ORDERED_CALL_CAP)):
            raise ValueError("recovery rows must cover all 48 ordinals in order")
        transition_ordinals = tuple(
            transition.ordinal for transition in self.requested_transitions
        )
        if transition_ordinals != tuple(sorted(set(transition_ordinals))):
            raise ValueError("recovery transitions must be sorted and unique")
        if self.conservative_spend_usd != sum(
            (row.conservative_cost_usd for row in self.rows),
            start=Decimal("0"),
        ):
            raise ValueError("recovery conservative spend must match all rows")
        if self.inventory_sha256 != _recovery_inventory_sha256(
            run_id=self.run_id,
            package_manifest_sha256=self.package_manifest_sha256,
            owner_recovery_public_key_sha256=(
                self.owner_recovery_public_key_sha256
            ),
            ledger_head_sha256=self.ledger_head_sha256,
            rows=self.rows,
        ):
            raise ValueError("recovery inventory hash must match the complete snapshot")
        return self

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class GateBRecoveryDecisionV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    ledger_head_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    requested_transitions: tuple[GateBRecoveryTransitionV3, ...]
    approved_by: str = Field(min_length=1)
    approved_at: AwareDatetime
    owner_signature_hex: str = Field(pattern=r"^[0-9a-f]{128}$")

    @field_validator("approved_at")
    @classmethod
    def validate_approved_at(cls, value: AwareDatetime) -> AwareDatetime:
        return _require_utc(value)

    def _approval_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"owner_signature_hex"})

    @property
    def owner_approval_sha256(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.owner_signature_hex)).hexdigest()

    def verify_owner_signature(self, owner_public_key: bytes) -> None:
        try:
            verifier = Ed25519PublicKey.from_public_bytes(owner_public_key)
            verifier.verify(
                bytes.fromhex(self.owner_signature_hex),
                _OWNER_SIGNATURE_DOMAIN
                + _canonical_json_bytes(self._approval_payload()),
            )
        except (InvalidSignature, ValueError) as exc:
            raise GateBLedgerErrorV3("owner_signature_invalid") from exc

    @classmethod
    def approve(
        cls,
        request: GateBRecoveryRequestV3,
        *,
        approved_by: str,
        approved_at: datetime,
        owner_private_key: bytes,
    ) -> Self:
        unsigned = cls(
            schema_version="3.0.0",
            request_sha256=request.canonical_sha256,
            ledger_head_sha256=request.ledger_head_sha256,
            inventory_sha256=request.inventory_sha256,
            requested_transitions=request.requested_transitions,
            approved_by=approved_by,
            approved_at=approved_at,
            owner_signature_hex="0" * 128,
        )
        try:
            signer = Ed25519PrivateKey.from_private_bytes(owner_private_key)
        except ValueError as exc:
            raise GateBLedgerErrorV3("owner_private_key_invalid") from exc
        return unsigned.model_copy(
            update={
                "owner_signature_hex": signer.sign(
                    _OWNER_SIGNATURE_DOMAIN
                    + _canonical_json_bytes(unsigned._approval_payload())
                ).hex()
            }
        )


def retry_allowed(state: GateBCallStateV3 | str) -> bool:
    """Only untouched pending rows may enter the runner again."""
    try:
        return GateBCallStateV3(state) is GateBCallStateV3.PENDING
    except ValueError:
        return False


def recovered_cost(state: GateBCallStateV3 | str) -> Decimal:
    """Return the mandatory conservative recovery cost for an unmeasured state."""
    try:
        normalized = GateBCallStateV3(state)
    except ValueError as exc:
        raise GateBLedgerErrorV3("ledger_state_invalid") from exc
    if normalized in {
        GateBCallStateV3.DISPATCHED,
        GateBCallStateV3.TERMINAL_UNKNOWN,
    }:
        return _PER_CALL_MAXIMUM_USD
    return Decimal("0")


def _read_all(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise GateBLedgerErrorV3("ledger_write_failed")
        view = view[written:]


def _fsync_prepared_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _link_anonymous_file(
    descriptor: int,
    parent_descriptor: int,
    final_name: str,
) -> None:
    """Publish a prepared O_TMPFILE inode atomically without replacement."""
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = libc.linkat
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    source = f"/proc/self/fd/{descriptor}".encode("ascii")
    result = linkat(
        _AT_FDCWD,
        source,
        parent_descriptor,
        os.fsencode(final_name),
        _AT_SYMLINK_FOLLOW,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number), final_name)
    raise OSError(error_number, os.strerror(error_number), final_name)


def _publish_prepared_file(
    parent_descriptor: int,
    final_name: str,
    payload: bytes,
    *,
    expected_uid: int,
    error_prefix: str,
) -> tuple[int, tuple[int, int]]:
    """Fsync an anonymous inode, then publish its complete bytes create-once."""
    try:
        descriptor = os.open(
            ".",
            os.O_RDWR | os.O_APPEND | os.O_TMPFILE | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise GateBLedgerErrorV3(f"{error_prefix}_atomic_create_unsupported") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 0
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
        ):
            raise GateBLedgerErrorV3(f"{error_prefix}_prepared_inode_invalid")
        _write_all(descriptor, payload)
        _fsync_prepared_file(descriptor)
        _link_anonymous_file(descriptor, parent_descriptor, final_name)
        os.fsync(parent_descriptor)
        published = os.fstat(descriptor)
        if published.st_nlink != 1:
            raise GateBLedgerErrorV3(f"{error_prefix}_publish_link_invalid")
        return descriptor, (published.st_dev, published.st_ino)
    except Exception:
        os.close(descriptor)
        raise


class GateBLedgerV3:
    """Descriptor-owned append-only ledger with independent dispatch evidence."""

    ledger_filename = "ledger.jsonl"
    dispatch_markers_dirname = "dispatch-markers"
    recordings_dirname = "recordings"

    def __init__(
        self,
        root: Path,
        launch_identity: GateBLaunchIdentityV3,
        package_manifest: GateBPackageManifestV3,
        *,
        owner_recovery_public_key: bytes,
        expected_uid: int | None = None,
    ) -> None:
        if (
            launch_identity.package_manifest_sha256
            != package_manifest.canonical_sha256
        ):
            raise GateBLedgerErrorV3("ledger_identity_package_mismatch")
        try:
            Ed25519PublicKey.from_public_bytes(owner_recovery_public_key)
        except ValueError as exc:
            raise GateBLedgerErrorV3("ledger_owner_public_key_invalid") from exc
        self.root = Path(root)
        self.launch_identity = launch_identity
        self.package_manifest = package_manifest
        self.owner_recovery_public_key = bytes(owner_recovery_public_key)
        self.owner_recovery_public_key_sha256 = hashlib.sha256(
            self.owner_recovery_public_key
        ).hexdigest()
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid
        self.ledger_path = self.root / self.ledger_filename
        self.dispatch_markers_path = self.root / self.dispatch_markers_dirname
        self.recordings_path = self.root / self.recordings_dirname
        self._root_descriptor = -1
        self._marker_directory_descriptor = -1
        self._recording_directory_descriptor = -1
        self._ledger_descriptor = -1
        self._root_identity: tuple[int, int] | None = None
        self._marker_directory_identity: tuple[int, int] | None = None
        self._recording_directory_identity: tuple[int, int] | None = None
        self._ledger_identity: tuple[int, int] | None = None
        self._ledger_head_sha256 = _ZERO_SHA256
        self._sequence = 0
        self._ledger_size = 0
        self._ledger_content_sha256 = hashlib.sha256(b"").hexdigest()
        self._rows: list[dict[str, Any]] = []
        self._markers: dict[int, dict[str, Any]] = {}
        self._recordings: dict[int, dict[str, Any]] = {}
        self._replayed_entries: tuple[dict[str, Any], ...] = ()
        try:
            self._open()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        for attribute in (
            "_ledger_descriptor",
            "_recording_directory_descriptor",
            "_marker_directory_descriptor",
            "_root_descriptor",
        ):
            descriptor = getattr(self, attribute, -1)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                finally:
                    setattr(self, attribute, -1)

    @property
    def ledger_head_sha256(self) -> str:
        return self._ledger_head_sha256

    @property
    def inventory_sha256(self) -> str:
        return _recovery_inventory_sha256(
            run_id=self.launch_identity.run_id,
            package_manifest_sha256=self.package_manifest.canonical_sha256,
            owner_recovery_public_key_sha256=(
                self.owner_recovery_public_key_sha256
            ),
            ledger_head_sha256=self.ledger_head_sha256,
            rows=self.rows(),
        )

    @property
    def conservative_spend_usd(self) -> Decimal:
        return sum(
            (row.conservative_cost_usd for row in self.rows()),
            start=Decimal("0"),
        )

    def state(self, ordinal: int) -> GateBCallStateV3:
        return self.row(ordinal).state

    def row(self, ordinal: int) -> GateBLedgerRowV3:
        payload = self._mutable_row(ordinal)
        return GateBLedgerRowV3.model_validate(dict(payload))

    def rows(self) -> tuple[GateBLedgerRowV3, ...]:
        return tuple(self.row(ordinal) for ordinal in range(_ORDERED_CALL_CAP))

    def retry_allowed(self, ordinal: int) -> bool:
        return retry_allowed(self.state(ordinal))

    def validate_snapshot(self) -> None:
        """Revalidate descriptor, bytes, and artifact inventory before export."""
        self._assert_mutation_safe()

    def reserve(self, ordinal: int) -> None:
        self._assert_mutation_safe()
        row = self._mutable_row(ordinal)
        if row["state"] is not GateBCallStateV3.PENDING:
            raise GateBLedgerErrorV3("ledger_transition_not_allowed")
        self._append_transition(
            ordinal,
            source=GateBCallStateV3.PENDING,
            target=GateBCallStateV3.RESERVED,
            actor="runner",
            dispatch_id=None,
            marker_sha256=None,
            recording_sha256=None,
            measured_cost_usd=None,
            conservative_cost_usd=Decimal("0"),
        )
        row["state"] = GateBCallStateV3.RESERVED

    def mark_dispatched(self, ordinal: int, *, dispatch_id: str) -> None:
        self._assert_mutation_safe()
        if not dispatch_id:
            raise GateBLedgerErrorV3("ledger_dispatch_id_invalid")
        row = self._mutable_row(ordinal)
        if row["state"] is not GateBCallStateV3.RESERVED:
            raise GateBLedgerErrorV3("ledger_transition_not_allowed")
        marker_payload = {
            "schema_version": "3.0.0",
            "artifact_kind": "dispatch_marker",
            "run_id": self.launch_identity.run_id,
            "package_manifest_sha256": self.package_manifest.canonical_sha256,
            "ordinal": ordinal,
            "input_sha256": row["input_sha256"],
            "dispatch_id": dispatch_id,
        }
        marker_sha256, marker_identity = self._create_inventory_file(
            directory_descriptor=self._marker_directory_descriptor,
            directory_kind="marker",
            filename=self._artifact_filename(ordinal),
            payload=marker_payload,
        )
        self._markers[ordinal] = {
            "payload": marker_payload,
            "sha256": marker_sha256,
            "identity": marker_identity,
        }
        row.update(
            state=GateBCallStateV3.DISPATCHED,
            dispatch_id=dispatch_id,
            dispatch_marker_sha256=marker_sha256,
            conservative_cost_usd=_PER_CALL_MAXIMUM_USD,
        )
        self._append_transition(
            ordinal,
            source=GateBCallStateV3.RESERVED,
            target=GateBCallStateV3.DISPATCHED,
            actor="runner",
            dispatch_id=dispatch_id,
            marker_sha256=marker_sha256,
            recording_sha256=None,
            measured_cost_usd=None,
            conservative_cost_usd=_PER_CALL_MAXIMUM_USD,
        )

    def record_success(
        self,
        ordinal: int,
        *,
        dispatch_id: str,
        provider_record_sha256: str,
        measured_cost_usd: Decimal,
    ) -> None:
        self._record_terminal(
            ordinal,
            target=GateBCallStateV3.SUCCESS,
            dispatch_id=dispatch_id,
            provider_record_sha256=provider_record_sha256,
            measured_cost_usd=measured_cost_usd,
            actor="runner",
            record_kind="provider_success",
            owner_decision_sha256=None,
            owner_decision=None,
            owner_recovery_request=None,
        )

    def record_failure(
        self,
        ordinal: int,
        *,
        dispatch_id: str,
        provider_record_sha256: str,
        measured_cost_usd: Decimal,
    ) -> None:
        self._record_terminal(
            ordinal,
            target=GateBCallStateV3.TERMINAL_FAILURE,
            dispatch_id=dispatch_id,
            provider_record_sha256=provider_record_sha256,
            measured_cost_usd=measured_cost_usd,
            actor="runner",
            record_kind="provider_terminal_failure",
            owner_decision_sha256=None,
            owner_decision=None,
            owner_recovery_request=None,
        )

    def record_unknown(self, ordinal: int, *, dispatch_id: str) -> None:
        self._record_terminal(
            ordinal,
            target=GateBCallStateV3.TERMINAL_UNKNOWN,
            dispatch_id=dispatch_id,
            provider_record_sha256=None,
            measured_cost_usd=None,
            actor="runner",
            record_kind="provider_terminal_unknown",
            owner_decision_sha256=None,
            owner_decision=None,
            owner_recovery_request=None,
        )

    def _open(self) -> None:
        root_name = self.root.name
        if not root_name or root_name in {".", ".."}:
            raise GateBLedgerErrorV3("ledger_root_unsafe")
        try:
            root_parent_descriptor = os.open(
                self.root.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise GateBLedgerErrorV3("ledger_root_parent_unsafe") from exc
        try:
            try:
                os.mkdir(
                    root_name,
                    _PRIVATE_DIRECTORY_MODE,
                    dir_fd=root_parent_descriptor,
                )
            except FileExistsError:
                pass
            except OSError as exc:
                raise GateBLedgerErrorV3("ledger_root_unsafe") from exc
            try:
                os.fsync(root_parent_descriptor)
            except OSError as exc:
                raise GateBLedgerErrorV3(
                    "ledger_root_parent_fsync_failed"
                ) from exc
            try:
                self._root_descriptor = os.open(
                    root_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=root_parent_descriptor,
                )
            except OSError as exc:
                raise GateBLedgerErrorV3("ledger_root_unsafe") from exc
        finally:
            os.close(root_parent_descriptor)
        self._root_identity = self._validate_directory_descriptor(
            self._root_descriptor, "ledger_root"
        )
        self._marker_directory_descriptor, self._marker_directory_identity = (
            self._open_private_directory(self.dispatch_markers_dirname)
        )
        (
            self._recording_directory_descriptor,
            self._recording_directory_identity,
        ) = self._open_private_directory(self.recordings_dirname)

        created = False
        flags = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW
        try:
            self._ledger_descriptor = os.open(
                self.ledger_filename,
                flags,
                dir_fd=self._root_descriptor,
            )
        except FileNotFoundError:
            initial_entry = self._initial_entry()
            initial_sha256 = canonical_json_sha256(initial_entry)
            persisted_initial = dict(initial_entry)
            persisted_initial["entry_sha256"] = initial_sha256
            prepared_initial = _canonical_json_bytes(persisted_initial) + b"\n"
            try:
                self._ledger_descriptor, self._ledger_identity = (
                    _publish_prepared_file(
                        self._root_descriptor,
                        self.ledger_filename,
                        prepared_initial,
                        expected_uid=self.expected_uid,
                        error_prefix="ledger",
                    )
                )
                self._ledger_head_sha256 = initial_sha256
                self._sequence = 1
                self._ledger_size = os.fstat(self._ledger_descriptor).st_size
                self._ledger_content_sha256 = hashlib.sha256(
                    prepared_initial
                ).hexdigest()
                created = True
            except FileExistsError:
                self._ledger_descriptor = os.open(
                    self.ledger_filename,
                    flags,
                    dir_fd=self._root_descriptor,
                )
            except OSError as exc:
                raise GateBLedgerErrorV3("ledger_path_unsafe") from exc
        except OSError as exc:
            raise GateBLedgerErrorV3("ledger_path_unsafe") from exc
        self._ledger_identity = self._validate_file_descriptor(
            self._ledger_descriptor, "ledger"
        )
        try:
            fcntl.flock(self._ledger_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise GateBLedgerErrorV3("ledger_locked") from exc
            raise GateBLedgerErrorV3("ledger_lock_failed") from exc

        self._markers = self._scan_inventory("marker")
        self._recordings = self._scan_inventory("recording")
        if created:
            if self._markers or self._recordings:
                raise GateBLedgerErrorV3("ledger_inventory_without_ledger")
            self._initialize_rows()
        else:
            self._load_existing()

    def _open_private_directory(self, name: str) -> tuple[int, tuple[int, int]]:
        try:
            os.mkdir(
                name,
                _PRIVATE_DIRECTORY_MODE,
                dir_fd=self._root_descriptor,
            )
            os.fsync(self._root_descriptor)
        except FileExistsError:
            pass
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self._root_descriptor,
            )
        except OSError as exc:
            raise GateBLedgerErrorV3(f"ledger_{name}_unsafe") from exc
        try:
            identity = self._validate_directory_descriptor(
                descriptor, f"ledger_{name}"
            )
        except Exception:
            os.close(descriptor)
            raise
        return descriptor, identity

    def _validate_directory_descriptor(
        self, descriptor: int, error_prefix: str
    ) -> tuple[int, int]:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self.expected_uid
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE
        ):
            raise GateBLedgerErrorV3(f"{error_prefix}_authority_invalid")
        return metadata.st_dev, metadata.st_ino

    def _validate_file_descriptor(
        self, descriptor: int, error_prefix: str
    ) -> tuple[int, int]:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self.expected_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
        ):
            raise GateBLedgerErrorV3(f"{error_prefix}_authority_invalid")
        return metadata.st_dev, metadata.st_ino

    def _assert_mutation_safe(self) -> None:
        if self._ledger_descriptor < 0:
            raise GateBLedgerErrorV3("ledger_closed")
        self._assert_root_identity()
        self._assert_relative_identity(
            self._root_descriptor,
            self.ledger_filename,
            self._ledger_descriptor,
            self._ledger_identity,
            "ledger_path_changed",
            require_regular=True,
        )
        self._assert_relative_identity(
            self._root_descriptor,
            self.dispatch_markers_dirname,
            self._marker_directory_descriptor,
            self._marker_directory_identity,
            "ledger_marker_directory_changed",
            require_regular=False,
        )
        current_ledger_bytes = _read_all(self._ledger_descriptor)
        if (
            len(current_ledger_bytes) != self._ledger_size
            or hashlib.sha256(current_ledger_bytes).hexdigest()
            != self._ledger_content_sha256
        ):
            raise GateBLedgerErrorV3("ledger_content_changed")
        if self._markers != self._scan_inventory("marker"):
            raise GateBLedgerErrorV3("marker_inventory_changed")
        if self._recordings != self._scan_inventory("recording"):
            raise GateBLedgerErrorV3("recording_inventory_changed")
        self._assert_relative_identity(
            self._root_descriptor,
            self.recordings_dirname,
            self._recording_directory_descriptor,
            self._recording_directory_identity,
            "ledger_recording_directory_changed",
            require_regular=False,
        )

    def _assert_root_identity(self) -> None:
        assert self._root_identity is not None
        descriptor_metadata = os.fstat(self._root_descriptor)
        try:
            path_metadata = os.stat(self.root, follow_symlinks=False)
        except OSError as exc:
            raise GateBLedgerErrorV3("ledger_root_changed") from exc
        if (
            not stat.S_ISDIR(path_metadata.st_mode)
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != self._root_identity
            or (path_metadata.st_dev, path_metadata.st_ino) != self._root_identity
        ):
            raise GateBLedgerErrorV3("ledger_root_changed")

    def _assert_relative_identity(
        self,
        parent_descriptor: int,
        name: str,
        descriptor: int,
        expected_identity: tuple[int, int] | None,
        error: str,
        *,
        require_regular: bool,
    ) -> None:
        assert expected_identity is not None
        descriptor_metadata = os.fstat(descriptor)
        try:
            path_metadata = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise GateBLedgerErrorV3(error) from exc
        expected_kind = stat.S_ISREG if require_regular else stat.S_ISDIR
        if (
            not expected_kind(path_metadata.st_mode)
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != expected_identity
            or (path_metadata.st_dev, path_metadata.st_ino) != expected_identity
        ):
            raise GateBLedgerErrorV3(error)
        if require_regular:
            self._validate_file_descriptor(descriptor, "ledger")
        else:
            self._validate_directory_descriptor(descriptor, error)

    def _initialize_rows(self) -> None:
        self._rows = [
            {
                "ordinal": ordinal,
                "input_sha256": input_sha256,
                "state": GateBCallStateV3.PENDING,
                "dispatch_id": None,
                "dispatch_marker_sha256": None,
                "recording_sha256": None,
                "measured_cost_usd": None,
                "conservative_cost_usd": Decimal("0"),
            }
            for ordinal, input_sha256 in enumerate(
                self.package_manifest.ordered_input_sha256s
            )
        ]

    def _initial_entry(self) -> dict[str, Any]:
        return {
            "schema_version": "3.0.0",
            "kind": "initial",
            "sequence": 0,
            "previous_entry_sha256": _ZERO_SHA256,
            "run_id": self.launch_identity.run_id,
            "package_manifest_sha256": self.package_manifest.canonical_sha256,
            "owner_recovery_public_key_sha256": (
                self.owner_recovery_public_key_sha256
            ),
            "ordered_input_sha256s": list(
                self.package_manifest.ordered_input_sha256s
            ),
        }

    def _append_transition(
        self,
        ordinal: int,
        *,
        source: GateBCallStateV3,
        target: GateBCallStateV3,
        actor: str,
        dispatch_id: str | None,
        marker_sha256: str | None,
        recording_sha256: str | None,
        measured_cost_usd: Decimal | None,
        conservative_cost_usd: Decimal,
        owner_decision_sha256: str | None = None,
        owner_decision: GateBRecoveryDecisionV3 | None = None,
        owner_recovery_request: GateBRecoveryRequestV3 | None = None,
    ) -> None:
        if not transition_allowed(source, target, actor=actor):
            raise GateBLedgerErrorV3("ledger_transition_not_allowed")
        row = self._mutable_row(ordinal)
        self._append_entry(
            {
                "schema_version": "3.0.0",
                "kind": "transition",
                "sequence": self._sequence,
                "previous_entry_sha256": self._ledger_head_sha256,
                "run_id": self.launch_identity.run_id,
                "package_manifest_sha256": self.package_manifest.canonical_sha256,
                "ordinal": ordinal,
                "input_sha256": row["input_sha256"],
                "source_state": source.value,
                "target_state": target.value,
                "actor": actor,
                "dispatch_id": dispatch_id,
                "dispatch_marker_sha256": marker_sha256,
                "recording_sha256": recording_sha256,
                "measured_cost_usd": (
                    None if measured_cost_usd is None else str(measured_cost_usd)
                ),
                "conservative_cost_usd": str(conservative_cost_usd),
                "owner_decision_sha256": owner_decision_sha256,
                "owner_decision": (
                    None if owner_decision is None else owner_decision.model_dump(mode="json")
                ),
                "owner_recovery_request": (
                    None
                    if owner_recovery_request is None
                    else owner_recovery_request.model_dump(mode="json")
                ),
            }
        )

    def _append_entry(self, entry: dict[str, Any]) -> None:
        self._assert_mutation_safe()
        if entry["sequence"] != self._sequence:
            raise GateBLedgerErrorV3("ledger_sequence_invalid")
        if entry["previous_entry_sha256"] != self._ledger_head_sha256:
            raise GateBLedgerErrorV3("ledger_chain_invalid")
        entry_sha256 = canonical_json_sha256(entry)
        persisted = dict(entry)
        persisted["entry_sha256"] = entry_sha256
        payload = _canonical_json_bytes(persisted) + b"\n"
        _write_all(self._ledger_descriptor, payload)
        os.fsync(self._ledger_descriptor)
        self._ledger_head_sha256 = entry_sha256
        self._sequence += 1
        current_ledger_bytes = _read_all(self._ledger_descriptor)
        self._ledger_size = len(current_ledger_bytes)
        self._ledger_content_sha256 = hashlib.sha256(
            current_ledger_bytes
        ).hexdigest()

    def _load_existing(self) -> None:
        raw_payload = _read_all(self._ledger_descriptor)
        if not raw_payload:
            raise GateBLedgerErrorV3("ledger_empty")
        payload = raw_payload
        torn_tail = not payload.endswith(b"\n")
        if torn_tail:
            final_newline = payload.rfind(b"\n")
            if final_newline < 0:
                raise GateBLedgerErrorV3("ledger_torn_without_prefix")
            payload = payload[: final_newline + 1]
        raw_lines = payload.splitlines(keepends=True)
        entries: list[dict[str, Any]] = []
        previous = _ZERO_SHA256
        for sequence, raw_line in enumerate(raw_lines):
            try:
                parsed = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GateBLedgerErrorV3("ledger_json_invalid") from exc
            if not isinstance(parsed, dict):
                raise GateBLedgerErrorV3("ledger_entry_invalid")
            if raw_line != _canonical_json_bytes(parsed) + b"\n":
                raise GateBLedgerErrorV3("ledger_entry_not_canonical")
            entry_hash = parsed.get("entry_sha256")
            unsigned = dict(parsed)
            unsigned.pop("entry_sha256", None)
            if (
                entry_hash != canonical_json_sha256(unsigned)
                or unsigned.get("sequence") != sequence
                or unsigned.get("previous_entry_sha256") != previous
            ):
                raise GateBLedgerErrorV3("ledger_chain_invalid")
            previous = entry_hash
            entries.append(unsigned)
        self._replay_entries(entries)
        self._replayed_entries = tuple(entries)
        self._ledger_head_sha256 = previous
        self._sequence = len(entries)
        self._reconcile_inventory()
        self._ledger_size = len(raw_payload)
        self._ledger_content_sha256 = hashlib.sha256(raw_payload).hexdigest()
        self._assert_mutation_safe()
        if torn_tail:
            os.ftruncate(self._ledger_descriptor, len(payload))
            os.fsync(self._ledger_descriptor)
            self._ledger_size = len(payload)
            self._ledger_content_sha256 = hashlib.sha256(payload).hexdigest()

    def _replay_entries(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            raise GateBLedgerErrorV3("ledger_empty")
        initial = entries[0]
        expected_initial = {
            "schema_version": "3.0.0",
            "kind": "initial",
            "sequence": 0,
            "previous_entry_sha256": _ZERO_SHA256,
            "run_id": self.launch_identity.run_id,
            "package_manifest_sha256": self.package_manifest.canonical_sha256,
            "owner_recovery_public_key_sha256": (
                self.owner_recovery_public_key_sha256
            ),
            "ordered_input_sha256s": list(
                self.package_manifest.ordered_input_sha256s
            ),
        }
        if initial != expected_initial:
            raise GateBLedgerErrorV3("ledger_identity_mismatch")
        self._initialize_rows()
        seen_owner_decisions: set[str] = set()
        expected_keys = {
            "schema_version",
            "kind",
            "sequence",
            "previous_entry_sha256",
            "run_id",
            "package_manifest_sha256",
            "ordinal",
            "input_sha256",
            "source_state",
            "target_state",
            "actor",
            "dispatch_id",
            "dispatch_marker_sha256",
            "recording_sha256",
            "measured_cost_usd",
            "conservative_cost_usd",
            "owner_decision_sha256",
            "owner_decision",
            "owner_recovery_request",
        }
        for entry in entries[1:]:
            if set(entry) != expected_keys or entry.get("kind") != "transition":
                raise GateBLedgerErrorV3("ledger_entry_invalid")
            if (
                entry["schema_version"] != "3.0.0"
                or entry["run_id"] != self.launch_identity.run_id
                or entry["package_manifest_sha256"]
                != self.package_manifest.canonical_sha256
            ):
                raise GateBLedgerErrorV3("ledger_identity_mismatch")
            ordinal = entry["ordinal"]
            if not isinstance(ordinal, int) or isinstance(ordinal, bool):
                raise GateBLedgerErrorV3("ledger_ordinal_invalid")
            row = self._mutable_row(ordinal)
            if entry["input_sha256"] != row["input_sha256"]:
                raise GateBLedgerErrorV3("ledger_input_identity_mismatch")
            try:
                source = GateBCallStateV3(entry["source_state"])
                target = GateBCallStateV3(entry["target_state"])
            except ValueError as exc:
                raise GateBLedgerErrorV3("ledger_state_invalid") from exc
            actor = entry["actor"]
            if not isinstance(actor, str) or not transition_allowed(
                source, target, actor=actor
            ):
                raise GateBLedgerErrorV3("ledger_transition_not_allowed")
            current = row["state"]
            inferred_dispatch = (
                current is GateBCallStateV3.RESERVED
                and source is GateBCallStateV3.DISPATCHED
                and ordinal in self._markers
            )
            if current is not source and not inferred_dispatch:
                raise GateBLedgerErrorV3("ledger_transition_source_invalid")
            self._validate_transition_evidence(
                entry,
                source,
                target,
                seen_owner_decisions,
            )
            row.update(
                state=target,
                dispatch_id=entry["dispatch_id"],
                dispatch_marker_sha256=entry["dispatch_marker_sha256"],
                recording_sha256=entry["recording_sha256"],
                measured_cost_usd=(
                    None
                    if entry["measured_cost_usd"] is None
                    else self._parse_cost(entry["measured_cost_usd"])
                ),
                conservative_cost_usd=self._parse_cost(
                    entry["conservative_cost_usd"]
                ),
            )

    def _validate_transition_evidence(
        self,
        entry: Mapping[str, Any],
        source: GateBCallStateV3,
        target: GateBCallStateV3,
        seen_owner_decisions: set[str],
    ) -> None:
        measured = (
            None
            if entry["measured_cost_usd"] is None
            else self._parse_cost(entry["measured_cost_usd"])
        )
        conservative = self._parse_cost(entry["conservative_cost_usd"])
        owner_decision_sha256 = entry["owner_decision_sha256"]
        if entry["actor"] == "owner_recovery":
            if not self._valid_sha(owner_decision_sha256):
                raise GateBLedgerErrorV3("ledger_owner_decision_invalid")
            exact_transition = GateBRecoveryTransitionV3(
                ordinal=entry["ordinal"],
                input_sha256=entry["input_sha256"],
                source_state=source,
                target_state=target,
            )
            decision, _request = self._validate_owner_recovery_evidence(
                decision_payload=entry["owner_decision"],
                request_payload=entry["owner_recovery_request"],
                owner_decision_sha256=owner_decision_sha256,
                exact_transition=exact_transition,
            )
            if owner_decision_sha256 not in seen_owner_decisions:
                if decision.ledger_head_sha256 != entry["previous_entry_sha256"]:
                    raise GateBLedgerErrorV3("ledger_owner_decision_head_mismatch")
                seen_owner_decisions.add(owner_decision_sha256)
        elif (
            owner_decision_sha256 is not None
            or entry["owner_decision"] is not None
            or entry["owner_recovery_request"] is not None
        ):
            raise GateBLedgerErrorV3("ledger_runner_owner_decision_forbidden")
        if conservative < 0 or conservative > _PER_CALL_MAXIMUM_USD:
            raise GateBLedgerErrorV3("ledger_cost_invalid")
        if source is GateBCallStateV3.PENDING:
            if any(
                entry[field] is not None
                for field in (
                    "dispatch_id",
                    "dispatch_marker_sha256",
                    "recording_sha256",
                    "measured_cost_usd",
                )
            ) or conservative != 0:
                raise GateBLedgerErrorV3("ledger_reservation_evidence_invalid")
        elif target is GateBCallStateV3.PENDING:
            if entry["actor"] != "owner_recovery" or any(
                entry[field] is not None
                for field in (
                    "dispatch_id",
                    "dispatch_marker_sha256",
                    "recording_sha256",
                    "measured_cost_usd",
                )
            ) or conservative != 0:
                raise GateBLedgerErrorV3("ledger_recovery_evidence_invalid")
        elif target is GateBCallStateV3.DISPATCHED:
            if (
                not self._valid_text(entry["dispatch_id"])
                or not self._valid_sha(entry["dispatch_marker_sha256"])
                or entry["recording_sha256"] is not None
                or measured is not None
                or conservative != _PER_CALL_MAXIMUM_USD
            ):
                raise GateBLedgerErrorV3("ledger_dispatch_evidence_invalid")
        elif target in _TERMINAL_STATES:
            if (
                not self._valid_text(entry["dispatch_id"])
                or not self._valid_sha(entry["dispatch_marker_sha256"])
                or not self._valid_sha(entry["recording_sha256"])
            ):
                raise GateBLedgerErrorV3("ledger_terminal_evidence_invalid")
            if target is GateBCallStateV3.TERMINAL_UNKNOWN:
                if measured is not None or conservative != _PER_CALL_MAXIMUM_USD:
                    raise GateBLedgerErrorV3("ledger_unknown_cost_invalid")
            elif measured is None or conservative != measured:
                raise GateBLedgerErrorV3("ledger_measured_cost_invalid")
            recording = self._recordings.get(entry["ordinal"])
            if recording is not None and (
                recording["sha256"] != entry["recording_sha256"]
                or recording["payload"]["owner_decision_sha256"]
                != owner_decision_sha256
            ):
                raise GateBLedgerErrorV3("ledger_recording_decision_conflict")

    def _validate_owner_recovery_evidence(
        self,
        *,
        decision_payload: object,
        request_payload: object,
        owner_decision_sha256: str,
        exact_transition: GateBRecoveryTransitionV3,
    ) -> tuple[GateBRecoveryDecisionV3, GateBRecoveryRequestV3]:
        try:
            decision = GateBRecoveryDecisionV3.model_validate_json(
                _canonical_json_bytes(decision_payload)
            )
            request = GateBRecoveryRequestV3.model_validate_json(
                _canonical_json_bytes(request_payload)
            )
        except (TypeError, ValidationError) as exc:
            raise GateBLedgerErrorV3("owner_decision_invalid") from exc
        decision.verify_owner_signature(self.owner_recovery_public_key)
        request_row = request.rows[exact_transition.ordinal]
        if (
            decision.owner_approval_sha256 != owner_decision_sha256
            or decision.request_sha256 != request.canonical_sha256
            or decision.ledger_head_sha256 != request.ledger_head_sha256
            or decision.inventory_sha256 != request.inventory_sha256
            or decision.requested_transitions != request.requested_transitions
            or exact_transition not in decision.requested_transitions
            or request_row.input_sha256 != exact_transition.input_sha256
            or request_row.state is not exact_transition.source_state
            or request.run_id != self.launch_identity.run_id
            or request.package_manifest_sha256
            != self.package_manifest.canonical_sha256
            or request.owner_recovery_public_key_sha256
            != self.owner_recovery_public_key_sha256
        ):
            raise GateBLedgerErrorV3("owner_decision_mismatch")
        return decision, request

    def _reconcile_inventory(self) -> None:
        for ordinal, row in enumerate(self._rows):
            marker = self._markers.get(ordinal)
            recording = self._recordings.get(ordinal)
            state = row["state"]
            if recording is not None and marker is None:
                raise GateBLedgerErrorV3("recording_without_marker")
            if marker is None:
                if state in {
                    GateBCallStateV3.DISPATCHED,
                    *_TERMINAL_STATES,
                }:
                    raise GateBLedgerErrorV3("marker_missing_for_ledger_dispatch")
                continue
            marker_payload = marker["payload"]
            if state is GateBCallStateV3.PENDING:
                raise GateBLedgerErrorV3("marker_without_reservation")
            if row["dispatch_id"] not in {None, marker_payload["dispatch_id"]}:
                raise GateBLedgerErrorV3("marker_dispatch_conflict")
            if row["dispatch_marker_sha256"] not in {None, marker["sha256"]}:
                raise GateBLedgerErrorV3("marker_hash_conflict")
            row.update(
                state=(
                    GateBCallStateV3.DISPATCHED
                    if state is GateBCallStateV3.RESERVED
                    else state
                ),
                dispatch_id=marker_payload["dispatch_id"],
                dispatch_marker_sha256=marker["sha256"],
                conservative_cost_usd=(
                    _PER_CALL_MAXIMUM_USD
                    if state is GateBCallStateV3.RESERVED
                    else row["conservative_cost_usd"]
                ),
            )
            if recording is None:
                if state in _TERMINAL_STATES:
                    raise GateBLedgerErrorV3("recording_missing_for_terminal")
                continue
            recording_payload = recording["payload"]
            if (
                recording_payload["dispatch_id"] != marker_payload["dispatch_id"]
                or recording_payload["dispatch_marker_sha256"] != marker["sha256"]
            ):
                raise GateBLedgerErrorV3("recording_marker_conflict")
            target = GateBCallStateV3(recording_payload["target_state"])
            if target not in _TERMINAL_STATES:
                raise GateBLedgerErrorV3("recording_state_invalid")
            measured = (
                None
                if recording_payload["measured_cost_usd"] is None
                else self._parse_cost(recording_payload["measured_cost_usd"])
            )
            conservative = self._parse_cost(
                recording_payload["conservative_cost_usd"]
            )
            if state in _TERMINAL_STATES and state is not target:
                raise GateBLedgerErrorV3("recording_terminal_conflict")
            if row["recording_sha256"] not in {None, recording["sha256"]}:
                raise GateBLedgerErrorV3("recording_hash_conflict")
            self._validate_recording_cost(target, measured, conservative)
            if (
                recording_payload["record_kind"]
                == "owner_recovery_terminal_unknown"
                and state not in _TERMINAL_STATES
            ):
                exact_transition = GateBRecoveryTransitionV3(
                    ordinal=ordinal,
                    input_sha256=row["input_sha256"],
                    source_state=GateBCallStateV3.DISPATCHED,
                    target_state=GateBCallStateV3.TERMINAL_UNKNOWN,
                )
                _decision, request = self._validate_owner_recovery_evidence(
                    decision_payload=recording_payload["owner_decision"],
                    request_payload=recording_payload["owner_recovery_request"],
                    owner_decision_sha256=(
                        recording_payload["owner_decision_sha256"]
                    ),
                    exact_transition=exact_transition,
                )
                if not self._owner_recording_matches_replayed_prefix(
                    decision=_decision,
                    request=request,
                    exact_transition=exact_transition,
                ):
                    raise GateBLedgerErrorV3(
                        "recording_owner_decision_head_mismatch"
                    )
            row.update(
                state=target,
                recording_sha256=recording["sha256"],
                measured_cost_usd=measured,
                conservative_cost_usd=conservative,
            )

    def _owner_recording_matches_replayed_prefix(
        self,
        *,
        decision: GateBRecoveryDecisionV3,
        request: GateBRecoveryRequestV3,
        exact_transition: GateBRecoveryTransitionV3,
    ) -> bool:
        try:
            transition_index = decision.requested_transitions.index(
                exact_transition
            )
        except ValueError:
            return False
        if request.ledger_head_sha256 == self.ledger_head_sha256:
            return transition_index == 0
        expected_prefix = decision.requested_transitions[:transition_index]
        replayed_suffix: list[dict[str, Any]] = []
        request_head_found = False
        for entry in self._replayed_entries[1:]:
            if not request_head_found:
                if (
                    entry["previous_entry_sha256"]
                    != request.ledger_head_sha256
                ):
                    continue
                request_head_found = True
            replayed_suffix.append(entry)
        if not request_head_found or len(replayed_suffix) != len(expected_prefix):
            return False
        for entry, transition in zip(replayed_suffix, expected_prefix, strict=True):
            if (
                entry["actor"] != "owner_recovery"
                or entry["owner_decision_sha256"]
                != decision.owner_approval_sha256
                or entry["ordinal"] != transition.ordinal
                or entry["input_sha256"] != transition.input_sha256
                or entry["source_state"] != transition.source_state.value
                or entry["target_state"] != transition.target_state.value
            ):
                return False
        return True

    def _scan_inventory(self, kind: Literal["marker", "recording"]) -> dict[int, dict[str, Any]]:
        descriptor = (
            self._marker_directory_descriptor
            if kind == "marker"
            else self._recording_directory_descriptor
        )
        result: dict[int, dict[str, Any]] = {}
        for name in os.listdir(descriptor):
            match = re.fullmatch(r"(0|[1-9][0-9]*)-([0-9a-f]{64})\.json", name)
            if match is None:
                raise GateBLedgerErrorV3(f"{kind}_inventory_unexplained_entry")
            ordinal = int(match.group(1))
            if not 0 <= ordinal < _ORDERED_CALL_CAP:
                raise GateBLedgerErrorV3(f"{kind}_ordinal_invalid")
            expected_input = self.package_manifest.ordered_input_sha256s[ordinal]
            if match.group(2) != expected_input or ordinal in result:
                raise GateBLedgerErrorV3(f"{kind}_inventory_identity_invalid")
            try:
                item_descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise GateBLedgerErrorV3(f"{kind}_path_unsafe") from exc
            try:
                identity = self._validate_file_descriptor(item_descriptor, kind)
                payload_bytes = _read_all(item_descriptor)
            finally:
                os.close(item_descriptor)
            try:
                payload = json.loads(payload_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GateBLedgerErrorV3(f"{kind}_json_invalid") from exc
            if (
                not isinstance(payload, dict)
                or payload_bytes != _canonical_json_bytes(payload) + b"\n"
            ):
                raise GateBLedgerErrorV3(f"{kind}_not_canonical")
            self._validate_inventory_payload(kind, ordinal, expected_input, payload)
            result[ordinal] = {
                "payload": payload,
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "identity": identity,
            }
        return result

    def _validate_inventory_payload(
        self,
        kind: Literal["marker", "recording"],
        ordinal: int,
        input_sha256: str,
        payload: Mapping[str, Any],
    ) -> None:
        common = {
            "schema_version",
            "artifact_kind",
            "run_id",
            "package_manifest_sha256",
            "ordinal",
            "input_sha256",
            "dispatch_id",
        }
        expected_keys = (
            common
            if kind == "marker"
            else common
            | {
                "dispatch_marker_sha256",
                "target_state",
                "record_kind",
                "provider_record_sha256",
                "measured_cost_usd",
                "conservative_cost_usd",
                "owner_decision_sha256",
                "owner_decision",
                "owner_recovery_request",
            }
        )
        expected_artifact_kind = "dispatch_marker" if kind == "marker" else "recording"
        if (
            set(payload) != expected_keys
            or payload["schema_version"] != "3.0.0"
            or payload["artifact_kind"] != expected_artifact_kind
            or payload["run_id"] != self.launch_identity.run_id
            or payload["package_manifest_sha256"]
            != self.package_manifest.canonical_sha256
            or payload["ordinal"] != ordinal
            or payload["input_sha256"] != input_sha256
            or not self._valid_text(payload["dispatch_id"])
        ):
            raise GateBLedgerErrorV3(f"{kind}_identity_invalid")
        if kind == "recording":
            if not self._valid_sha(payload["dispatch_marker_sha256"]):
                raise GateBLedgerErrorV3("recording_marker_hash_invalid")
            try:
                target = GateBCallStateV3(payload["target_state"])
            except ValueError as exc:
                raise GateBLedgerErrorV3("recording_state_invalid") from exc
            if target not in _TERMINAL_STATES:
                raise GateBLedgerErrorV3("recording_state_invalid")
            provider_sha = payload["provider_record_sha256"]
            owner_sha = payload["owner_decision_sha256"]
            if provider_sha is not None and not self._valid_sha(provider_sha):
                raise GateBLedgerErrorV3("recording_provider_hash_invalid")
            if owner_sha is not None and not self._valid_sha(owner_sha):
                raise GateBLedgerErrorV3("recording_owner_hash_invalid")
            measured = (
                None
                if payload["measured_cost_usd"] is None
                else self._parse_cost(payload["measured_cost_usd"])
            )
            conservative = self._parse_cost(payload["conservative_cost_usd"])
            self._validate_recording_cost(target, measured, conservative)
            record_kind = payload["record_kind"]
            owner_payload = payload["owner_decision"]
            request_payload = payload["owner_recovery_request"]
            if target is GateBCallStateV3.SUCCESS:
                if (
                    record_kind != "provider_success"
                    or not self._valid_sha(provider_sha)
                    or owner_sha is not None
                    or owner_payload is not None
                    or request_payload is not None
                ):
                    raise GateBLedgerErrorV3("recording_success_evidence_invalid")
            elif target is GateBCallStateV3.TERMINAL_FAILURE:
                if (
                    record_kind != "provider_terminal_failure"
                    or not self._valid_sha(provider_sha)
                    or owner_sha is not None
                    or owner_payload is not None
                    or request_payload is not None
                ):
                    raise GateBLedgerErrorV3("recording_failure_evidence_invalid")
            elif record_kind == "provider_terminal_unknown":
                if (
                    provider_sha is not None
                    or owner_sha is not None
                    or owner_payload is not None
                    or request_payload is not None
                ):
                    raise GateBLedgerErrorV3("recording_unknown_evidence_invalid")
            elif record_kind == "owner_recovery_terminal_unknown":
                if provider_sha is not None or not self._valid_sha(owner_sha):
                    raise GateBLedgerErrorV3("recording_recovery_evidence_invalid")
                exact_transition = GateBRecoveryTransitionV3(
                    ordinal=ordinal,
                    input_sha256=input_sha256,
                    source_state=GateBCallStateV3.DISPATCHED,
                    target_state=GateBCallStateV3.TERMINAL_UNKNOWN,
                )
                _decision, request = self._validate_owner_recovery_evidence(
                    decision_payload=owner_payload,
                    request_payload=request_payload,
                    owner_decision_sha256=owner_sha,
                    exact_transition=exact_transition,
                )
                request_row = request.rows[ordinal]
                if (
                    request_row.dispatch_id != payload["dispatch_id"]
                    or request_row.dispatch_marker_sha256
                    != payload["dispatch_marker_sha256"]
                    or request_row.recording_sha256 is not None
                ):
                    raise GateBLedgerErrorV3(
                        "recording_recovery_inventory_mismatch"
                    )
            else:
                raise GateBLedgerErrorV3("recording_kind_invalid")

    def _record_terminal(
        self,
        ordinal: int,
        *,
        target: GateBCallStateV3,
        dispatch_id: str,
        provider_record_sha256: str | None,
        measured_cost_usd: Decimal | None,
        actor: str,
        record_kind: str,
        owner_decision_sha256: str | None,
        owner_decision: GateBRecoveryDecisionV3 | None,
        owner_recovery_request: GateBRecoveryRequestV3 | None,
    ) -> None:
        self._assert_mutation_safe()
        row = self._mutable_row(ordinal)
        if (
            row["state"] is not GateBCallStateV3.DISPATCHED
            or row["dispatch_id"] != dispatch_id
            or row["dispatch_marker_sha256"] is None
        ):
            raise GateBLedgerErrorV3("ledger_transition_not_allowed")
        if target not in _TERMINAL_STATES:
            raise GateBLedgerErrorV3("ledger_terminal_state_invalid")
        if target is GateBCallStateV3.TERMINAL_UNKNOWN:
            measured = None
            conservative = _PER_CALL_MAXIMUM_USD
            if provider_record_sha256 is not None:
                raise GateBLedgerErrorV3("ledger_unknown_provider_record_forbidden")
        else:
            if not isinstance(measured_cost_usd, Decimal):
                raise GateBLedgerErrorV3("ledger_measured_cost_required")
            measured = measured_cost_usd
            conservative = measured
            if not self._valid_sha(provider_record_sha256):
                raise GateBLedgerErrorV3("ledger_provider_record_hash_invalid")
        self._validate_recording_cost(target, measured, conservative)
        recording_payload = {
            "schema_version": "3.0.0",
            "artifact_kind": "recording",
            "run_id": self.launch_identity.run_id,
            "package_manifest_sha256": self.package_manifest.canonical_sha256,
            "ordinal": ordinal,
            "input_sha256": row["input_sha256"],
            "dispatch_id": dispatch_id,
            "dispatch_marker_sha256": row["dispatch_marker_sha256"],
            "target_state": target.value,
            "record_kind": record_kind,
            "provider_record_sha256": provider_record_sha256,
            "measured_cost_usd": None if measured is None else str(measured),
            "conservative_cost_usd": str(conservative),
            "owner_decision_sha256": owner_decision_sha256,
            "owner_decision": (
                None if owner_decision is None else owner_decision.model_dump(mode="json")
            ),
            "owner_recovery_request": (
                None
                if owner_recovery_request is None
                else owner_recovery_request.model_dump(mode="json")
            ),
        }
        recording_sha256, recording_identity = self._create_inventory_file(
            directory_descriptor=self._recording_directory_descriptor,
            directory_kind="recording",
            filename=self._artifact_filename(ordinal),
            payload=recording_payload,
        )
        self._recordings[ordinal] = {
            "payload": recording_payload,
            "sha256": recording_sha256,
            "identity": recording_identity,
        }
        row.update(
            state=target,
            recording_sha256=recording_sha256,
            measured_cost_usd=measured,
            conservative_cost_usd=conservative,
        )
        self._append_transition(
            ordinal,
            source=GateBCallStateV3.DISPATCHED,
            target=target,
            actor=actor,
            dispatch_id=dispatch_id,
            marker_sha256=row["dispatch_marker_sha256"],
            recording_sha256=recording_sha256,
            measured_cost_usd=measured,
            conservative_cost_usd=conservative,
            owner_decision_sha256=owner_decision_sha256,
            owner_decision=owner_decision,
            owner_recovery_request=owner_recovery_request,
        )

    def _create_inventory_file(
        self,
        *,
        directory_descriptor: int,
        directory_kind: str,
        filename: str,
        payload: Mapping[str, Any],
    ) -> tuple[str, tuple[int, int]]:
        self._assert_mutation_safe()
        encoded = _canonical_json_bytes(payload) + b"\n"
        try:
            descriptor, identity = _publish_prepared_file(
                directory_descriptor,
                filename,
                encoded,
                expected_uid=self.expected_uid,
                error_prefix=directory_kind,
            )
        except FileExistsError as exc:
            raise GateBLedgerErrorV3(f"{directory_kind}_create_once_failed") from exc
        os.close(descriptor)
        return hashlib.sha256(encoded).hexdigest(), identity

    def _artifact_filename(self, ordinal: int) -> str:
        return f"{ordinal}-{self._mutable_row(ordinal)['input_sha256']}.json"

    def _mutable_row(self, ordinal: int) -> dict[str, Any]:
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or not 0 <= ordinal < _ORDERED_CALL_CAP
        ):
            raise GateBLedgerErrorV3("ledger_ordinal_invalid")
        try:
            return self._rows[ordinal]
        except IndexError as exc:
            raise GateBLedgerErrorV3("ledger_ordinal_invalid") from exc

    @staticmethod
    def _valid_sha(value: object) -> bool:
        return isinstance(value, str) and re.fullmatch(SHA256_PATTERN, value) is not None

    @staticmethod
    def _valid_text(value: object) -> bool:
        return isinstance(value, str) and bool(value)

    @staticmethod
    def _parse_cost(value: object) -> Decimal:
        if not isinstance(value, str):
            raise GateBLedgerErrorV3("ledger_cost_encoding_invalid")
        try:
            parsed = Decimal(value)
        except Exception as exc:
            raise GateBLedgerErrorV3("ledger_cost_invalid") from exc
        if not parsed.is_finite():
            raise GateBLedgerErrorV3("ledger_cost_invalid")
        return parsed

    @staticmethod
    def _validate_recording_cost(
        target: GateBCallStateV3,
        measured: Decimal | None,
        conservative: Decimal,
    ) -> None:
        if conservative < 0 or conservative > _PER_CALL_MAXIMUM_USD:
            raise GateBLedgerErrorV3("recording_cost_invalid")
        if target is GateBCallStateV3.TERMINAL_UNKNOWN:
            if measured is not None or conservative != _PER_CALL_MAXIMUM_USD:
                raise GateBLedgerErrorV3("recording_unknown_cost_invalid")
        elif measured is None or conservative != measured:
            raise GateBLedgerErrorV3("recording_measured_cost_invalid")


def build_recovery_request_v3(
    ledger: GateBLedgerV3,
    requested_transitions: Mapping[int, GateBCallStateV3 | str],
) -> GateBRecoveryRequestV3:
    ledger.validate_snapshot()
    transitions: list[GateBRecoveryTransitionV3] = []
    for ordinal, requested_target in sorted(requested_transitions.items()):
        row = ledger.row(ordinal)
        try:
            target = GateBCallStateV3(requested_target)
        except ValueError as exc:
            raise GateBLedgerErrorV3("recovery_transition_invalid") from exc
        if not transition_allowed(row.state, target, actor="owner_recovery"):
            if not (
                row.state is GateBCallStateV3.DISPATCHED
                and target is GateBCallStateV3.TERMINAL_UNKNOWN
            ):
                raise GateBLedgerErrorV3("recovery_transition_not_allowed")
        if target is GateBCallStateV3.PENDING and (
            row.dispatch_marker_sha256 is not None or row.recording_sha256 is not None
        ):
            raise GateBLedgerErrorV3("recovery_dispatch_evidence_present")
        if target is GateBCallStateV3.TERMINAL_UNKNOWN and (
            row.dispatch_marker_sha256 is None or row.recording_sha256 is not None
        ):
            raise GateBLedgerErrorV3("recovery_dispatch_evidence_invalid")
        transitions.append(
            GateBRecoveryTransitionV3(
                ordinal=ordinal,
                input_sha256=row.input_sha256,
                source_state=row.state,
                target_state=target,
            )
        )
    return GateBRecoveryRequestV3(
        schema_version="3.0.0",
        run_id=ledger.launch_identity.run_id,
        package_manifest_sha256=ledger.package_manifest.canonical_sha256,
        owner_recovery_public_key_sha256=(
            ledger.owner_recovery_public_key_sha256
        ),
        ledger_head_sha256=ledger.ledger_head_sha256,
        inventory_sha256=ledger.inventory_sha256,
        rows=ledger.rows(),
        conservative_spend_usd=ledger.conservative_spend_usd,
        requested_transitions=tuple(transitions),
    )


def apply_owner_recovery_v3(
    ledger: GateBLedgerV3,
    decision: GateBRecoveryDecisionV3,
) -> None:
    decision.verify_owner_signature(ledger.owner_recovery_public_key)
    if (
        decision.ledger_head_sha256 != ledger.ledger_head_sha256
        or decision.inventory_sha256 != ledger.inventory_sha256
    ):
        raise GateBLedgerErrorV3("recovery_decision_stale")
    requested = {
        transition.ordinal: transition.target_state
        for transition in decision.requested_transitions
    }
    current = build_recovery_request_v3(ledger, requested)
    if (
        decision.request_sha256 != current.canonical_sha256
        or decision.ledger_head_sha256 != current.ledger_head_sha256
        or decision.inventory_sha256 != current.inventory_sha256
        or decision.requested_transitions != current.requested_transitions
    ):
        raise GateBLedgerErrorV3("recovery_decision_stale")
    for transition in decision.requested_transitions:
        row = ledger._mutable_row(transition.ordinal)
        if transition.target_state is GateBCallStateV3.PENDING:
            ledger._append_transition(
                transition.ordinal,
                source=GateBCallStateV3.RESERVED,
                target=GateBCallStateV3.PENDING,
                actor="owner_recovery",
                dispatch_id=None,
                marker_sha256=None,
                recording_sha256=None,
                measured_cost_usd=None,
                conservative_cost_usd=Decimal("0"),
                owner_decision_sha256=decision.owner_approval_sha256,
                owner_decision=decision,
                owner_recovery_request=current,
            )
            row.update(
                state=GateBCallStateV3.PENDING,
                dispatch_id=None,
                dispatch_marker_sha256=None,
                recording_sha256=None,
                measured_cost_usd=None,
                conservative_cost_usd=Decimal("0"),
            )
        elif transition.target_state is GateBCallStateV3.TERMINAL_UNKNOWN:
            ledger._record_terminal(
                transition.ordinal,
                target=GateBCallStateV3.TERMINAL_UNKNOWN,
                dispatch_id=row["dispatch_id"],
                provider_record_sha256=None,
                measured_cost_usd=None,
                actor="owner_recovery",
                record_kind="owner_recovery_terminal_unknown",
                owner_decision_sha256=decision.owner_approval_sha256,
                owner_decision=decision,
                owner_recovery_request=current,
            )
        else:
            raise GateBLedgerErrorV3("recovery_transition_not_allowed")


# The package boundary below is deliberately additive.  It does not call the
# historical dry-run builder and it does not invoke the v3 projector: the
# projector still owns launch-time semantic recomputation, while this stage
# binds its immutable, content-addressed source and independent-review inputs.
_GATE_A_RUN_ID_V3 = "gate-a-20260816T141344Z"
_GATE_A_COMMIT_V3 = "65d60daae16093a9a7e34a11a159e2f789dd14dd"
_GATE_A_MANIFEST_SHA256_V3 = (
    "6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d"
)
_GATE_B_CORPUS_SHA256_V3 = (
    "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
)
_GATE_B_ALLOWLIST_SHA256_V3 = (
    "bc22330c9a17b3d6f325d75ab96712011e892de8a8bf66d06b9ff2ba12fa179c"
)
_GATE_A_SOURCE_ROOT_V3 = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    f"{_GATE_A_COMMIT_V3}"
)
_GATE_B_CORPUS_MANIFEST_PATH_V3 = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-b/"
    f"{_GATE_B_CORPUS_SHA256_V3}/corpus-manifest.json"
)
_GATE_B_PACKAGE_PARENT_V3 = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once"
)
_GATE_B_RUNTIME_EXPORT_ROOT_V3 = (
    _GATE_B_PACKAGE_PARENT_V3 / "immutable-runtime"
)
_GATE_B_ALLOWLIST_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "docs/evidence/product-search-gate-b/v3-fragment-allowlist.yaml"
)
_GATE_B_PROFILE_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/career_profile.v2.yaml"
)
_GATE_B_CANDIDATE_FACTS_PATH_V3 = Path(
    "/home/hermes/.hermes/private/career/"
    "denis_vanyushkin_structured_resume_v1_1.json"
)
_GATE_B_DECISION_CONTRACT_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/decision_contract.v2.yaml"
)
_GATE_B_PRODUCT_SOT_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "docs/superpowers/specs/"
    "2026-08-10-job-intel-search-product-redesign-design.md"
)
_GATE_B_SEARCH_CONTRACT_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/search_contract.v1.yaml"
)
_GATE_B_SEMANTIC_CONTRACT_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml"
)
_GATE_B_TASK10_POLICY_PATH_V3 = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/evidence_synthesis.v1.yaml"
)
_GATE_B_PROTECTED_PATHS_V3 = (
    Path("/home/hermes/.hermes/state.db"),
    Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3"),
    Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3-wal"),
    Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3-shm"),
    Path("/home/hermes/.hermes/hermes-agent/.env"),
    Path("/home/hermes/.hermes/hermes-agent/config.yml"),
    Path("/etc/job-intel/job-intel.env"),
)
_GATE_B_PENDING_RECEIPT_ROOT_V3 = Path(
    "/etc/job-intel/gate-b-at-most-once"
)
_GATE_B_CONSUMED_RECEIPT_ROOT_V3 = Path(
    "/run/job-intel/gate-b-at-most-once"
)
_GATE_B_RUNS_ROOT_V3 = _GATE_B_PACKAGE_PARENT_V3 / "runs"
_GATE_B_OWNER_CHECKPOINT_FILENAME_V3 = "owner-checkpoint.json"
_GATE_B_OWNER_RECOVERY_KEY_FILENAME_V3 = "owner-recovery-public-key.bin"
_GATE_B_PENDING_RECEIPT_FILENAME_V3 = "launch.pending.json"
_GATE_B_CONSUMED_RECEIPT_FILENAME_V3 = "launch.consumed.json"
_GATE_B_SOURCE_KEYS_V3 = frozenset(
    {
        "corpus_manifest",
        "gate_a_manifest",
        "benchmark_policy",
        "reviewed_fragment_allowlist",
        "career_profile",
        "candidate_facts",
        "decision_contract",
        "product_sot",
        "search_contract",
        "semantic_contract",
        "task10_policy",
        "raw_artifacts",
    }
)
_GATE_B_REVIEW_DECISIONS_V3 = frozenset(
    {
        "allow_role_responsibility",
        "allow_role_requirement",
        "exclude_company_fact",
        "exclude_ambiguous",
    }
)
_GATE_B_DIRECT_FIELDS_V3 = ("title", "location", "salary", "posted_at")
_GATE_B_ALLOWED_SECTIONS_V3 = frozenset(
    {
        "responsibilities",
        "what_you_will_do",
        "requirements",
        "qualifications",
        "skills",
        "experience",
    }
)
_GATE_B_MAX_SOURCE_BYTES_V3 = 16_000_000
_RENAME_NOREPLACE = 1
_MAPPING_PROXY_TYPE_V3 = type(MappingProxyType({}))


class GateBPackageErrorV3(ValueError):
    """Fail-closed v3 source, package, or materialization error."""


class _ReviewedFragmentEntryContractV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection_key: str = Field(pattern=SHA256_PATTERN)
    vacancy_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    source_locator: str = Field(pattern=r"^/description#[0-9]{3}$")
    text_sha256: str = Field(pattern=SHA256_PATTERN)
    decision: Literal[
        "allow_role_responsibility",
        "allow_role_requirement",
        "exclude_company_fact",
        "exclude_ambiguous",
    ]
    reviewer_role: Literal["independent_gate_b_evidence_reviewer"]
    reviewed_at: AwareDatetime

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: AwareDatetime) -> AwareDatetime:
        return _require_utc(value)


class _ReviewedFragmentAllowlistContractV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["3.0.0"]
    gate_a_run_id: Literal["gate-a-20260816T141344Z"]
    gate_b_corpus_sha256: Literal[
        "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
    ]
    entries: tuple[_ReviewedFragmentEntryContractV3, ...]

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        identities = tuple(
            (
                entry.selection_key,
                entry.vacancy_artifact_sha256,
                entry.source_locator,
                entry.text_sha256,
            )
            for entry in self.entries
        )
        if len(set(identities)) != len(identities):
            raise ValueError("reviewed fragment identities must be unique")
        return self


@dataclass(frozen=True, slots=True)
class GateBValidatedPackageV3:
    """Data-only result of pure validation; it grants no I/O capability."""

    package_sha256: str
    manifest_sha256: str
    manifest_bytes: bytes
    ordered_input_sha256s: tuple[str, ...]
    artifacts: Mapping[str, bytes]
    artifact_sha256s: Mapping[str, str]
    source_file_sha256s: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class GateBObservedOperationV3:
    kind: str
    path: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class GateBMaterializationReceiptV3:
    package_root: str
    package_sha256: str
    created: bool
    artifact_sha256s: Mapping[str, str]
    source_snapshot_sha256: str
    protected_snapshot_sha256: str
    observed_operations: tuple[GateBObservedOperationV3, ...]


class GateBRuntimeManifestV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    runtime_kind: Literal["gate_b_at_most_once"]
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    python_version: Literal["3.12.13"]
    runtime_tree_sha256: str = Field(pattern=SHA256_PATTERN)
    python_executable_sha256: str = Field(pattern=SHA256_PATTERN)
    stdlib_tree_sha256: str = Field(pattern=SHA256_PATTERN)
    dependency_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    installed_distributions_sha256: str = Field(pattern=SHA256_PATTERN)
    sys_path_sha256: str = Field(pattern=SHA256_PATTERN)
    editable_installs: tuple[str, ...]

    @field_validator("editable_installs", mode="before")
    @classmethod
    def normalize_editable_installs(cls, value: object) -> object:
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return tuple(value)
        return value

    @field_validator("editable_installs")
    @classmethod
    def validate_no_editable_installs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value:
            raise ValueError("editable installs are forbidden")
        return value

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class GateBLaunchBindingV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    run_id: str = Field(pattern=r"^gate-b-at-most-once-[0-9a-f]{16}$")
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    package_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    ordered_input_sha256s: tuple[str, ...] = Field(
        min_length=_ORDERED_CALL_CAP,
        max_length=_ORDERED_CALL_CAP,
    )
    ordered_projection_sha256s: tuple[str, ...] = Field(
        min_length=_ORDERED_CALL_CAP,
        max_length=_ORDERED_CALL_CAP,
    )
    source_authority_sha256s: dict[str, str]
    model_id: Literal["openai/gpt-5-mini"]
    maximum_output_tokens: Literal[2000]
    ordered_call_cap: Literal[48]
    per_call_maximum_usd: Decimal
    aggregate_maximum_usd: Decimal

    @field_validator("ordered_input_sha256s", "ordered_projection_sha256s")
    @classmethod
    def validate_ordered_sha256s(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(SHA256_PATTERN, item) is None for item in value):
            raise ValueError("ordered launch hashes must be SHA-256 values")
        if len(set(value)) != _ORDERED_CALL_CAP:
            raise ValueError("ordered launch hashes must be unique")
        return value

    @field_validator("source_authority_sha256s")
    @classmethod
    def validate_source_authorities(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or any(
            not name
            or re.fullmatch(SHA256_PATTERN, sha256) is None
            for name, sha256 in value.items()
        ):
            raise ValueError("source authority identity is invalid")
        return dict(sorted(value.items()))

    @field_validator("per_call_maximum_usd")
    @classmethod
    def validate_per_call_maximum(cls, value: Decimal) -> Decimal:
        if value != _PER_CALL_MAXIMUM_USD:
            raise ValueError("per-call maximum must be USD 0.01")
        return value

    @field_validator("aggregate_maximum_usd")
    @classmethod
    def validate_aggregate_maximum(cls, value: Decimal) -> Decimal:
        if value != _AGGREGATE_MAXIMUM_USD:
            raise ValueError("aggregate maximum must be USD 0.48")
        return value

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class GateBOwnerCheckpointManifestV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    checkpoint_kind: Literal["gate_b_at_most_once_owner_approval"]
    approved_at: AwareDatetime
    launch_identity: GateBLaunchBindingV3

    @field_validator("approved_at")
    @classmethod
    def validate_approved_at(cls, value: AwareDatetime) -> AwareDatetime:
        return _require_utc(value)

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class GateBOneTimeLaunchReceiptV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    receipt_kind: Literal["gate_b_at_most_once_launch"]
    run_id: str = Field(pattern=r"^gate-b-at-most-once-[0-9a-f]{16}$")
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    nonce: str = Field(pattern=SHA256_PATTERN)
    checkpoint_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    launch_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    package_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    ordered_call_cap: Literal[48]
    per_call_maximum_usd: Decimal
    aggregate_maximum_usd: Decimal

    @field_validator("issued_at", "expires_at")
    @classmethod
    def validate_timestamps(cls, value: AwareDatetime) -> AwareDatetime:
        return _require_utc(value)

    @field_validator("per_call_maximum_usd")
    @classmethod
    def validate_per_call_maximum(cls, value: Decimal) -> Decimal:
        if value != _PER_CALL_MAXIMUM_USD:
            raise ValueError("per-call maximum must be USD 0.01")
        return value

    @field_validator("aggregate_maximum_usd")
    @classmethod
    def validate_aggregate_maximum(cls, value: Decimal) -> Decimal:
        if value != _AGGREGATE_MAXIMUM_USD:
            raise ValueError("aggregate maximum must be USD 0.48")
        return value

    @model_validator(mode="after")
    def validate_one_time_window(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("launch receipt expiry must follow issuance")
        if self.expires_at - self.issued_at != timedelta(minutes=30):
            raise ValueError("launch receipt expiry must be exactly 30 minutes")
        return self

    @property
    def canonical_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


def _open_directory_nofollow_v3(path: Path) -> int:
    if not path.is_absolute():
        raise GateBPackageErrorV3("source_path_not_absolute")
    descriptor = os.open(
        "/",
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise GateBPackageErrorV3("source_path_component_invalid")
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise GateBPackageErrorV3("source_parent_not_directory")
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_path_nofollow_v3(
    path: Path,
    *,
    maximum_bytes: int = _GATE_B_MAX_SOURCE_BYTES_V3,
) -> bytes:
    parent_descriptor = _open_directory_nofollow_v3(path.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > maximum_bytes
        ):
            raise GateBPackageErrorV3("source_file_metadata_invalid")
        payload = _read_all(descriptor)
        if len(payload) != metadata.st_size:
            raise GateBPackageErrorV3("source_file_changed_while_reading")
        final_metadata = os.fstat(descriptor)
        if (
            final_metadata.st_dev != metadata.st_dev
            or final_metadata.st_ino != metadata.st_ino
            or final_metadata.st_size != metadata.st_size
            or final_metadata.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise GateBPackageErrorV3("source_file_changed_while_reading")
        return payload
    except OSError as exc:
        raise GateBPackageErrorV3("source_read_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def load_gate_b_source_bytes_v3() -> dict[str, bytes | dict[str, bytes]]:
    """Load only the fixed, pinned v3 sources without following symlinks."""
    corpus_bytes = _read_path_nofollow_v3(_GATE_B_CORPUS_MANIFEST_PATH_V3)
    if hashlib.sha256(corpus_bytes).hexdigest() != _GATE_B_CORPUS_SHA256_V3:
        raise GateBPackageErrorV3("corpus_manifest_sha256_mismatch")
    try:
        corpus = json.loads(corpus_bytes)
        records = corpus["records"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GateBPackageErrorV3("corpus_manifest_invalid") from exc
    if not isinstance(records, list) or len(records) != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("corpus_record_count_invalid")
    raw_artifacts: dict[str, bytes] = {}
    for record in records:
        if not isinstance(record, dict):
            raise GateBPackageErrorV3("corpus_record_invalid")
        reference = record.get("raw_reference")
        expected_sha256 = record.get("raw_content_sha256")
        if (
            not isinstance(reference, str)
            or not isinstance(expected_sha256, str)
            or reference != f"raw-evidence/{expected_sha256}.json"
            or re.fullmatch(SHA256_PATTERN, expected_sha256) is None
        ):
            raise GateBPackageErrorV3("corpus_raw_reference_invalid")
        payload = _read_path_nofollow_v3(_GATE_A_SOURCE_ROOT_V3 / reference)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise GateBPackageErrorV3("raw_artifact_sha256_mismatch")
        raw_artifacts[reference] = payload
    return {
        "corpus_manifest": corpus_bytes,
        "gate_a_manifest": _read_path_nofollow_v3(
            _GATE_A_SOURCE_ROOT_V3 / "manifest.yaml"
        ),
        "benchmark_policy": _read_path_nofollow_v3(
            DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH
        ),
        "reviewed_fragment_allowlist": _read_path_nofollow_v3(
            _GATE_B_ALLOWLIST_PATH_V3
        ),
        "career_profile": _read_path_nofollow_v3(_GATE_B_PROFILE_PATH_V3),
        "candidate_facts": _read_path_nofollow_v3(
            _GATE_B_CANDIDATE_FACTS_PATH_V3
        ),
        "decision_contract": _read_path_nofollow_v3(
            _GATE_B_DECISION_CONTRACT_PATH_V3
        ),
        "product_sot": _read_path_nofollow_v3(_GATE_B_PRODUCT_SOT_PATH_V3),
        "search_contract": _read_path_nofollow_v3(
            _GATE_B_SEARCH_CONTRACT_PATH_V3
        ),
        "semantic_contract": _read_path_nofollow_v3(
            _GATE_B_SEMANTIC_CONTRACT_PATH_V3
        ),
        "task10_policy": _read_path_nofollow_v3(_GATE_B_TASK10_POLICY_PATH_V3),
        "raw_artifacts": raw_artifacts,
    }


def _plain_mapping_v3(value: object, *, error: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GateBPackageErrorV3(error)
    if not all(isinstance(key, str) for key in value):
        raise GateBPackageErrorV3(error)
    return value


def _source_payload_v3(source_bytes: Mapping[str, object], key: str) -> bytes:
    value = source_bytes.get(key)
    if type(value) is not bytes:
        raise GateBPackageErrorV3(f"source_{key}_must_be_bytes")
    return value


def _decode_json_mapping_v3(payload: bytes, *, error: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateBPackageErrorV3(error) from exc
    return _plain_mapping_v3(value, error=error)


def _decode_yaml_mapping_v3(payload: bytes, *, error: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise GateBPackageErrorV3(error) from exc
    return _plain_mapping_v3(value, error=error)


def _profile_authority_hashes_v3(profile: Mapping[str, Any]) -> dict[str, str]:
    authorities = _plain_mapping_v3(
        profile.get("authorities"),
        error="career_profile_authorities_invalid",
    )
    required = {
        "candidate_facts_ref",
        "preference_model_ref",
        "product_sot_ref",
        "search_contract_ref",
    }
    if set(authorities) != required:
        raise GateBPackageErrorV3("career_profile_authorities_invalid")
    result: dict[str, str] = {}
    for name in sorted(required):
        reference = _plain_mapping_v3(
            authorities[name],
            error="career_profile_authority_ref_invalid",
        )
        sha256 = reference.get("sha256")
        if (
            set(reference) != {"artifact_id", "version", "sha256"}
            or not isinstance(reference.get("artifact_id"), str)
            or not isinstance(reference.get("version"), str)
            or not isinstance(sha256, str)
            or re.fullmatch(SHA256_PATTERN, sha256) is None
        ):
            raise GateBPackageErrorV3("career_profile_authority_ref_invalid")
        result[name] = sha256
    return result


def gate_b_governed_pricing_schedule_v3() -> GovernedPricingSchedule:
    """Return the one reviewed Gate B pricing and token schedule."""
    return GovernedPricingSchedule(
        version="openrouter-openai-gpt5-mini-2026-08-17",
        model_id="openai/gpt-5-mini",
        input_usd_per_mtok=Decimal("0.25"),
        output_usd_per_mtok=Decimal("2.00"),
        max_input_tokens=24_000,
        max_output_tokens=2_000,
    )


def _derive_launch_authority_sha256s_v3(
    source_bytes: Mapping[str, object],
) -> dict[str, str]:
    """Derive all non-corpus launch authorities without performing I/O."""
    profile_bytes = _source_payload_v3(source_bytes, "career_profile")
    candidate_facts_bytes = _source_payload_v3(source_bytes, "candidate_facts")
    decision_contract_bytes = _source_payload_v3(source_bytes, "decision_contract")
    product_sot_bytes = _source_payload_v3(source_bytes, "product_sot")
    search_contract_bytes = _source_payload_v3(source_bytes, "search_contract")
    semantic_contract_bytes = _source_payload_v3(source_bytes, "semantic_contract")
    task10_policy_bytes = _source_payload_v3(source_bytes, "task10_policy")
    benchmark_policy_bytes = _source_payload_v3(source_bytes, "benchmark_policy")

    profile = _decode_yaml_mapping_v3(profile_bytes, error="career_profile_invalid")
    profile_authorities = _profile_authority_hashes_v3(profile)
    actual_content_hashes = {
        "candidate_facts_ref": hashlib.sha256(candidate_facts_bytes).hexdigest(),
        "product_sot_ref": hashlib.sha256(product_sot_bytes).hexdigest(),
        "search_contract_ref": hashlib.sha256(search_contract_bytes).hexdigest(),
    }
    if any(
        actual_content_hashes[name] != profile_authorities[name]
        for name in actual_content_hashes
    ):
        raise GateBPackageErrorV3("profile_authority_content_mismatch")
    try:
        json.loads(candidate_facts_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateBPackageErrorV3("candidate_facts_invalid") from exc

    decision_contract = _decode_yaml_mapping_v3(
        decision_contract_bytes,
        error="decision_contract_invalid",
    )
    decision_authorities = _plain_mapping_v3(
        decision_contract.get("authority_hashes"),
        error="decision_contract_authorities_invalid",
    )
    expected_decision_authorities = {
        "career_profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "candidate_facts_sha256": actual_content_hashes["candidate_facts_ref"],
        "search_contract_sha256": actual_content_hashes["search_contract_ref"],
        "product_sot_sha256": actual_content_hashes["product_sot_ref"],
        "evidence_synthesis_contract_sha256": hashlib.sha256(
            task10_policy_bytes
        ).hexdigest(),
    }
    if any(
        decision_authorities.get(name) != value
        for name, value in expected_decision_authorities.items()
    ):
        raise GateBPackageErrorV3("decision_contract_authority_mismatch")

    semantic_payload = _decode_yaml_mapping_v3(
        semantic_contract_bytes,
        error="semantic_contract_invalid",
    )
    semantic_contract_data = semantic_payload.get("semantic_fact_contract")
    try:
        semantic_contract = SemanticFactContract.model_validate(
            semantic_contract_data
        )
        task10_policy = EvidenceSynthesisPolicyV1.model_validate(
            _decode_yaml_mapping_v3(
                task10_policy_bytes,
                error="task10_policy_invalid",
            )
        )
        benchmark_policy = load_gate_b_benchmark_policy_v3(
            _decode_yaml_mapping_v3(
                benchmark_policy_bytes,
                error="benchmark_policy_invalid",
            )
        )
    except (ValidationError, ValueError) as exc:
        raise GateBPackageErrorV3("launch_authority_contract_invalid") from exc
    semantic_prompt = build_prompt_for_version(
        task10_policy.semantic_prompt_version,
        semantic_contract,
    )
    task10_prompt = build_task10_prompt_v2(task10_policy)
    pricing = gate_b_governed_pricing_schedule_v3()
    if (
        pricing.model_id != task10_policy.model_id
        or pricing.max_output_tokens != 2_000
        or pricing.reservation_cost_usd != _PER_CALL_MAXIMUM_USD
    ):
        raise GateBPackageErrorV3("launch_pricing_contract_invalid")
    launch_limits = {
        "ordered_call_cap": benchmark_policy.ordered_call_cap,
        "per_call_maximum_usd": str(benchmark_policy.per_call_maximum_usd),
        "aggregate_maximum_usd": str(benchmark_policy.aggregate_maximum_usd),
        "maximum_output_tokens": pricing.max_output_tokens,
    }
    result = {
        "benchmark_policy": hashlib.sha256(benchmark_policy_bytes).hexdigest(),
        "career_profile": hashlib.sha256(profile_bytes).hexdigest(),
        "candidate_facts": actual_content_hashes["candidate_facts_ref"],
        "decision_contract": hashlib.sha256(decision_contract_bytes).hexdigest(),
        "product_sot": actual_content_hashes["product_sot_ref"],
        "search_contract": actual_content_hashes["search_contract_ref"],
        "semantic_contract": hashlib.sha256(semantic_contract_bytes).hexdigest(),
        "task10_policy": hashlib.sha256(task10_policy_bytes).hexdigest(),
        "task10_prompt": hashlib.sha256(task10_prompt.encode("utf-8")).hexdigest(),
        "task10_prompt_version": hashlib.sha256(
            TASK10_PROMPT_VERSION_V2.encode("utf-8")
        ).hexdigest(),
        "semantic_prompt": hashlib.sha256(
            semantic_prompt.encode("utf-8")
        ).hexdigest(),
        "semantic_prompt_version": hashlib.sha256(
            task10_policy.semantic_prompt_version.encode("utf-8")
        ).hexdigest(),
        "provider_output_schema": canonical_json_sha256(
            ProviderEvidencePayloadV2.model_json_schema()
        ),
        "model_id": hashlib.sha256(task10_policy.model_id.encode("utf-8")).hexdigest(),
        "pricing": pricing.identity_sha256,
        "launch_limits": canonical_json_sha256(launch_limits),
        **{
            f"profile_{name}": sha256
            for name, sha256 in profile_authorities.items()
        },
    }
    if any(re.fullmatch(SHA256_PATTERN, value) is None for value in result.values()):
        raise GateBPackageErrorV3("launch_authority_identity_invalid")
    return result


class _GateBDescriptionBlockV3:
    __slots__ = ("section", "text")

    def __init__(self, section: str | None, text: str) -> None:
        self.section = section
        self.text = text


def _gate_b_canonical_text_v3(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _gate_b_classify_section_v3(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not normalized:
        return None
    if "responsibilit" in normalized:
        return "responsibilities"
    if "what_you" in normalized and ("do" in normalized or "work" in normalized):
        return "what_you_will_do"
    if "who_you_are" in normalized:
        return "qualifications"
    if (
        "what_you" in normalized and "bring" in normalized
    ) or "who_are_you" in normalized:
        return "requirements"
    if "requirement" in normalized:
        return "requirements"
    if "qualification" in normalized:
        return "qualifications"
    if "skill" in normalized:
        return "skills"
    if "experience" in normalized:
        return "experience"
    if "about" in normalized or "company" in normalized:
        return "company"
    return None


def _gate_b_split_inline_section_v3(value: str) -> tuple[str | None, str]:
    match = re.match(
        (
            r"^(responsibilities?|what\s+you(?:'|’)ll\s+do|"
            r"what\s+you\s+will\s+do|requirements?|qualifications?|"
            r"skills?|experience)\s*(?::|-)?\s*"
        ),
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None, value
    return _gate_b_classify_section_v3(match.group(1)), value[match.end() :]


class _GateBSectionedDescriptionParserV3(HTMLParser):
    _BLOCK_TAGS = frozenset({"p", "div", "li", "br"})
    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_GateBDescriptionBlockV3] = []
        self._section: str | None = None
        self._buffer: list[str] = []
        self._heading_buffer: list[str] | None = None
        self._active_block_tag: str | None = None
        self._block_prior_section: str | None = None
        self._emphasis_depth = 0
        self._block_has_text = False
        self._block_has_non_emphasis_text = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        lowered = tag.casefold()
        if lowered in self._HEADING_TAGS:
            self._flush()
            self._heading_buffer = []
        elif lowered in self._BLOCK_TAGS:
            self._flush()
            if lowered != "br":
                self._active_block_tag = lowered
                self._block_prior_section = self._section
                self._block_has_text = False
                self._block_has_non_emphasis_text = False
        elif lowered in {"b", "strong", "em"}:
            self._emphasis_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self._HEADING_TAGS:
            heading = _gate_b_canonical_text_v3(
                " ".join(self._heading_buffer or ())
            )
            self._section = _gate_b_classify_section_v3(heading)
            self._heading_buffer = None
        elif lowered in {"p", "div", "li"}:
            text = _gate_b_canonical_text_v3(" ".join(self._buffer))
            prior_section = self._block_prior_section
            inline_section, _ = _gate_b_split_inline_section_v3(text)
            structural_heading = (
                self._is_emphasized_block_heading() and inline_section is None
            )
            self._flush()
            if structural_heading:
                heading_section = _gate_b_classify_section_v3(text)
                if heading_section is not None:
                    self._section = (
                        heading_section
                        if prior_section in _GATE_B_ALLOWED_SECTIONS_V3
                        else None
                    )
            self._active_block_tag = None
            self._block_prior_section = None
            self._block_has_text = False
            self._block_has_non_emphasis_text = False
        elif lowered in {"b", "strong", "em"} and self._emphasis_depth:
            self._emphasis_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._heading_buffer is not None:
            self._heading_buffer.append(data)
        else:
            self._buffer.append(data)
            if _gate_b_canonical_text_v3(data):
                self._block_has_text = True
                if self._emphasis_depth == 0:
                    self._block_has_non_emphasis_text = True

    def finish(self) -> tuple[_GateBDescriptionBlockV3, ...]:
        self._flush()
        return tuple(self.blocks)

    def _is_emphasized_block_heading(self) -> bool:
        return (
            self._active_block_tag is not None
            and self._block_has_text
            and not self._block_has_non_emphasis_text
        )

    def _flush(self) -> None:
        text = _gate_b_canonical_text_v3(" ".join(self._buffer))
        self._buffer = []
        if not text:
            return
        inline_section, body = _gate_b_split_inline_section_v3(text)
        if inline_section is not None:
            self._section = inline_section
            text = _gate_b_canonical_text_v3(body)
        if text:
            self.blocks.append(_GateBDescriptionBlockV3(self._section, text))


def _gate_b_description_blocks_v3(
    raw_description: object,
) -> tuple[_GateBDescriptionBlockV3, ...]:
    decoded = str(raw_description or "")
    for _ in range(3):
        next_value = unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    parser = _GateBSectionedDescriptionParserV3()
    parser.feed(decoded)
    parser.close()
    blocks = parser.finish()
    if blocks:
        return blocks
    text = _gate_b_canonical_text_v3(decoded)
    return () if not text else (_GateBDescriptionBlockV3(None, text),)


def _gate_b_exact_fragments_v3(value: str) -> tuple[str, ...]:
    pieces: list[str] = []
    for bullet in re.split(r"(?:\s*[•▪●]\s*|\s+-\s+)", value):
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", bullet):
            text = _gate_b_canonical_text_v3(sentence)
            if not text:
                continue
            while text:
                if len(text) <= 500:
                    pieces.append(text)
                    break
                boundary = text.rfind(" ", 0, 501)
                if boundary <= 0:
                    raise GateBPackageErrorV3(
                        "reviewed_fragment_candidate_contract_invalid"
                    )
                pieces.append(text[:boundary])
                text = text[boundary + 1 :]
    return tuple(pieces)


def _gate_b_candidate_contract_v3(
    record: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[str, dict[tuple[str, str, str, str], str]]:
    selection_key = str(record["selection_key"])
    artifact_fragments: list[dict[str, str]] = []
    candidate_fragments: list[tuple[str, str, str]] = []
    for field_name in _GATE_B_DIRECT_FIELDS_V3:
        text = _gate_b_canonical_text_v3(raw.get(field_name))
        if text:
            artifact_fragments.append(
                {"source_locator": f"/{field_name}#000", "text": text}
            )
    description_index = 0
    for block in _gate_b_description_blocks_v3(raw.get("description")):
        for text in _gate_b_exact_fragments_v3(block.text):
            locator = f"/description#{description_index:03d}"
            description_index += 1
            artifact_fragments.append({"source_locator": locator, "text": text})
            if block.section in _GATE_B_ALLOWED_SECTIONS_V3:
                candidate_fragments.append((locator, text, block.section))
    if not artifact_fragments:
        raise GateBPackageErrorV3("reviewed_fragment_candidate_contract_invalid")
    artifact_payload = {
        "schema_version": "1.0.0",
        "artifact_id": f"gate-b-v3-vacancy:{selection_key}",
        "artifact_version": "3.0.0",
        "redaction_state": "shareable_redacted",
        "fragments": artifact_fragments,
    }
    artifact_sha256 = canonical_json_sha256(artifact_payload)
    return artifact_sha256, {
        (
            selection_key,
            artifact_sha256,
            locator,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ): section
        for locator, text, section in candidate_fragments
    }


def _validated_review_entries_v3(
    payload: bytes,
    *,
    selection_keys: frozenset[str],
) -> tuple[_ReviewedFragmentEntryContractV3, ...]:
    mapping = _decode_yaml_mapping_v3(
        payload,
        error="reviewed_fragment_allowlist_invalid",
    )
    try:
        allowlist = _ReviewedFragmentAllowlistContractV3.model_validate(mapping)
    except ValidationError as exc:
        raise GateBPackageErrorV3("reviewed_fragment_allowlist_invalid") from exc
    if any(entry.selection_key not in selection_keys for entry in allowlist.entries):
        raise GateBPackageErrorV3("reviewed_fragment_selection_unknown")
    artifact_by_selection: dict[str, str] = {}
    for entry in allowlist.entries:
        existing = artifact_by_selection.setdefault(
            entry.selection_key,
            entry.vacancy_artifact_sha256,
        )
        if existing != entry.vacancy_artifact_sha256:
            raise GateBPackageErrorV3("reviewed_fragment_artifact_mixed")
        if entry.decision not in _GATE_B_REVIEW_DECISIONS_V3:
            raise GateBPackageErrorV3("reviewed_fragment_decision_invalid")
    return allowlist.entries


def _validate_corpus_v3(
    corpus: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    if set(corpus) != {"schema_version", "gate", "gate_a", "selection", "records"}:
        raise GateBPackageErrorV3("corpus_manifest_fields_invalid")
    gate_a = _plain_mapping_v3(
        corpus.get("gate_a"),
        error="corpus_gate_a_identity_invalid",
    )
    if (
        corpus.get("schema_version") != "1.0.0"
        or corpus.get("gate") != "gate-b"
        or gate_a
        != {
            "commit": _GATE_A_COMMIT_V3,
            "manifest_sha256": _GATE_A_MANIFEST_SHA256_V3,
            "run_id": _GATE_A_RUN_ID_V3,
        }
    ):
        raise GateBPackageErrorV3("corpus_identity_invalid")
    selection = _plain_mapping_v3(
        corpus.get("selection"),
        error="corpus_selection_invalid",
    )
    if selection.get("sample_size") != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("corpus_record_count_invalid")
    raw_records = corpus.get("records")
    if not isinstance(raw_records, list) or len(raw_records) != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("corpus_record_count_invalid")
    records: list[dict[str, Any]] = []
    identities: set[str] = set()
    for item in raw_records:
        record = _plain_mapping_v3(item, error="corpus_record_invalid")
        selection_key = record.get("selection_key")
        raw_sha256 = record.get("raw_content_sha256")
        raw_reference = record.get("raw_reference")
        if (
            not isinstance(selection_key, str)
            or re.fullmatch(SHA256_PATTERN, selection_key) is None
            or selection_key in identities
            or not isinstance(raw_sha256, str)
            or re.fullmatch(SHA256_PATTERN, raw_sha256) is None
            or raw_reference != f"raw-evidence/{raw_sha256}.json"
            or record.get("run_id") != _GATE_A_RUN_ID_V3
        ):
            raise GateBPackageErrorV3("corpus_record_identity_invalid")
        expected_selection_key = canonical_json_sha256(
            {
                "run_id": record.get("run_id"),
                "source_family": record.get("source_family"),
                "source_id": record.get("source_id"),
                "raw_content_sha256": raw_sha256,
            }
        )
        if selection_key != expected_selection_key:
            raise GateBPackageErrorV3("corpus_selection_key_mismatch")
        identities.add(selection_key)
        records.append(record)
    return tuple(records)


def _freeze_bytes_mapping_v3(value: Mapping[str, bytes]) -> Mapping[str, bytes]:
    return MappingProxyType(dict(value))


def _freeze_text_mapping_v3(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(value))


def validate_gate_b_package_pure_v3(
    source_bytes: Mapping[str, object],
) -> GateBValidatedPackageV3:
    """Validate and canonically package already-read bytes with zero I/O."""
    sources = _plain_mapping_v3(
        source_bytes,
        error="source_bytes_must_be_a_plain_mapping",
    )
    if set(sources) != _GATE_B_SOURCE_KEYS_V3:
        raise GateBPackageErrorV3("source_bytes_fields_invalid")
    corpus_bytes = _source_payload_v3(sources, "corpus_manifest")
    gate_a_manifest_bytes = _source_payload_v3(sources, "gate_a_manifest")
    policy_bytes = _source_payload_v3(sources, "benchmark_policy")
    allowlist_bytes = _source_payload_v3(sources, "reviewed_fragment_allowlist")
    launch_authorities = _derive_launch_authority_sha256s_v3(sources)
    raw_artifacts = _plain_mapping_v3(
        sources.get("raw_artifacts"),
        error="raw_artifacts_must_be_a_plain_mapping",
    )
    if not all(type(payload) is bytes for payload in raw_artifacts.values()):
        raise GateBPackageErrorV3("raw_artifacts_must_contain_bytes")

    corpus_sha256 = hashlib.sha256(corpus_bytes).hexdigest()
    gate_a_manifest_sha256 = hashlib.sha256(gate_a_manifest_bytes).hexdigest()
    if corpus_sha256 != _GATE_B_CORPUS_SHA256_V3:
        raise GateBPackageErrorV3("corpus_manifest_sha256_mismatch")
    if gate_a_manifest_sha256 != _GATE_A_MANIFEST_SHA256_V3:
        raise GateBPackageErrorV3("gate_a_manifest_sha256_mismatch")
    corpus = _decode_json_mapping_v3(corpus_bytes, error="corpus_manifest_invalid")
    records = _validate_corpus_v3(corpus)
    expected_raw_references = {str(record["raw_reference"]) for record in records}
    if set(raw_artifacts) != expected_raw_references:
        raise GateBPackageErrorV3("raw_artifact_inventory_invalid")

    try:
        policy = load_gate_b_benchmark_policy_v3(
            _decode_yaml_mapping_v3(policy_bytes, error="benchmark_policy_invalid")
        )
    except (ValidationError, ValueError) as exc:
        raise GateBPackageErrorV3("benchmark_policy_invalid") from exc
    if policy.ordered_call_cap != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("benchmark_policy_call_cap_invalid")
    if hashlib.sha256(allowlist_bytes).hexdigest() != _GATE_B_ALLOWLIST_SHA256_V3:
        raise GateBPackageErrorV3("reviewed_fragment_candidate_contract_invalid")
    selection_keys = frozenset(str(record["selection_key"]) for record in records)
    raw_by_reference: dict[str, dict[str, Any]] = {}
    artifact_by_selection: dict[str, str] = {}
    expected_candidate_sections: dict[tuple[str, str, str, str], str] = {}
    for record in records:
        reference = str(record["raw_reference"])
        raw_bytes = raw_artifacts[reference]
        raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if raw_sha256 != record["raw_content_sha256"]:
            raise GateBPackageErrorV3("raw_artifact_sha256_mismatch")
        raw = _decode_json_mapping_v3(raw_bytes, error="raw_artifact_invalid")
        for field_name in ("source_family", "source_id", "query_id", "company"):
            if raw.get(field_name) != record.get(field_name):
                raise GateBPackageErrorV3("raw_artifact_record_mismatch")
        raw_by_reference[reference] = raw
        artifact_sha256, candidates = _gate_b_candidate_contract_v3(record, raw)
        artifact_by_selection[str(record["selection_key"])] = artifact_sha256
        if set(expected_candidate_sections).intersection(candidates):
            raise GateBPackageErrorV3("reviewed_fragment_candidate_contract_invalid")
        expected_candidate_sections.update(candidates)
    review_entries = _validated_review_entries_v3(
        allowlist_bytes,
        selection_keys=selection_keys,
    )
    actual_candidates = {
        (
            entry.selection_key,
            entry.vacancy_artifact_sha256,
            entry.source_locator,
            entry.text_sha256,
        ): entry
        for entry in review_entries
    }
    if set(actual_candidates) != set(expected_candidate_sections):
        raise GateBPackageErrorV3("reviewed_fragment_candidate_contract_invalid")
    for identity, entry in actual_candidates.items():
        section = expected_candidate_sections[identity]
        if (
            entry.decision == "allow_role_responsibility"
            and section not in {"responsibilities", "what_you_will_do"}
        ) or (
            entry.decision == "allow_role_requirement"
            and section in {"responsibilities", "what_you_will_do"}
        ):
            raise GateBPackageErrorV3("reviewed_fragment_candidate_contract_invalid")
    entries_by_selection: dict[
        str,
        list[_ReviewedFragmentEntryContractV3],
    ] = {selection_key: [] for selection_key in selection_keys}
    for entry in review_entries:
        entries_by_selection[entry.selection_key].append(entry)

    source_authorities = {
        **launch_authorities,
        "corpus_manifest": corpus_sha256,
        "gate_a_manifest": gate_a_manifest_sha256,
        "reviewed_fragment_allowlist": hashlib.sha256(allowlist_bytes).hexdigest(),
    }
    artifacts: dict[str, bytes] = {}
    ordered_input_sha256s: list[str] = []
    index_records: list[dict[str, Any]] = []
    source_file_sha256s = {"manifest.yaml": gate_a_manifest_sha256}
    for ordinal, record in enumerate(records):
        reference = str(record["raw_reference"])
        raw_bytes = raw_artifacts[reference]
        raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        raw = raw_by_reference[reference]
        source_file_sha256s[reference] = raw_sha256
        selected_entries = sorted(
            entries_by_selection[str(record["selection_key"])],
            key=lambda entry: (
                entry.source_locator,
                entry.text_sha256,
                entry.decision,
            ),
        )
        reviewed_artifact_sha256 = artifact_by_selection[
            str(record["selection_key"])
        ]
        input_payload = {
            "schema_version": "3.0.0",
            "input_kind": "gate_b_projector_source",
            "ordinal": ordinal,
            "selection_key": record["selection_key"],
            "raw_content_sha256": raw_sha256,
            "source_record": dict(record),
            "raw": raw,
            "projection_contract": {
                "interface": "project_vacancy_evidence_v3",
                "reviewed_vacancy_artifact_sha256": reviewed_artifact_sha256,
                "reviewed_fragment_entries": [
                    entry.model_dump(mode="json") for entry in selected_entries
                ],
            },
            "source_authority_sha256s": source_authorities,
        }
        input_bytes = _canonical_json_bytes(input_payload)
        input_sha256 = hashlib.sha256(input_bytes).hexdigest()
        input_reference = f"task10-inputs/{input_sha256}.json"
        if input_reference in artifacts:
            raise GateBPackageErrorV3("ordered_input_hashes_not_unique")
        artifacts[input_reference] = input_bytes
        ordered_input_sha256s.append(input_sha256)
        index_records.append(
            {
                "ordinal": ordinal,
                "selection_key": record["selection_key"],
                "raw_reference": reference,
                "raw_content_sha256": raw_sha256,
                "reviewed_vacancy_artifact_sha256": reviewed_artifact_sha256,
                "reviewed_fragment_entry_count": len(selected_entries),
                "task10_input_reference": input_reference,
                "task10_input_sha256": input_sha256,
            }
        )
    if len(set(ordered_input_sha256s)) != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("ordered_input_hashes_not_unique")

    index_payload = {
        "schema_version": "3.0.0",
        "package_kind": "gate_b_at_most_once",
        "gate_a": {
            "run_id": _GATE_A_RUN_ID_V3,
            "commit": _GATE_A_COMMIT_V3,
            "manifest_sha256": gate_a_manifest_sha256,
        },
        "corpus_manifest_sha256": corpus_sha256,
        "source_authority_sha256s": source_authorities,
        "records": index_records,
    }
    index_bytes = _canonical_json_bytes(index_payload)
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    artifacts["package-index.json"] = index_bytes
    authority_sha256s = tuple(
        sorted({*source_authorities.values(), index_sha256})
    )
    manifest = GateBPackageManifestV3(
        schema_version="3.0.0",
        package_id=f"gate-b-at-most-once-v3:{corpus_sha256}",
        created_at=datetime(2026, 8, 16, 14, 13, 44, tzinfo=timezone.utc),
        ordered_input_sha256s=tuple(ordered_input_sha256s),
        authority_sha256s=authority_sha256s,
    )
    manifest_bytes = _canonical_json_bytes(manifest.model_dump(mode="json"))
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != manifest.canonical_sha256:
        raise GateBPackageErrorV3("package_manifest_not_canonical")
    artifacts["package-manifest.json"] = manifest_bytes
    artifact_sha256s = {
        reference: hashlib.sha256(payload).hexdigest()
        for reference, payload in artifacts.items()
    }
    return GateBValidatedPackageV3(
        package_sha256=manifest_sha256,
        manifest_sha256=manifest_sha256,
        manifest_bytes=manifest_bytes,
        ordered_input_sha256s=tuple(ordered_input_sha256s),
        artifacts=_freeze_bytes_mapping_v3(artifacts),
        artifact_sha256s=_freeze_text_mapping_v3(artifact_sha256s),
        source_file_sha256s=_freeze_text_mapping_v3(source_file_sha256s),
    )


def _validate_package_in_memory_v3(package: GateBValidatedPackageV3) -> None:
    if type(package) is not GateBValidatedPackageV3:
        raise GateBPackageErrorV3("validated_package_required")
    if type(package.source_file_sha256s) is not _MAPPING_PROXY_TYPE_V3:
        raise GateBPackageErrorV3("validated_package_source_inventory_invalid")
    if (
        type(package.artifacts) is not _MAPPING_PROXY_TYPE_V3
        or type(package.artifact_sha256s) is not _MAPPING_PROXY_TYPE_V3
        or type(package.manifest_bytes) is not bytes
        or type(package.ordered_input_sha256s) is not tuple
    ):
        raise GateBPackageErrorV3("validated_package_data_invalid")
    if (
        re.fullmatch(SHA256_PATTERN, package.package_sha256) is None
        or package.package_sha256 != package.manifest_sha256
        or package.manifest_bytes != package.artifacts.get("package-manifest.json")
        or hashlib.sha256(package.manifest_bytes).hexdigest()
        != package.package_sha256
        or set(package.artifacts) != set(package.artifact_sha256s)
    ):
        raise GateBPackageErrorV3("validated_package_identity_invalid")
    try:
        manifest = GateBPackageManifestV3.model_validate_json(package.manifest_bytes)
    except ValidationError as exc:
        raise GateBPackageErrorV3("validated_package_manifest_invalid") from exc
    if (
        manifest.canonical_sha256 != package.package_sha256
        or manifest.ordered_input_sha256s != package.ordered_input_sha256s
        or manifest.package_id
        != f"gate-b-at-most-once-v3:{_GATE_B_CORPUS_SHA256_V3}"
        or manifest.created_at
        != datetime(2026, 8, 16, 14, 13, 44, tzinfo=timezone.utc)
    ):
        raise GateBPackageErrorV3("validated_package_manifest_mismatch")
    expected_references = {
        "package-index.json",
        "package-manifest.json",
        *{
            f"task10-inputs/{sha256}.json"
            for sha256 in package.ordered_input_sha256s
        },
    }
    if set(package.artifacts) != expected_references:
        raise GateBPackageErrorV3("validated_package_inventory_invalid")
    index = _decode_json_mapping_v3(
        package.artifacts["package-index.json"],
        error="validated_package_index_invalid",
    )
    if (
        set(index)
        != {
            "schema_version",
            "package_kind",
            "gate_a",
            "corpus_manifest_sha256",
            "source_authority_sha256s",
            "records",
        }
        or index.get("schema_version") != "3.0.0"
        or index.get("package_kind") != "gate_b_at_most_once"
        or index.get("corpus_manifest_sha256") != _GATE_B_CORPUS_SHA256_V3
        or index.get("gate_a")
        != {
            "run_id": _GATE_A_RUN_ID_V3,
            "commit": _GATE_A_COMMIT_V3,
            "manifest_sha256": _GATE_A_MANIFEST_SHA256_V3,
        }
    ):
        raise GateBPackageErrorV3("validated_package_index_invalid")
    source_authorities = _plain_mapping_v3(
        index.get("source_authority_sha256s"),
        error="validated_package_index_invalid",
    )
    if not source_authorities or any(
        not isinstance(value, str)
        or re.fullmatch(SHA256_PATTERN, value) is None
        for value in source_authorities.values()
    ):
        raise GateBPackageErrorV3("validated_package_index_invalid")
    index_sha256 = hashlib.sha256(
        package.artifacts["package-index.json"]
    ).hexdigest()
    if set(manifest.authority_sha256s) != {
        *source_authorities.values(),
        index_sha256,
    }:
        raise GateBPackageErrorV3("validated_package_index_manifest_mismatch")
    trusted_artifact_sha256s = {
        "package-index.json": index_sha256,
        "package-manifest.json": package.package_sha256,
        **{
            f"task10-inputs/{sha256}.json": sha256
            for sha256 in package.ordered_input_sha256s
        },
    }
    if dict(package.artifact_sha256s) != trusted_artifact_sha256s:
        raise GateBPackageErrorV3("validated_package_artifact_hash_mismatch")
    for reference, payload in package.artifacts.items():
        if type(payload) is not bytes:
            raise GateBPackageErrorV3("validated_package_artifact_not_bytes")
        if (
            hashlib.sha256(payload).hexdigest()
            != trusted_artifact_sha256s[reference]
        ):
            raise GateBPackageErrorV3("validated_package_artifact_hash_mismatch")
    index_records = index.get("records")
    if not isinstance(index_records, list) or len(index_records) != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("validated_package_index_invalid")
    expected_source_files = {"manifest.yaml": _GATE_A_MANIFEST_SHA256_V3}
    for raw_record in index_records:
        record = _plain_mapping_v3(
            raw_record,
            error="validated_package_index_invalid",
        )
        reference = record.get("raw_reference")
        raw_sha256 = record.get("raw_content_sha256")
        if (
            not isinstance(raw_sha256, str)
            or re.fullmatch(SHA256_PATTERN, raw_sha256) is None
            or reference != f"raw-evidence/{raw_sha256}.json"
            or reference in expected_source_files
        ):
            raise GateBPackageErrorV3("validated_package_index_invalid")
        expected_source_files[reference] = raw_sha256
    if dict(package.source_file_sha256s) != expected_source_files:
        raise GateBPackageErrorV3("validated_package_source_inventory_invalid")


_GATE_B_RUNTIME_PAYLOAD_KEYS_V3 = frozenset(
    {
        "runtime_tree_manifest",
        "python_executable",
        "stdlib_tree_manifest",
        "dependency_lock",
        "installed_distributions",
        "sys_path",
    }
)


def _validate_runtime_payloads_v3(
    manifest: GateBRuntimeManifestV3,
    runtime_payloads: Mapping[str, object],
) -> None:
    payloads = _plain_mapping_v3(
        runtime_payloads,
        error="runtime_payloads_must_be_a_plain_mapping",
    )
    if set(payloads) != _GATE_B_RUNTIME_PAYLOAD_KEYS_V3 or any(
        type(value) is not bytes for value in payloads.values()
    ):
        raise GateBPackageErrorV3("runtime_payload_inventory_invalid")
    expected_hashes = {
        "runtime_tree_manifest": manifest.runtime_tree_sha256,
        "python_executable": manifest.python_executable_sha256,
        "stdlib_tree_manifest": manifest.stdlib_tree_sha256,
        "dependency_lock": manifest.dependency_lock_sha256,
        "installed_distributions": manifest.installed_distributions_sha256,
        "sys_path": manifest.sys_path_sha256,
    }
    for name, expected_sha256 in expected_hashes.items():
        value = payloads[name]
        assert isinstance(value, bytes)
        if hashlib.sha256(value).hexdigest() != expected_sha256:
            raise GateBPackageErrorV3(f"runtime_{name}_identity_mismatch")
    for name in ("runtime_tree_manifest", "stdlib_tree_manifest"):
        value = payloads[name]
        assert isinstance(value, bytes)
        inventory = _decode_json_mapping_v3(
            value,
            error=f"runtime_{name}_invalid",
        )
        if (
            not inventory
            or _canonical_json_bytes(inventory) != value
            or any(
                not isinstance(reference, str)
                or Path(reference).is_absolute()
                or ".." in Path(reference).parts
                or not isinstance(sha256, str)
                or re.fullmatch(SHA256_PATTERN, sha256) is None
                for reference, sha256 in inventory.items()
            )
        ):
            raise GateBPackageErrorV3(f"runtime_{name}_invalid")
    sys_path_bytes = payloads["sys_path"]
    assert isinstance(sys_path_bytes, bytes)
    try:
        sys_path = json.loads(sys_path_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateBPackageErrorV3("runtime_sys_path_invalid") from exc
    if (
        not isinstance(sys_path, list)
        or not sys_path
        or any(not isinstance(item, str) or not item for item in sys_path)
        or _canonical_json_bytes(sys_path) != sys_path_bytes
        or any("__editable__" in item for item in sys_path)
    ):
        raise GateBPackageErrorV3("runtime_sys_path_invalid")
    distributions = payloads["installed_distributions"]
    assert isinstance(distributions, bytes)
    if b" @ file:" in distributions or any(
        line.lstrip().startswith(b"-e ") for line in distributions.splitlines()
    ):
        raise GateBPackageErrorV3("runtime_editable_install_invalid")


def _load_current_runtime_identity_v3() -> GateBRuntimeManifestV3:
    """Recompute runtime identity from the one fixed immutable export root."""
    export_root = _GATE_B_RUNTIME_EXPORT_ROOT_V3
    runtime_root = export_root / "runtime"
    python_root = export_root / "python-runtime"
    identity_root = export_root / "runtime-identity"
    manifest_path = export_root / "runtime-manifest.json"
    manifest_sha256_path = export_root / "runtime-manifest.sha256"
    python_executable = Path(sys.executable).resolve()
    stdlib_root = Path(sysconfig.get_path("stdlib")).resolve()
    dependency_lock_path = runtime_root / "uv.lock"
    installed_distributions_path = (
        python_root / "installed-distributions.txt"
    )
    executing_module = Path(__file__).resolve()
    if (
        not export_root.is_absolute()
        or executing_module.parent.parent.parent != runtime_root
        or not python_executable.is_relative_to(python_root)
        or not stdlib_root.is_relative_to(python_root)
    ):
        raise GateBPackageErrorV3("runtime_execution_outside_fixed_export")
    manifest_bytes = _read_path_nofollow_v3(manifest_path)
    manifest_sha256_bytes = _read_path_nofollow_v3(
        manifest_sha256_path,
        maximum_bytes=65,
    )
    expected_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256_bytes != (expected_manifest_sha256 + "\n").encode("ascii"):
        raise GateBPackageErrorV3("runtime_manifest_content_hash_mismatch")
    try:
        manifest_payload = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateBPackageErrorV3("runtime_manifest_invalid") from exc
    if (
        type(manifest_payload) is not dict
        or _canonical_json_bytes(manifest_payload) != manifest_bytes
    ):
        raise GateBPackageErrorV3("runtime_manifest_invalid")
    runtime_manifest = _validate_model_from_json_mapping_v3(
        GateBRuntimeManifestV3,
        manifest_payload,
        error="runtime_manifest_invalid",
    )
    current_python_version = ".".join(
        str(item) for item in sys.version_info[:3]
    )
    if current_python_version != runtime_manifest.python_version:
        raise GateBPackageErrorV3("runtime_python_version_identity_mismatch")
    normalized_sys_path = tuple(
        str(Path(item or os.getcwd()).resolve()) for item in sys.path
    )
    if not normalized_sys_path or any(
        not Path(item).is_relative_to(export_root) for item in normalized_sys_path
    ):
        raise GateBPackageErrorV3("runtime_sys_path_outside_fixed_export")
    runtime_payloads = {
        "runtime_tree_manifest": _tree_manifest_bytes_v3(runtime_root),
        "python_executable": _read_path_nofollow_v3(python_executable),
        "stdlib_tree_manifest": _stdlib_tree_manifest_bytes_v3(stdlib_root),
        "dependency_lock": _read_path_nofollow_v3(dependency_lock_path),
        "installed_distributions": _read_path_nofollow_v3(
            installed_distributions_path
        ),
        "sys_path": _canonical_json_bytes(list(normalized_sys_path)),
    }
    _validate_runtime_payloads_v3(runtime_manifest, runtime_payloads)
    stored_payloads = {
        "runtime_tree_manifest": _read_path_nofollow_v3(
            identity_root / "runtime-tree.json"
        ),
        "stdlib_tree_manifest": _read_path_nofollow_v3(
            identity_root / "stdlib-tree.json"
        ),
        "sys_path": _read_path_nofollow_v3(identity_root / "sys-path.json"),
    }
    if any(runtime_payloads[name] != payload for name, payload in stored_payloads.items()):
        raise GateBPackageErrorV3("runtime_identity_artifact_mismatch")
    return runtime_manifest


def _validate_model_from_json_mapping_v3(
    model: type[BaseModel],
    payload: Mapping[str, object],
    *,
    error: str,
) -> Any:
    try:
        return model.model_validate_json(_canonical_json_bytes(dict(payload)))
    except (ValidationError, ValueError, TypeError) as exc:
        raise GateBPackageErrorV3(error) from exc


def _recompute_projection_sha256s_v3(
    package: GateBValidatedPackageV3,
    source_authorities: Mapping[str, str],
    allowlist_bytes: bytes,
) -> tuple[str, ...]:
    from job_intel.product_search.gate_b_evidence_v3 import (
        ReviewedFragmentAllowlistV3,
        project_vacancy_evidence_v3,
    )

    try:
        allowlist = ReviewedFragmentAllowlistV3.model_validate(
            _decode_yaml_mapping_v3(
                allowlist_bytes,
                error="launch_reviewed_allowlist_invalid",
            )
        )
    except ValidationError as exc:
        raise GateBPackageErrorV3("launch_reviewed_allowlist_invalid") from exc
    index = _decode_json_mapping_v3(
        package.artifacts["package-index.json"],
        error="launch_package_index_invalid",
    )
    records = index.get("records")
    if not isinstance(records, list) or len(records) != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("launch_package_index_invalid")
    projection_sha256s: list[str] = []
    for ordinal, input_sha256 in enumerate(package.ordered_input_sha256s):
        reference = f"task10-inputs/{input_sha256}.json"
        input_payload = _decode_json_mapping_v3(
            package.artifacts[reference],
            error="launch_package_input_invalid",
        )
        record = _plain_mapping_v3(
            records[ordinal],
            error="launch_package_index_invalid",
        )
        if (
            input_payload.get("ordinal") != ordinal
            or record.get("ordinal") != ordinal
            or record.get("task10_input_reference") != reference
            or record.get("task10_input_sha256") != input_sha256
            or input_payload.get("selection_key") != record.get("selection_key")
            or input_payload.get("source_authority_sha256s")
            != dict(source_authorities)
        ):
            raise GateBPackageErrorV3("launch_package_row_identity_mismatch")
        source_record = _plain_mapping_v3(
            input_payload.get("source_record"),
            error="launch_package_input_invalid",
        )
        raw = _plain_mapping_v3(
            input_payload.get("raw"),
            error="launch_package_input_invalid",
        )
        try:
            projection = project_vacancy_evidence_v3(
                source_record,
                raw,
                allowlist,
            )
        except (ValidationError, ValueError) as exc:
            raise GateBPackageErrorV3("launch_projection_invalid") from exc
        references = projection.assessment_input.references
        reference_hashes = {
            "career_profile": references.profile_ref.sha256,
            "candidate_facts": references.candidate_facts_ref.sha256,
            "semantic_contract": references.semantic_contract_ref.sha256,
            "search_contract": references.search_contract_ref.sha256,
            "product_sot": references.policy_ref.sha256,
        }
        if any(
            source_authorities.get(name) != sha256
            for name, sha256 in reference_hashes.items()
        ):
            raise GateBPackageErrorV3("launch_projection_authority_mismatch")
        projection_sha256s.append(
            hashlib.sha256(
                _canonical_json_bytes(projection.provider_payload())
            ).hexdigest()
        )
    result = tuple(projection_sha256s)
    if len(set(result)) != _ORDERED_CALL_CAP:
        raise GateBPackageErrorV3("launch_projection_identity_not_unique")
    return result


def recompute_launch_identity_v3(
    package: GateBValidatedPackageV3,
    owner_checkpoint_payload: Mapping[str, object],
    launch_receipt_payload: Mapping[str, object],
) -> GateBLaunchBindingV3:
    """Recompute every launch identity before any provider can be constructed."""
    _validate_package_in_memory_v3(package)
    runtime_manifest = _load_current_runtime_identity_v3()
    current_source_bytes = load_gate_b_source_bytes_v3()
    current_package = validate_gate_b_package_pure_v3(current_source_bytes)
    if (
        current_package.package_sha256 != package.package_sha256
        or current_package.manifest_bytes != package.manifest_bytes
        or dict(current_package.artifact_sha256s) != dict(package.artifact_sha256s)
    ):
        raise GateBPackageErrorV3("launch_package_identity_drift")
    index = _decode_json_mapping_v3(
        package.artifacts["package-index.json"],
        error="launch_package_index_invalid",
    )
    source_authorities = _plain_mapping_v3(
        index.get("source_authority_sha256s"),
        error="launch_package_authority_invalid",
    )
    derived_authorities = _derive_launch_authority_sha256s_v3(
        current_source_bytes
    )
    expected_authorities = {
        **derived_authorities,
        "corpus_manifest": hashlib.sha256(
            _source_payload_v3(current_source_bytes, "corpus_manifest")
        ).hexdigest(),
        "gate_a_manifest": hashlib.sha256(
            _source_payload_v3(current_source_bytes, "gate_a_manifest")
        ).hexdigest(),
        "reviewed_fragment_allowlist": hashlib.sha256(
            _source_payload_v3(current_source_bytes, "reviewed_fragment_allowlist")
        ).hexdigest(),
    }
    if source_authorities != expected_authorities:
        raise GateBPackageErrorV3("launch_package_authority_drift")
    projection_sha256s = _recompute_projection_sha256s_v3(
        package,
        expected_authorities,
        _source_payload_v3(current_source_bytes, "reviewed_fragment_allowlist"),
    )
    task10_policy = _decode_yaml_mapping_v3(
        _source_payload_v3(current_source_bytes, "task10_policy"),
        error="task10_policy_invalid",
    )
    model_id = task10_policy.get("model_id")
    pricing = gate_b_governed_pricing_schedule_v3()
    benchmark_policy = load_gate_b_benchmark_policy_v3(
        _decode_yaml_mapping_v3(
            _source_payload_v3(current_source_bytes, "benchmark_policy"),
            error="benchmark_policy_invalid",
        )
    )
    observed = GateBLaunchBindingV3(
        schema_version="3.0.0",
        run_id=f"gate-b-at-most-once-{package.package_sha256[:16]}",
        candidate_commit=runtime_manifest.candidate_commit,
        runtime_manifest_sha256=runtime_manifest.canonical_sha256,
        package_manifest_sha256=package.package_sha256,
        ordered_input_sha256s=package.ordered_input_sha256s,
        ordered_projection_sha256s=projection_sha256s,
        source_authority_sha256s=expected_authorities,
        model_id=model_id,
        maximum_output_tokens=pricing.max_output_tokens,
        ordered_call_cap=benchmark_policy.ordered_call_cap,
        per_call_maximum_usd=benchmark_policy.per_call_maximum_usd,
        aggregate_maximum_usd=benchmark_policy.aggregate_maximum_usd,
    )
    checkpoint = _validate_model_from_json_mapping_v3(
        GateBOwnerCheckpointManifestV3,
        owner_checkpoint_payload,
        error="owner_checkpoint_invalid",
    )
    if checkpoint.launch_identity != observed:
        raise GateBPackageErrorV3("owner_approval_identity_mismatch")
    receipt = _validate_model_from_json_mapping_v3(
        GateBOneTimeLaunchReceiptV3,
        launch_receipt_payload,
        error="launch_receipt_invalid",
    )
    receipt_identity = {
        "run_id": observed.run_id,
        "checkpoint_manifest_sha256": checkpoint.canonical_sha256,
        "launch_identity_sha256": observed.canonical_sha256,
        "candidate_commit": observed.candidate_commit,
        "runtime_manifest_sha256": observed.runtime_manifest_sha256,
        "package_manifest_sha256": observed.package_manifest_sha256,
        "ordered_call_cap": observed.ordered_call_cap,
        "per_call_maximum_usd": observed.per_call_maximum_usd,
        "aggregate_maximum_usd": observed.aggregate_maximum_usd,
    }
    if any(getattr(receipt, name) != value for name, value in receipt_identity.items()):
        raise GateBPackageErrorV3("launch_receipt_identity_mismatch")
    if receipt.issued_at < checkpoint.approved_at:
        raise GateBPackageErrorV3("launch_receipt_precedes_owner_approval")
    return observed


def _read_owned_launch_file_v3(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int | None = None,
    expected_mode: int,
    maximum_bytes: int = 16_000_000,
) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        raise GateBPackageErrorV3("launch_file_unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or (expected_gid is not None and metadata.st_gid != expected_gid)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_size > maximum_bytes
        ):
            raise GateBPackageErrorV3("launch_file_authority_invalid")
        return _read_all(descriptor)
    finally:
        os.close(descriptor)


def _validate_canonical_model_bytes_v3(
    model: type[BaseModel],
    payload: bytes,
    *,
    error: str,
) -> Any:
    try:
        value = model.model_validate_json(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise GateBPackageErrorV3(error) from exc
    if _canonical_json_bytes(value.model_dump(mode="json")) != payload:
        raise GateBPackageErrorV3(error)
    return value


def _one_pending_receipt_path_v3(pending_root: Path) -> Path:
    try:
        root_metadata = pending_root.lstat()
        entries = tuple(os.scandir(pending_root))
    except OSError as exc:
        raise GateBPackageErrorV3("pending_receipt_root_invalid") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or pending_root.is_symlink():
        raise GateBPackageErrorV3("pending_receipt_root_invalid")
    candidates: list[Path] = []
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            raise GateBPackageErrorV3("pending_receipt_root_unknown_entry")
        if re.fullmatch(r"gate-b-at-most-once-[0-9a-f]{16}", entry.name) is None:
            raise GateBPackageErrorV3("pending_receipt_run_id_invalid")
        candidate = Path(entry.path) / _GATE_B_PENDING_RECEIPT_FILENAME_V3
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        candidates.append(candidate)
    if len(candidates) != 1:
        raise GateBPackageErrorV3("pending_receipt_count_invalid")
    return candidates[0]


def _ensure_owned_directory_v3(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    try:
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise GateBPackageErrorV3("launch_directory_unsafe")
        os.chown(path, uid, gid)
        os.chmod(path, mode)
        metadata = path.lstat()
    except OSError as exc:
        raise GateBPackageErrorV3("launch_directory_unsafe") from exc
    if (
        metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise GateBPackageErrorV3("launch_directory_authority_invalid")


def _publish_owned_launch_file_v3(
    directory: Path,
    filename: str,
    payload: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> Path:
    directory_descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    temporary = f".{filename}.{os.getpid()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                filename,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise GateBPackageErrorV3("consumed_receipt_already_exists") from exc
        os.fsync(directory_descriptor)
        os.unlink(temporary, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise GateBPackageErrorV3("consumed_receipt_publish_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        os.close(directory_descriptor)
    return directory / filename


def consume_gate_b_launch_receipt_v3(
    *,
    pending_root: Path = _GATE_B_PENDING_RECEIPT_ROOT_V3,
    consumed_root: Path = _GATE_B_CONSUMED_RECEIPT_ROOT_V3,
    package_parent: Path = _GATE_B_PACKAGE_PARENT_V3,
    runtime_export_root: Path = _GATE_B_RUNTIME_EXPORT_ROOT_V3,
    now: datetime | None = None,
    expected_root_uid: int = 0,
    expected_root_gid: int = 0,
    expected_hermes_gid: int | None = None,
) -> Path:
    """Consume one root-owned launch receipt before the hermes process starts."""
    if os.geteuid() != expected_root_uid:
        raise GateBPackageErrorV3("root_launcher_required")
    if expected_hermes_gid is None:
        import grp

        expected_hermes_gid = grp.getgrnam("hermes").gr_gid
    current_time = now or datetime.now(timezone.utc)
    current_time = _require_utc(current_time)
    pending_path = _one_pending_receipt_path_v3(Path(pending_root))
    pending_directory = pending_path.parent
    directory_metadata = pending_directory.lstat()
    if (
        pending_directory.is_symlink()
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != expected_root_uid
        or directory_metadata.st_gid != expected_root_gid
    ):
        raise GateBPackageErrorV3("pending_receipt_directory_invalid")
    receipt_bytes = _read_owned_launch_file_v3(
        pending_path,
        expected_uid=expected_root_uid,
        expected_gid=expected_root_gid,
        expected_mode=0o400,
    )
    checkpoint_path = pending_directory / _GATE_B_OWNER_CHECKPOINT_FILENAME_V3
    recovery_key_path = (
        pending_directory / _GATE_B_OWNER_RECOVERY_KEY_FILENAME_V3
    )
    checkpoint_bytes = _read_owned_launch_file_v3(
        checkpoint_path,
        expected_uid=expected_root_uid,
        expected_gid=expected_root_gid,
        expected_mode=0o400,
    )
    recovery_public_key = _read_owned_launch_file_v3(
        recovery_key_path,
        expected_uid=expected_root_uid,
        expected_gid=expected_root_gid,
        expected_mode=0o400,
        maximum_bytes=32,
    )
    if len(recovery_public_key) != 32:
        raise GateBPackageErrorV3("owner_recovery_public_key_invalid")
    try:
        Ed25519PublicKey.from_public_bytes(recovery_public_key)
    except ValueError as exc:
        raise GateBPackageErrorV3("owner_recovery_public_key_invalid") from exc
    receipt = _validate_canonical_model_bytes_v3(
        GateBOneTimeLaunchReceiptV3,
        receipt_bytes,
        error="launch_receipt_invalid",
    )
    checkpoint = _validate_canonical_model_bytes_v3(
        GateBOwnerCheckpointManifestV3,
        checkpoint_bytes,
        error="owner_checkpoint_invalid",
    )
    if pending_directory.name != receipt.run_id:
        raise GateBPackageErrorV3("launch_receipt_run_path_mismatch")
    if not receipt.issued_at <= current_time <= receipt.expires_at:
        raise GateBPackageErrorV3("launch_receipt_expired_or_not_yet_valid")
    if receipt.expires_at - receipt.issued_at != timedelta(minutes=30):
        raise GateBPackageErrorV3("launch_receipt_window_not_exact")
    if receipt.run_id != (
        f"gate-b-at-most-once-{receipt.package_manifest_sha256[:16]}"
    ):
        raise GateBPackageErrorV3("launch_receipt_run_id_invalid")
    package_manifest_path = (
        Path(package_parent)
        / receipt.package_manifest_sha256
        / "package-manifest.json"
    )
    package_manifest_bytes = _read_path_nofollow_v3(package_manifest_path)
    package_manifest = _validate_canonical_model_bytes_v3(
        GateBPackageManifestV3,
        package_manifest_bytes,
        error="package_manifest_invalid",
    )
    if (
        hashlib.sha256(package_manifest_bytes).hexdigest()
        != receipt.package_manifest_sha256
        or package_manifest.canonical_sha256 != receipt.package_manifest_sha256
    ):
        raise GateBPackageErrorV3("launch_package_manifest_mismatch")
    runtime_manifest_bytes = _read_path_nofollow_v3(
        Path(runtime_export_root) / "runtime-manifest.json"
    )
    runtime_manifest = _validate_canonical_model_bytes_v3(
        GateBRuntimeManifestV3,
        runtime_manifest_bytes,
        error="runtime_manifest_invalid",
    )
    if (
        runtime_manifest.canonical_sha256 != receipt.runtime_manifest_sha256
        or runtime_manifest.candidate_commit != receipt.candidate_commit
    ):
        raise GateBPackageErrorV3("launch_runtime_manifest_mismatch")
    launch = checkpoint.launch_identity
    if (
        checkpoint.canonical_sha256 != receipt.checkpoint_manifest_sha256
        or launch.canonical_sha256 != receipt.launch_identity_sha256
        or launch.run_id != receipt.run_id
        or launch.candidate_commit != receipt.candidate_commit
        or launch.runtime_manifest_sha256 != receipt.runtime_manifest_sha256
        or launch.package_manifest_sha256 != receipt.package_manifest_sha256
        or launch.ordered_input_sha256s
        != package_manifest.ordered_input_sha256s
        or launch.ordered_call_cap != receipt.ordered_call_cap
        or launch.per_call_maximum_usd != receipt.per_call_maximum_usd
        or launch.aggregate_maximum_usd != receipt.aggregate_maximum_usd
    ):
        raise GateBPackageErrorV3("launch_receipt_identity_mismatch")
    destination_directory = Path(consumed_root) / receipt.run_id
    _ensure_owned_directory_v3(
        Path(consumed_root),
        uid=expected_root_uid,
        gid=expected_hermes_gid,
        mode=0o750,
    )
    _ensure_owned_directory_v3(
        destination_directory,
        uid=expected_root_uid,
        gid=expected_hermes_gid,
        mode=0o750,
    )
    consumed_path = _publish_owned_launch_file_v3(
        destination_directory,
        _GATE_B_CONSUMED_RECEIPT_FILENAME_V3,
        receipt_bytes,
        uid=expected_root_uid,
        gid=expected_hermes_gid,
        mode=0o440,
    )
    _publish_owned_launch_file_v3(
        destination_directory,
        _GATE_B_OWNER_CHECKPOINT_FILENAME_V3,
        checkpoint_bytes,
        uid=expected_root_uid,
        gid=expected_hermes_gid,
        mode=0o440,
    )
    _publish_owned_launch_file_v3(
        destination_directory,
        _GATE_B_OWNER_RECOVERY_KEY_FILENAME_V3,
        recovery_public_key,
        uid=expected_root_uid,
        gid=expected_hermes_gid,
        mode=0o440,
    )
    pending_descriptor = os.open(
        pending_directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.unlink(_GATE_B_PENDING_RECEIPT_FILENAME_V3, dir_fd=pending_descriptor)
        os.fsync(pending_descriptor)
    finally:
        os.close(pending_descriptor)
    return consumed_path


def _snapshot_sha256_v3(snapshot: tuple[tuple[object, ...], ...]) -> str:
    return canonical_json_sha256([list(item) for item in snapshot])


def _snapshot_gate_a_sources_v3(
    package: GateBValidatedPackageV3,
    operations: list[GateBObservedOperationV3],
    *,
    phase: str,
) -> tuple[tuple[object, ...], ...]:
    snapshot: list[tuple[object, ...]] = []
    for reference, expected_sha256 in sorted(package.source_file_sha256s.items()):
        path = _GATE_A_SOURCE_ROOT_V3 / reference
        payload = _read_path_nofollow_v3(path)
        observed_sha256 = hashlib.sha256(payload).hexdigest()
        if observed_sha256 != expected_sha256:
            raise GateBPackageErrorV3("gate_a_source_changed")
        snapshot.append((reference, observed_sha256, len(payload)))
        operations.append(
            GateBObservedOperationV3(
                kind="source_snapshot",
                path=str(path),
                detail=phase,
            )
        )
    return tuple(snapshot)


def _snapshot_protected_paths_v3(
    operations: list[GateBObservedOperationV3],
    *,
    phase: str,
) -> tuple[tuple[object, ...], ...]:
    snapshot: list[tuple[object, ...]] = []
    for path in _GATE_B_PROTECTED_PATHS_V3:
        credential_metadata_only = path.name in {".env", "job-intel.env"}
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            snapshot.append((str(path), "missing"))
            operations.append(
                GateBObservedOperationV3(
                    kind="protected_snapshot",
                    path=str(path),
                    detail=f"{phase}:missing",
                )
            )
            continue
        if stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            content_sha256 = None
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            content_sha256 = (
                None
                if credential_metadata_only
                else hashlib.sha256(_read_path_nofollow_v3(path)).hexdigest()
            )
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            content_sha256 = None
        else:
            kind = "other"
            content_sha256 = None
        snapshot.append(
            (
                str(path),
                kind,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_size,
                metadata.st_mtime_ns,
                content_sha256,
            )
        )
        operations.append(
            GateBObservedOperationV3(
                kind="protected_snapshot",
                path=str(path),
                detail=(
                    f"{phase}:{kind}:metadata_only"
                    if credential_metadata_only
                    else f"{phase}:{kind}"
                ),
            )
        )
    return tuple(snapshot)


def _open_materialization_parent_v3() -> int:
    try:
        descriptor = _open_directory_nofollow_v3(_GATE_B_PACKAGE_PARENT_V3)
    except (OSError, GateBPackageErrorV3) as exc:
        raise GateBPackageErrorV3("package_parent_unavailable") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise GateBPackageErrorV3("package_parent_metadata_invalid")
    return descriptor


def _open_child_directory_optional_v3(parent_descriptor: int, name: str) -> int | None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GateBPackageErrorV3("package_root_open_failed") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink < 2
        or stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE
    ):
        os.close(descriptor)
        raise GateBPackageErrorV3("package_root_metadata_invalid")
    return descriptor


def _write_package_artifact_v3(
    directory_descriptor: int,
    filename: str,
    payload: bytes,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
        ):
            raise GateBPackageErrorV3("package_artifact_metadata_invalid")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except OSError as exc:
        raise GateBPackageErrorV3("package_artifact_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _rename_directory_noreplace_v3(
    parent_descriptor: int,
    source_name: str,
    target_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise GateBPackageErrorV3("package_atomic_rename_unsupported") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(target_name),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number), target_name)
    raise GateBPackageErrorV3("package_atomic_rename_failed")


def _read_materialized_file_v3(
    directory_descriptor: int,
    filename: str,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
        ):
            raise GateBPackageErrorV3("package_artifact_metadata_invalid")
        return _read_all(descriptor)
    except OSError as exc:
        raise GateBPackageErrorV3("package_artifact_read_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_materialized_root_v3(
    root_descriptor: int,
    package: GateBValidatedPackageV3,
    operations: list[GateBObservedOperationV3],
) -> None:
    expected_root_names = {
        "package-index.json",
        "package-manifest.json",
        "task10-inputs",
    }
    if set(os.listdir(root_descriptor)) != expected_root_names:
        raise GateBPackageErrorV3("package_existing_unknown_content")
    input_descriptor = _open_child_directory_optional_v3(
        root_descriptor,
        "task10-inputs",
    )
    if input_descriptor is None:
        raise GateBPackageErrorV3("package_existing_unknown_content")
    try:
        expected_inputs = {
            f"{sha256}.json" for sha256 in package.ordered_input_sha256s
        }
        if set(os.listdir(input_descriptor)) != expected_inputs:
            raise GateBPackageErrorV3("package_existing_unknown_content")
        for reference, expected_payload in sorted(package.artifacts.items()):
            if "/" in reference:
                directory_descriptor = input_descriptor
                filename = reference.split("/", 1)[1]
            else:
                directory_descriptor = root_descriptor
                filename = reference
            observed_payload = _read_materialized_file_v3(
                directory_descriptor,
                filename,
            )
            if (
                observed_payload != expected_payload
                or hashlib.sha256(observed_payload).hexdigest()
                != package.artifact_sha256s[reference]
            ):
                raise GateBPackageErrorV3("package_artifact_rehash_mismatch")
            operations.append(
                GateBObservedOperationV3(
                    kind="artifact_rehash",
                    path=str(_GATE_B_PACKAGE_PARENT_V3 / package.package_sha256 / reference),
                    detail=package.artifact_sha256s[reference],
                )
            )
    finally:
        os.close(input_descriptor)


def materialize_gate_b_package_v3(
    package: GateBValidatedPackageV3,
) -> GateBMaterializationReceiptV3:
    """Atomically publish a pure package to its one fixed SHA-derived root."""
    _validate_package_in_memory_v3(package)
    operations: list[GateBObservedOperationV3] = []
    source_before = _snapshot_gate_a_sources_v3(
        package,
        operations,
        phase="before",
    )
    protected_before = _snapshot_protected_paths_v3(operations, phase="before")
    parent_descriptor = _open_materialization_parent_v3()
    created = False
    root_descriptor = -1
    staging_descriptor = -1
    try:
        root_descriptor = _open_child_directory_optional_v3(
            parent_descriptor,
            package.package_sha256,
        ) or -1
        if root_descriptor < 0:
            staging_name = f".{package.package_sha256}.materializing"
            if _open_child_directory_optional_v3(parent_descriptor, staging_name) is not None:
                raise GateBPackageErrorV3("package_staging_unknown_content")
            try:
                os.mkdir(
                    staging_name,
                    _PRIVATE_DIRECTORY_MODE,
                    dir_fd=parent_descriptor,
                )
                os.fsync(parent_descriptor)
            except OSError as exc:
                raise GateBPackageErrorV3("package_staging_create_failed") from exc
            operations.append(
                GateBObservedOperationV3(
                    kind="artifact_directory_create",
                    path=str(_GATE_B_PACKAGE_PARENT_V3 / staging_name),
                    detail="mode=0700",
                )
            )
            staging_descriptor = _open_child_directory_optional_v3(
                parent_descriptor,
                staging_name,
            ) or -1
            if staging_descriptor < 0:
                raise GateBPackageErrorV3("package_staging_open_failed")
            try:
                os.mkdir(
                    "task10-inputs",
                    _PRIVATE_DIRECTORY_MODE,
                    dir_fd=staging_descriptor,
                )
                os.fsync(staging_descriptor)
            except OSError as exc:
                raise GateBPackageErrorV3("package_input_directory_create_failed") from exc
            input_descriptor = _open_child_directory_optional_v3(
                staging_descriptor,
                "task10-inputs",
            )
            if input_descriptor is None:
                raise GateBPackageErrorV3("package_input_directory_open_failed")
            try:
                for reference, payload in sorted(package.artifacts.items()):
                    if "/" in reference:
                        directory_descriptor = input_descriptor
                        filename = reference.split("/", 1)[1]
                    else:
                        directory_descriptor = staging_descriptor
                        filename = reference
                    _write_package_artifact_v3(
                        directory_descriptor,
                        filename,
                        payload,
                    )
                    operations.append(
                        GateBObservedOperationV3(
                            kind="artifact_write",
                            path=str(
                                _GATE_B_PACKAGE_PARENT_V3 / staging_name / reference
                            ),
                            detail=package.artifact_sha256s[reference],
                        )
                    )
                os.fsync(input_descriptor)
                os.fsync(staging_descriptor)
            finally:
                os.close(input_descriptor)
            os.close(staging_descriptor)
            staging_descriptor = -1
            _rename_directory_noreplace_v3(
                parent_descriptor,
                staging_name,
                package.package_sha256,
            )
            os.fsync(parent_descriptor)
            operations.append(
                GateBObservedOperationV3(
                    kind="artifact_atomic_publish",
                    path=str(
                        _GATE_B_PACKAGE_PARENT_V3 / package.package_sha256
                    ),
                    detail=package.package_sha256,
                )
            )
            created = True
            root_descriptor = _open_child_directory_optional_v3(
                parent_descriptor,
                package.package_sha256,
            ) or -1
            if root_descriptor < 0:
                raise GateBPackageErrorV3("package_published_root_missing")
        _verify_materialized_root_v3(root_descriptor, package, operations)
    finally:
        if staging_descriptor >= 0:
            os.close(staging_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)
        os.close(parent_descriptor)

    source_after = _snapshot_gate_a_sources_v3(
        package,
        operations,
        phase="after",
    )
    protected_after = _snapshot_protected_paths_v3(operations, phase="after")
    if source_before != source_after:
        raise GateBPackageErrorV3("gate_a_sources_changed_during_materialization")
    if protected_before != protected_after:
        raise GateBPackageErrorV3("protected_paths_changed_during_materialization")
    return GateBMaterializationReceiptV3(
        package_root=str(_GATE_B_PACKAGE_PARENT_V3 / package.package_sha256),
        package_sha256=package.package_sha256,
        created=created,
        artifact_sha256s=_freeze_text_mapping_v3(package.artifact_sha256s),
        source_snapshot_sha256=_snapshot_sha256_v3(source_after),
        protected_snapshot_sha256=_snapshot_sha256_v3(protected_after),
        observed_operations=tuple(operations),
    )


class GateBRunnerErrorV3(ValueError):
    """Fail-closed one-shot runner or receipt execution error."""


class GateBRunnerRowSummaryV3(_StrictFrozenModel):
    ordinal: int = Field(ge=0, lt=_ORDERED_CALL_CAP)
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    state: GateBCallStateV3
    measured_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    conservative_cost_usd: Decimal = Field(ge=Decimal("0"))


class GateBRunnerSummaryV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    run_id: str = Field(pattern=r"^gate-b-at-most-once-[0-9a-f]{16}$")
    attempted_count: int = Field(ge=0, le=_ORDERED_CALL_CAP)
    success_count: int = Field(ge=0, le=_ORDERED_CALL_CAP)
    terminal_failure_count: int = Field(ge=0, le=_ORDERED_CALL_CAP)
    terminal_unknown_count: int = Field(ge=0, le=_ORDERED_CALL_CAP)
    pending_count: int = Field(ge=0, le=_ORDERED_CALL_CAP)
    conservative_spend_usd: Decimal = Field(
        ge=Decimal("0"),
        le=_AGGREGATE_MAXIMUM_USD,
    )
    rows: tuple[GateBRunnerRowSummaryV3, ...] = Field(
        min_length=_ORDERED_CALL_CAP,
        max_length=_ORDERED_CALL_CAP,
    )

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        counts = {
            state: sum(row.state is state for row in self.rows)
            for state in GateBCallStateV3
        }
        if (
            self.success_count != counts[GateBCallStateV3.SUCCESS]
            or self.terminal_failure_count
            != counts[GateBCallStateV3.TERMINAL_FAILURE]
            or self.terminal_unknown_count
            != counts[GateBCallStateV3.TERMINAL_UNKNOWN]
            or self.pending_count != counts[GateBCallStateV3.PENDING]
            or counts[GateBCallStateV3.RESERVED]
            or counts[GateBCallStateV3.DISPATCHED]
            or self.conservative_spend_usd
            != sum(
                (row.conservative_cost_usd for row in self.rows),
                start=Decimal("0"),
            )
        ):
            raise ValueError("runner summary does not match all 48 ledger rows")
        return self


@dataclass(slots=True)
class _GateBRunnerCapabilityBridgeV3:
    ledger: GateBLedgerV3
    launch_identity: GateBLaunchBindingV3
    nonce: str
    current_ordinal: int | None = None
    current_projection_sha256: str | None = None
    reservation_id: str | None = None
    reconciled: tuple[str, Decimal] | None = None

    def begin(self, ordinal: int, projection_sha256: str) -> None:
        if self.current_ordinal is not None:
            raise GateBRunnerErrorV3("runner_row_already_active")
        if self.ledger.state(ordinal) is not GateBCallStateV3.PENDING:
            raise GateBRunnerErrorV3("runner_row_not_pending")
        self.current_ordinal = ordinal
        self.current_projection_sha256 = projection_sha256
        self.reservation_id = None
        self.reconciled = None

    def reserve(self, input_hash: str, amount: Decimal) -> str:
        ordinal = self._active_ordinal()
        if (
            self.reservation_id is not None
            or input_hash != self.current_projection_sha256
            or amount != _PER_CALL_MAXIMUM_USD
            or self.ledger.conservative_spend_usd + amount
            > self.launch_identity.aggregate_maximum_usd
        ):
            raise GateBRunnerErrorV3("runner_reservation_invalid")
        self.ledger.reserve(ordinal)
        self.reservation_id = hashlib.sha256(
            _canonical_json_bytes(
                {
                    "run_id": self.launch_identity.run_id,
                    "nonce": self.nonce,
                    "ordinal": ordinal,
                    "projection_sha256": input_hash,
                }
            )
        ).hexdigest()
        return self.reservation_id

    def mark_dispatching(self, reservation_id: str) -> None:
        ordinal = self._active_ordinal()
        if (
            self.reservation_id is None
            or reservation_id != self.reservation_id
            or self.reconciled is not None
        ):
            raise GateBRunnerErrorV3("runner_dispatch_invalid")
        self.ledger.mark_dispatched(ordinal, dispatch_id=reservation_id)

    def reconcile(
        self,
        reservation_id: str,
        actual_cost: Decimal,
        outcome: str,
    ) -> None:
        ordinal = self._active_ordinal()
        if (
            reservation_id != self.reservation_id
            or self.ledger.state(ordinal) is not GateBCallStateV3.DISPATCHED
            or self.reconciled is not None
            or outcome not in {
                "success",
                "terminal_failure",
                "terminal_unknown",
            }
            or not isinstance(actual_cost, Decimal)
            or not actual_cost.is_finite()
            or actual_cost < 0
            or actual_cost > _PER_CALL_MAXIMUM_USD
            or (
                outcome == "terminal_unknown"
                and actual_cost != _PER_CALL_MAXIMUM_USD
            )
        ):
            raise GateBRunnerErrorV3("runner_reconciliation_invalid")
        self.reconciled = outcome, actual_cost

    def verify_and_finish(self, terminal: str, cost: Decimal) -> tuple[int, str]:
        ordinal = self._active_ordinal()
        if (
            self.reservation_id is None
            or self.reconciled != (terminal, cost)
            or self.ledger.state(ordinal) is not GateBCallStateV3.DISPATCHED
        ):
            raise GateBRunnerErrorV3("runner_reconciliation_missing")
        reservation_id = self.reservation_id
        self.current_ordinal = None
        self.current_projection_sha256 = None
        self.reservation_id = None
        self.reconciled = None
        return ordinal, reservation_id

    def _active_ordinal(self) -> int:
        if self.current_ordinal is None:
            raise GateBRunnerErrorV3("runner_row_not_active")
        return self.current_ordinal


def _build_gate_b_provider_v3(
    *,
    recordings_root: Path,
    capability: object,
    launch_identity: GateBLaunchBindingV3,
) -> object:
    """Construct the one live Semantic provider owned by this runner."""
    policy = load_evidence_synthesis_policy()
    if policy.model_id != launch_identity.model_id:
        raise GateBRunnerErrorV3("runner_provider_policy_mismatch")
    provider = build_live_llm_provider(
        store_dir=recordings_root,
        model_id=policy.model_id,
        prompt_version=policy.semantic_prompt_version,
    )
    if capability is None:
        raise GateBRunnerErrorV3("runner_capability_missing")
    return provider


def _runner_response_request_v3(
    *,
    projection: object,
    allowlist: object,
    input_sha256: str,
    launch_identity: GateBLaunchBindingV3,
) -> GovernedStructuredRequest:
    from job_intel.product_search.gate_b_evidence_v3 import (
        validate_provider_payload_v3,
    )

    policy: EvidenceSynthesisPolicyV1 = load_evidence_synthesis_policy()
    user_payload = projection.provider_payload()

    def response_validator(payload: object) -> str | None:
        failure = validate_provider_payload_v3(
            payload,
            synthesis_input=projection,
            reviewed_allowlist=allowlist,
        )
        return None if failure is None else failure.value

    return GovernedStructuredRequest(
        input_hash=input_sha256,
        system_prompt=build_task10_prompt_v2(policy),
        user_payload=user_payload,
        schema_name="product_search_gate_b_at_most_once_v3",
        response_schema=ProviderEvidencePayloadV2.model_json_schema(),
        governance_identity={
            "run_identity_sha256": launch_identity.canonical_sha256,
            "launch_identity_sha256": launch_identity.canonical_sha256,
            "prompt_version": TASK10_PROMPT_VERSION_V2,
        },
        forbidden_markers=_PRIVATE_MARKERS,
        response_validator=response_validator,
    )


def _validate_receipt_time_v3(
    receipt: GateBOneTimeLaunchReceiptV3,
    *,
    now: datetime,
) -> None:
    current = _require_utc(now)
    if (
        receipt.expires_at - receipt.issued_at != timedelta(minutes=30)
        or not receipt.issued_at <= current <= receipt.expires_at
    ):
        raise GateBRunnerErrorV3("launch_receipt_expired_or_not_yet_valid")


def _runner_summary_v3(
    ledger: GateBLedgerV3,
    *,
    attempted_count: int,
) -> GateBRunnerSummaryV3:
    rows = ledger.rows()
    summaries = tuple(
        GateBRunnerRowSummaryV3(
            ordinal=row.ordinal,
            input_sha256=row.input_sha256,
            state=row.state,
            measured_cost_usd=row.measured_cost_usd,
            conservative_cost_usd=row.conservative_cost_usd,
        )
        for row in rows
    )
    return GateBRunnerSummaryV3(
        schema_version="3.0.0",
        run_id=ledger.launch_identity.run_id,
        attempted_count=attempted_count,
        success_count=sum(
            row.state is GateBCallStateV3.SUCCESS for row in rows
        ),
        terminal_failure_count=sum(
            row.state is GateBCallStateV3.TERMINAL_FAILURE for row in rows
        ),
        terminal_unknown_count=sum(
            row.state is GateBCallStateV3.TERMINAL_UNKNOWN for row in rows
        ),
        pending_count=sum(
            row.state is GateBCallStateV3.PENDING for row in rows
        ),
        conservative_spend_usd=ledger.conservative_spend_usd,
        rows=summaries,
    )


def run_gate_b_at_most_once_v3(
    *,
    package: GateBValidatedPackageV3,
    owner_checkpoint_payload: Mapping[str, object],
    launch_receipt_payload: Mapping[str, object],
    ledger_root: Path,
    recordings_root: Path,
    owner_recovery_public_key: bytes,
    now: datetime | None = None,
) -> GateBRunnerSummaryV3:
    """Run pending rows sequentially; no post-dispatch outcome is retried."""
    checkpoint_payload = dict(owner_checkpoint_payload)
    receipt_payload = dict(launch_receipt_payload)
    current_time = now or datetime.now(timezone.utc)
    try:
        receipt = GateBOneTimeLaunchReceiptV3.model_validate_json(
            _canonical_json_bytes(receipt_payload)
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise GateBRunnerErrorV3("launch_receipt_invalid") from exc
    _validate_receipt_time_v3(receipt, now=current_time)
    launch_identity = recompute_launch_identity_v3(
        package,
        checkpoint_payload,
        receipt_payload,
    )
    if (
        receipt.run_id != launch_identity.run_id
        or receipt.launch_identity_sha256 != launch_identity.canonical_sha256
        or receipt.package_manifest_sha256 != package.package_sha256
    ):
        raise GateBRunnerErrorV3("runner_launch_identity_mismatch")
    manifest = GateBPackageManifestV3.model_validate_json(package.manifest_bytes)
    ledger_identity = GateBLaunchIdentityV3(
        schema_version="3.0.0",
        run_id=launch_identity.run_id,
        issued_at=receipt.issued_at,
        package_manifest_sha256=package.package_sha256,
    )
    source_bytes = load_gate_b_source_bytes_v3()
    from job_intel.product_search.gate_b_evidence_v3 import (
        ReviewedFragmentAllowlistV3,
        project_vacancy_evidence_v3,
    )

    try:
        allowlist = ReviewedFragmentAllowlistV3.model_validate(
            yaml.safe_load(
                _source_payload_v3(source_bytes, "reviewed_fragment_allowlist")
            )
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise GateBRunnerErrorV3("runner_reviewed_allowlist_invalid") from exc
    ledger_path = Path(ledger_root)
    recordings_path = Path(recordings_root)
    if (
        not ledger_path.is_absolute()
        or not recordings_path.is_absolute()
        or ledger_path == recordings_path
    ):
        raise GateBRunnerErrorV3("runner_output_path_invalid")
    with GateBLedgerV3(
        ledger_path,
        ledger_identity,
        manifest,
        owner_recovery_public_key=owner_recovery_public_key,
    ) as ledger:
        if any(
            row.state in {
                GateBCallStateV3.RESERVED,
                GateBCallStateV3.DISPATCHED,
            }
            for row in ledger.rows()
        ):
            raise GateBRunnerErrorV3("owner_recovery_required")
        bridge = _GateBRunnerCapabilityBridgeV3(
            ledger=ledger,
            launch_identity=launch_identity,
            nonce=receipt.nonce,
        )
        pricing = gate_b_governed_pricing_schedule_v3()
        capability = _issue_structured_call_capability(
            run_identity_sha256=launch_identity.canonical_sha256,
            pricing=pricing,
            exact_call_cap=launch_identity.ordered_call_cap,
            exact_spend_cap_usd=launch_identity.aggregate_maximum_usd,
            metadata_seal_key=hashlib.sha256(
                b"gate-b-at-most-once-v3\0" + bytes.fromhex(receipt.nonce)
            ).digest(),
            reserve=bridge.reserve,
            mark_dispatching=bridge.mark_dispatching,
            reconcile=bridge.reconcile,
        )
        provider = _build_gate_b_provider_v3(
            recordings_root=recordings_path,
            capability=capability,
            launch_identity=launch_identity,
        )
        attempted_count = 0
        for ordinal, package_input_sha256 in enumerate(
            package.ordered_input_sha256s
        ):
            observed_identity = recompute_launch_identity_v3(
                package,
                checkpoint_payload,
                receipt_payload,
            )
            if observed_identity != launch_identity:
                raise GateBRunnerErrorV3("runner_launch_identity_drift")
            state = ledger.state(ordinal)
            if state in _TERMINAL_STATES:
                continue
            if state is not GateBCallStateV3.PENDING:
                raise GateBRunnerErrorV3("owner_recovery_required")
            input_reference = f"task10-inputs/{package_input_sha256}.json"
            input_payload = _decode_json_mapping_v3(
                package.artifacts[input_reference],
                error="runner_package_input_invalid",
            )
            if (
                input_payload.get("ordinal") != ordinal
                or hashlib.sha256(package.artifacts[input_reference]).hexdigest()
                != package_input_sha256
            ):
                raise GateBRunnerErrorV3("runner_package_input_identity_mismatch")
            projection = project_vacancy_evidence_v3(
                _plain_mapping_v3(
                    input_payload.get("source_record"),
                    error="runner_source_record_invalid",
                ),
                _plain_mapping_v3(
                    input_payload.get("raw"),
                    error="runner_raw_artifact_invalid",
                ),
                allowlist,
            )
            provider_payload = projection.provider_payload()
            projection_sha256 = hashlib.sha256(
                _canonical_json_bytes(provider_payload)
            ).hexdigest()
            if projection_sha256 != launch_identity.ordered_projection_sha256s[
                ordinal
            ]:
                raise GateBRunnerErrorV3("runner_projection_identity_mismatch")
            bridge.begin(ordinal, projection_sha256)
            request = _runner_response_request_v3(
                projection=projection,
                allowlist=allowlist,
                input_sha256=projection_sha256,
                launch_identity=launch_identity,
            )
            provider_error: LLMProviderError | None = None
            result: object | None = None
            try:
                result = provider.governed_structured_call(
                    request=request,
                    capability=capability,
                )
            except LLMProviderError as exc:
                provider_error = exc
            try:
                outcome = post_dispatch_outcome_v3(provider)
            except (ValueError, AttributeError) as exc:
                raise GateBRunnerErrorV3(
                    "runner_provider_outcome_unsealed"
                ) from exc
            if outcome.terminal == "success":
                if provider_error is not None or not isinstance(
                    result, GovernedStructuredResult
                ):
                    raise GateBRunnerErrorV3("runner_provider_outcome_mismatch")
                try:
                    raw_payload = json.loads(result.raw_response_text)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise GateBRunnerErrorV3("runner_provider_payload_invalid") from exc
                validation_error = request.response_validator(raw_payload)
                if validation_error is not None:
                    raise GateBRunnerErrorV3("runner_provider_payload_invalid")
            elif outcome.terminal == "terminal_failure":
                if provider_error is None or result is not None:
                    raise GateBRunnerErrorV3("runner_provider_outcome_mismatch")
            elif outcome.terminal == "terminal_unknown":
                if provider_error is not None or not isinstance(
                    result, GovernedStructuredTerminalUnknown
                ):
                    raise GateBRunnerErrorV3("runner_provider_outcome_mismatch")
            else:
                raise GateBRunnerErrorV3("runner_provider_outcome_invalid")
            ordinal_from_bridge, dispatch_id = bridge.verify_and_finish(
                outcome.terminal,
                outcome.conservative_cost_usd,
            )
            if ordinal_from_bridge != ordinal:
                raise GateBRunnerErrorV3("runner_ordinal_mismatch")
            if outcome.terminal == "success":
                assert outcome.measured_cost_usd is not None
                ledger.record_success(
                    ordinal,
                    dispatch_id=dispatch_id,
                    provider_record_sha256=(
                        outcome.sealed_provider_record_sha256
                    ),
                    measured_cost_usd=outcome.measured_cost_usd,
                )
            elif outcome.terminal == "terminal_failure":
                assert outcome.measured_cost_usd is not None
                ledger.record_failure(
                    ordinal,
                    dispatch_id=dispatch_id,
                    provider_record_sha256=(
                        outcome.sealed_provider_record_sha256
                    ),
                    measured_cost_usd=outcome.measured_cost_usd,
                )
            else:
                ledger.record_unknown(ordinal, dispatch_id=dispatch_id)
            ledger.validate_snapshot()
            attempted_count += 1
        return _runner_summary_v3(ledger, attempted_count=attempted_count)


def _one_consumed_receipt_path_v3(consumed_root: Path) -> Path:
    try:
        entries = tuple(os.scandir(consumed_root))
    except OSError as exc:
        raise GateBRunnerErrorV3("consumed_receipt_root_invalid") from exc
    candidates: list[Path] = []
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            raise GateBRunnerErrorV3("consumed_receipt_root_unknown_entry")
        candidate = Path(entry.path) / _GATE_B_CONSUMED_RECEIPT_FILENAME_V3
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        candidates.append(candidate)
    if len(candidates) != 1:
        raise GateBRunnerErrorV3("consumed_receipt_count_invalid")
    return candidates[0]


def _write_runner_summary_create_once_v3(
    path: Path,
    summary: GateBRunnerSummaryV3,
) -> None:
    payload = _canonical_json_bytes(summary.model_dump(mode="json")) + b"\n"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
        )
    except OSError as exc:
        raise GateBRunnerErrorV3("runner_summary_create_failed") from exc
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def run_gate_b_from_consumed_receipt_v3() -> GateBRunnerSummaryV3:
    """Load the fixed consumed handoff and execute from immutable artifacts."""
    consumed_path = _one_consumed_receipt_path_v3(
        _GATE_B_CONSUMED_RECEIPT_ROOT_V3
    )
    receipt_bytes = _read_owned_launch_file_v3(
        consumed_path,
        expected_uid=0,
        expected_gid=os.getegid(),
        expected_mode=0o440,
    )
    checkpoint_bytes = _read_owned_launch_file_v3(
        consumed_path.parent / _GATE_B_OWNER_CHECKPOINT_FILENAME_V3,
        expected_uid=0,
        expected_gid=os.getegid(),
        expected_mode=0o440,
    )
    owner_recovery_public_key = _read_owned_launch_file_v3(
        consumed_path.parent / _GATE_B_OWNER_RECOVERY_KEY_FILENAME_V3,
        expected_uid=0,
        expected_gid=os.getegid(),
        expected_mode=0o440,
        maximum_bytes=32,
    )
    receipt = _validate_canonical_model_bytes_v3(
        GateBOneTimeLaunchReceiptV3,
        receipt_bytes,
        error="launch_receipt_invalid",
    )
    checkpoint = _validate_canonical_model_bytes_v3(
        GateBOwnerCheckpointManifestV3,
        checkpoint_bytes,
        error="owner_checkpoint_invalid",
    )
    source_bytes = load_gate_b_source_bytes_v3()
    package = validate_gate_b_package_pure_v3(source_bytes)
    if package.package_sha256 != receipt.package_manifest_sha256:
        raise GateBRunnerErrorV3("consumed_receipt_package_mismatch")
    package_root = _GATE_B_PACKAGE_PARENT_V3 / package.package_sha256
    package_descriptor = _open_directory_nofollow_v3(package_root)
    try:
        _verify_materialized_root_v3(package_descriptor, package, [])
    finally:
        os.close(package_descriptor)
    run_root = _GATE_B_RUNS_ROOT_V3 / receipt.run_id
    try:
        run_root.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=False)
    except FileExistsError:
        if run_root.is_symlink() or not run_root.is_dir():
            raise GateBRunnerErrorV3("runner_root_unsafe")
    except OSError as exc:
        raise GateBRunnerErrorV3("runner_root_create_failed") from exc
    summary = run_gate_b_at_most_once_v3(
        package=package,
        owner_checkpoint_payload=checkpoint.model_dump(mode="json"),
        launch_receipt_payload=receipt.model_dump(mode="json"),
        ledger_root=run_root / "ledger",
        recordings_root=run_root / "provider-recordings",
        owner_recovery_public_key=owner_recovery_public_key,
    )
    _write_runner_summary_create_once_v3(run_root / "summary.json", summary)
    return summary


def _tree_manifest_bytes_v3(root: Path) -> bytes:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise GateBPackageErrorV3("runtime_tree_root_invalid")
    inventory: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        reference = path.relative_to(root).as_posix()
        if path.is_symlink():
            inventory[reference] = hashlib.sha256(
                ("symlink:" + os.readlink(path)).encode("utf-8")
            ).hexdigest()
        elif path.is_file():
            if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
                raise GateBPackageErrorV3("runtime_bytecode_forbidden")
            inventory[reference] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise GateBPackageErrorV3("runtime_tree_entry_invalid")
    if not inventory:
        raise GateBPackageErrorV3("runtime_tree_empty")
    return _canonical_json_bytes(inventory)


def _stdlib_tree_manifest_bytes_v3(root: Path) -> bytes:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise GateBPackageErrorV3("stdlib_tree_root_invalid")
    inventory: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        reference = path.relative_to(root).as_posix()
        if path.is_symlink():
            inventory[reference] = hashlib.sha256(
                ("symlink:" + os.readlink(path)).encode("utf-8")
            ).hexdigest()
        elif path.is_file():
            inventory[reference] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise GateBPackageErrorV3("stdlib_tree_entry_invalid")
    if not inventory:
        raise GateBPackageErrorV3("stdlib_tree_empty")
    return _canonical_json_bytes(inventory)


def _export_runtime_manifest_v3(root: Path, candidate_commit: str) -> Path:
    root = root.resolve()
    runtime_root = root / "runtime"
    python_executable = Path(sys.executable).resolve()
    stdlib_root = Path(sysconfig.get_path("stdlib")).resolve()
    dependency_lock_path = runtime_root / "uv.lock"
    distributions_path = root / "python-runtime/installed-distributions.txt"
    if re.fullmatch(r"[0-9a-f]{40}", candidate_commit) is None:
        raise GateBPackageErrorV3("runtime_candidate_commit_invalid")
    if ".".join(str(item) for item in sys.version_info[:3]) != "3.12.13":
        raise GateBPackageErrorV3("runtime_python_version_invalid")
    for required in (
        runtime_root,
        python_executable,
        stdlib_root,
        dependency_lock_path,
        distributions_path,
    ):
        if not required.is_relative_to(root):
            raise GateBPackageErrorV3("runtime_path_outside_export")
    runtime_tree_bytes = _tree_manifest_bytes_v3(runtime_root)
    stdlib_tree_bytes = _stdlib_tree_manifest_bytes_v3(stdlib_root)
    executable_bytes = python_executable.read_bytes()
    dependency_lock_bytes = dependency_lock_path.read_bytes()
    installed_distributions_bytes = distributions_path.read_bytes()
    editable_installs = tuple(
        line.decode("utf-8", errors="replace")
        for line in installed_distributions_bytes.splitlines()
        if line.lstrip().startswith(b"-e ") or b" @ file:" in line
    )
    normalized_sys_path = tuple(
        str(Path(item or os.getcwd()).resolve()) for item in sys.path
    )
    if any(not Path(item).is_absolute() for item in normalized_sys_path):
        raise GateBPackageErrorV3("runtime_sys_path_invalid")
    sys_path_bytes = _canonical_json_bytes(list(normalized_sys_path))
    manifest = GateBRuntimeManifestV3(
        schema_version="3.0.0",
        runtime_kind="gate_b_at_most_once",
        candidate_commit=candidate_commit,
        python_version="3.12.13",
        runtime_tree_sha256=hashlib.sha256(runtime_tree_bytes).hexdigest(),
        python_executable_sha256=hashlib.sha256(executable_bytes).hexdigest(),
        stdlib_tree_sha256=hashlib.sha256(stdlib_tree_bytes).hexdigest(),
        dependency_lock_sha256=hashlib.sha256(dependency_lock_bytes).hexdigest(),
        installed_distributions_sha256=hashlib.sha256(
            installed_distributions_bytes
        ).hexdigest(),
        sys_path_sha256=hashlib.sha256(sys_path_bytes).hexdigest(),
        editable_installs=editable_installs,
    )
    identity_root = root / "runtime-identity"
    identity_root.mkdir(mode=0o700)
    (identity_root / "runtime-tree.json").write_bytes(runtime_tree_bytes)
    (identity_root / "stdlib-tree.json").write_bytes(stdlib_tree_bytes)
    (identity_root / "sys-path.json").write_bytes(sys_path_bytes)
    manifest_path = root / "runtime-manifest.json"
    manifest_path.write_bytes(_canonical_json_bytes(manifest.model_dump(mode="json")))
    return manifest_path


def _main_v3(arguments: list[str]) -> int:
    if len(arguments) == 3 and arguments[0] == "export-runtime-manifest":
        _export_runtime_manifest_v3(Path(arguments[1]), arguments[2])
        return 0
    if arguments == ["consume-launch-receipt"]:
        consumed = consume_gate_b_launch_receipt_v3()
        print(consumed)
        return 0
    if arguments == ["run-at-most-once"]:
        summary = run_gate_b_from_consumed_receipt_v3()
        print(_canonical_json_bytes(summary.model_dump(mode="json")).decode("utf-8"))
        return 0
    raise SystemExit(
        "usage: gate_b_benchmark_v3 "
        "export-runtime-manifest ROOT COMMIT | consume-launch-receipt | "
        "run-at-most-once"
    )


if __name__ == "__main__":
    raise SystemExit(_main_v3(sys.argv[1:]))
