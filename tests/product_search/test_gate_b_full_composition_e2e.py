from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_intel.product_search import evidence_synthesis as synthesis
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    CorpusRow,
    EvidenceManifestRow,
    ForegroundDispatchLedger,
    JournalState,
    ManifestRef,
    RecordingStore,
)
from job_intel.product_search.gate_b_evidence_v3 import ReviewedFragmentAllowlistV3
from job_intel.product_search.decision_v2 import load_decision_policy
from job_intel.product_search import gate_b_evidence_runner_v1 as runner
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore as SemanticRecordingStore,
)
from tests.product_search.test_gate_b_composition import (
    _ProductionShapedSemanticFake,
    _projected_fixture,
    _provider_payload,
)


V2_RECORD_MUTATION_FIELDS = (
    "recording_format_version",
    "input_hash",
    "semantic_input_sha256",
    "provider_input_sha256",
    "input",
    "input_payload_sha256",
    "provider_id",
    "provider_version",
    "model_id",
    "requested_model",
    "response_model",
    "semantic_prompt_version",
    "prompt_version",
    "schema_version",
    "output_sha256",
    "raw_response_text",
    "response_hash",
    "usage",
    "cost_usd",
    "measured_cost_usd",
    "conservative_cost_usd",
    "latency_ms",
    "retry_count",
    "post_dispatch_outcome_v3",
    "status",
    "failure_code",
    "failure_diagnostic",
    "provider_record_kind",
    "provider_sha256",
    "model_sha256",
    "prompt_sha256",
    "response_schema_sha256",
    "provider_authority_identity",
    "pricing",
    "pricing_sha256",
    "max_output_tokens",
    "semantic_transport_record_sha256",
)


def _mutated_v2_value(value: object) -> object:
    if isinstance(value, str):
        return "f" * 64
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, int):
        return value + 1
    return "tampered"


def _fixture_manifest(
    *,
    projected: synthesis.EvidenceSynthesisInputV2,
    provider: object,
    manifest_sha256: str,
) -> tuple[object, CorpusRow]:
    request_payload = projected.provider_payload()
    input_sha256 = runner._sha256(runner._canonical_bytes(request_payload))
    projection_sha256 = runner._sha256(
        runner._canonical_bytes(projected.model_dump(mode="json"))
    )
    raw = {
        "company": "Northstar",
        "title": "Head of Product",
        "location": "Remote",
        "posted_at": "2026-08-23T00:00:00Z",
    }
    ref = ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256=manifest_sha256,
        ordinal=0,
        input_sha256=input_sha256,
        projection_sha256=projection_sha256,
    )
    row = EvidenceManifestRow(
        ordinal=0,
        corpus_key="northstar/head-of-product",
        raw_sha256=runner._sha256(runner._canonical_bytes(raw)),
        input_sha256=input_sha256,
        projection_sha256=projection_sha256,
    )
    manifest = SimpleNamespace(
        run_id=ref.run_id,
        manifest_sha256=manifest_sha256,
        rows=(row,),
        row_count=1,
        corpus_sha256=None,
        row_ref=lambda ordinal: ref,
        row=lambda ordinal: row,
        authorities=SimpleNamespace(
            source_authority_sha256s={
                "provider": provider.authority_identity["provider_sha256"]
            },
            model_sha256=provider.authority_identity["model_sha256"],
            prompt_sha256=provider.authority_identity["prompt_sha256"],
            response_schema_sha256=provider.authority_identity[
                "response_schema_sha256"
            ],
            pricing_sha256=provider.authority_identity["pricing_sha256"],
        ),
        limits=SimpleNamespace(
            ordered_call_cap=1,
            per_call_maximum_usd=provider.pricing.reservation_cost_usd,
            aggregate_maximum_usd=provider.pricing.reservation_cost_usd,
        ),
        decision_clock=SimpleNamespace(),
    )
    return manifest, CorpusRow(ordinal=0, record={}, raw=raw)


def test_full_run_collection_reaches_recording_provider_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED: the full production composition exposes the split recording anchors."""
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    provider_payload = _provider_payload(projected)
    manifest_sha256 = "a" * 64
    (tmp_path / "provider-records").mkdir()

    # These are the only transport substitutions: the governed provider and
    # both persistence paths below remain production implementations.
    semantic_provider = LLMObservationProvider(
        store=SemanticRecordingStore(tmp_path / "semantic-records"),
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(provider_payload),
        prompt_version=policy.semantic_prompt_version,
    )
    monkeypatch.setattr(
        runner,
        "project_vacancy_evidence_v3",
        lambda *_args, **_kwargs: projected_v3,
    )
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    provider = runner._LiveGateBProvider(semantic_provider)
    runner_manifest, corpus_row = _fixture_manifest(
        projected=projected_v3,
        provider=provider,
        manifest_sha256=manifest_sha256,
    )
    runner_manifest.decision_clock = datetime(2026, 8, 23, tzinfo=timezone.utc)
    ledger = ForegroundDispatchLedger(
        runner_manifest,
        committed_budget_reserver=runner.NoDurableAccounting(),
    )
    allowlist = ReviewedFragmentAllowlistV3(
        schema_version="3.1.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256="b" * 64,
        entries=(),
    )

    def provider_factory() -> object:
        return provider

    def binding_verifier(*_args: object, **_kwargs: object) -> None:
        return None

    runner.run_collection(
        manifest=runner_manifest,
        corpus_rows=(corpus_row,),
        reviewed_allowlist=allowlist,
        provider_factory=provider_factory,
        ledger=ledger,
        recordings=RecordingStore(tmp_path / "recordings"),
        decision_evidence=runner.DecisionEvidenceStore(tmp_path / "decisions"),
        decision_policy=load_decision_policy(),
        decision_request_factory=runner.build_decision_request_from_context_v2,
        source_artifact=SimpleNamespace(),
        runtime=SimpleNamespace(),
        authorities=SimpleNamespace(),
        binding_verifier=binding_verifier,
    )


def test_live_dispatch_keeps_full_projection_local_and_sends_only_redacted_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    manifest_sha256 = "d" * 64
    (tmp_path / "provider-records").mkdir()
    semantic_fake = _ProductionShapedSemanticFake(_provider_payload(projected))
    semantic_provider = LLMObservationProvider(
        store=SemanticRecordingStore(tmp_path / "semantic-records"),
        mode="record",
        model_id=policy.model_id,
        transport=semantic_fake,
        prompt_version=policy.semantic_prompt_version,
    )
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    provider = runner._LiveGateBProvider(semantic_provider)
    runner_manifest, _corpus_row = _fixture_manifest(
        projected=projected_v3,
        provider=provider,
        manifest_sha256=manifest_sha256,
    )
    runner_manifest.decision_clock = datetime(2026, 8, 23, tzinfo=timezone.utc)
    ledger = ForegroundDispatchLedger(
        runner_manifest,
        committed_budget_reserver=runner.NoDurableAccounting(),
    )
    capability = runner._issue_collection_capability(
        manifest=runner_manifest,
        provider=provider,
        ledger=ledger,
    )
    provider_payload = projected_v3.provider_payload()
    full_payload = projected.model_dump(mode="json")
    assert "vacancy_evidence" in full_payload
    assert "prohibited_company_claim_text_sha256s" in full_payload
    assert "vacancy_evidence" not in provider_payload
    assert "prohibited_company_claim_text_sha256s" not in provider_payload

    for hidden_field in (
        "vacancy_evidence",
        "prohibited_company_claim_text_sha256s",
    ):
        invalid_payload = dict(provider_payload)
        invalid_payload[hidden_field] = full_payload[hidden_field]
        with pytest.raises(ValueError, match="provider_payload_mismatch"):
            runner.GateBDispatchRequestV2(
                synthesis_input=projected_v3,
                provider_payload=invalid_payload,
            )
    assert semantic_fake.chat.completions.calls == []

    request = runner.GateBDispatchRequestV2(
        synthesis_input=projected_v3,
        provider_payload=provider_payload,
    )
    dispatch_input_hash = runner._reservation_input_hash(runner_manifest.row_ref(0))
    provider.dispatch(
        request,
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    sent_payload = json.loads(
        semantic_fake.chat.completions.calls[0]["messages"][1]["content"]
    )
    assert runner._canonical_bytes(sent_payload) == runner._canonical_bytes(provider_payload)


def test_dispatch_request_rejects_unredacted_v2_input_before_dispatch() -> None:
    projected_v3 = _projected_fixture()
    projected_v2 = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    with pytest.raises(ValueError, match="v3_synthesis_input_required"):
        runner.GateBDispatchRequestV2(
            synthesis_input=projected_v2,
            provider_payload=projected_v2.provider_payload(),
        )


def _live_dispatch_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    row_count: int = 1,
) -> tuple[
    object,
    object,
    ForegroundDispatchLedger,
    tuple[ManifestRef, ...],
    runner.GateBDispatchRequestV2,
]:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    manifest_sha256 = "c" * 64
    (tmp_path / "provider-records").mkdir()
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    semantic_provider = LLMObservationProvider(
        store=SemanticRecordingStore(tmp_path / "semantic-records"),
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(_provider_payload(projected)),
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    payload = projected_v3.provider_payload()
    input_sha256 = runner._sha256(runner._canonical_bytes(payload))
    refs = tuple(
        ManifestRef(
            run_id="gate-b-evidence-v1-0123456789abcdef",
            manifest_sha256=manifest_sha256,
            ordinal=ordinal,
            input_sha256=input_sha256,
            projection_sha256="2" * 64,
        )
        for ordinal in range(row_count)
    )
    rows = tuple(
        EvidenceManifestRow(
            ordinal=ref.ordinal,
            corpus_key=f"northstar/head-of-product-{ref.ordinal}",
            raw_sha256="4" * 64,
            input_sha256=ref.input_sha256,
            projection_sha256=ref.projection_sha256,
        )
        for ref in refs
    )
    manifest = SimpleNamespace(
        manifest_sha256=manifest_sha256,
        rows=rows,
        row_count=row_count,
        row_ref=lambda ordinal: refs[ordinal],
        authorities=SimpleNamespace(
            source_authority_sha256s={
                "provider": provider.authority_identity["provider_sha256"]
            },
            model_sha256=provider.authority_identity["model_sha256"],
            prompt_sha256=provider.authority_identity["prompt_sha256"],
            response_schema_sha256=provider.authority_identity[
                "response_schema_sha256"
            ],
            pricing_sha256=provider.authority_identity["pricing_sha256"],
        ),
        limits=SimpleNamespace(
            ordered_call_cap=row_count,
            per_call_maximum_usd=provider.pricing.reservation_cost_usd,
            aggregate_maximum_usd=provider.pricing.reservation_cost_usd * row_count,
        ),
    )
    ledger = ForegroundDispatchLedger(
        manifest,
        committed_budget_reserver=runner.NoDurableAccounting(),
    )
    capability = runner._issue_collection_capability(
        manifest=manifest,
        provider=provider,
        ledger=ledger,
    )
    return (
        provider,
        capability,
        ledger,
        refs,
        runner.GateBDispatchRequestV2(
            synthesis_input=projected_v3,
            provider_payload=payload,
        ),
    )


def test_recording_anchor_uses_semantic_transport_sha_and_keeps_v2_envelope_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, ledger, refs, request = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(refs[0])
    provider.dispatch(
        request,
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    (
        provider_record,
        provider_record_sha256,
        outcome,
        response_bytes,
        _measured_cost,
        conservative_cost,
    ) = runner._provider_dispatch_result(provider, dispatch_input_hash, None)
    semantic_transport_sha256 = provider_record["semantic_transport_record_sha256"]
    assert isinstance(semantic_transport_sha256, str)
    assert semantic_transport_sha256 != provider_record_sha256

    ref = refs[0]
    recordings = RecordingStore(tmp_path / "recordings")
    recording_ref = recordings.save_exclusive(
        runner.SealedRecording(
            manifest_ref=ref,
            request_bytes=runner._canonical_bytes(request.provider_payload),
            response_bytes=response_bytes,
            outcome=runner.TerminalOutcome(outcome),
            metadata={
                "input_sha256": ref.input_sha256,
                "projection_sha256": ref.projection_sha256,
                "response_sha256": runner._sha256(response_bytes),
                "semantic_transport_record_sha256": semantic_transport_sha256,
                "provider_record_sha256": provider_record_sha256,
                "conservative_cost_usd": str(conservative_cost),
            },
        )
    )
    dispatch_entry = ledger.entries()[0]
    manifest = SimpleNamespace(row_ref=lambda _ordinal: ref)
    assert dispatch_entry.recording_sha256 == semantic_transport_sha256
    assert dispatch_entry.recording_sha256 != provider_record_sha256
    recordings.verify(recording_ref, manifest, dispatch_entry)


def test_v2_record_without_discriminator_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, _ledger, ref_tuple, request = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(ref_tuple[0])
    provider.dispatch(
        request,
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    record = runner._provider_record(provider, dispatch_input_hash)
    tampered = dict(record)
    tampered.pop("provider_record_kind", None)
    unsigned = {
        key: value
        for key, value in tampered.items()
        if key not in {"metadata_sha256", "metadata_hmac_sha256"}
    }
    tampered["metadata_sha256"] = runner._sha256(runner._canonical_bytes(unsigned))
    monkeypatch.setattr(provider.store, "load", lambda _input_hash: tampered)
    with pytest.raises(LLMProviderError, match="provider_metadata_mismatch"):
        runner._provider_record(provider, dispatch_input_hash)


def test_v2_record_keyed_verifier_control_accepts_untampered_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, _ledger, ref_tuple, request = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(ref_tuple[0])
    provider.dispatch(
        request,
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    record = runner._provider_record(provider, dispatch_input_hash)
    assert set(record) - {"metadata_sha256", "metadata_hmac_sha256"} == set(
        V2_RECORD_MUTATION_FIELDS
    )
    provider.verify_provider_record(record)


@pytest.mark.parametrize("field_name", V2_RECORD_MUTATION_FIELDS)
def test_v2_record_field_mutation_is_rejected_by_keyed_verifier(
    field_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, _ledger, ref_tuple, request = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(ref_tuple[0])
    provider.dispatch(
        request,
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    record = runner._provider_record(provider, dispatch_input_hash)
    tampered = dict(record)
    if field_name == "provider_record_kind":
        tampered.pop(field_name)
    else:
        tampered[field_name] = _mutated_v2_value(tampered[field_name])
    unsigned = {
        key: value
        for key, value in tampered.items()
        if key not in {"metadata_sha256", "metadata_hmac_sha256"}
    }
    # Recompute the open hash while preserving the original keyed HMAC.
    tampered["metadata_sha256"] = runner._sha256(runner._canonical_bytes(unsigned))
    monkeypatch.setattr(provider.store, "load", lambda _input_hash: tampered)
    with pytest.raises(LLMProviderError, match="provider_metadata_mismatch"):
        runner._provider_record(provider, dispatch_input_hash)


def test_v2_publication_failure_does_not_leave_paid_terminal_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, ledger, ref_tuple, request = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(ref_tuple[0])

    def fail_publication(_record: dict[str, object]) -> None:
        raise RuntimeError("v2 publication failure")

    monkeypatch.setattr(provider.store, "save_exclusive", fail_publication)
    with pytest.raises(RuntimeError, match="v2 publication failure"):
        provider.dispatch(
            request,
            input_hash=dispatch_input_hash,
            capability=capability,
        )
    assert ledger.entries()[0].state is JournalState.DISPATCHED


def test_v2_publication_resume_uses_stored_transport_without_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    semantic_fake = _ProductionShapedSemanticFake(_provider_payload(projected))
    manifest_sha256 = "e" * 64
    (tmp_path / "provider-records").mkdir()
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    semantic_provider = LLMObservationProvider(
        store=SemanticRecordingStore(tmp_path / "semantic-records"),
        mode="record",
        model_id=policy.model_id,
        transport=semantic_fake,
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    manifest, corpus_row = _fixture_manifest(
        projected=projected_v3,
        provider=provider,
        manifest_sha256=manifest_sha256,
    )
    manifest.decision_clock = datetime(2026, 8, 23, tzinfo=timezone.utc)
    monkeypatch.setattr(
        runner,
        "project_vacancy_evidence_v3",
        lambda *_args, **_kwargs: projected_v3,
    )
    ledger = ForegroundDispatchLedger(
        manifest, committed_budget_reserver=runner.NoDurableAccounting()
    )
    allowlist = ReviewedFragmentAllowlistV3(
        schema_version="3.1.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256="b" * 64,
        entries=(),
    )
    recordings = RecordingStore(tmp_path / "recordings")
    decision_evidence = runner.DecisionEvidenceStore(tmp_path / "decisions")
    original_save = provider.store.save_exclusive

    def fail_publication(_record: dict[str, object]) -> None:
        raise RuntimeError("v2 publication failure")

    monkeypatch.setattr(provider.store, "save_exclusive", fail_publication)
    with pytest.raises(RuntimeError, match="v2 publication failure"):
        runner.run_collection(
            manifest=manifest,
            corpus_rows=(corpus_row,),
            reviewed_allowlist=allowlist,
            provider_factory=lambda: provider,
            ledger=ledger,
            recordings=recordings,
            decision_evidence=decision_evidence,
            decision_policy=load_decision_policy(),
            decision_request_factory=runner.build_decision_request_from_context_v2,
            source_artifact=SimpleNamespace(),
            runtime=SimpleNamespace(),
            authorities=SimpleNamespace(),
            binding_verifier=lambda *_args, **_kwargs: None,
        )
    assert ledger.entries()[0].state is JournalState.DISPATCHED
    assert len(semantic_fake.chat.completions.calls) == 1

    monkeypatch.setattr(provider.store, "save_exclusive", original_save)
    monkeypatch.setattr(
        provider,
        "dispatch",
        lambda *_args, **_kwargs: pytest.fail("resume must not redispatch"),
    )
    monkeypatch.setattr(
        ledger,
        "append_pre_dispatch",
        lambda *_args, **_kwargs: pytest.fail("resume must not reserve"),
    )
    runner.run_collection(
        manifest=manifest,
        corpus_rows=(corpus_row,),
        reviewed_allowlist=allowlist,
        provider_factory=lambda: provider,
        ledger=ledger,
        recordings=recordings,
        decision_evidence=decision_evidence,
        decision_policy=load_decision_policy(),
        decision_request_factory=runner.build_decision_request_from_context_v2,
        source_artifact=SimpleNamespace(),
        runtime=SimpleNamespace(),
        authorities=SimpleNamespace(),
        binding_verifier=lambda *_args, **_kwargs: None,
    )
    assert len(semantic_fake.chat.completions.calls) == 1
    entry = ledger.entries()[0]
    assert entry.state is JournalState.SUCCESS
    assert entry.recording_sha256 is not None
    v2_record = runner._provider_record(
        provider, runner._reservation_input_hash(manifest.row_ref(0))
    )
    assert entry.recording_sha256 == v2_record["semantic_transport_record_sha256"]


def test_duplicate_provider_input_binds_to_distinct_manifest_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, ledger, refs, request = _live_dispatch_fixture(
        tmp_path, monkeypatch, row_count=2
    )
    dispatch_keys = [runner._reservation_input_hash(ref) for ref in refs]
    requests = (
        request,
        runner.GateBDispatchRequestV2(
            synthesis_input=request.synthesis_input,
            provider_payload=dict(request.provider_payload),
        ),
    )
    results = [
        provider.dispatch(request_item, input_hash=dispatch_key, capability=capability)
        for request_item, dispatch_key in zip(requests, dispatch_keys, strict=True)
    ]

    assert runner._canonical_bytes(requests[0].provider_payload) == runner._canonical_bytes(
        requests[1].provider_payload
    )
    transport_calls = provider._semantic_provider._transport.chat.completions.calls
    assert len(transport_calls) == 2
    assert transport_calls[0]["messages"][1]["content"] == transport_calls[1][
        "messages"
    ][1]["content"]
    assert dispatch_keys[0] != dispatch_keys[1]
    assert [entry.manifest_ref.ordinal for entry in ledger.entries()] == [0, 1]

    recordings = RecordingStore(tmp_path / "recordings")
    recording_refs = []
    for ref, dispatch_key, request_item, result in zip(
        refs, dispatch_keys, requests, results, strict=True
    ):
        provider_record = runner._provider_record(provider, dispatch_key)
        response_bytes = runner._canonical_bytes(result)
        entry = ledger.entries()[ref.ordinal]
        recording_ref = recordings.save_exclusive(
            runner.SealedRecording(
                manifest_ref=ref,
                request_bytes=runner._canonical_bytes(request_item.provider_payload),
                response_bytes=response_bytes,
                outcome=runner.TerminalOutcome.SUCCESS,
                metadata={
                    "input_sha256": ref.input_sha256,
                    "projection_sha256": ref.projection_sha256,
                    "response_sha256": runner._sha256(response_bytes),
                    "semantic_transport_record_sha256": provider_record[
                        "semantic_transport_record_sha256"
                    ],
                    "provider_record_sha256": runner._sha256(
                        runner._canonical_bytes(provider_record)
                    ),
                    "conservative_cost_usd": "0.01",
                },
            )
        )
        recordings.verify(recording_ref, ledger.manifest, entry)
        recording_refs.append(recording_ref)

    assert recording_refs[0].manifest_ref.ordinal == 0
    assert recording_refs[1].manifest_ref.ordinal == 1
    assert recording_refs[0].recording_sha256 != recording_refs[1].recording_sha256
