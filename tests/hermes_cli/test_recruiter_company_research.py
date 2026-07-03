from __future__ import annotations

from hermes_cli.recruiter_company_research import (
    ALLOWED_COMPANY_RESEARCH_SOURCE_TYPES,
    COMPANY_RESEARCH_PACKET_SCHEMA,
    CompanyResearchClaim,
    CompanyResearchQualityGateStatus,
    run_company_research_quality_gate,
    validate_company_research_claim,
)


def _claim(**overrides):
    payload = {
        "claim": "Airwallex raised a Series F round in 2025.",
        "category": "funding",
        "source": "https://www.airwallex.com/newsroom/series-f",
        "source_type": "press_release",
        "date_or_access_timestamp": "2026-06-20",
        "confidence": "high",
        "fact_vs_inference": "fact",
    }
    payload.update(overrides)
    return payload


class TestClaimValidation:
    def test_valid_claim_accepted(self):
        errors = validate_company_research_claim(_claim())
        assert errors == []

    def test_missing_source_rejected(self):
        assert validate_company_research_claim(_claim(source="")) != []

    def test_missing_date_rejected(self):
        assert validate_company_research_claim(_claim(date_or_access_timestamp="")) != []

    def test_missing_confidence_rejected(self):
        assert validate_company_research_claim(_claim(confidence="")) != []

    def test_invalid_fact_vs_inference_rejected(self):
        assert validate_company_research_claim(_claim(fact_vs_inference="guess")) != []

    def test_unknown_source_type_flagged(self):
        errors = validate_company_research_claim(_claim(source_type="anonymous_rumor"))
        assert any("source_type" in error for error in errors)

    def test_from_dict_roundtrip(self):
        claim = CompanyResearchClaim.from_dict(_claim())
        assert claim.source_type == "press_release"
        assert claim.to_dict()["fact_vs_inference"] == "fact"


class TestQualityGate:
    def test_ready_with_multiple_source_categories(self):
        report = run_company_research_quality_gate(
            [
                _claim(),
                _claim(
                    claim="Employee reviews repeatedly mention long hours.",
                    category="reputation",
                    source="https://www.glassdoor.com/airwallex",
                    source_type="employee_reviews",
                    fact_vs_inference="inference",
                    confidence="medium",
                ),
                _claim(
                    claim="Product docs cover payment rails in 60+ countries.",
                    category="product",
                    source="https://www.airwallex.com/docs",
                    source_type="product_documentation",
                ),
            ]
        )
        assert report.status is CompanyResearchQualityGateStatus.READY
        assert report.ready is True
        assert report.schema == COMPANY_RESEARCH_PACKET_SCHEMA

    def test_no_claims_blocks_with_research_unavailable(self):
        report = run_company_research_quality_gate([])
        assert report.status is CompanyResearchQualityGateStatus.BLOCKED
        assert report.blocked_reason == "COMPANY_RESEARCH_UNAVAILABLE"

    def test_invalid_claims_block_with_too_weak(self):
        report = run_company_research_quality_gate([_claim(source=""), _claim(confidence="")])
        assert report.status is CompanyResearchQualityGateStatus.BLOCKED
        assert report.blocked_reason == "COMPANY_RESEARCH_TOO_WEAK"
        assert report.errors

    def test_single_source_reputation_conclusion_blocks(self):
        report = run_company_research_quality_gate(
            [
                _claim(
                    claim="The company is toxic.",
                    category="reputation",
                    source="https://blind.example/post/1",
                    source_type="employee_reviews",
                    fact_vs_inference="inference",
                )
            ]
        )
        assert report.status is CompanyResearchQualityGateStatus.BLOCKED
        assert "reputation" in (report.blocked_reason or "").lower() or any(
            "reputation" in error for error in report.errors
        )

    def test_reputation_with_corroboration_passes(self):
        report = run_company_research_quality_gate(
            [
                _claim(
                    claim="Reviews mention burnout.",
                    category="reputation",
                    source="https://blind.example/post/1",
                    source_type="employee_reviews",
                    fact_vs_inference="inference",
                    confidence="medium",
                ),
                _claim(
                    claim="Glassdoor pattern shows workload complaints across 2024-2026.",
                    category="reputation",
                    source="https://glassdoor.example/airwallex",
                    source_type="employee_reviews",
                    fact_vs_inference="inference",
                    confidence="medium",
                ),
                _claim(),
            ]
        )
        assert report.status is CompanyResearchQualityGateStatus.READY

    def test_stale_claims_produce_warning(self):
        report = run_company_research_quality_gate(
            [
                _claim(stale=True),
                _claim(
                    claim="Docs are thorough.",
                    category="product",
                    source="https://docs.example",
                    source_type="developer_docs",
                ),
            ]
        )
        assert report.status is CompanyResearchQualityGateStatus.READY
        assert any("stale" in warning for warning in report.warnings)

    def test_allowed_source_types_cover_spec_list(self):
        for source_type in (
            "company_website",
            "press_release",
            "funding_announcement",
            "regulatory_filing",
            "news",
            "employee_reviews",
            "product_documentation",
            "developer_docs",
            "customer_case_study",
            "layoff_tracker",
        ):
            assert source_type in ALLOWED_COMPANY_RESEARCH_SOURCE_TYPES
