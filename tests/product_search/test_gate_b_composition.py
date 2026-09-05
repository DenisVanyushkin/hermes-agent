from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from typing import Any

from job_intel.product_search import decision_v2
from job_intel.product_search import evidence_synthesis as synthesis
from job_intel.product_search.company_evidence import load_company_evidence_bundle
from job_intel.product_search.decision_v2 import load_decision_policy, run_decision_v2
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    build_decision_request_v2,
)
from job_intel.product_search.gate_b_evidence_v3 import (
    CompanyEvidenceCatalogV3,
    ReviewedFragmentAllowlistV3,
    project_vacancy_evidence_v3,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    DEFAULT_MODEL_ID,
    GovernedPricingSchedule,
    LLMObservationProvider,
    RecordingStore,
    _issue_structured_call_capability,
)


FIXTURES = Path(__file__).parent / "fixtures"
COMPANY_FIXTURES = FIXTURES / "company_evidence"


def _projected_fixture() -> Any:
    bundle = load_company_evidence_bundle(
        COMPANY_FIXTURES / "company-evidence-bundle.v1.yaml"
    )
    catalog = CompanyEvidenceCatalogV3(
        company_evidence_contract_sha256="a" * 64,
        bundles=(bundle,),
    )
    allowlist = ReviewedFragmentAllowlistV3(
        schema_version="3.1.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256="b" * 64,
        entries=(),
    )
    return project_vacancy_evidence_v3(
        {"selection_key": "a" * 64},
        {
            "company": "Northstar",
            "title": "Head of Product",
            "location": "Remote",
            "description": "",
            "posted_at": "2026-08-23T00:00:00Z",
        },
        allowlist,
        company_evidence_catalog=catalog,
    )


def _provider_payload(projected: synthesis.EvidenceSynthesisInputV2) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    for dimension in synthesis.EvidenceDimension:
        fragment = next(
            fragment
            for fragment in projected.fragments
            if any(claim.dimension is dimension for claim in fragment.allowed_claims)
        )
        allowed = next(
            claim
            for claim in fragment.allowed_claims
            if claim.dimension is dimension
        )
        claims.append(
            {
                "claim_id": f"claim:{dimension.value}",
                "dimension": dimension.value,
                "status": allowed.status.value,
                "claim_code": allowed.claim_code,
                "statement": allowed.statement,
                "citations": [fragment.fragment_id],
            }
        )
    return {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflicts": [],
        "question_candidates": [],
    }


class _FakeUsage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.refusal = None


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()
        self.model = DEFAULT_MODEL_ID


class _FakeCompletions:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        )


class _FakeChat:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.completions = _FakeCompletions(payload)


class _ProductionShapedSemanticFake:
    """Offline OpenAI-shaped transport behind the real governed provider."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.chat = _FakeChat(payload)


def _pricing() -> GovernedPricingSchedule:
    return GovernedPricingSchedule(
        version="openrouter-openai-gpt5-mini-2026-08-17",
        model_id=DEFAULT_MODEL_ID,
        input_usd_per_mtok=Decimal("0.25"),
        output_usd_per_mtok=Decimal("2.00"),
        max_input_tokens=24_000,
        max_output_tokens=2_000,
    )


def _record_capability(pricing: GovernedPricingSchedule) -> Any:
    return _issue_structured_call_capability(
        run_identity_sha256="2" * 64,
        pricing=pricing,
        exact_call_cap=1,
        exact_spend_cap_usd=Decimal("0.48"),
        metadata_seal_key=b"fixture-owner-bound-seal-key",
        reserve=lambda input_hash, amount: f"reservation:{input_hash}",
        mark_dispatching=lambda reservation_id: None,
        reconcile=lambda reservation_id, actual_cost, outcome: None,
    )


def test_gate_b_composes_v2_provider_through_decision() -> None:
    projected_v3 = _projected_fixture()
    projected = synthesis.EvidenceSynthesisInputV2.model_validate(
        projected_v3.model_dump(mode="json")
    )
    payload = _provider_payload(projected)
    policy = synthesis.load_evidence_synthesis_policy()
    pricing = _pricing()

    with tempfile.TemporaryDirectory() as store_dir:
        store = RecordingStore(store_dir)
        semantic_fake = _ProductionShapedSemanticFake(payload)
        semantic_provider = LLMObservationProvider(
            store=store,
            mode="record",
            model_id=policy.model_id,
            transport=semantic_fake,
            prompt_version=policy.semantic_prompt_version,
        )
        provider = synthesis.RecordedEvidenceSynthesisProviderV2(
            semantic_provider=semantic_provider,
            policy=policy,
            pricing=pricing,
            record_capability=_record_capability(pricing),
        )
        result = synthesis.run_evidence_synthesis_v2(
            synthesis_input=projected,
            provider=provider,
            policy=policy,
        )
        assert result.status is synthesis.EvidenceSynthesisStatus.DELIVERABLE
        assert result.deliverable is True
        assert len(semantic_fake.chat.completions.calls) == 1
        assert (
            decision_v2._provider_output_sha256(result)
            == result.metadata.output_sha256
        )

        request = build_decision_request_v2(
            response_payload=result.model_dump(mode="json"),
            projected=projected,
            provider_input_sha256=result.metadata.input_sha256,
            raw={
                "company": "Northstar",
                "title": "Head of Product",
                "location": "Remote",
                "posted_at": "2026-08-23T00:00:00Z",
            },
            provider_record=result.metadata.model_dump(mode="json"),
            validation_status=None,
            decision_policy=load_decision_policy(),
            decision_clock=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        decision = run_decision_v2(
            request,
            policy=load_decision_policy(),
        )

    assert decision.status is decision_v2.DecisionRunStatus.ASSESSED
    assert decision.assessment is not None
