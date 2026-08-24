from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

import pytest

from job_intel.product_search import decision_v2
from job_intel.product_search import evidence_synthesis as synthesis
from job_intel.product_search.decision_v2 import load_decision_policy, run_decision_v2
from job_intel.product_search import gate_b_evidence_runner_v1 as runner
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    EvidenceManifestRow,
    ForegroundDispatchLedger,
    ManifestRef,
    build_decision_request_v2,
)
from job_intel.product_search.gate_b_spend_record_v1 import SpendRecordStore
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMObservationProvider,
    RecordingStore,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import LLMProviderError
from tests.product_search.test_gate_b_composition import (
    _ProductionShapedSemanticFake,
    _pricing,
    _projected_fixture,
    _provider_payload,
)


AUTHORITY_FIELDS = (
    "provider_sha256",
    "model_sha256",
    "prompt_sha256",
    "response_schema_sha256",
    "pricing_sha256",
)


def _production_capability(
    provider: object, pricing: object, manifest_sha256: str
) -> tuple[object, object, ManifestRef]:
    ref = ManifestRef(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        manifest_sha256=manifest_sha256,
        ordinal=0,
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
    )
    row = EvidenceManifestRow(
        ordinal=0,
        corpus_key="northstar/head-of-product",
        raw_sha256="4" * 64,
        input_sha256=ref.input_sha256,
        projection_sha256=ref.projection_sha256,
    )
    manifest = SimpleNamespace(
        manifest_sha256=manifest_sha256,
        rows=(row,),
        row_ref=lambda ordinal: ref,
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
            per_call_maximum_usd=pricing.reservation_cost_usd,
            aggregate_maximum_usd=pricing.reservation_cost_usd,
        ),
    )
    ledger = ForegroundDispatchLedger(
        manifest, committed_budget_reserver=lambda _amount: None
    )
    capability = runner._issue_collection_capability(
        manifest=manifest,
        provider=provider,
        ledger=ledger,
    )
    return capability, ledger, ref


def test_live_provider_factory_requires_explicit_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOB_INTEL_LLM_LIVE_APPROVED", raising=False)
    with pytest.raises(LLMProviderError, match="live_calls_not_approved"):
        runner.build_live_provider_factory()


def test_live_provider_factory_uses_the_governed_live_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JOB_INTEL_LLM_LIVE_APPROVED", "1")
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "records"))
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", "a" * 64)
    calls: list[dict[str, object]] = []

    class FakeProvider:
        store = object()
        semantic_prompt_sha256 = "p"
        model_id = "openai/gpt-5-mini"

    def fake_builder(**kwargs: object) -> FakeProvider:
        calls.append(kwargs)
        return FakeProvider()

    monkeypatch.setattr(runner, "build_live_llm_provider", fake_builder)
    provider = runner.build_live_provider_factory()

    assert provider.store is not None
    assert calls and calls[0]["store_dir"] == str(tmp_path / "records")


def test_zero_remaining_budget_refuses_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_sha256 = "a" * 64
    root = tmp_path / "spend"
    SpendRecordStore.provision(
        root=root,
        manifest_sha256=manifest_sha256,
        aggregate_maximum_cents=1,
    )
    store = SpendRecordStore.open(root=root, manifest_sha256=manifest_sha256)
    store.reserve(1)
    manifest = SimpleNamespace(
        manifest_sha256=manifest_sha256,
        limits=SimpleNamespace(per_call_maximum_usd=Decimal("0.01")),
    )
    monkeypatch.setenv("GATE_B_SPEND_RECORD_ROOT", str(root))
    with pytest.raises(ValueError, match="committed_budget_exhausted"):
        runner._build_committed_budget_reserver(manifest)


def test_generic_transport_record_stays_raw_and_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    pricing = _pricing()
    manifest_sha256 = "4" * 64
    store = RecordingStore(tmp_path / "provider-records")
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    semantic_provider = LLMObservationProvider(
        store=store,
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(_provider_payload(projected)),
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    capability, _ledger, ref = _production_capability(
        provider, pricing, manifest_sha256
    )
    dispatch_input_hash = runner._reservation_input_hash(ref)

    provider.dispatch(
        projected.provider_payload(),
        input_hash=dispatch_input_hash,
        capability=capability,
    )

    provider_input_hash = provider._adapter.last_call_metadata["input_hash"]
    transport_record = store.load(provider_input_hash)
    assert transport_record["input"] == projected.provider_payload()
    # response_schema_sha256 and pricing_sha256 are generic semantic metadata;
    # the V2-only authority projection must not enter the transport artifact.
    for authority_name in ("provider_sha256", "model_sha256", "prompt_sha256"):
        assert authority_name not in transport_record
    assert "provider_authority_identity" not in transport_record
    assert "semantic_transport_record_sha256" not in transport_record


def test_v2_record_has_authority_before_seal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    pricing = _pricing()
    manifest_sha256 = "5" * 64
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    semantic_provider = LLMObservationProvider(
        store=RecordingStore(tmp_path / "provider-records"),
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(_provider_payload(projected)),
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    capability, _ledger, ref = _production_capability(
        provider, pricing, manifest_sha256
    )
    preseal_records: list[dict[str, object]] = []
    original_seal = capability.seal_record

    def observe_before_seal(record: dict[str, object]) -> dict[str, object]:
        preseal_records.append(dict(record))
        return original_seal(record)

    monkeypatch.setattr(capability, "seal_record", observe_before_seal)
    provider.dispatch(
        projected.provider_payload(),
        input_hash=runner._reservation_input_hash(ref),
        capability=capability,
    )

    v2_preseal_records = [
        record
        for record in preseal_records
        if record.get("provider_record_kind") == "gate-b-evidence-synthesis-v2"
    ]
    assert len(v2_preseal_records) == 1
    preseal_record = v2_preseal_records[0]
    assert set(provider.authority_identity) == set(AUTHORITY_FIELDS)
    for authority_name in AUTHORITY_FIELDS:
        authority_value = provider.authority_identity[authority_name]
        assert preseal_record[authority_name] == authority_value
    assert "semantic_transport_record_sha256" in preseal_record
    assert "metadata_sha256" not in preseal_record
    assert "metadata_hmac_sha256" not in preseal_record


@pytest.mark.parametrize("authority_field", AUTHORITY_FIELDS)
def test_v2_authority_tampering_after_save_is_detected(
    authority_field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    pricing = _pricing()
    manifest_sha256 = "6" * 64
    store_dir = tmp_path / "provider-records"
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(store_dir))
    semantic_provider = LLMObservationProvider(
        store=RecordingStore(store_dir),
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(_provider_payload(projected)),
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    capability, _ledger, ref = _production_capability(
        provider, pricing, manifest_sha256
    )
    dispatch_input_hash = runner._reservation_input_hash(ref)

    provider.dispatch(
        projected.provider_payload(),
        input_hash=dispatch_input_hash,
        capability=capability,
    )

    # The untouched V2 envelope is the control group and passes the same verifier.
    untouched = runner._provider_record(provider, dispatch_input_hash)
    provider.verify_provider_record(untouched)
    assert untouched[authority_field] == provider.authority_identity[authority_field]

    tampered = dict(untouched)
    tampered[authority_field] = "f" * 64
    provider.store.save(tampered)

    with pytest.raises(LLMProviderError, match="recording_corrupt"):
        runner._provider_record(provider, dispatch_input_hash)


def test_live_provider_publishes_v2_record_used_by_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    payload = _provider_payload(projected)
    policy = synthesis.load_evidence_synthesis_policy()
    pricing = _pricing()
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", "2" * 64)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))

    semantic_provider = LLMObservationProvider(
        store=RecordingStore(tmp_path / "provider-records"),
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(payload),
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    capability, _ledger, ref = _production_capability(provider, pricing, "2" * 64)
    dispatch_input_hash = runner._reservation_input_hash(ref)
    response_payload = provider.dispatch(
        projected.provider_payload(),
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    provider_record = runner._provider_record(provider, dispatch_input_hash)

    provider_input_sha256 = synthesis.synthesis_input_sha256(
        projected.provider_payload(), provider=provider._adapter
    )
    request = build_decision_request_v2(
        response_payload=response_payload,
        projected=projected,
        provider_input_sha256=provider_input_sha256,
        raw={
            "company": "Northstar",
            "title": "Head of Product",
            "location": "Remote",
            "posted_at": "2026-08-23T00:00:00Z",
        },
        provider_record=provider_record,
        validation_status=None,
        decision_policy=load_decision_policy(),
        decision_clock=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    decision = run_decision_v2(request, policy=load_decision_policy())

    assert provider_record["schema_version"] == "2.0.0"
    assert provider_record["provider_version"] == "product-search-evidence-replay/2.0"
    assert provider_record["prompt_version"] == "product-search-evidence-synthesis-2.0.0"
    assert provider_record["output_sha256"] == synthesis._safe_output_sha256(response_payload)
    for authority_name, authority_value in provider.authority_identity.items():
        assert provider_record[authority_name] == authority_value
    assert isinstance(provider_record["metadata_hmac_sha256"], str)
    assert decision.status is decision_v2.DecisionRunStatus.ASSESSED


def test_live_provider_with_collection_capability_reaches_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    policy = synthesis.load_evidence_synthesis_policy()
    pricing = _pricing()
    manifest_sha256 = "3" * 64
    monkeypatch.setenv("GATE_B_MANIFEST_SHA256", manifest_sha256)
    monkeypatch.setenv("GATE_B_PROVIDER_STORE_DIR", str(tmp_path / "provider-records"))
    semantic_provider = LLMObservationProvider(
        store=RecordingStore(tmp_path / "provider-records"),
        mode="record",
        model_id=policy.model_id,
        transport=_ProductionShapedSemanticFake(_provider_payload(projected)),
        prompt_version=policy.semantic_prompt_version,
    )
    provider = runner._LiveGateBProvider(semantic_provider)
    capability, ledger, ref = _production_capability(
        provider, pricing, manifest_sha256
    )
    dispatch_input_hash = runner._reservation_input_hash(ref)
    published_results: list[object] = []
    original_publish = provider._publish_v2_provider_record

    def capture_publish(**kwargs: object) -> dict[str, object]:
        published_results.append(kwargs["result"])
        return original_publish(**kwargs)

    monkeypatch.setattr(provider, "_publish_v2_provider_record", capture_publish)

    response_payload = provider.dispatch(
        projected.provider_payload(),
        input_hash=dispatch_input_hash,
        capability=capability,
    )
    provider_record = runner._provider_record(provider, dispatch_input_hash)
    provider_input_sha256 = synthesis.synthesis_input_sha256(
        projected.provider_payload(), provider=provider._adapter
    )
    request = build_decision_request_v2(
        response_payload=response_payload,
        projected=projected,
        provider_input_sha256=provider_input_sha256,
        raw={
            "company": "Northstar",
            "title": "Head of Product",
            "location": "Remote",
            "posted_at": "2026-08-23T00:00:00Z",
        },
        provider_record=provider_record,
        validation_status=None,
        decision_policy=load_decision_policy(),
        decision_clock=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    decision = run_decision_v2(request, policy=load_decision_policy())

    assert ledger.entries()[0].recording_sha256 == provider_record[
        "semantic_transport_record_sha256"
    ]
    assert decision.status is decision_v2.DecisionRunStatus.ASSESSED
    with pytest.raises(LLMProviderError, match="recording_exists"):
        provider._publish_v2_provider_record(
            dispatch_input_hash=dispatch_input_hash,
            input_payload=projected.provider_payload(),
            result=published_results[0],
            capability=capability,
        )

    tampered = dict(provider_record)
    tampered["provider_sha256"] = "f" * 64
    unsigned = {
        key: value
        for key, value in tampered.items()
        if key not in {"metadata_sha256", "metadata_hmac_sha256"}
    }
    tampered["metadata_sha256"] = runner._sha256(runner._canonical_bytes(unsigned))
    monkeypatch.setattr(provider.store, "load", lambda _input_hash: tampered)
    with pytest.raises(LLMProviderError, match="provider_metadata_mismatch"):
        runner._provider_record(provider, dispatch_input_hash)
