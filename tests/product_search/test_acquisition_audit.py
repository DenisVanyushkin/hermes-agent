from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from job_intel.product_search.acquisition_audit import (
    CapabilityRegistry,
    SourceCapability,
    load_capability_registry,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config/product_search/source_capabilities.v1.yaml"


def capability(**overrides) -> SourceCapability:
    payload = {
        "source_id": "linkedin-query-a",
        "family": "linkedin",
        "status": "partial",
        "public_interface": "job_intel.sources.fetch_linkedin_vacancies",
        "seed_dependencies": [],
        "query_controls": ["query"],
        "geography_controls": [],
        "freshness": "live_on_attempt",
        "auth_state": "session_required",
        "evidence_completeness": "listing_plus_optional_detail",
        "limits": ["browser_profile", "anti_bot"],
        "failure_domain": "linkedin_session",
        "inspection": {"commit": "abc", "inspected_at": "2026-08-11"},
    }
    payload.update(overrides)
    return SourceCapability.model_validate(payload)


def test_capability_schema_is_closed_and_requires_operational_evidence() -> None:
    row = capability()
    assert row.status.value == "partial"

    with pytest.raises(ValidationError):
        capability(extra_field="not allowed")
    with pytest.raises(ValidationError):
        capability(public_interface="")
    with pytest.raises(ValidationError):
        capability(status="healthy")


def test_query_variants_on_one_backend_remain_one_independent_family() -> None:
    registry = CapabilityRegistry(
        version="1.0.0",
        sources=(
            capability(source_id="linkedin-vp", family="linkedin"),
            capability(source_id="linkedin-cpo", family="linkedin"),
            capability(source_id="headhunter-vp", family="headhunter"),
        ),
    )

    assert registry.independent_families() == {"linkedin", "headhunter"}
    assert registry.independent_family_count == 2


def test_seeded_ats_tenant_does_not_claim_broad_market_discovery() -> None:
    row = capability(
        source_id="greenhouse",
        family="greenhouse",
        seed_dependencies=["company_registry_ats_slug"],
        query_controls=["role_query"],
        limits=["known_tenants_only"],
    )

    assert row.proves_broad_market_discovery is False


def test_registry_matches_live_invocation_sources_and_has_fresh_pointers() -> None:
    registry = load_capability_registry(REGISTRY)
    expected = {
        "target_companies",
        "linkedin",
        "headhunter",
        "greenhouse",
        "lever",
        "ashby",
        "teamtailor",
        "smartrecruiters",
        "personio",
        "recruitee",
        "duckduckgo",
        "remoteok",
        "remotive",
    }

    assert registry.source_ids == expected
    assert registry.unregistered_live_sources(expected) == set()
    assert registry.unregistered_live_sources(expected | {"mystery"}) == {"mystery"}
    assert registry.stale_pointers(ROOT) == []


def test_registry_exposes_cell_and_mandate_gaps_without_claiming_failure() -> None:
    registry = load_capability_registry(REGISTRY)

    assert registry.by_id("linkedin").status.value == "partial"
    assert registry.by_id("duckduckgo").status.value == "unknown"
    assert "country_cell" not in registry.by_id("linkedin").geography_controls
    assert "broad_market" in registry.by_id("duckduckgo").query_controls
    assert registry.by_id("greenhouse").proves_broad_market_discovery is False
    assert registry.coverage_dimensions.search_cells["kazakhstan"] == "partial"
    assert registry.coverage_dimensions.search_cells["gcc_country_cells"] == "unknown"
    assert "executive_product" in registry.coverage_dimensions.mandate_vocabularies
    assert "adjacent_non_fintech" in registry.coverage_dimensions.industry_business_models
