"""Closed, additive Gate B at-most-once benchmark vocabulary (v3)."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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
        try:
            self.root.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            pass
        try:
            self._root_descriptor = os.open(
                self.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise GateBLedgerErrorV3("ledger_root_unsafe") from exc
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
