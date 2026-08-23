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
    _record_capability,
)


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
    dispatch_input_hash = "d" * 64
    response_payload = provider.dispatch(
        projected.provider_payload(),
        input_hash=dispatch_input_hash,
        capability=_record_capability(pricing),
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
