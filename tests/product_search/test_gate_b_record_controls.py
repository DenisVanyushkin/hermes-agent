from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import shutil
import socket
import sqlite3
import subprocess
from threading import Event, Thread
import time
from types import SimpleNamespace
from typing import Any

import pytest

import job_intel.product_search.gate_b as gate_b
from job_intel.product_search.evidence_synthesis import (
    RecordedEvidenceSynthesisProvider,
    load_evidence_synthesis_policy,
)
from job_intel.product_search.gate_b import (
    GateBBudgetLedger,
    GateBPreflightError,
    authorize_record_run,
    build_dry_run_preflight,
    governed_pricing_schedule,
    read_contained_nofollow,
    run_gate_b_record,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMProviderError,
    LLMObservationProvider,
    RecordingStore,
)


GATE_A_ROOT = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    "65d60daae16093a9a7e34a11a159e2f789dd14dd"
)
OWNER_CAPABILITY = "owner-random-fixture-capability-7c7ca978"


def _approval(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "status": "approved",
        "run_identity_sha256": preflight["record_identity_sha256"],
        "capability_sha256": hashlib.sha256(OWNER_CAPABILITY.encode()).hexdigest(),
        "exact_call_cap": 48,
        "exact_spend_cap_usd": "0.48",
        "max_cost_per_call_usd": "0.01",
        "pricing_sha256": preflight["record_identity"]["pricing_sha256"],
        "corpus_manifest_sha256": preflight["corpus"]["manifest_sha256"],
        "input_manifest_sha256": preflight["inputs"]["manifest_sha256"],
        "ordered_input_hashes_sha256": preflight["inputs"][
            "ordered_input_hashes_sha256"
        ],
        "max_output_tokens": preflight["record_identity"]["max_output_tokens"],
    }


def _preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path)
    return build_dry_run_preflight(gate_a_root=GATE_A_ROOT)


def _claim_test_runner(authorization: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    witness = gate_b.export_gate_b_launch_witness_request(authorization)
    monkeypatch.setattr(gate_b, "_read_privileged_launch_witness", lambda: witness)
    gate_b._claim_privileged_launch(authorization)


def _authorize_test_reconciliation(
    preflight: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Any:
    request = gate_b.build_gate_b_owner_reconciliation_request(preflight)
    monkeypatch.setattr(
        gate_b,
        "_read_privileged_reconciliation_witness",
        lambda: request,
    )
    return gate_b.authorize_gate_b_reconciliation(
        preflight,
        approval_record={
            **_approval(preflight),
            "owner_reconciliation_artifact": request,
        },
        owner_capability=OWNER_CAPABILITY,
    )


def _write_legacy_sqlite_ledger(authorization: Any) -> Path:
    """Create the exact populated private ledger used before the JSONL switch."""
    private_root = authorization.experiment_root / (
        f".gate-b-ledger-{authorization.run_identity_sha256[:16]}"
    )
    private_root.mkdir(mode=0o700)
    database = private_root / "ledger.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            "CREATE TABLE run_budget ("
            "run_identity_sha256 TEXT PRIMARY KEY, call_cap INTEGER NOT NULL, "
            "spend_cap_microusd INTEGER NOT NULL);"
            "CREATE TABLE call_ledger ("
            "input_hash TEXT PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL, "
            "reserved_microusd INTEGER NOT NULL, actual_microusd INTEGER, "
            "status TEXT NOT NULL CHECK(status IN "
            "('reserved','charge_unknown','success','failure')));"
            "CREATE TABLE run_inputs ("
            "ordinal INTEGER PRIMARY KEY, input_hash TEXT UNIQUE NOT NULL);"
            "CREATE TABLE charge_unknown_reconciliation ("
            "input_hash TEXT PRIMARY KEY, run_identity_sha256 TEXT NOT NULL, "
            "disposition TEXT NOT NULL, cost_semantics TEXT NOT NULL, "
            "actual_microusd INTEGER NOT NULL, "
            "provider_evidence_sha256 TEXT NOT NULL, "
            "owner_capability_sha256 TEXT NOT NULL, "
            "record_metadata_sha256 TEXT NOT NULL);"
        )
        connection.execute(
            "INSERT INTO run_budget VALUES (?, ?, ?)",
            (authorization.run_identity_sha256, 48, 480_000),
        )
        connection.executemany(
            "INSERT INTO run_inputs VALUES (?, ?)",
            enumerate(authorization.ordered_input_sha256s),
        )
        first, second, third = authorization.ordered_input_sha256s[:3]
        connection.executemany(
            "INSERT INTO call_ledger VALUES (?, ?, ?, ?, ?)",
            [
                (first, f"reservation:{first}", 10_000, None, "charge_unknown"),
                (second, f"reservation:{second}", 10_000, 1_250, "success"),
                (third, f"reservation:{third}", 10_000, 10_000, "failure"),
            ],
        )
        connection.execute(
            "INSERT INTO charge_unknown_reconciliation VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                third,
                authorization.run_identity_sha256,
                "charge_amount_unknown",
                "unknown_reserved_max",
                10_000,
                "d" * 64,
                authorization._owner_capability_sha256,
                "e" * 64,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    database.chmod(0o600)
    return database


def test_opaque_owner_capability_binds_exact_identity_and_exact_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path, monkeypatch)
    approval = _approval(preflight)
    authorized = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    assert authorized.exact_call_cap == 48
    assert authorized.exact_spend_cap_usd == Decimal("0.48")

    mutations = [
        ({**approval, "exact_call_cap": 49}, OWNER_CAPABILITY, "exact_caps"),
        (
            {**approval, "exact_spend_cap_usd": "0.49"},
            OWNER_CAPABILITY,
            "exact_caps",
        ),
        ({**approval, "run_identity_sha256": "0" * 64}, OWNER_CAPABILITY, "identity"),
        (approval, "approve-gate-b-record:self-generated", "capability"),
        (approval, None, "capability"),
    ]
    for mutated, capability, reason in mutations:
        with pytest.raises(GateBPreflightError, match=reason):
            authorize_record_run(
                preflight,
                approval_record=mutated,
                owner_capability=capability,
            )


def test_transactional_ledger_prevents_double_reserve_and_preserves_crash_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = (
        Path(preflight["corpus"]["manifest_path"]).parent / "run-ledger.sqlite3"
    )
    ledger = GateBBudgetLedger(ledger_path, authorization)
    barrier_hash = authorization.ordered_input_sha256s[0]

    def attempt() -> str:
        try:
            return ledger.reserve(barrier_hash, Decimal("0.010000"))
        except GateBPreflightError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(value.startswith("reservation:") for value in outcomes) == 1
    assert sum("input_already_reserved" in value for value in outcomes) == 1

    resumed = GateBBudgetLedger(ledger_path, authorization)
    with pytest.raises(GateBPreflightError, match="input_already_reserved"):
        resumed.reserve(barrier_hash, Decimal("0.010000"))
    snapshot = resumed.snapshot()
    assert snapshot["calls_reserved"] == 1
    assert snapshot["spend_reserved_usd"] == "0.010000"
    assert snapshot["outstanding_reserved_usd"] == "0.010000"
    assert snapshot["measured_actual_usd"] == "0.000000"
    assert snapshot["calls_completed"] == 0


def test_stale_inflight_without_record_can_retry_but_completed_call_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    first = ledger.reserve(input_hash, Decimal("0.010000"))

    assert ledger.retry_reserved_without_record(input_hash) is True
    second = ledger.reserve(input_hash, Decimal("0.010000"))
    assert second == first
    ledger.reconcile(second, Decimal("0.000000"), "failure")

    with pytest.raises(GateBPreflightError, match="completed_call_without_record"):
        ledger.retry_reserved_without_record(input_hash)


def test_post_dispatch_crash_requires_owner_reconciliation_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    witness = gate_b.export_gate_b_launch_witness_request(authorization)
    monkeypatch.setattr(gate_b, "_read_privileged_launch_witness", lambda: witness)

    class _CrashingCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs: Any) -> object:
            self.calls += 1
            if self.calls == 1:
                raise SystemExit("fixture crash after dispatch")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps({
                                "schema_version": "2.0.0",
                                "claims": [],
                                "conflicts": [],
                                "question_candidates": [],
                            }),
                            refusal=None,
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=10, total_tokens=20
                ),
                model="openai/gpt-5-mini",
            )

    completions = _CrashingCompletions()

    def build_fixture_provider(**kwargs: Any) -> LLMObservationProvider:
        return LLMObservationProvider(
            store=RecordingStore(kwargs["store_dir"]),
            mode="record",
            transport=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        )

    monkeypatch.setattr(gate_b, "build_live_llm_provider", build_fixture_provider)

    with pytest.raises(SystemExit, match="after dispatch"):
        run_gate_b_record(authorization=authorization)

    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    assert ledger.call_state(input_hash) == "charge_unknown"
    with pytest.raises(GateBPreflightError, match="runner_capability_consumed"):
        run_gate_b_record(authorization=authorization)
    assert completions.calls == 1

    evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": "charge_amount_unknown",
        "provider_evidence_sha256": "a" * 64,
    }
    with pytest.raises(
        GateBPreflightError,
        match="separate_owner_reconciliation_authorization_required",
    ):
        gate_b.reconcile_gate_b_charge_unknown(
            authorization=authorization,
            owner_capability=OWNER_CAPABILITY,
            input_hash=input_hash,
            disposition="charge_amount_unknown",
            measured_cost_usd=None,
            reconciliation_evidence=evidence,
        )
    authorization._run_authority.close()
    recovery = _authorize_test_reconciliation(preflight, monkeypatch)
    with pytest.raises(GateBPreflightError, match="owner_capability"):
        gate_b.reconcile_gate_b_charge_unknown(
            authorization=recovery,
            owner_capability="wrong-owner-capability",
            input_hash=input_hash,
            disposition="charge_amount_unknown",
            measured_cost_usd=None,
            reconciliation_evidence=evidence,
        )
    reconciliation = gate_b.reconcile_gate_b_charge_unknown(
        authorization=recovery,
        owner_capability=OWNER_CAPABILITY,
        input_hash=input_hash,
        disposition="charge_amount_unknown",
        measured_cost_usd=None,
        reconciliation_evidence=evidence,
    )
    assert reconciliation == {
        "status": "terminal_failure_recorded",
        "cost_semantics": "unknown_reserved_max",
        "cost_usd": "0.010000",
        "input_hash": input_hash,
        "record_replayed": False,
    }

    with pytest.raises(GateBPreflightError, match="runner_capability_consumed"):
        run_gate_b_record(authorization=authorization)
    assert completions.calls == 1
    recovery_ledger = GateBBudgetLedger(
        recovery.experiment_root / "run-ledger.sqlite3", recovery
    )
    assert recovery_ledger.call_state(input_hash) == "failure"
    assert recovery_ledger.reconciliation_for(input_hash) == {
        "disposition": "charge_amount_unknown",
        "cost_semantics": "unknown_reserved_max",
        "actual_cost_usd": "0.010000",
        "provider_evidence_sha256": "a" * 64,
        "run_identity_sha256": authorization.run_identity_sha256,
    }
    snapshot = recovery_ledger.snapshot()
    assert snapshot["calls_completed"] == 1
    assert snapshot["outstanding_reserved_usd"] == "0.000000"


def test_reconciliation_waits_across_same_content_manifest_inode_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    reservation = ledger.reserve(input_hash, Decimal("0.010000"))
    ledger.mark_dispatching(reservation)
    assert ledger.call_state(input_hash) == "charge_unknown"
    authorization._run_authority.close()

    request = gate_b.build_gate_b_owner_reconciliation_request(preflight)
    assert (
        request["observed_namespace_inventory"]["inventory_sha256"]
        == (request["observed_namespace_inventory_sha256"])
    )
    approval = {
        **_approval(preflight),
        "owner_reconciliation_artifact": request,
    }
    monkeypatch.setattr(
        gate_b,
        "_read_privileged_reconciliation_witness",
        lambda: request,
    )
    recovery = gate_b.authorize_gate_b_reconciliation(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )

    provider_calls = 0

    def forbidden_provider(**_: Any) -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("reconciliation authorization is offline-only")

    monkeypatch.setattr(gate_b, "build_live_llm_provider", forbidden_provider)
    with pytest.raises(GateBPreflightError, match="reconciliation.*offline"):
        run_gate_b_record(authorization=recovery)
    assert provider_calls == 0

    evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": recovery.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": "confirmed_unbilled",
        "provider_evidence_sha256": "c" * 64,
    }
    result = gate_b.reconcile_gate_b_charge_unknown(
        authorization=recovery,
        owner_capability=OWNER_CAPABILITY,
        input_hash=input_hash,
        disposition="confirmed_unbilled",
        measured_cost_usd=None,
        reconciliation_evidence=evidence,
    )
    assert result["status"] == "terminal_failure_recorded"
    assert result["cost_semantics"] == "confirmed_zero"


def test_reconciliation_artifact_must_bind_the_complete_current_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "artifact", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    authorization._run_authority.close()
    request = gate_b.build_gate_b_owner_reconciliation_request(preflight)

    incomplete = deepcopy(request)
    incomplete["observed_namespace_inventory"]["entries"].pop()
    with pytest.raises(
        GateBPreflightError, match="owner_reconciliation_artifact_mismatch"
    ):
        gate_b.authorize_gate_b_reconciliation(
            preflight,
            approval_record={
                **_approval(preflight),
                "owner_reconciliation_artifact": incomplete,
            },
            owner_capability=OWNER_CAPABILITY,
        )

    (authorization.experiment_root / "late-sidecar.json").write_text(
        "{}\n", encoding="utf-8"
    )
    with pytest.raises(
        GateBPreflightError, match="owner_reconciliation_artifact_mismatch"
    ):
        gate_b.authorize_gate_b_reconciliation(
            preflight,
            approval_record={
                **_approval(preflight),
                "owner_reconciliation_artifact": request,
            },
            owner_capability=OWNER_CAPABILITY,
        )


@pytest.mark.parametrize(
    ("disposition", "measured", "expected_semantics", "expected_cost"),
    [
        ("confirmed_unbilled", None, "confirmed_zero", "0.000000"),
        (
            "confirmed_charged_measured",
            Decimal("0.001250"),
            "measured",
            "0.001250",
        ),
        ("charge_amount_unknown", None, "unknown_reserved_max", "0.010000"),
    ],
)
def test_owner_reconciliation_has_closed_terminal_cost_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
    measured: Decimal | None,
    expected_semantics: str,
    expected_cost: str,
) -> None:
    preflight = _preflight(tmp_path / disposition, monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    reservation = ledger.reserve(input_hash, Decimal("0.010000"))
    ledger.mark_dispatching(reservation)
    authorization._run_authority.close()
    recovery = _authorize_test_reconciliation(preflight, monkeypatch)
    evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": recovery.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": disposition,
        "provider_evidence_sha256": "b" * 64,
    }

    result = gate_b.reconcile_gate_b_charge_unknown(
        authorization=recovery,
        owner_capability=OWNER_CAPABILITY,
        input_hash=input_hash,
        disposition=disposition,
        measured_cost_usd=measured,
        reconciliation_evidence=evidence,
    )

    assert result["cost_semantics"] == expected_semantics
    assert result["cost_usd"] == expected_cost
    recovery_ledger = GateBBudgetLedger(
        recovery.experiment_root / "run-ledger.sqlite3", recovery
    )
    assert recovery_ledger.call_state(input_hash) == "failure"
    replayed = gate_b.reconcile_gate_b_charge_unknown(
        authorization=recovery,
        owner_capability=OWNER_CAPABILITY,
        input_hash=input_hash,
        disposition=disposition,
        measured_cost_usd=measured,
        reconciliation_evidence=evidence,
    )
    assert replayed == {
        "status": "sealed_record_replayed",
        "cost_semantics": expected_semantics,
        "cost_usd": expected_cost,
        "input_hash": input_hash,
        "record_replayed": True,
    }


def test_ledger_reconciles_crash_after_owner_sealed_record_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    ledger.reserve(input_hash, Decimal("0.010000"))
    capability = ledger.structured_capability()
    record = capability.seal_record({
        "input_hash": input_hash,
        "status": "success",
        "cost_usd": "0.001250",
    })

    ledger.reconcile_existing_record(input_hash, record, capability)

    snapshot = ledger.snapshot()
    assert snapshot["calls_completed"] == 1
    assert snapshot["spend_reserved_usd"] == "0.001250"
    assert snapshot["outstanding_reserved_usd"] == "0.000000"
    assert snapshot["measured_actual_usd"] == "0.001250"


def test_runner_never_auto_resumes_a_preexisting_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    ledger.reserve(authorization.ordered_input_sha256s[0], Decimal("0.010000"))
    authorization._run_authority.close()
    with pytest.raises(GateBPreflightError, match="namespace_not_fresh"):
        authorize_record_run(
            preflight,
            approval_record=_approval(preflight),
            owner_capability=OWNER_CAPABILITY,
        )


def test_direct_task10_record_without_runner_capability_never_enters_transport(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class _Completions:
        def create(self, **kwargs: Any) -> object:
            calls.append(kwargs)
            raise AssertionError("transport must not be entered")

    semantic = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        transport=SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
    )
    with pytest.raises(ValueError, match="runner-issued capability"):
        RecordedEvidenceSynthesisProvider(
            semantic_provider=semantic,
            policy=load_evidence_synthesis_policy(),
        )
    assert calls == []


def test_nofollow_reader_rejects_traversal_absolute_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "gate-a"
    root.mkdir()
    inside = root / "raw.json"
    inside.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (root / "link.json").symlink_to(outside)

    assert read_contained_nofollow(root, "raw.json") == b"{}"
    for reference in ("../outside.json", str(outside), "link.json"):
        with pytest.raises(GateBPreflightError, match="contained_nofollow"):
            read_contained_nofollow(root, reference)

    relocated = tmp_path / "relocated-root"
    root.rename(relocated)
    root.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(GateBPreflightError, match="root_symlink"):
        read_contained_nofollow(root, "raw.json")


def test_ledger_rejects_final_symlink_without_mutating_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    external = tmp_path / "external.sqlite3"
    external.write_bytes(b"")
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger_path.symlink_to(external)

    with pytest.raises(
        GateBPreflightError, match="owner_reconciliation_artifact_required"
    ):
        GateBBudgetLedger(ledger_path, authorization)

    assert external.read_bytes() == b""


def test_ledger_uses_descriptor_journal_without_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    monkeypatch.setattr(
        gate_b.sqlite3,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("budget ledger must not use SQLite")
        ),
    )
    ledger = GateBBudgetLedger(ledger_path, authorization)
    assert ledger.snapshot()["calls_reserved"] == 0
    assert ledger_path.is_file()
    assert not list(authorization.experiment_root.glob(".gate-b-ledger-*"))


def test_ledger_creation_fsyncs_state_and_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    original_fsync = gate_b.os.fsync
    synced_modes: list[int] = []

    def tracked_fsync(descriptor: int) -> None:
        synced_modes.append(os.fstat(descriptor).st_mode)
        original_fsync(descriptor)

    monkeypatch.setattr(gate_b.os, "fsync", tracked_fsync)
    GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )

    assert any(gate_b.stat.S_ISREG(mode) for mode in synced_modes)
    assert any(gate_b.stat.S_ISDIR(mode) for mode in synced_modes)


def test_r3_identity_is_distinct_while_preserving_exact_inputs_and_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "r3-identity", monkeypatch)

    assert Path(preflight["inputs"]["package_root"]).name == "input-package-v2-r3"
    assert (
        preflight["record_identity"]["record_state_protocol_version"]
        == "gate-b-record-state-r3"
    )
    assert len(preflight["inputs"]["ordered_input_sha256s"]) == 48
    assert preflight["budget"]["exact_call_cap"] == 48
    assert preflight["budget"]["exact_spend_cap_usd"] == "0.48"
    summary = json.loads(
        (
            gate_b.REPO_ROOT
            / "docs/evidence/product-search-gate-b/benchmark-summary.json"
        ).read_text()
    )
    assert (
        gate_b._sha256_json(summary["record_identity"])
        == summary["record_identity_sha256"]
    )
    assert preflight["record_identity_sha256"] == summary["record_identity_sha256"]


def test_r3_rejects_state_bearing_legacy_sqlite_without_loading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "legacy-state", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger_path.touch(mode=0o600)
    legacy_database = _write_legacy_sqlite_ledger(authorization)
    legacy_bytes = legacy_database.read_bytes()
    monkeypatch.setattr(
        gate_b.sqlite3,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy SQLite must never be loaded")
        ),
    )

    with pytest.raises(
        GateBPreflightError,
        match="owner_reconciliation_artifact_required",
    ):
        GateBBudgetLedger(ledger_path, authorization)

    assert ledger_path.read_bytes() == b""
    assert legacy_database.read_bytes() == legacy_bytes


def test_r3_rejects_legacy_sqlite_restored_after_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "restored-legacy", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger = GateBBudgetLedger(ledger_path, authorization)
    legacy_database = _write_legacy_sqlite_ledger(authorization)
    legacy_bytes = legacy_database.read_bytes()

    with pytest.raises(
        GateBPreflightError,
        match="ledger_legacy_state_requires_owner_review",
    ):
        GateBBudgetLedger(ledger_path, authorization)
    with pytest.raises(
        GateBPreflightError,
        match="ledger_legacy_state_requires_owner_review",
    ):
        ledger.snapshot()

    assert legacy_database.read_bytes() == legacy_bytes


def test_unexpected_fresh_checkpoint_does_not_poison_correct_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "fresh-checkpoint", monkeypatch)
    approval = _approval(preflight)

    with pytest.raises(GateBPreflightError, match="historical_checkpoint_unsupported"):
        authorize_record_run(
            preflight,
            approval_record={**approval, "run_checkpoint": {}},
            owner_capability=OWNER_CAPABILITY,
        )

    authorization = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    assert authorization.run_identity_sha256 == preflight["record_identity_sha256"]


def test_empty_legacy_marker_blocks_first_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "empty-legacy", monkeypatch)
    approval = _approval(preflight)
    authorization = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger_path.touch(mode=0o600)

    with pytest.raises(
        GateBPreflightError,
        match="owner_reconciliation_artifact_required",
    ):
        GateBBudgetLedger(ledger_path, authorization)


def test_historical_checkpoint_cannot_resume_a_state_bearing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "restart-checkpoint", monkeypatch)
    approval = _approval(preflight)
    authorization = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger = GateBBudgetLedger(ledger_path, authorization)
    ledger.reserve(authorization.ordered_input_sha256s[0], Decimal("0.010000"))
    with pytest.raises(GateBPreflightError, match="historical_checkpoint_unsupported"):
        gate_b.export_gate_b_run_checkpoint(authorization)
    with pytest.raises(GateBPreflightError, match="historical_checkpoint_unsupported"):
        authorize_record_run(
            preflight,
            approval_record={**approval, "run_checkpoint": {}},
            owner_capability=OWNER_CAPABILITY,
        )


def test_total_same_uid_state_rollback_cannot_reuse_old_launch_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "coordinated-rollback", monkeypatch)
    approval = _approval(preflight)
    authorization = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    old_witness = gate_b.export_gate_b_launch_witness_request(authorization)
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger = GateBBudgetLedger(ledger_path, authorization)
    ledger.reserve(authorization.ordered_input_sha256s[0], Decimal("0.010000"))
    old_authority = authorization._run_authority
    authority_paths = [
        authorization.experiment_root / old_authority._authority_name,
        authorization.experiment_root / old_authority._authority_pin_name,
    ]
    ledger.close()
    old_authority.close()
    for path in (*authority_paths, ledger_path):
        path.unlink()

    replacement = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    provider_calls = 0

    def forbidden_provider(**_: Any) -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("rolled-back state must not reach provider")

    monkeypatch.setattr(gate_b, "build_live_llm_provider", forbidden_provider)
    monkeypatch.setattr(gate_b, "_read_privileged_launch_witness", lambda: old_witness)
    with pytest.raises(GateBPreflightError, match="launch_witness_mismatch"):
        run_gate_b_record(authorization=replacement)
    assert provider_calls == 0


@pytest.mark.parametrize("mutation", ["empty", "valid_prefix", "new_inode"])
def test_ledger_anchor_rejects_empty_prefix_and_new_inode_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    preflight = _preflight(tmp_path / mutation, monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    input_hash = authorization.ordered_input_sha256s[0]
    ledger = GateBBudgetLedger(ledger_path, authorization)
    reservation = ledger.reserve(input_hash, Decimal("0.010000"))
    reserved_prefix = ledger_path.read_bytes()
    ledger.mark_dispatching(reservation)
    ledger.close()

    if mutation == "empty":
        ledger_path.write_bytes(b"")
    elif mutation == "valid_prefix":
        ledger_path.write_bytes(reserved_prefix)
    else:
        displaced = ledger_path.with_name("run-ledger.displaced")
        ledger_path.rename(displaced)
        ledger_path.write_bytes(b"")
        ledger_path.chmod(0o600)

    with pytest.raises(GateBPreflightError, match="ledger_(authority|rollback|path)"):
        GateBBudgetLedger(ledger_path, authorization)


def test_ledger_promotes_exact_hmac_state_after_anchor_append_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "anchor-crash", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    input_hash = authorization.ordered_input_sha256s[0]
    ledger = GateBBudgetLedger(ledger_path, authorization)
    original_append = authorization._run_authority.append_ledger_anchor

    def crash_before_anchor(_: object) -> None:
        raise SystemExit("fixture crash before authority anchor")

    monkeypatch.setattr(
        authorization._run_authority,
        "append_ledger_anchor",
        crash_before_anchor,
    )
    with pytest.raises(SystemExit, match="before authority anchor"):
        ledger.reserve(input_hash, Decimal("0.010000"))
    monkeypatch.setattr(
        authorization._run_authority,
        "append_ledger_anchor",
        original_append,
    )

    resumed = GateBBudgetLedger(ledger_path, authorization)

    assert resumed.call_state(input_hash) == "reserved"
    assert resumed.snapshot()["spend_reserved_usd"] == "0.010000"


def test_unreadable_legacy_sqlite_fails_closed_without_initializing_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    private_root = authorization.experiment_root / (
        f".gate-b-ledger-{authorization.run_identity_sha256[:16]}"
    )
    private_root.mkdir(mode=0o700)
    database = private_root / "ledger.db"
    database.write_bytes(b"not-a-sqlite-ledger")
    database.chmod(0o600)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger_path.touch(mode=0o600)

    with pytest.raises(
        GateBPreflightError, match="owner_reconciliation_artifact_required"
    ):
        GateBBudgetLedger(ledger_path, authorization)

    assert ledger_path.read_bytes() == b""
    assert database.read_bytes() == b"not-a-sqlite-ledger"


def test_ledger_rejects_preexisting_hard_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    external = tmp_path / "external-ledger-state"
    external.write_bytes(b"")
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    os.link(external, ledger_path)

    with pytest.raises(
        GateBPreflightError, match="owner_reconciliation_artifact_required"
    ):
        GateBBudgetLedger(ledger_path, authorization)

    assert external.read_bytes() == b""


def test_open_ledger_remains_pinned_when_public_path_is_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger = GateBBudgetLedger(ledger_path, authorization)
    displaced = authorization.experiment_root / "run-ledger.displaced"
    external = tmp_path / "external-ledger-target"
    external.write_bytes(b"outside")
    ledger_path.rename(displaced)
    ledger_path.symlink_to(external)

    input_hash = authorization.ordered_input_sha256s[0]
    ledger.reserve(input_hash, Decimal("0.010000"))

    assert ledger.call_state(input_hash) == "reserved"
    assert external.read_bytes() == b"outside"
    ledger.close()
    ledger_path.unlink()
    displaced.rename(ledger_path)
    resumed = GateBBudgetLedger(ledger_path, authorization)
    assert resumed.call_state(input_hash) == "reserved"


def test_ledger_recovers_last_fsynced_state_from_torn_journal_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    input_hash = authorization.ordered_input_sha256s[0]
    ledger = GateBBudgetLedger(ledger_path, authorization)
    ledger.reserve(input_hash, Decimal("0.010000"))
    ledger.close()
    with ledger_path.open("ab") as journal:
        journal.write(b'{"partial"')

    resumed = GateBBudgetLedger(ledger_path, authorization)

    assert resumed.call_state(input_hash) == "reserved"
    assert ledger_path.read_bytes().endswith(b"\n")
    assert b'{"partial"' not in ledger_path.read_bytes()


def test_recording_store_remains_anchored_after_root_path_swap(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    store = RecordingStore(root)
    first_hash = "1" * 64
    second_hash = "2" * 64
    store.save_exclusive({"input_hash": first_hash})
    relocated = tmp_path / "recordings-relocated"
    root.rename(relocated)
    outside = tmp_path / "outside-recordings"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)

    store.save_exclusive({"input_hash": second_hash})

    assert (relocated / f"{second_hash}.json").is_file()
    assert not (outside / f"{second_hash}.json").exists()


def test_recording_load_rejects_nonregular_opened_descriptor_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RecordingStore(tmp_path)
    input_hash = "3" * 64
    read_descriptor, write_descriptor = os.pipe()
    os.write(write_descriptor, b"{}")
    os.close(write_descriptor)
    original_open = os.open

    def swapped_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if path == f"{input_hash}.json":
            assert flags & os.O_NONBLOCK
            return read_descriptor
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    with pytest.raises(LLMProviderError, match="recording_not_regular"):
        store.load(input_hash)


def test_package_publish_remains_anchored_after_subdirectory_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "package"
    outside = tmp_path / "outside"
    outside.mkdir()
    relocated = tmp_path / "vacancy-artifacts-relocated"
    payload = b'{"fixture":true}'
    original_link = gate_b.os.link
    swapped = False

    def swap_then_link(source: object, target: object, **kwargs: object) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            artifact_dir = package_root / "vacancy-artifacts"
            artifact_dir.rename(relocated)
            artifact_dir.symlink_to(outside, target_is_directory=True)
            (outside / Path(str(source)).name).write_bytes(payload)
        original_link(source, target, **kwargs)

    monkeypatch.setattr(gate_b.os, "link", swap_then_link)
    gate_b._secure_package_write(
        package_root=package_root,
        reference="vacancy-artifacts/fixture.json",
        payload=payload,
        boundary=gate_b.DryRunBoundary(),
    )

    assert (relocated / "fixture.json").read_bytes() == payload
    assert not (outside / "fixture.json").exists()


@pytest.mark.parametrize(
    "operation",
    [
        "provider",
        "network",
        "slack_credential",
        "production_write",
        "runtime_mutation",
        "protected_write",
    ],
)
def test_real_dry_preflight_denies_injected_forbidden_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path / operation)

    def attempt(boundary: object) -> None:
        getattr(boundary, operation)()

    with pytest.raises(GateBPreflightError, match=f"dry_run_forbidden:{operation}"):
        build_dry_run_preflight(gate_a_root=GATE_A_ROOT, boundary_attempt=attempt)


@pytest.mark.parametrize(
    "operation", ["socket", "subprocess", "outside_write", "thread_write"]
)
def test_dry_preflight_blocks_forbidden_io_at_the_actual_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    output_root = tmp_path / "allowed-output"
    outside = tmp_path / "outside.txt"
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)

    def attempt(_: object) -> None:
        if operation == "socket":
            with socket.socket():
                pass
        elif operation == "subprocess":
            subprocess.run(["true"], check=True)
        elif operation == "thread_write":
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(outside.write_text, "forbidden", encoding="utf-8").result()
        else:
            outside.write_text("forbidden", encoding="utf-8")

    with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
        build_dry_run_preflight(gate_a_root=GATE_A_ROOT, boundary_attempt=attempt)
    outside.unlink(missing_ok=True)


@pytest.mark.parametrize("operation", ["descriptor", "socket", "sqlite"])
def test_dry_preflight_neutralizes_preopened_outside_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    output_root = tmp_path / "allowed-output"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"unchanged")
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)
    cleanup: list[Any] = []
    if operation == "descriptor":
        descriptor = os.open(outside, os.O_WRONLY | os.O_APPEND)
        cleanup.append(lambda: os.close(descriptor))

        def attempt(_: object) -> None:
            os.write(descriptor, b"forbidden")

    elif operation == "socket":
        sender, receiver = socket.socketpair()
        receiver.setblocking(False)
        cleanup.extend((sender.close, receiver.close))

        def attempt(_: object) -> None:
            sender.sendall(b"forbidden")

    else:
        database = tmp_path / "outside.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE protected(value TEXT)")
        connection.commit()
        cleanup.append(connection.close)

        def attempt(_: object) -> None:
            connection.execute("INSERT INTO protected VALUES ('forbidden')")
            connection.commit()

    try:
        with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
            build_dry_run_preflight(gate_a_root=GATE_A_ROOT, boundary_attempt=attempt)
        assert outside.read_bytes() == b"unchanged"
        if operation == "socket":
            with pytest.raises(BlockingIOError):
                receiver.recv(1)
        if operation == "sqlite":
            assert connection.execute("SELECT COUNT(*) FROM protected").fetchone() == (
                0,
            )
    finally:
        for close in cleanup:
            close()


def test_dry_preflight_resolves_nested_symlink_containment_and_repo_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "allowed-output"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output_root / "nested-link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)

    def symlink_attempt(_: object) -> None:
        (output_root / "nested-link" / "escape.txt").write_text(
            "forbidden", encoding="utf-8"
        )

    with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
        build_dry_run_preflight(
            gate_a_root=GATE_A_ROOT, boundary_attempt=symlink_attempt
        )
    assert not (outside / "escape.txt").exists()

    def repo_secret_attempt(_: object) -> None:
        (gate_b.REPO_ROOT / "pyproject.toml").read_bytes()

    with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
        build_dry_run_preflight(
            gate_a_root=GATE_A_ROOT, boundary_attempt=repo_secret_attempt
        )


@pytest.mark.parametrize("descriptor", [0, 1, 2])
def test_dry_preflight_neutralizes_inherited_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor: int,
) -> None:
    output_root = tmp_path / f"stdio-{descriptor}"
    outside = tmp_path / f"stdio-{descriptor}.log"
    outside.write_bytes(b"unchanged")
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)
    saved = os.dup(descriptor)
    target = os.open(outside, os.O_WRONLY | os.O_APPEND)
    os.dup2(target, descriptor)

    def attempt(_: object) -> None:
        os.write(descriptor, b"forbidden")

    try:
        with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
            build_dry_run_preflight(
                gate_a_root=GATE_A_ROOT,
                boundary_attempt=attempt,
            )
    finally:
        os.dup2(saved, descriptor)
        os.close(saved)
        os.close(target)

    assert outside.read_bytes() == b"unchanged"


def test_dry_preflight_neutralizes_fd_opened_concurrently_before_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "concurrent-fd-output"
    outside = tmp_path / "concurrent-fd.log"
    outside.write_bytes(b"unchanged")
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)
    real_fork = os.fork
    start_open = Event()
    opened = Event()
    descriptors: list[int] = []

    def opener() -> None:
        assert start_open.wait(10)
        descriptors.append(os.open(outside, os.O_WRONLY | os.O_APPEND))
        opened.set()

    opener_thread = Thread(target=opener)
    opener_thread.start()

    def fork_after_concurrent_open() -> int:
        start_open.set()
        assert opened.wait(10)
        return real_fork()

    monkeypatch.setattr(gate_b.os, "fork", fork_after_concurrent_open)

    def attempt(_: object) -> None:
        os.write(descriptors[0], b"forbidden")

    try:
        with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
            build_dry_run_preflight(
                gate_a_root=GATE_A_ROOT,
                boundary_attempt=attempt,
            )
    finally:
        opener_thread.join(10)
        if descriptors:
            os.close(descriptors[0])

    assert outside.read_bytes() == b"unchanged"


def test_dry_preflight_keeps_policy_until_delayed_child_thread_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "delayed-thread-output"
    outside = tmp_path / "delayed-thread.log"
    outside.write_bytes(b"unchanged")
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)
    original_canonical_json = gate_b._canonical_json

    def yield_during_child_protocol(value: object) -> str:
        if isinstance(value, dict) and "ok" in value:
            time.sleep(0.2)
        return original_canonical_json(value)

    monkeypatch.setattr(gate_b, "_canonical_json", yield_during_child_protocol)

    def attempt(_: object) -> None:
        def delayed_write() -> None:
            while gate_b._ACTIVE_DRY_RUN_POLICY is not None:
                time.sleep(0.001)
            outside.write_text("forbidden", encoding="utf-8")

        Thread(target=delayed_write, daemon=True).start()

    with pytest.raises(GateBPreflightError, match="dry_run_child_thread"):
        build_dry_run_preflight(
            gate_a_root=GATE_A_ROOT,
            boundary_attempt=attempt,
        )

    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize("failure", ["serialization", "control_write"])
def test_dry_preflight_protocol_failure_never_releases_child_io_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    output_root = tmp_path / failure
    outside = tmp_path / f"{failure}.log"
    outside.write_bytes(b"unchanged")
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)
    original_canonical_json = gate_b._canonical_json
    original_write = gate_b.os.write

    if failure == "serialization":

        def fail_protocol_serialization(value: object) -> str:
            if isinstance(value, dict) and "ok" in value:
                raise RuntimeError("fixture protocol serialization failure")
            return original_canonical_json(value)

        monkeypatch.setattr(gate_b, "_canonical_json", fail_protocol_serialization)
    else:

        def fail_control_write(descriptor: int, payload: bytes) -> int:
            if b'"ok":' in payload and (
                b'"gate_b_error":' in payload or payload.startswith(b'{"ok":')
            ):
                raise OSError("fixture control write failure")
            return original_write(descriptor, payload)

        monkeypatch.setattr(gate_b.os, "write", fail_control_write)

    def attempt(_: object) -> None:
        def escape_after_policy_teardown() -> None:
            while gate_b._ACTIVE_DRY_RUN_POLICY is not None:
                time.sleep(0.001)
            outside.write_text("forbidden", encoding="utf-8")
            os._exit(97)

        Thread(target=escape_after_policy_teardown, daemon=True).start()

    with pytest.raises(GateBPreflightError, match="dry_run_child"):
        build_dry_run_preflight(
            gate_a_root=GATE_A_ROOT,
            boundary_attempt=attempt,
        )

    assert outside.read_bytes() == b"unchanged"


def test_dry_preflight_callback_has_no_fresh_path_io_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "callback-output"
    output_root.mkdir()
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", output_root)

    def attempt(_: object) -> None:
        (output_root / "callback.txt").write_text("forbidden", encoding="utf-8")

    with pytest.raises(GateBPreflightError, match="dry_run_io_denied"):
        build_dry_run_preflight(
            gate_a_root=GATE_A_ROOT,
            boundary_attempt=attempt,
        )


def test_dry_preflight_scrubs_slack_credentials_inside_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path / "output")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "fixture-secret-must-not-be-visible")
    observed: list[str | None] = []

    def attempt(_: object) -> None:
        observed.append(os.getenv("SLACK_BOT_TOKEN"))

    result = build_dry_run_preflight(
        gate_a_root=GATE_A_ROOT,
        boundary_attempt=attempt,
    )

    # The callback executes in the isolated child, so its parent closure is
    # intentionally not mutable even though the credential was absent there.
    assert observed == []
    assert result["side_effect_evidence"]["slack_credentials_scrubbed"] == 1
    assert os.environ["SLACK_BOT_TOKEN"] == "fixture-secret-must-not-be-visible"


def test_preflight_rejects_output_root_symlink_and_reports_measured_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", link)
    with pytest.raises(GateBPreflightError, match="workspace_symlink"):
        build_dry_run_preflight(gate_a_root=GATE_A_ROOT)

    isolated = tmp_path / "isolated"
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", isolated)
    result = build_dry_run_preflight(gate_a_root=GATE_A_ROOT)
    evidence = result["side_effect_evidence"]
    assert evidence["gate_a_files_read"] == 2416
    assert evidence["corpus_files_created"] == 1
    assert evidence["package_files_created"] == 97
    assert evidence["provider_attempts_denied"] == 0
    assert evidence["network_attempts_denied"] == 0
    files = [path for path in isolated.rglob("*") if path.is_file()]
    assert len(files) == 98
    assert Path(result["corpus"]["manifest_path"]) in files


def test_authorization_rehashes_corpus_and_rejects_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path, monkeypatch)
    manifest = Path(preflight["corpus"]["manifest_path"])
    manifest.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(GateBPreflightError, match="corpus_manifest_changed"):
        authorize_record_run(
            preflight,
            approval_record=_approval(preflight),
            owner_capability=OWNER_CAPABILITY,
        )


def test_single_record_runner_has_no_caller_provider_or_loader_bypass() -> None:
    assert set(inspect.signature(run_gate_b_record).parameters) == {"authorization"}


def test_authorization_is_read_only_and_issues_opaque_process_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "canonical", monkeypatch)
    before = sorted(
        (path.relative_to(tmp_path).as_posix(), path.stat().st_ino)
        for path in tmp_path.rglob("*")
    )

    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )

    after = sorted(
        (path.relative_to(tmp_path).as_posix(), path.stat().st_ino)
        for path in tmp_path.rglob("*")
    )
    assert after == before
    assert authorization._run_authority is None
    with pytest.raises(TypeError, match="process-bound"):
        pickle.dumps(authorization._runner_capability)
    monkeypatch.setattr(
        gate_b,
        "GATE_B_LAUNCH_WITNESS_PATH",
        tmp_path / "missing-root-witness-parent" / "launch.json",
    )
    with pytest.raises(GateBPreflightError, match="launch_witness_unavailable"):
        authorization._runner_capability.claim()
    with pytest.raises(GateBPreflightError, match="runner_capability_not_claimed"):
        GateBBudgetLedger(
            authorization.experiment_root / "run-ledger.sqlite3", authorization
        )
    assert (
        sorted(
            (path.relative_to(tmp_path).as_posix(), path.stat().st_ino)
            for path in tmp_path.rglob("*")
        )
        == before
    )


def test_authorization_rejects_byte_identical_sibling_package_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "canonical", monkeypatch)
    canonical_root = Path(preflight["corpus"]["manifest_path"]).parent
    sibling_root = canonical_root.with_name("byte-identical-sibling")
    shutil.copytree(canonical_root, sibling_root)
    sibling = deepcopy(preflight)
    sibling["corpus"]["manifest_path"] = str(sibling_root / "corpus-manifest.json")
    sibling["inputs"]["package_root"] = str(sibling_root / "input-package-v2-r3")
    sibling["inputs"]["manifest_path"] = str(
        sibling_root / "input-package-v2-r3" / "run-manifest.v2.json"
    )

    with pytest.raises(GateBPreflightError, match="canonical_path"):
        authorize_record_run(
            sibling,
            approval_record=_approval(sibling),
            owner_capability=OWNER_CAPABILITY,
        )


def test_historical_checkpoint_cannot_restart_one_shot_record_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "checkpoint", monkeypatch)
    approval = _approval(preflight)
    authorization = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    with pytest.raises(GateBPreflightError, match="historical_checkpoint_unsupported"):
        gate_b.export_gate_b_run_checkpoint(authorization)

    with pytest.raises(
        GateBPreflightError,
        match="historical_checkpoint_unsupported",
    ):
        authorize_record_run(
            preflight,
            approval_record={**approval, "run_checkpoint": {}},
            owner_capability=OWNER_CAPABILITY,
        )


def test_full_recording_inventory_blocks_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "recordings", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    witness = gate_b.export_gate_b_launch_witness_request(authorization)
    monkeypatch.setattr(gate_b, "_read_privileged_launch_witness", lambda: witness)
    recording_root = authorization.experiment_root / "recordings"
    recording_root.mkdir()
    late_hash = authorization.ordered_input_sha256s[-1]
    (recording_root / f"{late_hash}.json").write_text(
        '{"legacy":true}\n', encoding="utf-8"
    )
    factory_calls = 0

    def forbidden_factory(**_: Any) -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("provider factory must not be constructed")

    monkeypatch.setattr(gate_b, "build_live_llm_provider", forbidden_factory)

    with pytest.raises(
        GateBPreflightError,
        match="owner_reconciliation_artifact_required|namespace_not_fresh|recording",
    ):
        run_gate_b_record(authorization=authorization)
    assert factory_calls == 0


def test_manifest_hardlink_added_after_authorization_is_rejected_on_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "manifest-hardlink", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    hardlink = tmp_path / "manifest-hardlink-copy.json"
    os.link(authorization.input_manifest_path, hardlink)

    with pytest.raises(GateBPreflightError, match="input_manifest_lock_not_regular"):
        GateBBudgetLedger(
            authorization.experiment_root / "run-ledger.sqlite3", authorization
        )


def test_missing_privileged_launch_witness_blocks_before_provider_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "witness", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    witness_path = tmp_path / "not-installed" / "launch-witness.json"
    monkeypatch.setattr(gate_b, "GATE_B_LAUNCH_WITNESS_PATH", witness_path)
    factory_calls = 0

    def forbidden_factory(**_: Any) -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("provider factory must not be constructed")

    monkeypatch.setattr(gate_b, "build_live_llm_provider", forbidden_factory)

    with pytest.raises(GateBPreflightError, match="launch_witness_unavailable"):
        run_gate_b_record(authorization=authorization)
    assert factory_calls == 0


def test_runner_capability_is_one_shot_and_root_aba_is_detected_before_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "aba", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    request = gate_b.export_gate_b_launch_witness_request(authorization)
    monkeypatch.setattr(
        gate_b,
        "_read_privileged_launch_witness",
        lambda: request,
    )
    canonical_root = authorization.experiment_root
    displaced = canonical_root.with_name("displaced-canonical-root")
    canonical_root.rename(displaced)
    shutil.copytree(displaced, canonical_root)
    factory_calls = 0

    def forbidden_factory(**_: Any) -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("provider factory must not be constructed")

    monkeypatch.setattr(gate_b, "build_live_llm_provider", forbidden_factory)

    with pytest.raises(GateBPreflightError, match="namespace_inventory_changed"):
        run_gate_b_record(authorization=authorization)
    with pytest.raises(GateBPreflightError, match="runner_capability_consumed"):
        run_gate_b_record(authorization=authorization)
    assert factory_calls == 0


@pytest.mark.parametrize(
    "artifact",
    [
        ".gate-b-ledger-r1/ledger.db",
        ".gate-b-run-authority-r2.state",
        ".gate-b-r3-run-authority-deadbeef.state",
        "run-ledger.sqlite3-wal",
        "legacy-no-live-run-receipt.json",
        "recordings/deadbeef.json",
    ],
)
def test_whole_canonical_namespace_inventory_rejects_every_unplanned_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
) -> None:
    preflight = _preflight(tmp_path / artifact.replace("/", "-"), monkeypatch)
    target = Path(preflight["corpus"]["manifest_path"]).parent / artifact
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"legacy-or-partial-state")

    with pytest.raises(
        GateBPreflightError,
        match="owner_reconciliation_artifact_required|namespace_not_fresh",
    ):
        authorize_record_run(
            preflight,
            approval_record=_approval(preflight),
            owner_capability=OWNER_CAPABILITY,
        )


def test_process_capability_has_no_caller_accessible_claim_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "issuer", monkeypatch)
    authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )

    assert not hasattr(gate_b, "_RUNNER_CAPABILITY_ISSUER")


def test_process_capability_cannot_trust_caller_supplied_stale_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "stale-witness", monkeypatch)
    first = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    second = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    stale_witness = gate_b.export_gate_b_launch_witness_request(first)
    monkeypatch.setattr(
        gate_b, "_read_privileged_launch_witness", lambda: stale_witness
    )
    claim_parameters = inspect.signature(
        type(second._runner_capability).claim
    ).parameters

    def direct_claim() -> None:
        if "expected_witness" in claim_parameters:
            second._runner_capability.claim(expected_witness=stale_witness)
        else:
            second._runner_capability.claim()

    with pytest.raises(GateBPreflightError, match="launch_witness_mismatch"):
        direct_claim()
    assert set(claim_parameters) == {"self"}
    with pytest.raises(GateBPreflightError, match="runner_capability_not_claimed"):
        GateBBudgetLedger(second.experiment_root / "run-ledger.sqlite3", second)


def test_self_appended_reconciliation_request_is_not_owner_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "separate-approval", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    reservation = ledger.reserve(input_hash, Decimal("0.010000"))
    ledger.mark_dispatching(reservation)
    authorization._run_authority.close()
    request = gate_b.build_gate_b_owner_reconciliation_request(preflight)
    monkeypatch.setattr(
        gate_b,
        "GATE_B_RECONCILIATION_WITNESS_PATH",
        tmp_path / "not-owner-installed" / "r3-reconciliation.json",
        raising=False,
    )

    with pytest.raises(GateBPreflightError, match="reconciliation_witness_unavailable"):
        gate_b.authorize_gate_b_reconciliation(
            preflight,
            approval_record={
                **_approval(preflight),
                "owner_reconciliation_artifact": request,
            },
            owner_capability=OWNER_CAPABILITY,
        )


def _reconciliation_ledger_and_record_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, GateBBudgetLedger, Any, str]:
    preflight = _preflight(tmp_path, monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    record_capability = ledger.structured_capability()
    input_hash = authorization.ordered_input_sha256s[0]
    reservation = ledger.reserve(input_hash, Decimal("0.010000"))
    ledger.mark_dispatching(reservation)
    authorization._run_authority.close()
    request = gate_b.build_gate_b_owner_reconciliation_request(preflight)
    monkeypatch.setattr(
        gate_b,
        "_read_privileged_reconciliation_witness",
        lambda: request,
        raising=False,
    )
    recovery = gate_b.authorize_gate_b_reconciliation(
        preflight,
        approval_record={
            **_approval(preflight),
            "owner_reconciliation_artifact": request,
        },
        owner_capability=OWNER_CAPABILITY,
    )
    recovery_ledger = GateBBudgetLedger(
        recovery.experiment_root / "run-ledger.sqlite3", recovery
    )
    return recovery, recovery_ledger, record_capability, input_hash


def test_reconciliation_cannot_issue_generic_record_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, recovery_ledger, _, _ = _reconciliation_ledger_and_record_capability(
        tmp_path / "generic-capability", monkeypatch
    )

    with pytest.raises(GateBPreflightError, match="reconciliation.*offline"):
        recovery_ledger.structured_capability()


def test_reconciliation_rejects_caller_sealed_ordinary_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, recovery_ledger, record_capability, input_hash = (
        _reconciliation_ledger_and_record_capability(
            tmp_path / "ordinary-record", monkeypatch
        )
    )
    fabricated = record_capability.seal_record({
        "input_hash": input_hash,
        "status": "success",
        "cost_usd": "0.001250",
    })

    with pytest.raises(
        GateBPreflightError, match="reconciliation_governed_api_required"
    ):
        recovery_ledger.reconcile_existing_record(
            input_hash, fabricated, record_capability
        )


def test_canonical_namespace_root_must_be_owner_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "unsafe-root", monkeypatch)
    canonical_root = Path(preflight["corpus"]["manifest_path"]).parent
    canonical_root.chmod(0o777)

    with pytest.raises(GateBPreflightError, match="namespace_root_unsafe"):
        authorize_record_run(
            preflight,
            approval_record=_approval(preflight),
            owner_capability=OWNER_CAPABILITY,
        )
