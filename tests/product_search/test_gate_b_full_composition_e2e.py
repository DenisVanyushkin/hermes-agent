from __future__ import annotations

from datetime import datetime, timezone
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


def _live_dispatch_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    row_count: int = 1,
) -> tuple[object, object, ForegroundDispatchLedger, tuple[ManifestRef, ...], dict[str, object]]:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    manifest_sha256 = "c" * 64
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
    payload = projected.provider_payload()
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
    return provider, capability, ledger, refs, payload


@pytest.mark.xfail(strict=True, reason="Task 10: V2 HMAC discriminator bypass is not fail-closed")
def test_v2_record_without_discriminator_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, _ledger, ref_tuple, payload = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(ref_tuple[0])
    provider.dispatch(payload, input_hash=dispatch_input_hash, capability=capability)
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


@pytest.mark.xfail(
    strict=True,
    reason="Task 10: publication failure after reconcile leaves a terminal ledger entry",
)
def test_v2_publication_failure_does_not_leave_paid_terminal_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, capability, ledger, ref_tuple, payload = _live_dispatch_fixture(
        tmp_path, monkeypatch
    )
    dispatch_input_hash = runner._reservation_input_hash(ref_tuple[0])

    def fail_publication(_record: dict[str, object]) -> None:
        raise RuntimeError("v2 publication failure")

    monkeypatch.setattr(provider.store, "save_exclusive", fail_publication)
    with pytest.raises(RuntimeError, match="v2 publication failure"):
        provider.dispatch(payload, input_hash=dispatch_input_hash, capability=capability)
    assert ledger.entries()[0].state is JournalState.DISPATCHED


def test_duplicate_provider_input_cannot_bind_to_two_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _provider, capability, _ledger, refs, _payload = _live_dispatch_fixture(
        tmp_path, monkeypatch, row_count=2
    )
    first_dispatch = runner._reservation_input_hash(refs[0])
    second_dispatch = runner._reservation_input_hash(refs[1])
    shared_provider_input = "d" * 64
    capability.bind_record_identity(first_dispatch, shared_provider_input)
    with pytest.raises(ValueError, match="transport_receipt_identity_conflict"):
        capability.bind_record_identity(second_dispatch, shared_provider_input)
