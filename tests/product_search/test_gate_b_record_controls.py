from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path
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
    ledger_path = Path(preflight["corpus"]["manifest_path"]).parent / "run-ledger.sqlite3"
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
    record = capability.seal_record(
        {
            "input_hash": input_hash,
            "status": "success",
            "cost_usd": "0.001250",
        }
    )

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
                            content=json.dumps(
                                {
                                    "schema_version": "2.0.0",
                                    "claims": [],
                                    "conflicts": [],
                                    "question_candidates": [],
                                }
                            ),
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
