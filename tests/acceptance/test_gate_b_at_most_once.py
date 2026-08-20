from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

import job_intel.product_search.gate_b_benchmark_v3 as gate_b_v3
from job_intel.product_search.gate_b_evidence_v3 import (
    ReviewedFragmentAllowlistV3,
    project_vacancy_evidence_v3,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    GovernedStructuredResult,
    GovernedStructuredTerminalUnknown,
    LLMProviderError,
)


OWNER_PRIVATE_KEY = bytes.fromhex("11" * 32)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _package_and_launch() -> tuple[object, object, object, object]:
    sources = gate_b_v3.load_gate_b_source_bytes_v3()
    package = gate_b_v3.validate_gate_b_package_pure_v3(sources)
    allowlist = ReviewedFragmentAllowlistV3.model_validate(
        yaml.safe_load(sources["reviewed_fragment_allowlist"])
    )
    index = json.loads(package.artifacts["package-index.json"])
    projections: list[str] = []
    for input_sha256 in package.ordered_input_sha256s:
        payload = json.loads(
            package.artifacts[f"task10-inputs/{input_sha256}.json"]
        )
        projection = project_vacancy_evidence_v3(
            payload["source_record"],
            payload["raw"],
            allowlist,
        )
        projections.append(
            hashlib.sha256(_canonical(projection.provider_payload())).hexdigest()
        )
    launch = gate_b_v3.GateBLaunchBindingV3(
        schema_version="3.0.0",
        run_id=f"gate-b-at-most-once-{package.package_sha256[:16]}",
        candidate_commit="a1c1f5e2ea26ea066d3aa07b7a9f107692121545",
        runtime_manifest_sha256="1" * 64,
        package_manifest_sha256=package.package_sha256,
        ordered_input_sha256s=package.ordered_input_sha256s,
        ordered_projection_sha256s=tuple(projections),
        source_authority_sha256s=index["source_authority_sha256s"],
        model_id="openai/gpt-5-mini",
        maximum_output_tokens=2_000,
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    checkpoint = gate_b_v3.GateBOwnerCheckpointManifestV3(
        schema_version="3.0.0",
        checkpoint_kind="gate_b_at_most_once_owner_approval",
        approved_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        launch_identity=launch,
    )
    receipt = gate_b_v3.GateBOneTimeLaunchReceiptV3(
        schema_version="3.0.0",
        receipt_kind="gate_b_at_most_once_launch",
        run_id=launch.run_id,
        issued_at=datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 20, 12, 31, tzinfo=timezone.utc),
        nonce="3" * 64,
        checkpoint_manifest_sha256=checkpoint.canonical_sha256,
        launch_identity_sha256=launch.canonical_sha256,
        candidate_commit=launch.candidate_commit,
        runtime_manifest_sha256=launch.runtime_manifest_sha256,
        package_manifest_sha256=launch.package_manifest_sha256,
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    return package, launch, checkpoint, receipt


def _valid_payload(user_payload: dict[str, object]) -> dict[str, object]:
    dimensions = (
        "feasibility",
        "mandate_fit",
        "company_fit",
        "transferability",
        "career_value",
        "evidence_confidence",
    )
    fragments = user_payload["fragments"]
    assert isinstance(fragments, list)
    claims: list[dict[str, object]] = []
    for dimension in dimensions:
        selected: tuple[dict[str, object], dict[str, object]] | None = None
        for fragment in fragments:
            assert isinstance(fragment, dict)
            allowed_claims = fragment["allowed_claims"]
            assert isinstance(allowed_claims, list)
            for allowed in allowed_claims:
                assert isinstance(allowed, dict)
                if allowed["dimension"] == dimension:
                    selected = fragment, allowed
                    break
            if selected is not None:
                break
        assert selected is not None
        fragment, allowed = selected
        claims.append(
            {
                "claim_id": f"claim-{dimension}",
                "dimension": dimension,
                "status": allowed["status"],
                "claim_code": allowed["claim_code"],
                "statement": allowed["statement"],
                "citations": [fragment["fragment_id"]],
            }
        )
    return {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflicts": [],
        "question_candidates": [],
    }


class _DeterministicProvider:
    def __init__(self, capability: object, dispatches: list[str]) -> None:
        self.capability = capability
        self.dispatches = dispatches
        self.last_call_metadata: dict[str, object] = {}

    def governed_structured_call(self, *, request: object, capability: object) -> object:
        assert capability is self.capability
        ordinal = len(self.dispatches)
        input_hash = request.input_hash
        reservation_id = capability.reserve(input_hash)
        capability.mark_dispatching(reservation_id)
        self.dispatches.append(input_hash)
        provider_record_sha256 = hashlib.sha256(
            f"provider-record-{ordinal}".encode("ascii")
        ).hexdigest()
        if ordinal == 7:
            terminal = "terminal_failure"
            measured_cost = Decimal("0.001")
            conservative_cost = measured_cost
        elif ordinal == 19:
            terminal = "terminal_unknown"
            measured_cost = None
            conservative_cost = Decimal("0.01")
        else:
            terminal = "success"
            measured_cost = Decimal("0.001")
            conservative_cost = measured_cost
        capability.reconcile(reservation_id, conservative_cost, terminal)
        self.last_call_metadata = {
            "post_dispatch_outcome_v3": terminal,
            "measured_cost_usd": (
                None if measured_cost is None else str(measured_cost)
            ),
            "conservative_cost_usd": str(conservative_cost),
            "sealed_provider_record_sha256": provider_record_sha256,
        }
        if terminal == "terminal_unknown":
            return GovernedStructuredTerminalUnknown(
                raw_response_text="",
                latency_ms=1,
                failure_code="timeout",
                failure_diagnostic="deterministic_fake",
                conservative_cost_usd=conservative_cost,
                record={},
            )
        if terminal == "terminal_failure":
            raise LLMProviderError("schema_invalid", "deterministic_fake")
        payload = _valid_payload(request.user_payload)
        assert request.response_validator(payload) is None
        return GovernedStructuredResult(
            raw_response_text=_canonical(payload).decode("utf-8"),
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            cost_usd=measured_cost,
            latency_ms=1,
            response_model="openai/gpt-5-mini",
            record={},
        )


def test_gate_b_runner_dispatches_each_of_48_inputs_at_most_once_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, launch, checkpoint, receipt = _package_and_launch()
    preflight_events: list[str] = []
    dispatches: list[str] = []
    provider_factories: list[str] = []

    def recompute(package_arg: object, checkpoint_arg: object, receipt_arg: object) -> object:
        assert package_arg is package
        assert checkpoint_arg == checkpoint.model_dump(mode="json")
        assert receipt_arg == receipt.model_dump(mode="json")
        preflight_events.append("preflight")
        return launch

    def provider_factory(*, recordings_root: Path, capability: object, **_: object) -> object:
        assert preflight_events
        assert recordings_root == tmp_path / "provider-recordings"
        provider_factories.append("factory")
        return _DeterministicProvider(capability, dispatches)

    monkeypatch.setattr(gate_b_v3, "recompute_launch_identity_v3", recompute)
    monkeypatch.setattr(gate_b_v3, "_build_gate_b_provider_v3", provider_factory)

    summary = gate_b_v3.run_gate_b_at_most_once_v3(
        package=package,
        owner_checkpoint_payload=checkpoint.model_dump(mode="json"),
        launch_receipt_payload=receipt.model_dump(mode="json"),
        ledger_root=tmp_path / "ledger",
        recordings_root=tmp_path / "provider-recordings",
        owner_recovery_public_key=gate_b_v3.Ed25519PrivateKey.from_private_bytes(
            OWNER_PRIVATE_KEY
        ).public_key().public_bytes_raw(),
        now=datetime(2026, 8, 20, 12, 10, tzinfo=timezone.utc),
    )

    assert provider_factories == ["factory"]
    assert len(preflight_events) == 49
    assert dispatches == list(launch.ordered_projection_sha256s)
    assert len(dispatches) == len(set(dispatches)) == 48
    assert summary.attempted_count == 48
    assert summary.success_count == 46
    assert summary.terminal_failure_count == 1
    assert summary.terminal_unknown_count == 1
    assert summary.pending_count == 0
    assert summary.conservative_spend_usd == Decimal("0.057")
    assert summary.rows[19].measured_cost_usd is None
    assert summary.rows[19].conservative_cost_usd == Decimal("0.01")
    assert all(row.state.value != "dispatched" for row in summary.rows)


def test_reserved_row_requires_owner_recovery_before_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, launch, checkpoint, receipt = _package_and_launch()
    manifest = gate_b_v3.GateBPackageManifestV3.model_validate_json(
        package.manifest_bytes
    )
    ledger_identity = gate_b_v3.GateBLaunchIdentityV3(
        schema_version="3.0.0",
        run_id=launch.run_id,
        issued_at=receipt.issued_at,
        package_manifest_sha256=package.package_sha256,
    )
    public_key = gate_b_v3.Ed25519PrivateKey.from_private_bytes(
        OWNER_PRIVATE_KEY
    ).public_key().public_bytes_raw()
    with gate_b_v3.GateBLedgerV3(
        tmp_path / "ledger",
        ledger_identity,
        manifest,
        owner_recovery_public_key=public_key,
    ) as ledger:
        ledger.reserve(0)
    monkeypatch.setattr(
        gate_b_v3,
        "recompute_launch_identity_v3",
        lambda *_args, **_kwargs: launch,
    )
    monkeypatch.setattr(
        gate_b_v3,
        "_build_gate_b_provider_v3",
        lambda **_kwargs: pytest.fail("provider must not be constructed"),
    )

    with pytest.raises(ValueError, match="owner_recovery_required"):
        gate_b_v3.run_gate_b_at_most_once_v3(
            package=package,
            owner_checkpoint_payload=checkpoint.model_dump(mode="json"),
            launch_receipt_payload=receipt.model_dump(mode="json"),
            ledger_root=tmp_path / "ledger",
            recordings_root=tmp_path / "provider-recordings",
            owner_recovery_public_key=public_key,
            now=datetime(2026, 8, 20, 12, 10, tzinfo=timezone.utc),
        )


def test_dispatched_recovery_is_owner_approved_terminal_unknown_at_max_cost(
    tmp_path: Path,
) -> None:
    package, launch, _checkpoint, receipt = _package_and_launch()
    manifest = gate_b_v3.GateBPackageManifestV3.model_validate_json(
        package.manifest_bytes
    )
    ledger_identity = gate_b_v3.GateBLaunchIdentityV3(
        schema_version="3.0.0",
        run_id=launch.run_id,
        issued_at=receipt.issued_at,
        package_manifest_sha256=package.package_sha256,
    )
    private_key = gate_b_v3.Ed25519PrivateKey.from_private_bytes(
        OWNER_PRIVATE_KEY
    )
    public_key = private_key.public_key().public_bytes_raw()
    with gate_b_v3.GateBLedgerV3(
        tmp_path / "ledger",
        ledger_identity,
        manifest,
        owner_recovery_public_key=public_key,
    ) as ledger:
        ledger.reserve(0)
        ledger.mark_dispatched(0, dispatch_id="crashed-after-dispatch")
        request = gate_b_v3.build_recovery_request_v3(
            ledger,
            {0: gate_b_v3.GateBCallStateV3.TERMINAL_UNKNOWN},
        )
        decision = gate_b_v3.GateBRecoveryDecisionV3.approve(
            request,
            approved_by="gate-b-owner",
            approved_at=datetime(2026, 8, 20, 12, 20, tzinfo=timezone.utc),
            owner_private_key=OWNER_PRIVATE_KEY,
        )

        gate_b_v3.apply_owner_recovery_v3(ledger, decision)

        assert ledger.state(0) is gate_b_v3.GateBCallStateV3.TERMINAL_UNKNOWN
        assert ledger.row(0).measured_cost_usd is None
        assert ledger.row(0).conservative_cost_usd == Decimal("0.01")
        assert ledger.retry_allowed(0) is False


def test_v3_runner_import_does_not_load_blocked_runner() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import job_intel.product_search.gate_b_benchmark_v3 as v3; "
                "assert hasattr(v3, 'run_gate_b_at_most_once_v3'); "
                "assert 'job_intel.product_search.gate_b' not in sys.modules"
            ),
        ],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
