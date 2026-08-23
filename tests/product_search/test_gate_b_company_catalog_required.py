from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from job_intel.product_search.gate_b_evidence_runner_v1 import CollectionConfig
import job_intel.product_search.gate_b_evidence_v3 as evidence
from job_intel.product_search.gate_b_evidence_v3 import (
    ReviewedFragmentAllowlistV3,
    ReviewedFragmentDecisionV3,
    ReviewedFragmentEntryV3,
)


def _allowlist(raw: dict[str, str]) -> ReviewedFragmentAllowlistV3:
    record = {"selection_key": "a" * 64}
    candidates = evidence.build_vacancy_projection_candidates_v3(record, raw)
    entries = tuple(
        ReviewedFragmentEntryV3(
            selection_key=candidates.selection_key,
            vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
            source_locator=candidate.source_locator,
            text_sha256=candidate.text_sha256,
            decision=ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
            reviewer_role="independent_gate_b_evidence_reviewer",
            reviewed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        for candidate in candidates.description_candidates
    )
    return ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256="b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69",
        entries=entries,
    )


def test_collection_config_requires_company_evidence_root() -> None:
    with pytest.raises(ValidationError, match="company_evidence_root"):
        CollectionConfig.model_validate(
            {
                "manifest_path": "manifest.json",
                "corpus_rows_path": "corpus.json",
                "reviewed_allowlist_path": "allowlist.yaml",
                "decision_policy_path": "policy.yaml",
                "decision_request_factory": "x:y",
                "authority_paths": {},
            }
        )


def test_projection_without_catalog_reports_catalog_not_connected() -> None:
    raw = {
        "company": "Example Corp",
        "title": "Head of Product",
        "location": "Almaty",
        "description": "Lead quarterly roadmap planning with engineering and design.",
    }
    projected = evidence.project_vacancy_evidence_v3(
        {"selection_key": "a" * 64}, raw, _allowlist(raw)
    )

    assert projected.company_authority.reason.value == "company_evidence_unavailable"
