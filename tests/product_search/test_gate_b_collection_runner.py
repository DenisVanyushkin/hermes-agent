from __future__ import annotations

from pathlib import Path
import hashlib
from unittest.mock import Mock

import job_intel.product_search.gate_b_evidence_runner_v1 as runner
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    CollectionReport,
    DispatchReceipt,
    DecisionEvidenceStore,
    EvidenceManifestRow,
    ManifestRef,
    RecordingRef,
    TerminalOutcome,
)

from job_intel.product_search.gate_b_evidence_runner_v1 import (
    CorpusRow,
    run_collection,
)
from job_intel.product_search.decision_v2 import DecisionResultV2, DecisionRunStatus


def test_collection_runner_verifies_binding_before_provider_and_at_finalization(
    tmp_path: Path, monkeypatch: object,
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
    manifest.row.return_value = row
    manifest.row_ref.return_value = ref
    verifier = Mock()
    provider = Mock()
    provider.dispatch.return_value = {}
    provider_factory = Mock(return_value=provider)
    journal = Mock()
    journal.append_pre_dispatch.return_value = DispatchReceipt(
        manifest_ref=ref, sequence=0
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
    hashes = iter((projection_hash, input_hash))

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
    assert report.rows[0].outcome is TerminalOutcome.SUCCESS
    assert decision_calls == [pinned_policy]
    assert report.rows[0].decision_ref.manifest_ref == ref
    assert report.rows[0].decision_bytes == decision_store.bytes_for(
        report.rows[0].decision_ref
    )
    decision_store.verify(report.rows[0].decision_ref, manifest)


def test_collection_report_has_no_decision_or_threshold_result() -> None:
    assert "decision" not in CollectionReport.model_fields
    assert "gate_decision" not in CollectionReport.model_fields
