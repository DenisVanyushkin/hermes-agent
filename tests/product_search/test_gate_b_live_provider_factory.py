from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

import pytest

from job_intel.product_search import gate_b_evidence_runner_v1 as runner
from job_intel.product_search.gate_b_spend_record_v1 import SpendRecordStore
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import LLMProviderError


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
