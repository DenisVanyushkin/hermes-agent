from __future__ import annotations

import shutil
from pathlib import Path

from job_intel.product_search.company_evidence import load_company_evidence_bundle
from job_intel.product_search.gate_b_evidence_v3 import (
    CompanyEvidenceCatalogV3,
    ReviewedFragmentAllowlistV3,
    project_vacancy_evidence_v3,
)
from job_intel.product_search.evidence_synthesis import (
    build_task10_prompt_v2,
    load_evidence_synthesis_policy,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/product_search/fixtures/company_evidence"


def test_projection_resolves_company_bundle_and_preserves_insufficient_state() -> None:
    bundle = load_company_evidence_bundle(FIXTURES / "company-evidence-bundle.v1.yaml")
    catalog = CompanyEvidenceCatalogV3(
        company_evidence_contract_sha256="a" * 64,
        bundles=(bundle,),
    )
    projected = project_vacancy_evidence_v3(
        {"selection_key": "b" * 64},
        {
            "company": "Northstar",
            "title": "Head of Product",
            "location": "Remote",
            "description": "",
        },
        ReviewedFragmentAllowlistV3(
            schema_version="3.0.0",
            gate_a_run_id="gate-a-20260816T141344Z",
            gate_b_corpus_sha256=(
                "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
            ),
            entries=(),
        ),
        company_evidence_catalog=catalog,
    )
    assert projected.company_authority.status == "available"
    assert projected.company_authority.company_evidence_bundle.company_identity.company_id == bundle.company_identity.company_id
    assert projected.provider_payload()["company_authority"]["company_evidence_bundle"]["sufficiency_state"] == bundle.sufficiency_state.value


def test_nested_content_addressed_bundle_uses_sibling_sources_layout(tmp_path: Path) -> None:
    source_root = tmp_path / "gitlab" / "sources"
    source_root.mkdir(parents=True)
    shutil.copytree(FIXTURES / "sources", source_root, dirs_exist_ok=True)
    bundle = load_company_evidence_bundle(FIXTURES / "company-evidence-bundle.v1.yaml")
    nested = tmp_path / "gitlab" / bundle.content_sha256
    nested.mkdir()
    shutil.copy2(FIXTURES / "company-evidence-bundle.v1.yaml", nested / "company-evidence-bundle.v1.yaml")
    loaded = load_company_evidence_bundle(nested / "company-evidence-bundle.v1.yaml")
    assert loaded.content_sha256 == bundle.content_sha256


def test_insufficient_authority_rule_is_in_the_pinned_provider_prompt() -> None:
    prompt = build_task10_prompt_v2(load_evidence_synthesis_policy())
    assert "company_evidence_insufficient_unknown" in prompt
    assert "company_evidence_insufficient_confidence_unknown" in prompt
