from __future__ import annotations

from pathlib import Path
import hashlib
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import job_intel.product_search.gate_b_evidence_runner_v1 as runner
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    CollectionReport,
    DispatchReceipt,
    DecisionEvidenceRef,
    DecisionEvidenceStore,
    EvidenceManifestRow,
    JournalEntry,
    JournalState,
    ManifestRef,
    RecordingRef,
    TerminalOutcome,
)

from job_intel.product_search.gate_b_evidence_runner_v1 import (
    CorpusRow,
    run_collection,
)
from job_intel.product_search.decision_v2 import DecisionResultV2, DecisionRunStatus


def test_decision_evidence_store_namespaces_identical_bytes_by_manifest_ref(
    tmp_path: Path,
) -> None:
    store = DecisionEvidenceStore(tmp_path / "decisions")
    refs = tuple(
        ManifestRef(
            run_id="gate-b-evidence-v1-0123456789abcdef",
            manifest_sha256="a" * 64,
            ordinal=ordinal,
            input_sha256="b" * 64,
            projection_sha256="c" * 64,
        )
        for ordinal in (0, 1)
    )

    first = store.save_exclusive(refs[0], b"identical decision bytes")
    second = store.save_exclusive(refs[1], b"identical decision bytes")

    assert first.decision_sha256 == second.decision_sha256
    assert len(tuple((tmp_path / "decisions").glob("*.json"))) == 2
    assert store.bytes_for(first) == b"identical decision bytes"
    assert store.bytes_for(second) == b"identical decision bytes"
    assert store.find_for_manifest_ref(refs[0]) == first
    assert store.find_for_manifest_ref(refs[1]) == second


def test_decision_evidence_missing_ref_fails_with_named_error(tmp_path: Path) -> None:
    store = DecisionEvidenceStore(tmp_path / "decisions")
    ref = DecisionEvidenceRef(
        manifest_ref=ManifestRef(
            run_id="gate-b-evidence-v1-0123456789abcdef",
            manifest_sha256="a" * 64,
            ordinal=0,
            input_sha256="b" * 64,
            projection_sha256="c" * 64,
        ),
        decision_sha256="d" * 64,
    )

    with pytest.raises(ValueError, match="decision evidence missing"):
        store.bytes_for(ref)


@pytest.mark.parametrize("provider_outcome", ["success", "terminal_unknown"])
def test_collection_runner_verifies_binding_before_provider_and_at_finalization(
    tmp_path: Path, monkeypatch: object, provider_outcome: str,
) -> None:
    input_hash = "1" * 64
    projection_hash = "2" * 64
    row = EvidenceManifestRow(
        ordinal=0,
        corpus_key="k",
        raw_sha256="3" * 64,
        input_sha256=input_hash,
        projection_sha256=projection_hash,
    )
    ref = ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="4" * 64,
        ordinal=0,
        input_sha256=input_hash,
        projection_sha256=projection_hash,
    )
    manifest = Mock(
        row_count=1,
        rows=(row,),
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="4" * 64,
    )
    manifest.authorities = SimpleNamespace(
        model_sha256="m",
        prompt_sha256="p",
        response_schema_sha256="s",
        pricing_sha256="q",
        source_authority_sha256s={"provider": "v"},
    )
    manifest.limits = SimpleNamespace(
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    manifest.row.return_value = row
    manifest.row_ref.return_value = ref
    verifier = Mock()
    class ProviderStore:
        def __init__(self) -> None:
            self.records: dict[str, dict[str, object]] = {}

        def load(self, input_hash: str) -> dict[str, object]:
            return self.records[input_hash]

    provider = Mock()
    provider.store = ProviderStore()
    provider.pricing = SimpleNamespace(
        identity_sha256="q", reservation_cost_usd=Decimal("0.01")
    )
    provider.authority_identity = {
        "provider_sha256": "v",
        "model_sha256": "m",
        "prompt_sha256": "p",
        "response_schema_sha256": "s",
        "pricing_sha256": "q",
    }

    def dispatch(payload: dict[str, object], *, input_hash: str, capability: object) -> object:
        reservation = capability.reserve(input_hash)
        capability.mark_dispatching(reservation)
        record = {
            "provider_id": "provider",
            "model_id": "model",
            "provider_sha256": "v",
            "model_sha256": "m",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "raw_response_text": "{}" if provider_outcome == "success" else "",
            "post_dispatch_outcome_v3": provider_outcome,
            "measured_cost_usd": "0" if provider_outcome == "success" else None,
            "conservative_cost_usd": "0.01",
            "pricing_sha256": "q",
        }
        provider.store.records[input_hash] = record
        capability.reconcile(reservation, Decimal("0"), "success")
        return SimpleNamespace(record=record)

    provider.dispatch.side_effect = dispatch
    provider_factory = Mock(return_value=provider)
    journal = Mock()
    journal.append_pre_dispatch.return_value = DispatchReceipt(
        manifest_ref=ref, sequence=0
    )
    journal.entries.return_value = (
        JournalEntry(
            manifest_ref=ref,
            sequence=0,
            state=JournalState.SUCCESS,
            recording_sha256="a" * 64,
            measured_cost_usd=Decimal("0"),
            conservative_cost_usd=Decimal("0.01"),
        ),
    )
    recordings = Mock()
    recording_ref = RecordingRef(manifest_ref=ref, recording_sha256="5" * 64)
    recordings.save_exclusive.return_value = recording_ref
    recordings.bytes_for.return_value = b"recording"
    decision_store = DecisionEvidenceStore(tmp_path / "decisions")
    projection = Mock()
    projection.model_dump.return_value = {}
    projection.provider_payload.return_value = {}
    monkeypatch.setattr(runner, "project_vacancy_evidence_v3", lambda *args: projection)
    monkeypatch.setattr(runner, "validate_provider_payload_v3", lambda *args, **kwargs: None)
    reservation_hash = hashlib.sha256(
        runner._canonical_bytes(ref.model_dump(mode="json"))
    ).hexdigest()
    hashes = iter((reservation_hash, projection_hash, input_hash, reservation_hash))

    def fake_sha256(value: bytes) -> str:
        try:
            return next(hashes)
        except StopIteration:
            return hashlib.sha256(value).hexdigest()

    monkeypatch.setattr(runner, "_sha256", fake_sha256)
    decision = DecisionResultV2(
        status=DecisionRunStatus.FAIL_CLOSED,
        failure_reason="test",
        assessment=None,
    )
    decision_calls: list[object] = []

    def fake_decision(request: object, *, policy: object) -> DecisionResultV2:
        decision_calls.append(policy)
        return decision

    pinned_policy = Mock()
    monkeypatch.setattr(runner, "run_decision_v2", fake_decision)

    report = run_collection(
        manifest=manifest,
        corpus_rows=(CorpusRow(ordinal=0, record={}, raw={}),),
        reviewed_allowlist=Mock(),
        provider_factory=provider_factory,
        journal=journal,
        recordings=recordings,
        decision_evidence=decision_store,
        decision_policy=pinned_policy,
        decision_request_factory=lambda payload, row: Mock(),
        binding_verifier=verifier,
    )

    assert isinstance(report, CollectionReport)
    assert verifier.call_count == 2
    assert verifier.call_args_list[0] == verifier.call_args_list[1]
    provider_factory.assert_called_once_with()
    assert report.metrics.observed_row_count == 1
    assert report.rows[0].outcome is TerminalOutcome(provider_outcome)
    assert provider.dispatch.call_count == 1
    assert decision_calls == [pinned_policy]
    assert report.rows[0].decision_ref.manifest_ref == ref
    assert report.rows[0].decision_bytes == decision_store.bytes_for(
        report.rows[0].decision_ref
    )
    decision_store.verify(report.rows[0].decision_ref, manifest)


def test_collection_report_has_no_decision_or_threshold_result() -> None:
    assert "decision" not in CollectionReport.model_fields
    assert "gate_decision" not in CollectionReport.model_fields


def test_terminal_unknown_uses_empty_provider_record_and_conservative_cost() -> None:
    input_hash = "1" * 64
    ref = ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="4" * 64,
        ordinal=0,
        input_sha256=input_hash,
        projection_sha256="2" * 64,
    )
    row = SimpleNamespace(ordinal=0, input_sha256=input_hash)
    manifest = SimpleNamespace(
        rows=(row,),
        manifest_sha256=ref.manifest_sha256,
        authorities=SimpleNamespace(
            pricing_sha256="q",
            model_sha256="m",
            prompt_sha256="p",
            response_schema_sha256="s",
            source_authority_sha256s={"provider": "v"},
        ),
        limits=SimpleNamespace(
            ordered_call_cap=48,
            per_call_maximum_usd=Decimal("0.01"),
            aggregate_maximum_usd=Decimal("0.48"),
        ),
        row_ref=lambda ordinal: ref,
    )

    class Store:
        def __init__(self) -> None:
            self.record: dict[str, object] | None = None

        def load(self, _input_hash: str) -> dict[str, object]:
            assert self.record is not None
            return self.record

    provider = SimpleNamespace(
        store=Store(),
        pricing=SimpleNamespace(
            identity_sha256="q", reservation_cost_usd=Decimal("0.01")
        ),
        authority_identity={
            "provider_sha256": "v",
            "model_sha256": "m",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "pricing_sha256": "q",
        },
    )
    journal = Mock()
    journal.append_pre_dispatch.return_value = DispatchReceipt(
        manifest_ref=ref, sequence=0
    )
    capability = runner._issue_collection_capability(
        manifest=manifest, provider=provider, journal=journal
    )
    dispatch_input_hash = runner._reservation_input_hash(ref)
    reservation = capability.reserve(dispatch_input_hash)
    capability.mark_dispatching(reservation)
    provider.store.record = {
        "provider_id": "provider",
        "model_id": "model",
        "provider_sha256": "v",
        "model_sha256": "m",
        "prompt_sha256": "p",
        "response_schema_sha256": "s",
        "raw_response_text": "",
        "post_dispatch_outcome_v3": "terminal_unknown",
        "measured_cost_usd": None,
        "conservative_cost_usd": "0.01",
        "pricing_sha256": "q",
    }
    capability.reconcile(reservation, Decimal("0.01"), "terminal_unknown")

    args = journal.commit_terminal.call_args.args
    assert args[1] is TerminalOutcome.TERMINAL_UNKNOWN
    assert args[3] is None
    assert args[4] == Decimal("0.01")
    record, record_hash, _, response_bytes, _, conservative = runner._provider_dispatch_result(
        provider, dispatch_input_hash, SimpleNamespace(record=provider.store.record)
    )
    assert record["post_dispatch_outcome_v3"] == "terminal_unknown"
    assert record_hash == args[2]
    assert response_bytes == b""
    assert conservative == Decimal("0.01")
    assert journal.append_pre_dispatch.call_count == 1


def test_reservation_identity_keeps_duplicate_inputs_distinct() -> None:
    common = {
        "run_id": "gate-b-evidence-v1-0123456789abcdef",
        "manifest_sha256": "4" * 64,
        "input_sha256": "1" * 64,
        "projection_sha256": "2" * 64,
    }
    first = ManifestRef(ordinal=0, **common)
    second = ManifestRef(ordinal=1, **common)

    assert runner._reservation_input_hash(first) != runner._reservation_input_hash(second)


def test_reservation_callbacks_bind_duplicate_inputs_to_their_ordinal() -> None:
    common = {
        "run_id": "gate-b-evidence-v1-0123456789abcdef",
        "manifest_sha256": "4" * 64,
        "input_sha256": "1" * 64,
        "projection_sha256": "2" * 64,
    }
    refs = {
        ordinal: ManifestRef(ordinal=ordinal, **common)
        for ordinal in (0, 1)
    }
    rows = tuple(
        SimpleNamespace(ordinal=ordinal, input_sha256=common["input_sha256"])
        for ordinal in (0, 1)
    )
    manifest = SimpleNamespace(
        rows=rows,
        manifest_sha256=common["manifest_sha256"],
        authorities=SimpleNamespace(
            pricing_sha256="q",
            model_sha256="m",
            prompt_sha256="p",
            response_schema_sha256="s",
            source_authority_sha256s={"provider": "v"},
        ),
        limits=SimpleNamespace(
            ordered_call_cap=48,
            per_call_maximum_usd=Decimal("0.01"),
            aggregate_maximum_usd=Decimal("0.48"),
        ),
        row_ref=lambda ordinal: refs[ordinal],
    )
    provider = SimpleNamespace(
        pricing=SimpleNamespace(
            identity_sha256="q", reservation_cost_usd=Decimal("0.01")
        ),
        store=SimpleNamespace(),
    )
    provider.authority_identity = {
        "provider_sha256": "v",
        "model_sha256": "m",
        "prompt_sha256": "p",
        "response_schema_sha256": "s",
        "pricing_sha256": "q",
    }
    journal = Mock()
    journal.append_pre_dispatch.side_effect = lambda ref: DispatchReceipt(
        manifest_ref=ref, sequence=ref.ordinal
    )
    journal.entries.return_value = tuple(
        JournalEntry(
            manifest_ref=refs[ordinal],
            sequence=ordinal,
            state=JournalState.SUCCESS,
            recording_sha256="a" * 64,
            measured_cost_usd=Decimal("0"),
            conservative_cost_usd=Decimal("0.01"),
        )
        for ordinal in (0, 1)
    )
    capability = runner._issue_collection_capability(
        manifest=manifest, provider=provider, journal=journal
    )

    for ordinal in (0, 1):
        reservation = capability.reserve(
            runner._reservation_input_hash(refs[ordinal])
        )
        capability.mark_dispatching(reservation)

    assert [call.args[0] for call in journal.append_pre_dispatch.call_args_list] == [
        refs[0],
        refs[1],
    ]
    with pytest.raises(ValueError, match="reservation_manifest_ref_missing"):
        capability.reserve("f" * 64)


def test_collection_runner_dispatches_duplicate_inputs_as_distinct_rows(
    tmp_path: Path, monkeypatch: object
) -> None:
    input_hash = runner._sha256(runner._canonical_bytes({}))
    projection_hash = input_hash
    rows = tuple(
        EvidenceManifestRow(
            ordinal=ordinal,
            corpus_key=f"duplicate-{ordinal}",
            raw_sha256=(str(ordinal + 3) * 64),
            input_sha256=input_hash,
            projection_sha256=projection_hash,
        )
        for ordinal in (0, 1)
    )
    refs = {
        ordinal: ManifestRef(
            run_id="gate-b-evidence-v1-0123456789abcdef",
            manifest_sha256="4" * 64,
            ordinal=ordinal,
            input_sha256=input_hash,
            projection_sha256=projection_hash,
        )
        for ordinal in (0, 1)
    }
    manifest = Mock(
        row_count=2,
        rows=rows,
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="4" * 64,
    )
    manifest.authorities = SimpleNamespace(
        model_sha256="m",
        prompt_sha256="p",
        response_schema_sha256="s",
        pricing_sha256="q",
        source_authority_sha256s={"provider": "v"},
    )
    manifest.limits = SimpleNamespace(
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    manifest.row.side_effect = lambda ordinal: rows[ordinal]
    manifest.row_ref.side_effect = lambda ordinal: refs[ordinal]

    class Store:
        def __init__(self) -> None:
            self.records: dict[str, dict[str, object]] = {}

        def load(self, dispatch_key: str) -> dict[str, object]:
            return self.records[dispatch_key]

    provider = Mock()
    provider.store = Store()
    provider.pricing = SimpleNamespace(
        identity_sha256="q", reservation_cost_usd=Decimal("0.01")
    )
    provider.authority_identity = {
        "provider_sha256": "v",
        "model_sha256": "m",
        "prompt_sha256": "p",
        "response_schema_sha256": "s",
        "pricing_sha256": "q",
    }

    def dispatch(
        payload: dict[str, object], *, input_hash: str, capability: object
    ) -> object:
        reservation = capability.reserve(input_hash)
        capability.mark_dispatching(reservation)
        record = {
            "provider_id": "provider",
            "model_id": "model",
            "provider_sha256": "v",
            "model_sha256": "m",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "raw_response_text": "{}",
            "post_dispatch_outcome_v3": "success",
            "measured_cost_usd": "0",
            "conservative_cost_usd": "0.01",
            "pricing_sha256": "q",
        }
        provider.store.records[input_hash] = record
        capability.reconcile(reservation, Decimal("0"), "success")
        return SimpleNamespace(record=record)

    provider.dispatch.side_effect = dispatch
    provider_factory = Mock(return_value=provider)
    journal = Mock()
    journal.append_pre_dispatch.side_effect = lambda ref: DispatchReceipt(
        manifest_ref=ref, sequence=ref.ordinal
    )
    journal.entries.return_value = tuple(
        JournalEntry(
            manifest_ref=refs[ordinal],
            sequence=ordinal,
            state=JournalState.SUCCESS,
            recording_sha256="a" * 64,
            measured_cost_usd=Decimal("0"),
            conservative_cost_usd=Decimal("0.01"),
        )
        for ordinal in (0, 1)
    )
    recordings = Mock()
    recordings.save_exclusive.side_effect = lambda recording: RecordingRef(
        manifest_ref=recording.manifest_ref,
        recording_sha256=("5" if recording.manifest_ref.ordinal == 0 else "6") * 64,
    )
    recordings.bytes_for.side_effect = lambda recording_ref: b"recording"
    decision_store = Mock()
    decision_store.save_exclusive.side_effect = (
        lambda ref, decision_bytes: DecisionEvidenceRef(
            manifest_ref=ref,
            decision_sha256=("7" if ref.ordinal == 0 else "8") * 64,
        )
    )
    decision_store.bytes_for.side_effect = lambda ref: b"decision"
    projection = Mock()
    projection.model_dump.return_value = {}
    projection.provider_payload.return_value = {}
    monkeypatch.setattr(
        runner, "project_vacancy_evidence_v3", lambda *args: projection
    )
    monkeypatch.setattr(
        runner, "validate_provider_payload_v3", lambda *args, **kwargs: None
    )
    decision = DecisionResultV2(
        status=DecisionRunStatus.FAIL_CLOSED,
        failure_reason="test",
        assessment=None,
    )
    monkeypatch.setattr(runner, "run_decision_v2", lambda *args, **kwargs: decision)

    report = run_collection(
        manifest=manifest,
        corpus_rows=(
            CorpusRow(ordinal=0, record={}, raw={}),
            CorpusRow(ordinal=1, record={}, raw={}),
        ),
        reviewed_allowlist=Mock(),
        provider_factory=provider_factory,
        journal=journal,
        recordings=recordings,
        decision_evidence=decision_store,
        decision_policy=Mock(),
        decision_request_factory=lambda payload, ref: Mock(),
        binding_verifier=Mock(),
    )

    dispatch_keys = [
        call.kwargs["input_hash"] for call in provider.dispatch.call_args_list
    ]
    assert dispatch_keys == [
        runner._reservation_input_hash(refs[0]),
        runner._reservation_input_hash(refs[1]),
    ]
    assert len(set(dispatch_keys)) == 2
    assert len(provider.store.records) == 2
    assert [call.args[0] for call in journal.append_pre_dispatch.call_args_list] == [
        refs[0],
        refs[1],
    ]
    assert journal.commit_terminal.call_count == 2
    assert [result.manifest_ref for result in report.rows] == [refs[0], refs[1]]


def test_recovery_reconciles_existing_provider_record_without_dispatch() -> None:
    input_hash = "1" * 64
    ref = ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="4" * 64,
        ordinal=0,
        input_sha256=input_hash,
        projection_sha256="2" * 64,
    )
    row = SimpleNamespace(ordinal=0)
    manifest = SimpleNamespace(
        rows=(row,),
        limits=SimpleNamespace(per_call_maximum_usd=Decimal("0.01")),
        row_ref=lambda ordinal: ref,
        authorities=SimpleNamespace(
            pricing_sha256="q",
            model_sha256="m",
            prompt_sha256="p",
            response_schema_sha256="s",
            source_authority_sha256s={"provider": "v"},
        ),
    )
    dispatch_key = runner._reservation_input_hash(ref)
    record = {
        "provider_id": "provider",
        "model_id": "model",
        "provider_sha256": "v",
        "model_sha256": "m",
        "prompt_sha256": "p",
        "response_schema_sha256": "s",
        "pricing_sha256": "q",
        "raw_response_text": "{}",
        "post_dispatch_outcome_v3": "success",
        "measured_cost_usd": "0",
        "conservative_cost_usd": "0.01",
    }
    provider = SimpleNamespace(
        store=SimpleNamespace(load=lambda key: record),
        authority_identity={
            "provider_sha256": "v",
            "model_sha256": "m",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "pricing_sha256": "q",
        },
    )
    journal = Mock()
    entry = JournalEntry(
        manifest_ref=ref,
        sequence=0,
        state=JournalState.DISPATCHED,
        recording_sha256=None,
        measured_cost_usd=None,
        conservative_cost_usd=Decimal("0.01"),
    )

    recovered = runner._recover_dispatched_row(
        manifest=manifest,
        provider=provider,
        journal=journal,
        entry=entry,
    )

    assert recovered[2] == "success"
    assert recovered[3] == b"{}"
    assert journal.commit_terminal.call_args.args[1] is TerminalOutcome.SUCCESS
    assert dispatch_key == runner._reservation_input_hash(ref)


def test_recovery_publishes_explicit_unknown_record_when_provider_record_missing() -> None:
    input_hash = "1" * 64
    ref = ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256="4" * 64,
        ordinal=0,
        input_sha256=input_hash,
        projection_sha256="2" * 64,
    )
    manifest = SimpleNamespace(
        rows=(SimpleNamespace(ordinal=0),),
        limits=SimpleNamespace(per_call_maximum_usd=Decimal("0.01")),
        row_ref=lambda ordinal: ref,
        authorities=SimpleNamespace(
            pricing_sha256="q",
            model_sha256="m",
            prompt_sha256="p",
            response_schema_sha256="s",
            source_authority_sha256s={"provider": "v"},
        ),
    )

    class Store:
        def __init__(self) -> None:
            self.records: dict[str, dict[str, object]] = {}

        def load(self, key: str) -> dict[str, object]:
            if key not in self.records:
                raise KeyError(key)
            return self.records[key]

        def save_exclusive(self, record: dict[str, object]) -> None:
            self.records[str(record["input_hash"])] = record

    store = Store()
    provider = SimpleNamespace(
        store=store,
        authority_identity={
            "provider_sha256": "v",
            "model_sha256": "m",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "pricing_sha256": "q",
        },
    )
    journal = Mock()
    entry = JournalEntry(
        manifest_ref=ref,
        sequence=0,
        state=JournalState.DISPATCHED,
        recording_sha256=None,
        measured_cost_usd=None,
        conservative_cost_usd=Decimal("0.01"),
    )

    recovered = runner._recover_dispatched_row(
        manifest=manifest,
        provider=provider,
        journal=journal,
        entry=entry,
    )

    assert recovered[2] == "terminal_unknown"
    assert recovered[3] == b""
    assert recovered[4] is None
    assert recovered[5] == Decimal("0.01")
    assert next(iter(store.records.values()))["recovery_artifact"] is True
    assert (
        journal.commit_terminal.call_args.args[1]
        is TerminalOutcome.TERMINAL_UNKNOWN
    )


def test_provider_authority_drift_fails_before_dispatch() -> None:
    manifest = SimpleNamespace(
        authorities=SimpleNamespace(
            pricing_sha256="q",
            model_sha256="m",
            prompt_sha256="p",
            response_schema_sha256="s",
            source_authority_sha256s={"provider": "v"},
        )
    )
    provider = SimpleNamespace(
        authority_identity={
            "provider_sha256": "v",
            "model_sha256": "m-drift",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "pricing_sha256": "q",
        }
    )
    with pytest.raises(ValueError, match="provider_authority_mismatch"):
        runner._assert_provider_authority(manifest, provider)


def test_governed_adapter_forwards_to_real_structured_call_boundary() -> None:
    provider = Mock()
    provider.store = object()
    expected = object()
    provider.governed_structured_call.return_value = expected
    adapter = runner.GovernedStructuredProviderAdapter(
        provider=provider,
        request_factory=lambda payload, input_hash: (payload, input_hash),
        pricing=SimpleNamespace(identity_sha256="q"),
        authority_identity={
            "provider_sha256": "v",
            "model_sha256": "m",
            "prompt_sha256": "p",
            "response_schema_sha256": "s",
            "pricing_sha256": "q",
        },
    )

    result = adapter.dispatch(
        {"title": "x"}, input_hash="1" * 64, capability=expected
    )

    assert result is expected
    provider.governed_structured_call.assert_called_once_with(
        request=({"title": "x"}, "1" * 64), capability=expected
    )
