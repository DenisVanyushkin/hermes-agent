from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

from job_intel.product_search.decision_v2 import load_decision_policy
from job_intel.product_search.company_evidence import (
    load_company_evidence_bundle,
    load_company_thesis_input,
)
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    _load_company_authority_inputs,
    build_decision_request_v2,
)
from job_intel.product_search.gate_b_evidence_v3 import (
    CompanyEvidenceCatalogV3,
    ReviewedFragmentAllowlistV3,
    project_vacancy_evidence_v3,
)
from job_intel.product_search.evidence_synthesis import EvidenceSynthesisStatus, EvidenceDimension

from tests.product_search.test_gate_b_company_authority_v3 import FIXTURES


def _projected():
    bundle = load_company_evidence_bundle(FIXTURES / "company-evidence-bundle.v1.yaml")
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


def test_factory_binds_provider_output_and_uses_identity_clock() -> None:
    projected = _projected()
    thesis = load_company_thesis_input(
        FIXTURES / "company-thesis-input.v1.yaml",
        evidence_bundle=projected.company_authority.company_evidence_bundle,
    )
    input_sha = hashlib.sha256(b"provider-input").hexdigest()
    claim = next(
        claim
        for fragment in projected.fragments
        for claim in fragment.allowed_claims
        if claim.dimension is EvidenceDimension.MANDATE_FIT
    )
    payload = {
        "schema_version": "2.0.0",
        "claims": [
            {
                "claim_id": "claim:mandate",
                "dimension": claim.dimension.value,
                "status": claim.status.value,
                "claim_code": claim.claim_code,
                "statement": claim.statement,
                "citations": [next(f.fragment_id for f in projected.fragments if claim in f.allowed_claims)],
            }
        ],
        "conflicts": [],
        "question_candidates": [],
    }
    request = build_decision_request_v2(
        response_payload=payload,
        projected=projected,
        provider_input_sha256=input_sha,
        raw={"company": "Northstar", "title": "Head of Product", "location": "Remote", "posted_at": "2026-08-23T00:00:00Z"},
        provider_record={
            "provider_id": "fake",
            "provider_version": "provider-v1",
            "model_id": "model-v1",
            "semantic_prompt_version": "llm-obs-1.0.0",
            "prompt_version": "product-search-evidence-synthesis-1.0.0",
            "latency_ms": 1,
            "cost_usd": "0",
        },
        validation_status=None,
        decision_policy=load_decision_policy(),
        decision_clock=datetime(2026, 8, 23, tzinfo=timezone.utc),
        company_thesis_input=thesis,
    )
    assert request.references.provider_input_sha256 == input_sha
    assert request.references.provider_output_sha256 == request.synthesis.metadata.output_sha256
    assert request.authority_inputs.company_evidence_bundle_ref.sha256 == projected.company_authority.company_evidence_bundle.content_sha256
    assert request.company_action is None
    assert request.authority_inputs.company_thesis_input_ref is not None
    assert request.synthesis.status is EvidenceSynthesisStatus.DELIVERABLE
    assert request.synthesis.deliverable is True
    assert request.daily_digest_at == request.assessed_at == request.evaluated_at


def test_company_authority_loader_returns_only_valid_thesis_inputs() -> None:
    manifest = SimpleNamespace(
        authorities=SimpleNamespace(
            source_authority_sha256s={"company_evidence_contract": "a" * 64}
        )
    )
    catalog, theses = _load_company_authority_inputs(FIXTURES, manifest)
    assert catalog is not None
    assert len(catalog.bundles) == 1
    assert set(theses) == {catalog.bundles[0].company_identity.company_id}
