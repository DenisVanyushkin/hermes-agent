from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import builtins
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import inspect
import hashlib
from dataclasses import replace
from types import MappingProxyType

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
    Ed25519PrivateKey
    .from_private_bytes(OWNER_PRIVATE_KEY)
    .public_key()
    .public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
)

GATE_A_ROOT_V3 = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    "65d60daae16093a9a7e34a11a159e2f789dd14dd"
)
GATE_B_CORPUS_MANIFEST_V3 = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-b/"
    "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69/"
    "corpus-manifest.json"
)


def _gate_b_source_bytes_v3() -> dict[str, object]:
    corpus_bytes = GATE_B_CORPUS_MANIFEST_V3.read_bytes()
    corpus = json.loads(corpus_bytes)
    return {
        "corpus_manifest": corpus_bytes,
        "gate_a_manifest": (GATE_A_ROOT_V3 / "manifest.yaml").read_bytes(),
        "benchmark_policy": POLICY_PATH.read_bytes(),
        "reviewed_fragment_allowlist": (
            ROOT / "docs/evidence/product-search-gate-b/v3-fragment-allowlist.yaml"
        ).read_bytes(),
        "career_profile": (
            ROOT / "config/product_search/career_profile.v2.yaml"
        ).read_bytes(),
        "candidate_facts": Path(
            "/home/hermes/.hermes/private/career/"
            "denis_vanyushkin_structured_resume_v1_1.json"
        ).read_bytes(),
        "decision_contract": (
            ROOT / "config/product_search/decision_contract.v2.yaml"
        ).read_bytes(),
        "product_sot": (
            ROOT / "docs/superpowers/specs/"
            "2026-08-10-job-intel-search-product-redesign-design.md"
        ).read_bytes(),
        "search_contract": (
            ROOT / "config/product_search/search_contract.v1.yaml"
        ).read_bytes(),
        "semantic_contract": (
            ROOT
            / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml"
        ).read_bytes(),
        "task10_policy": (
            ROOT / "config/product_search/evidence_synthesis.v1.yaml"
        ).read_bytes(),
        "raw_artifacts": {
            record["raw_reference"]: (
                GATE_A_ROOT_V3 / record["raw_reference"]
            ).read_bytes()
            for record in corpus["records"]
        },
    }


def _write_gate_a_source_fixture_v3(
    root: Path,
    source_bytes: dict[str, object],
) -> None:
    root.mkdir(mode=0o700)
    (root / "raw-evidence").mkdir(mode=0o700)
    (root / "manifest.yaml").write_bytes(source_bytes["gate_a_manifest"])
    raw_artifacts = source_bytes["raw_artifacts"]
    assert isinstance(raw_artifacts, dict)
    for reference, payload in raw_artifacts.items():
        assert isinstance(reference, str)
        assert isinstance(payload, bytes)
        (root / reference).write_bytes(payload)


def _path_snapshot_v3(paths: tuple[Path, ...]) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for root in paths:
        candidates = (root, *sorted(root.rglob("*"))) if root.is_dir() else (root,)
        for path in candidates:
            metadata = path.lstat()
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
            )
            snapshot[str(path)] = (metadata.st_mode, metadata.st_size, digest)
    return snapshot


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
    actor = (
        "owner_recovery" if (source, target) == ("reserved", "pending") else "runner"
    )
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
@pytest.mark.parametrize(
    "fault_stage", ["before_write", "during_write", "before_fsync"]
)
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


def test_absent_root_parent_fsync_precedes_ledger_publication(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    events: list[str] = []
    original_fsync = os.fsync
    original_publish = gate_b_v3._publish_prepared_file

    def observe_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == parent_identity:
            events.append("parent_fsync")
        original_fsync(descriptor)

    def observe_publish(*args: object, **kwargs: object) -> tuple[int, tuple[int, int]]:
        events.append("ledger_publish")
        assert "parent_fsync" in events
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(gate_b_v3.os, "fsync", observe_fsync)
    monkeypatch.setattr(gate_b_v3, "_publish_prepared_file", observe_publish)

    ledger = _open_ledger(root, v3_identity)
    assert events.index("parent_fsync") < events.index("ledger_publish")
    ledger.close()


def test_absent_root_parent_fsync_failure_prevents_ledger_initialization(
    tmp_path: Path,
    v3_identity: tuple[GateBPackageManifestV3, GateBLaunchIdentityV3],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "run"
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    original_fsync = os.fsync
    original_publish = gate_b_v3._publish_prepared_file
    parent_fsync_attempts = 0
    events: list[str] = []

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsync_attempts
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == parent_identity:
            parent_fsync_attempts += 1
            if parent_fsync_attempts == 1:
                raise OSError("simulated crash before root durability")
            events.append("parent_fsync")
        original_fsync(descriptor)

    def observe_publish(*args: object, **kwargs: object) -> tuple[int, tuple[int, int]]:
        events.append("ledger_publish")
        assert "parent_fsync" in events
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(gate_b_v3.os, "fsync", fail_parent_fsync)
    monkeypatch.setattr(gate_b_v3, "_publish_prepared_file", observe_publish)

    with pytest.raises(
        GateBLedgerErrorV3,
        match="ledger_root_parent_fsync_failed",
    ):
        _open_ledger(root, v3_identity)
    assert not (root / "ledger.jsonl").exists()

    ledger = _open_ledger(root, v3_identity)
    assert parent_fsync_attempts == 2
    assert events.index("parent_fsync") < events.index("ledger_publish")
    ledger.close()


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

    if mutation in {
        "ledger_dispatch_without_marker",
        "terminal_recording_without_marker",
    }:
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
        Ed25519PrivateKey
        .from_private_bytes(bytes.fromhex("22" * 32))
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


def test_validate_gate_b_package_pure_v3_performs_zero_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_bytes = _gate_b_source_bytes_v3()
    validate = getattr(gate_b_v3, "validate_gate_b_package_pure_v3")
    attempted: list[str] = []

    def deny(operation: str):
        def denied(*_args: object, **_kwargs: object) -> None:
            attempted.append(operation)
            raise AssertionError(f"pure validation attempted {operation}")

        return denied

    monkeypatch.setattr(builtins, "open", deny("builtins.open"))
    for name in (
        "open",
        "stat",
        "lstat",
        "fstat",
        "access",
        "listdir",
        "scandir",
        "readlink",
        "getenv",
    ):
        monkeypatch.setattr(os, name, deny(f"os.{name}"))
    for name in ("open", "read_bytes", "read_text", "stat", "lstat", "resolve"):
        monkeypatch.setattr(Path, name, deny(f"Path.{name}"))
    monkeypatch.setattr(socket, "socket", deny("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", deny("socket.create_connection"))
    monkeypatch.setattr(subprocess, "Popen", deny("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", deny("subprocess.run"))
    monkeypatch.setattr(sqlite3, "connect", deny("sqlite3.connect"))

    package = validate(source_bytes)

    assert attempted == []
    assert package.package_sha256 == hashlib.sha256(package.manifest_bytes).hexdigest()
    assert package.manifest_sha256 == package.package_sha256
    assert len(package.ordered_input_sha256s) == 48
    assert len(set(package.ordered_input_sha256s)) == 48
    assert tuple(sorted(package.artifacts)) == (
        "package-index.json",
        "package-manifest.json",
        *tuple(
            f"task10-inputs/{input_sha256}.json"
            for input_sha256 in sorted(package.ordered_input_sha256s)
        ),
    )


def test_public_gate_b_v3_package_apis_accept_no_io_capabilities() -> None:
    load = getattr(gate_b_v3, "load_gate_b_source_bytes_v3")
    validate = getattr(gate_b_v3, "validate_gate_b_package_pure_v3")
    materialize = getattr(gate_b_v3, "materialize_gate_b_package_v3")

    assert tuple(inspect.signature(load).parameters) == ()
    assert tuple(inspect.signature(validate).parameters) == ("source_bytes",)
    assert tuple(inspect.signature(materialize).parameters) == ("package",)
    for api in (load, validate, materialize):
        parameters = inspect.signature(api).parameters.values()
        assert all(
            parameter.kind not in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}
            for parameter in parameters
        )
        assert not {
            "callback",
            "boundary",
            "boundary_attempt",
            "file",
            "file_handle",
            "output_root",
            "path",
            "root",
            "io_capability",
        } & set(inspect.signature(api).parameters)

    with pytest.raises(TypeError):
        load(boundary_attempt=lambda _boundary: None)
    with pytest.raises(TypeError):
        validate({}, output_root=Path("/tmp/forbidden"))
    with pytest.raises(TypeError):
        materialize(object(), io_capability=object())


def test_materialize_gate_b_package_v3_is_atomic_scoped_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_bytes = _gate_b_source_bytes_v3()
    validate = getattr(gate_b_v3, "validate_gate_b_package_pure_v3")
    materialize = getattr(gate_b_v3, "materialize_gate_b_package_v3")
    package = validate(source_bytes)
    package_parent = tmp_path / "gate-b-at-most-once"
    package_parent.mkdir(mode=0o700)
    gate_a_root = tmp_path / "gate-a"
    _write_gate_a_source_fixture_v3(gate_a_root, source_bytes)
    protected_file = tmp_path / "protected.sqlite3"
    protected_file.write_bytes(b"immutable production state")
    protected_directory = tmp_path / "protected-runtime"
    protected_directory.mkdir(mode=0o700)
    (protected_directory / "runtime.conf").write_bytes(b"immutable runtime")
    protected_paths = (gate_a_root, protected_file, protected_directory)
    protected_before = _path_snapshot_v3(protected_paths)
    external_attempts: list[str] = []

    monkeypatch.setattr(gate_b_v3, "_GATE_B_PACKAGE_PARENT_V3", package_parent)
    monkeypatch.setattr(gate_b_v3, "_GATE_A_SOURCE_ROOT_V3", gate_a_root)
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_PROTECTED_PATHS_V3",
        (protected_file, protected_directory),
    )

    def deny_external(operation: str):
        def denied(*_args: object, **_kwargs: object) -> None:
            external_attempts.append(operation)
            raise AssertionError(f"materialization attempted {operation}")

        return denied

    monkeypatch.setattr(socket, "socket", deny_external("socket.socket"))
    monkeypatch.setattr(
        socket,
        "create_connection",
        deny_external("socket.create_connection"),
    )
    monkeypatch.setattr(subprocess, "Popen", deny_external("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", deny_external("subprocess.run"))
    monkeypatch.setattr(sqlite3, "connect", deny_external("sqlite3.connect"))

    receipt = materialize(package)

    package_root = package_parent / package.package_sha256
    assert receipt.package_root == str(package_root)
    assert receipt.created is True
    assert external_attempts == []
    assert _path_snapshot_v3(protected_paths) == protected_before
    assert set(receipt.artifact_sha256s) == set(package.artifacts)
    assert receipt.observed_operations
    assert sum(
        operation.kind == "artifact_write" for operation in receipt.observed_operations
    ) == len(package.artifacts)
    assert sum(
        operation.kind == "artifact_rehash" for operation in receipt.observed_operations
    ) == len(package.artifacts)
    assert all(
        Path(operation.path).is_relative_to(package_parent)
        for operation in receipt.observed_operations
        if operation.path is not None and operation.kind.startswith("artifact_")
    )
    assert not any(
        path.name.endswith(".materializing") for path in package_parent.iterdir()
    )

    second = materialize(package)

    assert second.created is False
    assert not any(
        operation.kind == "artifact_write" for operation in second.observed_operations
    )
    assert sum(
        operation.kind == "artifact_rehash" for operation in second.observed_operations
    ) == len(package.artifacts)
    assert _path_snapshot_v3(protected_paths) == protected_before


def test_materialize_gate_b_package_v3_rejects_existing_unknown_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_bytes = _gate_b_source_bytes_v3()
    validate = getattr(gate_b_v3, "validate_gate_b_package_pure_v3")
    materialize = getattr(gate_b_v3, "materialize_gate_b_package_v3")
    package = validate(source_bytes)
    package_parent = tmp_path / "gate-b-at-most-once"
    package_parent.mkdir(mode=0o700)
    gate_a_root = tmp_path / "gate-a"
    _write_gate_a_source_fixture_v3(gate_a_root, source_bytes)
    monkeypatch.setattr(gate_b_v3, "_GATE_B_PACKAGE_PARENT_V3", package_parent)
    monkeypatch.setattr(gate_b_v3, "_GATE_A_SOURCE_ROOT_V3", gate_a_root)
    monkeypatch.setattr(gate_b_v3, "_GATE_B_PROTECTED_PATHS_V3", ())
    materialize(package)
    unexpected = package_parent / package.package_sha256 / "unexpected"
    unexpected.write_bytes(b"unknown")

    with pytest.raises(ValueError, match="unknown_content"):
        materialize(package)

    assert unexpected.read_bytes() == b"unknown"


def test_materialize_gate_b_package_v3_rejects_forged_source_inventory_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = gate_b_v3.validate_gate_b_package_pure_v3(_gate_b_source_bytes_v3())
    forged = type(package)(
        package_sha256=package.package_sha256,
        manifest_sha256=package.manifest_sha256,
        manifest_bytes=package.manifest_bytes,
        ordered_input_sha256s=package.ordered_input_sha256s,
        artifacts=package.artifacts,
        artifact_sha256s=package.artifact_sha256s,
        source_file_sha256s={"../../outside": "a" * 64},
    )

    def deny_io(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unvalidated source inventory reached I/O")

    monkeypatch.setattr(os, "open", deny_io)

    with pytest.raises(ValueError, match="source_inventory_invalid"):
        gate_b_v3.materialize_gate_b_package_v3(forged)


def test_pure_package_rejects_missing_invented_or_misbound_review_candidates() -> None:
    sources = _gate_b_source_bytes_v3()
    allowlist = yaml.safe_load(sources["reviewed_fragment_allowlist"])
    assert isinstance(allowlist, dict)
    entries = allowlist["entries"]
    assert isinstance(entries, list)
    assert entries

    empty = deepcopy(allowlist)
    empty["entries"] = []
    invented = deepcopy(allowlist)
    invented["entries"][0]["text_sha256"] = "f" * 64
    misbound = deepcopy(allowlist)
    selection_key = misbound["entries"][0]["selection_key"]
    for entry in misbound["entries"]:
        if entry["selection_key"] == selection_key:
            entry["vacancy_artifact_sha256"] = "f" * 64

    for mutated in (empty, invented, misbound):
        mutated_sources = dict(sources)
        mutated_sources["reviewed_fragment_allowlist"] = yaml.safe_dump(
            mutated,
            sort_keys=True,
        ).encode("utf-8")
        with pytest.raises(ValueError, match="reviewed_fragment_candidate_contract"):
            gate_b_v3.validate_gate_b_package_pure_v3(mutated_sources)


def test_materializer_rejects_forged_index_not_bound_by_manifest_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = gate_b_v3.validate_gate_b_package_pure_v3(_gate_b_source_bytes_v3())
    index = json.loads(package.artifacts["package-index.json"])
    index["source_authority_sha256s"]["benchmark_policy"] = "f" * 64
    index_bytes = json.dumps(
        index,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    artifacts = dict(package.artifacts)
    artifacts["package-index.json"] = index_bytes
    artifact_sha256s = dict(package.artifact_sha256s)
    artifact_sha256s["package-index.json"] = hashlib.sha256(index_bytes).hexdigest()
    forged = type(package)(
        package_sha256=package.package_sha256,
        manifest_sha256=package.manifest_sha256,
        manifest_bytes=package.manifest_bytes,
        ordered_input_sha256s=package.ordered_input_sha256s,
        artifacts=MappingProxyType(artifacts),
        artifact_sha256s=MappingProxyType(artifact_sha256s),
        source_file_sha256s=package.source_file_sha256s,
    )

    def deny_io(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forged package index reached I/O")

    monkeypatch.setattr(os, "open", deny_io)

    with pytest.raises(ValueError, match="index_manifest_mismatch"):
        gate_b_v3.materialize_gate_b_package_v3(forged)


def test_materializer_never_reads_credential_file_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _gate_b_source_bytes_v3()
    package = gate_b_v3.validate_gate_b_package_pure_v3(sources)
    package_parent = tmp_path / "gate-b-at-most-once"
    package_parent.mkdir(mode=0o700)
    gate_a_root = tmp_path / "gate-a"
    _write_gate_a_source_fixture_v3(gate_a_root, sources)
    credential_env = tmp_path / ".env"
    credential_env.write_bytes(b"credential bytes must remain unread\n")
    job_intel_env = tmp_path / "job-intel.env"
    job_intel_env.write_bytes(b"second credential payload must remain unread\n")
    original_read = gate_b_v3._read_path_nofollow_v3
    credential_paths = {credential_env, job_intel_env}

    def reject_credential_read(path: Path, **kwargs: object) -> bytes:
        if path in credential_paths:
            raise AssertionError("credential contents were read")
        return original_read(path, **kwargs)

    monkeypatch.setattr(gate_b_v3, "_GATE_B_PACKAGE_PARENT_V3", package_parent)
    monkeypatch.setattr(gate_b_v3, "_GATE_A_SOURCE_ROOT_V3", gate_a_root)
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_PROTECTED_PATHS_V3",
        (credential_env, job_intel_env),
    )
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_METADATA_ONLY_PROTECTED_PATHS_V3",
        frozenset(credential_paths),
    )
    monkeypatch.setattr(
        gate_b_v3,
        "_read_path_nofollow_v3",
        reject_credential_read,
    )

    receipt = gate_b_v3.materialize_gate_b_package_v3(package)

    credential_operations = [
        operation
        for operation in receipt.observed_operations
        if operation.path in {str(credential_env), str(job_intel_env)}
    ]
    assert len(credential_operations) == 4
    assert all(
        "metadata_only" in operation.detail for operation in credential_operations
    )


def test_production_protected_snapshot_policy_is_complete_and_partitioned() -> None:
    mutable_databases = (
        Path("/home/hermes/.hermes/state.db"),
        Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3"),
        Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3-wal"),
        Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3-shm"),
    )
    credentials = (
        Path("/home/hermes/.hermes/hermes-agent/.env"),
        Path("/etc/job-intel/job-intel.env"),
    )
    immutable_content = (Path("/home/hermes/.hermes/hermes-agent/config.yml"),)

    assert gate_b_v3._GATE_B_MUTABLE_DATABASE_PATHS_V3 == mutable_databases
    assert gate_b_v3._GATE_B_CREDENTIAL_PATHS_V3 == credentials
    assert gate_b_v3._GATE_B_METADATA_ONLY_PROTECTED_PATHS_V3 == frozenset((
        *mutable_databases,
        *credentials,
    ))
    assert gate_b_v3._GATE_B_PROTECTED_PATHS_V3 == (
        *mutable_databases,
        *credentials,
        *immutable_content,
    )


def test_materializer_snapshots_large_mutable_databases_without_content_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _gate_b_source_bytes_v3()
    package = gate_b_v3.validate_gate_b_package_pure_v3(sources)
    package_parent = tmp_path / "gate-b-at-most-once"
    package_parent.mkdir(mode=0o700)
    gate_a_root = tmp_path / "gate-a"
    _write_gate_a_source_fixture_v3(gate_a_root, sources)

    state_db = tmp_path / "state.db"
    job_intel_db = tmp_path / "job_intel.sqlite3"
    job_intel_wal = tmp_path / "job_intel.sqlite3-wal"
    job_intel_shm = tmp_path / "job_intel.sqlite3-shm"
    mutable_paths = {
        state_db,
        job_intel_db,
        job_intel_wal,
        job_intel_shm,
    }
    for path, size in (
        (state_db, 1_714_511_872),
        (job_intel_db, 98_000_000),
        (job_intel_wal, 32_000_000),
        (job_intel_shm, 65_536),
    ):
        path.touch(mode=0o600)
        os.truncate(path, size)

    credential_env = tmp_path / ".env"
    credential_env.write_bytes(b"credential bytes must remain unread\n")
    job_intel_env = tmp_path / "job-intel.env"
    job_intel_env.write_bytes(b"second credential payload must remain unread\n")
    credential_paths = {credential_env, job_intel_env}
    immutable_config = tmp_path / "config.yml"
    immutable_config.write_bytes(b"reviewed: immutable\n")
    metadata_only_paths = mutable_paths | credential_paths
    protected_paths = (*sorted(metadata_only_paths), immutable_config)
    original_read = gate_b_v3._read_path_nofollow_v3
    read_paths: list[Path] = []

    def reject_metadata_only_read(path: Path, **kwargs: object) -> bytes:
        if path in metadata_only_paths:
            raise AssertionError(f"metadata-only protected path was read: {path}")
        read_paths.append(path)
        return original_read(path, **kwargs)

    monkeypatch.setattr(gate_b_v3, "_GATE_B_PACKAGE_PARENT_V3", package_parent)
    monkeypatch.setattr(gate_b_v3, "_GATE_A_SOURCE_ROOT_V3", gate_a_root)
    monkeypatch.setattr(gate_b_v3, "_GATE_B_PROTECTED_PATHS_V3", protected_paths)
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_METADATA_ONLY_PROTECTED_PATHS_V3",
        frozenset(metadata_only_paths),
        raising=False,
    )
    monkeypatch.setattr(gate_b_v3, "_read_path_nofollow_v3", reject_metadata_only_read)

    receipt = gate_b_v3.materialize_gate_b_package_v3(package)

    metadata_operations = [
        operation
        for operation in receipt.observed_operations
        if operation.path in {str(path) for path in metadata_only_paths}
    ]
    assert len(metadata_operations) == 2 * len(metadata_only_paths)
    assert all("metadata_only" in operation.detail for operation in metadata_operations)
    assert read_paths.count(immutable_config) == 2


@pytest.mark.parametrize("mutation", ["mode", "inode", "size", "mtime"])
def test_materializer_fails_closed_on_metadata_only_protected_stat_drift(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _gate_b_source_bytes_v3()
    package = gate_b_v3.validate_gate_b_package_pure_v3(sources)
    package_parent = tmp_path / "gate-b-at-most-once"
    package_parent.mkdir(mode=0o700)
    gate_a_root = tmp_path / "gate-a"
    _write_gate_a_source_fixture_v3(gate_a_root, sources)
    state_db = tmp_path / "state.db"
    state_db.touch(mode=0o600)
    os.truncate(state_db, 1_714_511_872)
    initial = state_db.stat()
    original_snapshot = gate_b_v3._snapshot_protected_paths_v3
    snapshot_calls = 0

    def mutate_after_before_snapshot(*args: object, **kwargs: object):
        nonlocal snapshot_calls
        snapshot = original_snapshot(*args, **kwargs)
        snapshot_calls += 1
        if snapshot_calls != 1:
            return snapshot
        if mutation == "mode":
            state_db.chmod(0o640)
        elif mutation == "inode":
            replacement = state_db.with_suffix(".replacement")
            replacement.touch(mode=0o600)
            os.truncate(replacement, initial.st_size)
            os.utime(replacement, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            replacement.replace(state_db)
        elif mutation == "size":
            os.truncate(state_db, initial.st_size + 1)
        elif mutation == "mtime":
            os.utime(
                state_db,
                ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000),
            )
        else:  # pragma: no cover - parametrization is closed above
            raise AssertionError(f"unexpected mutation: {mutation}")
        return snapshot

    monkeypatch.setattr(gate_b_v3, "_GATE_B_PACKAGE_PARENT_V3", package_parent)
    monkeypatch.setattr(gate_b_v3, "_GATE_A_SOURCE_ROOT_V3", gate_a_root)
    monkeypatch.setattr(gate_b_v3, "_GATE_B_PROTECTED_PATHS_V3", (state_db,))
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_METADATA_ONLY_PROTECTED_PATHS_V3",
        frozenset({state_db}),
        raising=False,
    )
    monkeypatch.setattr(
        gate_b_v3,
        "_snapshot_protected_paths_v3",
        mutate_after_before_snapshot,
    )

    with pytest.raises(
        gate_b_v3.GateBPackageErrorV3,
        match="protected_paths_changed_during_materialization",
    ):
        gate_b_v3.materialize_gate_b_package_v3(package)


def test_immutable_protected_config_retains_the_16_mb_content_hash_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _gate_b_source_bytes_v3()
    package = gate_b_v3.validate_gate_b_package_pure_v3(sources)
    package_parent = tmp_path / "gate-b-at-most-once"
    package_parent.mkdir(mode=0o700)
    gate_a_root = tmp_path / "gate-a"
    _write_gate_a_source_fixture_v3(gate_a_root, sources)
    immutable_config = tmp_path / "config.yml"
    immutable_config.touch(mode=0o600)
    os.truncate(immutable_config, 16_000_001)

    monkeypatch.setattr(gate_b_v3, "_GATE_B_PACKAGE_PARENT_V3", package_parent)
    monkeypatch.setattr(gate_b_v3, "_GATE_A_SOURCE_ROOT_V3", gate_a_root)
    monkeypatch.setattr(gate_b_v3, "_GATE_B_PROTECTED_PATHS_V3", (immutable_config,))
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_METADATA_ONLY_PROTECTED_PATHS_V3",
        frozenset(),
        raising=False,
    )

    with pytest.raises(
        gate_b_v3.GateBPackageErrorV3,
        match="source_file_metadata_invalid",
    ):
        gate_b_v3.materialize_gate_b_package_v3(package)


@pytest.fixture
def _actual_runtime_identity_fixture_v3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Path], object, dict[str, bytes]]:
    export_root = tmp_path / "gate-b-runtime"
    runtime_root = export_root / "runtime"
    module_path = runtime_root / "job_intel/product_search/gate_b_benchmark_v3.py"
    python_executable = export_root / "python-runtime/venv/bin/python"
    stdlib_root = export_root / "python-runtime/stdlib"
    dependency_lock = runtime_root / "uv.lock"
    installed_distributions = export_root / "python-runtime/installed-distributions.txt"
    module_path.parent.mkdir(parents=True)
    python_executable.parent.mkdir(parents=True)
    stdlib_root.mkdir(parents=True)
    module_path.write_bytes(b"reviewed runtime module\n")
    python_executable.write_bytes(b"pinned-cpython-3.12.13")
    (stdlib_root / "pathlib.py").write_bytes(b"reviewed stdlib\n")
    dependency_lock.write_bytes(b"version = 1\n")
    installed_distributions.write_bytes(b"pydantic==2.11.7\n")
    sys_path = [str(runtime_root), str(stdlib_root)]
    runtime_payloads = {
        "runtime_tree_manifest": gate_b_v3._tree_manifest_bytes_v3(runtime_root),
        "python_executable": python_executable.read_bytes(),
        "stdlib_tree_manifest": gate_b_v3._stdlib_tree_manifest_bytes_v3(stdlib_root),
        "dependency_lock": dependency_lock.read_bytes(),
        "installed_distributions": installed_distributions.read_bytes(),
        "sys_path": json.dumps(
            sys_path,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
    }
    manifest_type = getattr(gate_b_v3, "GateBRuntimeManifestV3")
    manifest = manifest_type(
        schema_version="3.0.0",
        runtime_kind="gate_b_at_most_once",
        candidate_commit="a" * 40,
        python_version="3.12.13",
        runtime_tree_sha256=hashlib.sha256(
            runtime_payloads["runtime_tree_manifest"]
        ).hexdigest(),
        python_executable_sha256=hashlib.sha256(
            runtime_payloads["python_executable"]
        ).hexdigest(),
        stdlib_tree_sha256=hashlib.sha256(
            runtime_payloads["stdlib_tree_manifest"]
        ).hexdigest(),
        dependency_lock_sha256=hashlib.sha256(
            runtime_payloads["dependency_lock"]
        ).hexdigest(),
        installed_distributions_sha256=hashlib.sha256(
            runtime_payloads["installed_distributions"]
        ).hexdigest(),
        sys_path_sha256=hashlib.sha256(runtime_payloads["sys_path"]).hexdigest(),
        editable_installs=(),
    )
    identity_root = export_root / "runtime-identity"
    identity_root.mkdir()
    (identity_root / "runtime-tree.json").write_bytes(
        runtime_payloads["runtime_tree_manifest"]
    )
    (identity_root / "stdlib-tree.json").write_bytes(
        runtime_payloads["stdlib_tree_manifest"]
    )
    (identity_root / "sys-path.json").write_bytes(runtime_payloads["sys_path"])
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    (export_root / "runtime-manifest.json").write_bytes(manifest_bytes)
    (export_root / "runtime-manifest.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "\n",
        encoding="ascii",
    )
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_RUNTIME_EXPORT_ROOT_V3",
        export_root,
        raising=False,
    )
    monkeypatch.setattr(gate_b_v3.sys, "executable", str(python_executable))
    monkeypatch.setattr(gate_b_v3.sys, "path", sys_path)
    monkeypatch.setattr(gate_b_v3.sys, "version_info", (3, 12, 13))
    monkeypatch.setattr(gate_b_v3, "__file__", str(module_path))
    monkeypatch.setattr(
        gate_b_v3.sysconfig,
        "get_path",
        lambda name: str(stdlib_root) if name == "stdlib" else None,
    )
    return (
        {
            "export_root": export_root,
            "runtime_tree_manifest": module_path,
            "python_executable": python_executable,
            "stdlib_tree_manifest": stdlib_root / "pathlib.py",
            "dependency_lock": dependency_lock,
            "installed_distributions": installed_distributions,
        },
        manifest,
        runtime_payloads,
    )


def _resize_runtime_python_fixture_v3(
    runtime_paths: dict[str, Path],
    manifest: object,
    *,
    size: int,
) -> object:
    python_executable = runtime_paths["python_executable"]
    os.truncate(python_executable, size)
    with python_executable.open("rb") as stream:
        executable_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    updated = manifest.model_copy(
        update={"python_executable_sha256": executable_sha256}
    )
    manifest_bytes = json.dumps(
        updated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    export_root = runtime_paths["export_root"]
    (export_root / "runtime-manifest.json").write_bytes(manifest_bytes)
    (export_root / "runtime-manifest.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "\n",
        encoding="ascii",
    )
    return updated


def test_runtime_identity_allows_pinned_30_mb_python_with_explicit_cap(
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    runtime_paths, manifest, _runtime_payloads = _actual_runtime_identity_fixture_v3
    expected = _resize_runtime_python_fixture_v3(
        runtime_paths,
        manifest,
        size=30_845_896,
    )

    observed = gate_b_v3._load_current_runtime_identity_v3()

    assert observed == expected


def test_runtime_identity_rejects_python_above_explicit_64_mb_cap(
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    runtime_paths, manifest, _runtime_payloads = _actual_runtime_identity_fixture_v3
    _resize_runtime_python_fixture_v3(
        runtime_paths,
        manifest,
        size=64_000_001,
    )

    with pytest.raises(
        gate_b_v3.GateBPackageErrorV3,
        match="source_file_metadata_invalid",
    ):
        gate_b_v3._load_current_runtime_identity_v3()


def _projection_hashes_v3(package: object) -> tuple[str, ...]:
    from job_intel.product_search.gate_b_evidence_v3 import (
        ReviewedFragmentAllowlistV3,
        project_vacancy_evidence_v3,
    )

    sources = _gate_b_source_bytes_v3()
    allowlist = ReviewedFragmentAllowlistV3.model_validate(
        yaml.safe_load(sources["reviewed_fragment_allowlist"])
    )
    hashes: list[str] = []
    for input_sha256 in package.ordered_input_sha256s:
        payload = json.loads(package.artifacts[f"task10-inputs/{input_sha256}.json"])
        projection = project_vacancy_evidence_v3(
            payload["source_record"],
            payload["raw"],
            allowlist,
        )
        hashes.append(
            hashlib.sha256(
                json.dumps(
                    projection.provider_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
        )
    return tuple(hashes)


def _launch_approval_fixture_v3(
    runtime_identity: tuple[object, dict[str, bytes]],
) -> tuple[
    object,
    object,
    dict[str, bytes],
    dict[str, object],
    object,
    object,
    object,
]:
    sources = _gate_b_source_bytes_v3()
    package = gate_b_v3.validate_gate_b_package_pure_v3(sources)
    runtime_manifest, runtime_payloads = runtime_identity
    package_index = json.loads(package.artifacts["package-index.json"])
    authority_sha256s = package_index["source_authority_sha256s"]
    policy = gate_b_v3.load_gate_b_benchmark_policy_v3(
        yaml.safe_load(sources["benchmark_policy"])
    )
    launch_type = getattr(gate_b_v3, "GateBLaunchBindingV3")
    launch = launch_type(
        schema_version="3.0.0",
        run_id=f"gate-b-at-most-once-{package.package_sha256[:16]}",
        candidate_commit=runtime_manifest.candidate_commit,
        runtime_manifest_sha256=runtime_manifest.canonical_sha256,
        package_manifest_sha256=package.package_sha256,
        ordered_input_sha256s=package.ordered_input_sha256s,
        ordered_projection_sha256s=_projection_hashes_v3(package),
        source_authority_sha256s=authority_sha256s,
        model_id="openai/gpt-5-mini",
        maximum_output_tokens=2_000,
        ordered_call_cap=48,
        per_call_maximum_usd=policy.per_call_maximum_usd,
        aggregate_maximum_usd=policy.aggregate_maximum_usd,
    )
    checkpoint_type = getattr(gate_b_v3, "GateBOwnerCheckpointManifestV3")
    checkpoint = checkpoint_type(
        schema_version="3.0.0",
        checkpoint_kind="gate_b_at_most_once_owner_approval",
        approved_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        launch_identity=launch,
    )
    receipt_type = getattr(gate_b_v3, "GateBOneTimeLaunchReceiptV3")
    receipt = receipt_type(
        schema_version="3.0.0",
        receipt_kind="gate_b_at_most_once_launch",
        launch_kind="initial",
        benchmark_run_id=launch.run_id,
        launch_attempt_id=f"{launch.run_id}-{'3' * 64}",
        issued_at=datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 20, 12, 31, tzinfo=timezone.utc),
        nonce="3" * 64,
        checkpoint_manifest_sha256=checkpoint.canonical_sha256,
        launch_identity_sha256=launch.canonical_sha256,
        candidate_commit=launch.candidate_commit,
        runtime_manifest_sha256=launch.runtime_manifest_sha256,
        package_manifest_sha256=launch.package_manifest_sha256,
        ordered_call_cap=launch.ordered_call_cap,
        per_call_maximum_usd=launch.per_call_maximum_usd,
        aggregate_maximum_usd=launch.aggregate_maximum_usd,
    )
    return (
        package,
        runtime_manifest,
        runtime_payloads,
        sources,
        checkpoint,
        receipt,
        launch,
    )


def test_launch_identity_recomputation_binds_current_package_runtime_and_approval(
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    _runtime_paths, actual_runtime_manifest, actual_runtime_payloads = (
        _actual_runtime_identity_fixture_v3
    )
    (
        package,
        _runtime_manifest,
        _runtime_payloads,
        _sources,
        checkpoint,
        receipt,
        expected,
    ) = _launch_approval_fixture_v3((actual_runtime_manifest, actual_runtime_payloads))

    observed = getattr(gate_b_v3, "recompute_launch_identity_v3")(
        package,
        checkpoint.model_dump(mode="json"),
        receipt.model_dump(mode="json"),
    )

    assert observed == expected
    assert len(observed.ordered_input_sha256s) == 48
    assert len(observed.ordered_projection_sha256s) == 48


def test_launch_identity_rejects_mutated_actual_export_with_replayed_runtime_snapshots(
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    runtime_paths, runtime_manifest, runtime_payloads = (
        _actual_runtime_identity_fixture_v3
    )
    (
        package,
        _runtime_manifest,
        _runtime_payloads,
        _sources,
        checkpoint,
        receipt,
        _expected,
    ) = _launch_approval_fixture_v3((runtime_manifest, runtime_payloads))
    assert tuple(
        inspect.signature(gate_b_v3.recompute_launch_identity_v3).parameters
    ) == (
        "package",
        "owner_checkpoint_payload",
        "launch_receipt_payload",
    )
    runtime_paths["runtime_tree_manifest"].write_bytes(
        b"mutated after manifest approval\n"
    )

    with pytest.raises(ValueError, match="runtime"):
        gate_b_v3.recompute_launch_identity_v3(
            package,
            checkpoint.model_dump(mode="json"),
            receipt.model_dump(mode="json"),
        )


@pytest.mark.parametrize(
    "source_name",
    [
        "benchmark_policy",
        "career_profile",
        "candidate_facts",
        "decision_contract",
        "product_sot",
        "search_contract",
        "semantic_contract",
        "task10_policy",
    ],
)
def test_launch_identity_rejects_stale_approval_after_authority_drift(
    source_name: str,
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    _runtime_paths, actual_runtime_manifest, actual_runtime_payloads = (
        _actual_runtime_identity_fixture_v3
    )
    (
        package,
        _runtime_manifest,
        _runtime_payloads,
        sources,
        checkpoint,
        receipt,
        _expected,
    ) = _launch_approval_fixture_v3((actual_runtime_manifest, actual_runtime_payloads))
    changed_sources = deepcopy(sources)
    changed_sources[source_name] += b"\n"

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            gate_b_v3,
            "load_gate_b_source_bytes_v3",
            lambda: changed_sources,
        )
        with pytest.raises(
            ValueError, match="identity|authority|package|policy|contract"
        ):
            gate_b_v3.recompute_launch_identity_v3(
                package,
                checkpoint.model_dump(mode="json"),
                receipt.model_dump(mode="json"),
            )


@pytest.mark.parametrize(
    "runtime_payload_name",
    [
        "runtime_tree_manifest",
        "python_executable",
        "stdlib_tree_manifest",
        "dependency_lock",
        "installed_distributions",
        "sys_path",
    ],
)
def test_launch_identity_rejects_runtime_payload_drift(
    runtime_payload_name: str,
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    runtime_paths, actual_runtime_manifest, actual_runtime_payloads = (
        _actual_runtime_identity_fixture_v3
    )
    (
        package,
        _runtime_manifest,
        _runtime_payloads,
        _sources,
        checkpoint,
        receipt,
        _expected,
    ) = _launch_approval_fixture_v3((actual_runtime_manifest, actual_runtime_payloads))
    if runtime_payload_name == "sys_path":
        gate_b_v3.sys.path.append(str(runtime_paths["export_root"] / "drift"))
    else:
        changed_path = runtime_paths[runtime_payload_name]
        changed_path.write_bytes(changed_path.read_bytes() + b"drift")

    with pytest.raises(ValueError, match="runtime"):
        gate_b_v3.recompute_launch_identity_v3(
            package,
            checkpoint.model_dump(mode="json"),
            receipt.model_dump(mode="json"),
        )


def test_launch_identity_rejects_candidate_commit_and_input_order_drift(
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    runtime_paths, actual_runtime_manifest, actual_runtime_payloads = (
        _actual_runtime_identity_fixture_v3
    )
    (
        package,
        runtime_manifest,
        _runtime_payloads,
        _sources,
        checkpoint,
        receipt,
        _expected,
    ) = _launch_approval_fixture_v3((actual_runtime_manifest, actual_runtime_payloads))
    changed_runtime_manifest = runtime_manifest.model_copy(
        update={"candidate_commit": "b" * 40}
    )
    reordered_package = replace(
        package,
        ordered_input_sha256s=tuple(reversed(package.ordered_input_sha256s)),
    )

    manifest_bytes = json.dumps(
        changed_runtime_manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    (runtime_paths["export_root"] / "runtime-manifest.json").write_bytes(manifest_bytes)
    (runtime_paths["export_root"] / "runtime-manifest.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "\n",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="runtime|approval|receipt|identity"):
        gate_b_v3.recompute_launch_identity_v3(
            package,
            checkpoint.model_dump(mode="json"),
            receipt.model_dump(mode="json"),
        )
    (runtime_paths["export_root"] / "runtime-manifest.json").write_bytes(
        json.dumps(
            runtime_manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    restored_manifest_bytes = (
        runtime_paths["export_root"] / "runtime-manifest.json"
    ).read_bytes()
    (runtime_paths["export_root"] / "runtime-manifest.sha256").write_text(
        hashlib.sha256(restored_manifest_bytes).hexdigest() + "\n",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="package|identity|order"):
        gate_b_v3.recompute_launch_identity_v3(
            reordered_package,
            checkpoint.model_dump(mode="json"),
            receipt.model_dump(mode="json"),
        )


@pytest.mark.parametrize(
    "authority_name",
    [
        "task10_prompt",
        "task10_prompt_version",
        "semantic_prompt",
        "semantic_prompt_version",
        "provider_output_schema",
        "model_id",
        "pricing",
        "launch_limits",
    ],
)
def test_launch_identity_rejects_derived_prompt_schema_model_pricing_or_cap_drift(
    authority_name: str,
    monkeypatch: pytest.MonkeyPatch,
    _actual_runtime_identity_fixture_v3: tuple[
        dict[str, Path], object, dict[str, bytes]
    ],
) -> None:
    _runtime_paths, actual_runtime_manifest, actual_runtime_payloads = (
        _actual_runtime_identity_fixture_v3
    )
    (
        package,
        _runtime_manifest,
        _runtime_payloads,
        _sources,
        checkpoint,
        receipt,
        _expected,
    ) = _launch_approval_fixture_v3((actual_runtime_manifest, actual_runtime_payloads))
    original = gate_b_v3._derive_launch_authority_sha256s_v3

    def changed(source_bytes: dict[str, object]) -> dict[str, str]:
        result = original(source_bytes)
        result[authority_name] = "f" * 64
        return result

    monkeypatch.setattr(gate_b_v3, "_derive_launch_authority_sha256s_v3", changed)

    with pytest.raises(ValueError, match="authority|package|identity"):
        gate_b_v3.recompute_launch_identity_v3(
            package,
            checkpoint.model_dump(mode="json"),
            receipt.model_dump(mode="json"),
        )


def test_one_time_launch_receipt_rejects_long_expiry_or_extra_authority() -> None:
    receipt_type = getattr(gate_b_v3, "GateBOneTimeLaunchReceiptV3")
    payload = {
        "schema_version": "3.0.0",
        "receipt_kind": "gate_b_at_most_once_launch",
        "launch_kind": "initial",
        "benchmark_run_id": "gate-b-at-most-once-" + "1" * 16,
        "launch_attempt_id": ("gate-b-at-most-once-" + "1" * 16 + "-" + "2" * 64),
        "issued_at": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 8, 20, 12, 30, 1, tzinfo=timezone.utc),
        "nonce": "2" * 64,
        "checkpoint_manifest_sha256": "3" * 64,
        "launch_identity_sha256": "4" * 64,
        "candidate_commit": "5" * 40,
        "runtime_manifest_sha256": "6" * 64,
        "package_manifest_sha256": "7" * 64,
        "ordered_call_cap": 48,
        "per_call_maximum_usd": Decimal("0.01"),
        "aggregate_maximum_usd": Decimal("0.48"),
    }

    with pytest.raises(ValidationError, match="30 minutes"):
        receipt_type.model_validate(payload)
    payload["expires_at"] = datetime(2026, 8, 20, 12, 29, 59, tzinfo=timezone.utc)
    with pytest.raises(ValidationError, match="exactly 30 minutes"):
        receipt_type.model_validate(payload)
    payload["expires_at"] = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)
    payload["provider_token"] = "forbidden"
    with pytest.raises(ValidationError, match="Extra inputs"):
        receipt_type.model_validate(payload)
