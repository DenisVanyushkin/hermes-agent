from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

import job_intel.product_search.gate_b_benchmark_v3 as gate_b_v3
from job_intel.product_search.gate_b_benchmark_v3 import (
    GateBBenchmarkPolicyV3,
    GateBCallStateV3,
    GateBLedgerErrorV3,
    GateBLedgerV3,
    GateBLaunchIdentityV3,
    GateBPackageManifestV3,
    GateBRecoveryDecisionV3,
    GateBTerminalKindV3,
    apply_owner_recovery_v3,
    build_recovery_request_v3,
    canonical_json_sha256,
    load_gate_b_benchmark_policy_v3,
    recovered_cost,
    retry_allowed,
    transition_allowed,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config/product_search/gate_b_benchmark.v3.yaml"
OWNER_PRIVATE_KEY = bytes.fromhex("11" * 32)
OWNER_PUBLIC_KEY = (
    Ed25519PrivateKey.from_private_bytes(OWNER_PRIVATE_KEY)
    .public_key()
    .public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
)


def test_v3_call_state_is_closed() -> None:
    assert tuple(item.value for item in GateBCallStateV3) == (
        "pending",
        "reserved",
        "dispatched",
        "success",
        "terminal_failure",
        "terminal_unknown",
    )
    assert tuple(item.value for item in GateBTerminalKindV3) == (
        "terminal_failure",
        "terminal_unknown",
    )


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        ("pending", "reserved", True),
        ("reserved", "pending", True),
        ("reserved", "dispatched", True),
        ("dispatched", "success", True),
        ("dispatched", "terminal_failure", True),
        ("dispatched", "terminal_unknown", True),
        ("dispatched", "reserved", False),
        ("terminal_unknown", "dispatched", False),
        ("success", "pending", False),
    ],
)
def test_v3_transition_matrix(source: str, target: str, allowed: bool) -> None:
    actor = "owner_recovery" if (source, target) == ("reserved", "pending") else "runner"
    assert transition_allowed(source, target, actor=actor) is allowed


def test_runner_cannot_make_a_reserved_row_retryable() -> None:
    assert transition_allowed("reserved", "pending", actor="runner") is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unknown_contract_key", "forbidden"),
        ("ordered_call_cap", 47),
        ("per_call_maximum_usd", "0.02"),
        ("aggregate_maximum_usd", "0.47"),
        ("automatic_restart", True),
    ],
)
def test_v3_policy_rejects_unknown_or_non_exact_controls(
    field: str, value: object
) -> None:
    payload = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    mutated = deepcopy(payload)
    mutated[field] = value

    with pytest.raises(ValidationError):
        GateBBenchmarkPolicyV3.model_validate(mutated)


def test_v3_policy_file_parses_as_the_closed_contract() -> None:
    policy = load_gate_b_benchmark_policy_v3()

    assert policy.ordered_call_cap == 48
    assert str(policy.per_call_maximum_usd) == "0.01"
    assert str(policy.aggregate_maximum_usd) == "0.48"
    assert policy.automatic_restart is False


def test_v3_package_manifest_carries_every_input_and_authority_hash() -> None:
    ordered_input_sha256s = tuple(f"{index:064x}" for index in range(48))
    authority_sha256s = ("a" * 64, "b" * 64)
    manifest = GateBPackageManifestV3(
        schema_version="3.0.0",
        package_id="gate-b-v3",
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        ordered_input_sha256s=ordered_input_sha256s,
        authority_sha256s=authority_sha256s,
    )

    assert manifest.ordered_input_sha256s == ordered_input_sha256s
    assert manifest.authority_sha256s == authority_sha256s
    assert len(manifest.ordered_input_sha256s) == 48
    assert len(manifest.canonical_sha256) == 64


def test_v3_manifest_and_launch_identity_require_utc_timestamps() -> None:
    non_utc = datetime(2026, 8, 20, 18, 0, tzinfo=timezone(timedelta(hours=6)))

    with pytest.raises(ValidationError, match="UTC"):
        GateBPackageManifestV3(
            schema_version="3.0.0",
            package_id="gate-b-v3",
            created_at=non_utc,
            ordered_input_sha256s=tuple(f"{index:064x}" for index in range(48)),
            authority_sha256s=("a" * 64,),
        )
    with pytest.raises(ValidationError, match="UTC"):
        GateBLaunchIdentityV3(
            schema_version="3.0.0",
            run_id="gate-b-v3",
            issued_at=non_utc,
            package_manifest_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("per_call_maximum_usd", 0.01),
        ("aggregate_maximum_usd", 0.48),
    ],
)
def test_v3_policy_model_rejects_coercible_float_costs(
    field: str, value: float
) -> None:
    payload = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["per_call_maximum_usd"] = Decimal("0.01")
    payload["aggregate_maximum_usd"] = Decimal("0.48")
    payload[field] = value

    with pytest.raises(ValidationError):
        GateBBenchmarkPolicyV3.model_validate(payload)


@pytest.fixture
def v3_identity() -> tuple[GateBPackageManifestV3, GateBLaunchIdentityV3]:
    manifest = GateBPackageManifestV3(
        schema_version="3.0.0",
        package_id="gate-b-v3-test",
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        ordered_input_sha256s=tuple(f"{index + 1:064x}" for index in range(48)),
        authority_sha256s=("a" * 64, "b" * 64),
    )
    launch = GateBLaunchIdentityV3(
        schema_version="3.0.0",
        run_id="gate-b-v3-run",
        issued_at=datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
        package_manifest_sha256=manifest.canonical_sha256,
    )
    return manifest, launch


def _open_ledger(
    root: Path,
    identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> GateBLedgerV3:
    manifest, launch = identity
    return GateBLedgerV3(
        root,
        launch,
        manifest,
        owner_recovery_public_key=OWNER_PUBLIC_KEY,
    )


def _marker_path(ledger: GateBLedgerV3, ordinal: int = 0) -> Path:
    row = ledger.row(ordinal)
    return ledger.dispatch_markers_path / f"{ordinal}-{row.input_sha256}.json"


def _recording_path(ledger: GateBLedgerV3, ordinal: int = 0) -> Path:
    row = ledger.row(ordinal)
    return ledger.recordings_path / f"{ordinal}-{row.input_sha256}.json"


@pytest.mark.parametrize(
    ("checkpoint", "expected_state"),
    [
        ("before_reserve_fsync", GateBCallStateV3.PENDING),
        ("after_reserve_fsync", GateBCallStateV3.RESERVED),
        ("after_dispatch_fsync", GateBCallStateV3.DISPATCHED),
        ("during_transport", GateBCallStateV3.DISPATCHED),
        ("after_sealed_success", GateBCallStateV3.SUCCESS),
        ("after_validated_failure", GateBCallStateV3.TERMINAL_FAILURE),
        ("after_ambiguous_transport", GateBCallStateV3.TERMINAL_UNKNOWN),
    ],
)
def test_ledger_reopens_each_crash_checkpoint_conservatively(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    checkpoint: str,
    expected_state: GateBCallStateV3,
) -> None:
    root = tmp_path / checkpoint
    ledger = _open_ledger(root, v3_identity)
    if checkpoint != "before_reserve_fsync":
        ledger.reserve(0)
    if checkpoint in {
        "after_dispatch_fsync",
        "during_transport",
        "after_sealed_success",
        "after_validated_failure",
        "after_ambiguous_transport",
    }:
        ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    if checkpoint == "after_sealed_success":
        ledger.record_success(
            0,
            dispatch_id="dispatch-0",
            provider_record_sha256="c" * 64,
            measured_cost_usd=Decimal("0.004"),
        )
    elif checkpoint == "after_validated_failure":
        ledger.record_failure(
            0,
            dispatch_id="dispatch-0",
            provider_record_sha256="d" * 64,
            measured_cost_usd=Decimal("0.006"),
        )
    elif checkpoint == "after_ambiguous_transport":
        ledger.record_unknown(0, dispatch_id="dispatch-0")
    ledger.close()

    reopened = _open_ledger(root, v3_identity)
    assert reopened.state(0) is expected_state
    if expected_state is GateBCallStateV3.TERMINAL_UNKNOWN:
        assert reopened.row(0).measured_cost_usd is None
        assert reopened.row(0).conservative_cost_usd == Decimal("0.01")
    reopened.close()


def test_transition_cost_and_retry_helpers_are_fail_closed() -> None:
    assert recovered_cost("dispatched") == Decimal("0.01")
    assert retry_allowed("pending") is True
    assert retry_allowed("terminal_unknown") is False
    assert retry_allowed("terminal_failure") is False


def test_recovery_returns_only_unmarked_reserved_row_to_pending(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    request = build_recovery_request_v3(ledger, {0: "pending"})
    assert len(request.rows) == 48
    assert request.rows[0].state is GateBCallStateV3.RESERVED
    assert request.rows[0].dispatch_marker_sha256 is None
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )

    apply_owner_recovery_v3(ledger, decision)

    assert ledger.state(0) is GateBCallStateV3.PENDING
    assert ledger.retry_allowed(0) is True
    final_entry = json.loads(ledger.ledger_path.read_text().splitlines()[-1])
    assert final_entry["actor"] == "owner_recovery"
    assert final_entry["owner_decision_sha256"] == decision.owner_approval_sha256
    assert final_entry["owner_decision"]["owner_signature_hex"] == (
        decision.owner_signature_hex
    )
    ledger.close()
    reopened = _open_ledger(tmp_path / "run", v3_identity)
    assert reopened.state(0) is GateBCallStateV3.PENDING
    reopened.close()


def test_recovery_converts_ambiguous_dispatch_to_costed_terminal_unknown(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    request = build_recovery_request_v3(ledger, {0: "terminal_unknown"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )

    apply_owner_recovery_v3(ledger, decision)

    row = ledger.row(0)
    assert row.state is GateBCallStateV3.TERMINAL_UNKNOWN
    assert row.measured_cost_usd is None
    assert row.conservative_cost_usd == Decimal("0.01")
    assert ledger.conservative_spend_usd == Decimal("0.01")
    assert ledger.retry_allowed(0) is False
    recording = json.loads(_recording_path(ledger).read_text(encoding="utf-8"))
    assert recording["record_kind"] == "owner_recovery_terminal_unknown"
    assert recording["owner_decision_sha256"] == decision.owner_approval_sha256
    final_entry = json.loads(ledger.ledger_path.read_text().splitlines()[-1])
    assert final_entry["actor"] == "owner_recovery"
    assert final_entry["owner_decision_sha256"] == decision.owner_approval_sha256
    assert final_entry["owner_decision"]["owner_signature_hex"] == (
        decision.owner_signature_hex
    )
    ledger.close()
    reopened = _open_ledger(tmp_path / "run", v3_identity)
    assert reopened.state(0) is GateBCallStateV3.TERMINAL_UNKNOWN
    assert reopened.row(0).conservative_cost_usd == Decimal("0.01")
    reopened.close()


def test_recovery_rejects_unsigned_stale_or_terminal_to_nonterminal_decisions(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    request = build_recovery_request_v3(ledger, {0: "pending"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )
    unsigned = decision.model_copy(update={"owner_signature_hex": "0" * 128})
    with pytest.raises(GateBLedgerErrorV3, match="owner_signature"):
        apply_owner_recovery_v3(ledger, unsigned)

    ledger.reserve(1)
    with pytest.raises(GateBLedgerErrorV3, match="stale"):
        apply_owner_recovery_v3(ledger, decision)

    ledger.mark_dispatched(1, dispatch_id="dispatch-1")
    ledger.record_success(
        1,
        dispatch_id="dispatch-1",
        provider_record_sha256="e" * 64,
        measured_cost_usd=Decimal("0.003"),
    )
    with pytest.raises(GateBLedgerErrorV3, match="recovery_transition"):
        build_recovery_request_v3(ledger, {1: "pending"})
    ledger.close()


def test_recovery_rejects_a_valid_signature_from_an_unbound_key(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    request = build_recovery_request_v3(ledger, {0: "pending"})
    foreign_private_key = bytes.fromhex("22" * 32)
    foreign_decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=foreign_private_key,
    )

    with pytest.raises(GateBLedgerErrorV3, match="owner_signature"):
        apply_owner_recovery_v3(ledger, foreign_decision)
    assert ledger.state(0) is GateBCallStateV3.RESERVED
    ledger.close()


def test_reopen_reverifies_the_full_persisted_owner_decision(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    request = build_recovery_request_v3(ledger, {0: "pending"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )
    apply_owner_recovery_v3(ledger, decision)
    ledger_path = ledger.ledger_path
    ledger.close()
    entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    forged = entries[-1]
    forged["owner_decision_sha256"] = "a" * 64
    unsigned = dict(forged)
    unsigned.pop("entry_sha256")
    forged["entry_sha256"] = canonical_json_sha256(unsigned)
    ledger_path.write_text(
        "\n".join(
            json.dumps(
                entry,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            for entry in entries
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GateBLedgerErrorV3, match="owner_decision"):
        _open_ledger(root, v3_identity)


def test_one_signed_decision_can_apply_and_reopen_a_mixed_recovery_batch(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.reserve(1)
    ledger.mark_dispatched(1, dispatch_id="dispatch-1")
    request = build_recovery_request_v3(
        ledger,
        {0: "pending", 1: "terminal_unknown"},
    )
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )
    apply_owner_recovery_v3(ledger, decision)
    ledger.close()

    reopened = _open_ledger(root, v3_identity)
    assert reopened.state(0) is GateBCallStateV3.PENDING
    assert reopened.state(1) is GateBCallStateV3.TERMINAL_UNKNOWN
    assert reopened.conservative_spend_usd == Decimal("0.01")
    reopened.close()


def test_recovery_decision_is_bound_to_complete_inventory_not_only_ledger_head(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    request = build_recovery_request_v3(ledger, {0: "pending"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )
    original_head = ledger.ledger_head_sha256

    def crash_before_dispatch_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after marker fsync")

    monkeypatch.setattr(ledger, "_append_transition", crash_before_dispatch_entry)
    with pytest.raises(OSError):
        ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    assert ledger.ledger_head_sha256 == original_head
    assert ledger.inventory_sha256 != request.inventory_sha256

    with pytest.raises(GateBLedgerErrorV3, match="stale"):
        apply_owner_recovery_v3(ledger, decision)
    ledger.close()


def test_recovery_request_revalidates_same_size_ledger_bytes(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    payload = ledger.ledger_path.read_bytes()
    assert b'"actor":"runner"' in payload
    ledger.ledger_path.write_bytes(
        payload.replace(b'"actor":"runner"', b'"actor":"runneX"', 1)
    )

    with pytest.raises(GateBLedgerErrorV3, match="ledger_content_changed"):
        build_recovery_request_v3(ledger, {0: "pending"})
    ledger.close()


def test_marker_fsync_without_ledger_dispatch_reopens_as_dispatched(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)

    def crash_before_dispatch_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after marker fsync")

    monkeypatch.setattr(ledger, "_append_transition", crash_before_dispatch_entry)
    with pytest.raises(OSError, match="simulated crash"):
        ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    ledger.close()

    reopened = _open_ledger(root, v3_identity)
    assert reopened.state(0) is GateBCallStateV3.DISPATCHED
    assert reopened.retry_allowed(0) is False
    request = build_recovery_request_v3(reopened, {0: "terminal_unknown"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )
    apply_owner_recovery_v3(reopened, decision)
    assert reopened.state(0) is GateBCallStateV3.TERMINAL_UNKNOWN
    reopened.close()
    final = _open_ledger(root, v3_identity)
    assert final.state(0) is GateBCallStateV3.TERMINAL_UNKNOWN
    assert final.retry_allowed(0) is False
    final.close()


def test_torn_final_entry_is_discarded_to_inventory_proven_state(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reserved_root = tmp_path / "reserved"
    reserved = _open_ledger(reserved_root, v3_identity)
    reserved.reserve(0)
    reserved.close()
    with (reserved_root / "ledger.jsonl").open("ab") as stream:
        stream.write(b'{"seq":2,"kind":"transition"')
    reopened_reserved = _open_ledger(reserved_root, v3_identity)
    assert reopened_reserved.state(0) is GateBCallStateV3.RESERVED
    reopened_reserved.close()
    assert (reserved_root / "ledger.jsonl").read_bytes().endswith(b"\n")

    dispatched_root = tmp_path / "dispatched"
    dispatched = _open_ledger(dispatched_root, v3_identity)
    dispatched.reserve(0)

    def crash_before_dispatch_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after marker fsync")

    monkeypatch.setattr(dispatched, "_append_transition", crash_before_dispatch_entry)
    with pytest.raises(OSError):
        dispatched.mark_dispatched(0, dispatch_id="dispatch-0")
    dispatched.close()
    with (dispatched_root / "ledger.jsonl").open("ab") as stream:
        stream.write(b'{"seq":2,"kind":"transition"')

    reopened_dispatched = _open_ledger(dispatched_root, v3_identity)
    assert reopened_dispatched.state(0) is GateBCallStateV3.DISPATCHED
    assert reopened_dispatched.retry_allowed(0) is False
    reopened_dispatched.close()


def test_invalid_inventory_does_not_truncate_a_torn_ledger_tail(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    marker = _marker_path(ledger)
    ledger.close()
    marker.unlink()
    ledger_path = root / "ledger.jsonl"
    with ledger_path.open("ab") as stream:
        stream.write(b'{"seq":3,"kind":"transition"')
    evidence_before_reopen = ledger_path.read_bytes()

    with pytest.raises(GateBLedgerErrorV3, match="marker"):
        _open_ledger(root, v3_identity)

    assert ledger_path.read_bytes() == evidence_before_reopen


@pytest.mark.parametrize("artifact_kind", ["ledger", "marker", "recording"])
@pytest.mark.parametrize("fault_stage", ["before_write", "during_write", "before_fsync"])
def test_create_once_artifact_fault_never_publishes_empty_or_partial_final_name(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
    artifact_kind: str,
    fault_stage: str,
) -> None:
    root = tmp_path / f"{artifact_kind}-{fault_stage}"
    ledger: GateBLedgerV3 | None = None
    if artifact_kind != "ledger":
        ledger = _open_ledger(root, v3_identity)
        ledger.reserve(0)
        if artifact_kind == "recording":
            ledger.mark_dispatched(0, dispatch_id="dispatch-0")

    original_write_all = gate_b_v3._write_all
    original_prepared_fsync = gate_b_v3._fsync_prepared_file
    if fault_stage == "before_write":
        def fail_write(_descriptor: int, _payload: bytes) -> None:
            raise OSError("fault after anonymous inode create")

        monkeypatch.setattr(gate_b_v3, "_write_all", fail_write)
    elif fault_stage == "during_write":
        def fail_during_write(descriptor: int, payload: bytes) -> None:
            os.write(descriptor, payload[:5])
            raise OSError("fault during anonymous inode write")

        monkeypatch.setattr(gate_b_v3, "_write_all", fail_during_write)
    else:
        def fail_fsync(_descriptor: int) -> None:
            raise OSError("fault before prepared inode fsync")

        monkeypatch.setattr(gate_b_v3, "_fsync_prepared_file", fail_fsync)

    if artifact_kind == "ledger":
        with pytest.raises((OSError, GateBLedgerErrorV3)):
            _open_ledger(root, v3_identity)
        final_path = root / "ledger.jsonl"
    elif artifact_kind == "marker":
        assert ledger is not None
        with pytest.raises((OSError, GateBLedgerErrorV3)):
            ledger.mark_dispatched(0, dispatch_id="dispatch-0")
        final_path = _marker_path(ledger)
        assert ledger.state(0) is GateBCallStateV3.RESERVED
    else:
        assert ledger is not None
        with pytest.raises((OSError, GateBLedgerErrorV3)):
            ledger.record_unknown(0, dispatch_id="dispatch-0")
        final_path = _recording_path(ledger)
        assert ledger.state(0) is GateBCallStateV3.DISPATCHED

    assert not final_path.exists()
    monkeypatch.setattr(gate_b_v3, "_write_all", original_write_all)
    monkeypatch.setattr(
        gate_b_v3,
        "_fsync_prepared_file",
        original_prepared_fsync,
    )
    if ledger is not None:
        ledger.close()

    resumed = _open_ledger(root, v3_identity)
    if artifact_kind == "marker":
        resumed.mark_dispatched(0, dispatch_id="dispatch-0")
        assert resumed.state(0) is GateBCallStateV3.DISPATCHED
    elif artifact_kind == "recording":
        resumed.record_unknown(0, dispatch_id="dispatch-0")
        assert resumed.state(0) is GateBCallStateV3.TERMINAL_UNKNOWN
    else:
        assert resumed.state(0) is GateBCallStateV3.PENDING
    resumed.close()


def test_recording_fsync_without_terminal_ledger_entry_reopens_terminal(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")

    def crash_before_terminal_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after recording fsync")

    monkeypatch.setattr(ledger, "_append_transition", crash_before_terminal_entry)
    with pytest.raises(OSError, match="simulated crash"):
        ledger.record_success(
            0,
            dispatch_id="dispatch-0",
            provider_record_sha256="c" * 64,
            measured_cost_usd=Decimal("0.004"),
        )
    ledger.close()

    reopened = _open_ledger(root, v3_identity)
    assert reopened.state(0) is GateBCallStateV3.SUCCESS
    assert reopened.row(0).measured_cost_usd == Decimal("0.004")
    reopened.close()


def test_owner_recovery_recording_only_crash_reverifies_full_decision(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    request = build_recovery_request_v3(ledger, {0: "terminal_unknown"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )

    def crash_before_terminal_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after owner recording fsync")

    monkeypatch.setattr(ledger, "_append_transition", crash_before_terminal_entry)
    with pytest.raises(OSError, match="simulated crash"):
        apply_owner_recovery_v3(ledger, decision)
    recording_path = _recording_path(ledger)
    ledger.close()
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    recording["owner_decision_sha256"] = "a" * 64
    recording_path.write_text(
        json.dumps(
            recording,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GateBLedgerErrorV3, match="owner_decision"):
        _open_ledger(root, v3_identity)


def test_legitimate_owner_recovery_recording_only_crash_reopens_unknown(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    request = build_recovery_request_v3(ledger, {0: "terminal_unknown"})
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )

    def crash_before_terminal_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after owner recording fsync")

    monkeypatch.setattr(ledger, "_append_transition", crash_before_terminal_entry)
    with pytest.raises(OSError):
        apply_owner_recovery_v3(ledger, decision)
    ledger.close()

    reopened = _open_ledger(root, v3_identity)
    assert reopened.state(0) is GateBCallStateV3.TERMINAL_UNKNOWN
    assert reopened.retry_allowed(0) is False
    reopened.close()


def test_mixed_owner_recovery_batch_recording_only_crash_reopens(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.reserve(1)
    ledger.mark_dispatched(1, dispatch_id="dispatch-1")
    request = build_recovery_request_v3(
        ledger,
        {0: "pending", 1: "terminal_unknown"},
    )
    decision = GateBRecoveryDecisionV3.approve(
        request,
        approved_by="owner:denis",
        approved_at=datetime(2026, 8, 20, 12, 2, tzinfo=timezone.utc),
        owner_private_key=OWNER_PRIVATE_KEY,
    )
    append_transition = ledger._append_transition

    def crash_on_second_transition(
        ordinal: int, *args: object, **kwargs: object
    ) -> None:
        if ordinal == 1:
            raise OSError("simulated crash after later owner recording fsync")
        append_transition(ordinal, *args, **kwargs)

    monkeypatch.setattr(ledger, "_append_transition", crash_on_second_transition)
    with pytest.raises(OSError, match="later owner recording"):
        apply_owner_recovery_v3(ledger, decision)
    ledger.close()

    reopened = _open_ledger(root, v3_identity)
    assert reopened.state(0) is GateBCallStateV3.PENDING
    assert reopened.state(1) is GateBCallStateV3.TERMINAL_UNKNOWN
    assert reopened.retry_allowed(1) is False
    reopened.close()


def test_recording_only_crash_rejects_a_success_record_without_provider_hash(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")

    def crash_before_terminal_entry(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash after recording fsync")

    monkeypatch.setattr(ledger, "_append_transition", crash_before_terminal_entry)
    with pytest.raises(OSError):
        ledger.record_success(
            0,
            dispatch_id="dispatch-0",
            provider_record_sha256="c" * 64,
            measured_cost_usd=Decimal("0.004"),
        )
    recording_path = _recording_path(ledger)
    ledger.close()
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    recording["provider_record_sha256"] = None
    recording_path.write_text(
        json.dumps(
            recording,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(GateBLedgerErrorV3, match="recording"):
        _open_ledger(root, v3_identity)


@pytest.mark.parametrize(
    "mutation",
    [
        "ledger_dispatch_without_marker",
        "marker_for_another_ordinal",
        "duplicate_marker",
        "terminal_recording_without_marker",
        "marker_symlink",
        "marker_hardlink",
    ],
)
def test_ledger_rejects_inconsistent_or_unsafe_marker_inventory(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    mutation: str,
) -> None:
    root = tmp_path / mutation
    ledger = _open_ledger(root, v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    if mutation == "terminal_recording_without_marker":
        ledger.record_success(
            0,
            dispatch_id="dispatch-0",
            provider_record_sha256="c" * 64,
            measured_cost_usd=Decimal("0.004"),
        )
    marker = _marker_path(ledger)
    ledger.close()

    if mutation in {"ledger_dispatch_without_marker", "terminal_recording_without_marker"}:
        marker.unlink()
    elif mutation == "marker_for_another_ordinal":
        marker.rename(marker.with_name(f"1-{'2' * 64}.json"))
    elif mutation == "duplicate_marker":
        shutil.copyfile(marker, marker.with_name(f"1-{'2' * 64}.json"))
    elif mutation == "marker_symlink":
        target = tmp_path / "external-marker"
        target.write_text("external", encoding="utf-8")
        marker.unlink()
        marker.symlink_to(target)
    elif mutation == "marker_hardlink":
        os.link(marker, tmp_path / "marker-hardlink")

    with pytest.raises(GateBLedgerErrorV3, match="marker"):
        _open_ledger(root, v3_identity)


def test_ledger_rejects_reordered_package_and_mixed_run_identity(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    root = tmp_path / "run"
    ledger = _open_ledger(root, v3_identity)
    ledger.close()
    manifest, launch = v3_identity
    reordered = manifest.model_copy(
        update={
            "ordered_input_sha256s": tuple(reversed(manifest.ordered_input_sha256s))
        }
    )
    reordered_launch = launch.model_copy(
        update={"package_manifest_sha256": reordered.canonical_sha256}
    )
    with pytest.raises(GateBLedgerErrorV3, match="identity"):
        GateBLedgerV3(
            root,
            reordered_launch,
            reordered,
            owner_recovery_public_key=OWNER_PUBLIC_KEY,
        )

    changed_package = manifest.model_copy(update={"package_id": "changed-package"})
    changed_launch = launch.model_copy(
        update={"package_manifest_sha256": changed_package.canonical_sha256}
    )
    with pytest.raises(GateBLedgerErrorV3, match="identity"):
        GateBLedgerV3(
            root,
            changed_launch,
            changed_package,
            owner_recovery_public_key=OWNER_PUBLIC_KEY,
        )

    mixed_run = launch.model_copy(update={"run_id": "another-run"})
    with pytest.raises(GateBLedgerErrorV3, match="identity"):
        GateBLedgerV3(
            root,
            mixed_run,
            manifest,
            owner_recovery_public_key=OWNER_PUBLIC_KEY,
        )

    foreign_public_key = (
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    with pytest.raises(GateBLedgerErrorV3, match="identity"):
        GateBLedgerV3(
            root,
            launch,
            manifest,
            owner_recovery_public_key=foreign_public_key,
        )


def test_manifest_rejects_duplicate_rows_explicitly() -> None:
    rows = [f"{index + 1:064x}" for index in range(48)]
    rows[47] = rows[0]
    with pytest.raises(ValidationError, match="unique"):
        GateBPackageManifestV3(
            schema_version="3.0.0",
            package_id="duplicate",
            created_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            ordered_input_sha256s=tuple(rows),
            authority_sha256s=("a" * 64,),
        )


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "inode_drift"])
def test_ledger_rejects_symlink_hardlink_and_inode_drift(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    unsafe_kind: str,
) -> None:
    root = tmp_path / unsafe_kind
    root.mkdir(mode=0o700)
    ledger_path = root / "ledger.jsonl"
    if unsafe_kind == "symlink":
        external = tmp_path / "external-ledger"
        external.write_text("external", encoding="utf-8")
        ledger_path.symlink_to(external)
        with pytest.raises(GateBLedgerErrorV3, match="ledger"):
            _open_ledger(root, v3_identity)
        assert external.read_text(encoding="utf-8") == "external"
        return

    ledger = _open_ledger(root, v3_identity)
    if unsafe_kind == "hardlink":
        ledger.close()
        os.link(ledger_path, tmp_path / "ledger-hardlink")
        with pytest.raises(GateBLedgerErrorV3, match="ledger"):
            _open_ledger(root, v3_identity)
        return

    displaced = root / "ledger.displaced"
    ledger_path.rename(displaced)
    ledger_path.write_text("replacement", encoding="utf-8")
    ledger_path.chmod(0o600)
    with pytest.raises(GateBLedgerErrorV3, match="ledger_path_changed"):
        ledger.reserve(0)
    ledger.close()
    assert ledger_path.read_text(encoding="utf-8") == "replacement"


def test_open_ledger_rejects_dispatch_marker_inode_replacement(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    marker = _marker_path(ledger)
    marker_bytes = marker.read_bytes()
    displaced = tmp_path / "displaced-marker"
    marker.rename(displaced)
    marker.write_bytes(marker_bytes)
    marker.chmod(0o600)

    with pytest.raises(GateBLedgerErrorV3, match="marker_inventory_changed"):
        build_recovery_request_v3(ledger, {0: "terminal_unknown"})
    with pytest.raises(GateBLedgerErrorV3, match="marker_inventory_changed"):
        ledger.record_unknown(0, dispatch_id="dispatch-0")
    assert not _recording_path(ledger).exists()
    ledger.close()


def test_two_concurrent_ledger_starts_are_rejected(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    root = tmp_path / "run"
    first = _open_ledger(root, v3_identity)
    with pytest.raises(GateBLedgerErrorV3, match="locked"):
        _open_ledger(root, v3_identity)
    first.close()

    resumed = _open_ledger(root, v3_identity)
    assert resumed.state(0) is GateBCallStateV3.PENDING
    resumed.close()


@pytest.mark.parametrize("ordinal", [-1, 48])
def test_ledger_rejects_out_of_range_ordinals_before_any_transition(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    ordinal: int,
) -> None:
    ledger = _open_ledger(tmp_path / f"run-{ordinal}", v3_identity)
    original_head = ledger.ledger_head_sha256

    with pytest.raises(GateBLedgerErrorV3, match="ordinal"):
        ledger.reserve(ordinal)
    with pytest.raises(GateBLedgerErrorV3, match="ordinal"):
        ledger.row(ordinal)

    assert ledger.ledger_head_sha256 == original_head
    assert all(row.state is GateBCallStateV3.PENDING for row in ledger.rows())
    ledger.close()


def test_ledger_files_are_private_canonical_and_hash_chained(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
) -> None:
    ledger = _open_ledger(tmp_path / "run", v3_identity)
    ledger.reserve(0)
    ledger.mark_dispatched(0, dispatch_id="dispatch-0")
    ledger.close()

    ledger_path = tmp_path / "run" / "ledger.jsonl"
    assert ledger_path.stat().st_mode & 0o777 == 0o600
    assert ledger_path.stat().st_nlink == 1
    entries = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    previous = "0" * 64
    for sequence, entry in enumerate(entries):
        entry_hash = entry.pop("entry_sha256")
        assert entry["sequence"] == sequence
        assert entry["previous_entry_sha256"] == previous
        assert canonical_json_sha256(entry) == entry_hash
        previous = entry_hash
