from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
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
    semantic = LLMObservationProvider(
        store=RecordingStore(authorization.experiment_root / "recordings"),
        mode="record",
        transport=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    monkeypatch.setattr(gate_b, "build_live_llm_provider", lambda **_: semantic)

    with pytest.raises(SystemExit, match="after dispatch"):
        run_gate_b_record(authorization=authorization)

    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    assert ledger.call_state(input_hash) == "charge_unknown"
    with pytest.raises(GateBPreflightError, match="owner_reconciliation_required"):
        run_gate_b_record(authorization=authorization)
    assert completions.calls == 1

    evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": "charge_amount_unknown",
        "provider_evidence_sha256": "a" * 64,
    }
    with pytest.raises(GateBPreflightError, match="owner_capability"):
        gate_b.reconcile_gate_b_charge_unknown(
            authorization=authorization,
            owner_capability="wrong-owner-capability",
            input_hash=input_hash,
            disposition="charge_amount_unknown",
            measured_cost_usd=None,
            reconciliation_evidence=evidence,
        )
    reconciliation = gate_b.reconcile_gate_b_charge_unknown(
        authorization=authorization,
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

    results = run_gate_b_record(authorization=authorization)
    assert len(results) == 48
    assert completions.calls == 48
    assert ledger.call_state(input_hash) == "failure"
    assert ledger.reconciliation_for(input_hash) == {
        "disposition": "charge_amount_unknown",
        "cost_semantics": "unknown_reserved_max",
        "actual_cost_usd": "0.010000",
        "provider_evidence_sha256": "a" * 64,
        "run_identity_sha256": authorization.run_identity_sha256,
    }
    snapshot = ledger.snapshot()
    assert snapshot["calls_completed"] == 48
    assert snapshot["outstanding_reserved_usd"] == "0.000000"


def test_reconciliation_waits_for_live_runner_manifest_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    entered_transport = Event()
    release_transport = Event()

    class _BlockingCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_: Any) -> object:
            self.calls += 1
            if self.calls == 1:
                entered_transport.set()
                assert release_transport.wait(10)
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

    completions = _BlockingCompletions()
    semantic = LLMObservationProvider(
        store=RecordingStore(authorization.experiment_root / "recordings"),
        mode="record",
        transport=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    monkeypatch.setattr(gate_b, "build_live_llm_provider", lambda **_: semantic)
    runner_errors: list[BaseException] = []
    reconciliation_errors: list[BaseException] = []
    reconciliation_results: list[dict[str, Any]] = []

    def run_live() -> None:
        try:
            run_gate_b_record(authorization=authorization)
        except BaseException as exc:
            runner_errors.append(exc)

    input_hash = authorization.ordered_input_sha256s[0]
    evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": "confirmed_unbilled",
        "provider_evidence_sha256": "c" * 64,
    }

    def reconcile() -> None:
        try:
            reconciliation_results.append(
                gate_b.reconcile_gate_b_charge_unknown(
                    authorization=authorization,
                    owner_capability=OWNER_CAPABILITY,
                    input_hash=input_hash,
                    disposition="confirmed_unbilled",
                    measured_cost_usd=None,
                    reconciliation_evidence=evidence,
                )
            )
        except BaseException as exc:
            reconciliation_errors.append(exc)

    runner = Thread(target=run_live)
    recovery = Thread(target=reconcile)
    runner.start()
    assert entered_transport.wait(10)
    recovery.start()
    recovery.join(0.5)
    recovery_was_serialized = recovery.is_alive()
    release_transport.set()
    runner.join(20)
    recovery.join(20)

    assert recovery_was_serialized
    assert not runner.is_alive()
    assert not recovery.is_alive()
    assert runner_errors == []
    assert reconciliation_errors == []
    assert reconciliation_results[0]["status"] == "sealed_record_replayed"
    assert reconciliation_results[0]["cost_semantics"] == "measured"
    assert completions.calls == 48


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
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    input_hash = authorization.ordered_input_sha256s[0]
    reservation = ledger.reserve(input_hash, Decimal("0.010000"))
    ledger.mark_dispatching(reservation)
    evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": disposition,
        "provider_evidence_sha256": "b" * 64,
    }

    result = gate_b.reconcile_gate_b_charge_unknown(
        authorization=authorization,
        owner_capability=OWNER_CAPABILITY,
        input_hash=input_hash,
        disposition=disposition,
        measured_cost_usd=measured,
        reconciliation_evidence=evidence,
    )

    assert result["cost_semantics"] == expected_semantics
    assert result["cost_usd"] == expected_cost
    assert ledger.call_state(input_hash) == "failure"
    replayed = gate_b.reconcile_gate_b_charge_unknown(
        authorization=authorization,
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


def test_runner_retries_stale_reservation_and_never_duplicates_completed_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    ledger.reserve(authorization.ordered_input_sha256s[0], Decimal("0.010000"))

    class _Completions:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> object:
            self.calls.append(kwargs)
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

    completions = _Completions()
    semantic = LLMObservationProvider(
        store=RecordingStore(authorization.experiment_root / "recordings"),
        mode="record",
        transport=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    monkeypatch.setattr(gate_b, "build_live_llm_provider", lambda **_: semantic)

    first = run_gate_b_record(authorization=authorization)
    second = run_gate_b_record(authorization=authorization)

    assert len(first) == len(second) == 48
    assert len(completions.calls) == 48
    snapshot = ledger.snapshot()
    assert snapshot["calls_completed"] == 48


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
    external = tmp_path / "external.sqlite3"
    external.write_bytes(b"")
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    ledger_path.symlink_to(external)

    with pytest.raises(GateBPreflightError, match="ledger.*symlink"):
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


def test_ledger_eliminates_private_path_swap_connect_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    private_root = authorization.experiment_root / (
        f".gate-b-ledger-{authorization.run_identity_sha256[:16]}"
    )
    private_root.mkdir(mode=0o700)
    database = private_root / "ledger.db"
    database.write_bytes(b"")
    displaced = private_root / "ledger.displaced"
    external = tmp_path / "aba-target.sqlite3"
    external.write_bytes(b"")
    original_connect = gate_b.sqlite3.connect
    swapped = False

    def aba_connect(database: object, *args: object, **kwargs: object) -> object:
        nonlocal swapped
        if not swapped:
            swapped = True
            database_path = private_root / "ledger.db"
            database_path.rename(displaced)
            database_path.symlink_to(external)
            connection = original_connect(database, *args, **kwargs)
            database_path.unlink()
            displaced.rename(database_path)
            return connection
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(gate_b.sqlite3, "connect", aba_connect)
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )

    assert ledger.snapshot()["calls_reserved"] == 0
    assert swapped is False
    assert external.read_bytes() == b""


def test_ledger_rejects_preexisting_hard_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "experiment", monkeypatch)
    authorization = authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    external = tmp_path / "external-ledger-state"
    external.write_bytes(b"")
    ledger_path = authorization.experiment_root / "run-ledger.sqlite3"
    os.link(external, ledger_path)

    with pytest.raises(GateBPreflightError, match="ledger_path_unsafe"):
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
