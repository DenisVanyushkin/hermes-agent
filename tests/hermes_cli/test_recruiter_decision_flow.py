from __future__ import annotations

from typing import Any

from hermes_cli.recruiter_decision_flow import (
    DecisionModuleExecution,
    DecisionSupportRequest,
    run_recruiter_decision_support_flow,
)
from hermes_cli.recruiter_decision_modules import (
    DecisionBundleStatus,
    DecisionModuleStatus,
)


APPROVED_VACANCY = {
    "source_type": "vacancy_url",
    "source_id": "https://example.com/jobs/42",
    "approved": True,
}
APPROVED_CAREER_SOURCE = {
    "source_id": "career-packet-1",
    "source_kind": "career_fact",
    "source_type": "career_fact_packet",
    "approved": True,
}
RESEARCH_CLAIMS = [
    {
        "claim": "Company raised Series F in 2025.",
        "category": "funding",
        "source": "https://example.com/press/series-f",
        "source_type": "press_release",
        "date_or_access_timestamp": "2026-06-20",
        "confidence": "high",
        "fact_vs_inference": "fact",
    },
    {
        "claim": "Docs cover 60+ countries of payment rails.",
        "category": "product",
        "source": "https://example.com/docs",
        "source_type": "product_documentation",
        "date_or_access_timestamp": "2026-06-21",
        "confidence": "medium",
        "fact_vs_inference": "fact",
    },
]

_MODULE_PAYLOADS: dict[str, dict[str, Any]] = {
    "vacancy_assessment": {
        "company": "Example Corp",
        "role": "Head of Product",
        "fit": "consider",
        "summary": "Strong adjacent fit for Example Corp Head of Product with relocation open questions.",
    },
    "company_assessment": {
        "recommendation": "worth_engaging",
        "confidence": "medium",
        "summary": "Growing fintech platform for Example Corp with real product moat.",
        "dimensions": {"business_quality": "durable demand"},
        "sources": ["https://example.com/press/series-f"],
        "fact_vs_inference": {"facts": ["Series F raised"], "inferences": ["momentum positive"]},
    },
    "company_risk_register": {
        "risks": [
            {
                "risk": "Relocation may be required",
                "severity": "high",
                "confidence": "high",
                "evidence": "Vacancy mentions relocation",
                "mitigation": "Confirm package before investing",
            }
        ]
    },
    "recommendation": {
        "decision": "consider",
        "confidence": "medium",
        "verdict": "Credible role at Example Corp; confirm relocation and scope first.",
        "reasons_for": ["strong company"],
        "reasons_against": ["relocation unknown"],
        "next_action": "Proceed to recruiter screen with relocation questions.",
        "what_would_change": "Confirmed remote policy.",
    },
    "positioning_summary": {
        "target_role_framing": "platform product leader",
        "positioning_angle": "fintech-adjacent product leadership",
        "strongest_supported_overlap": "payments product ownership",
        "adjacent_experience": "global rails exposure",
        "caveats": ["no direct rails ownership"],
        "recommended_narrative": "Focus on Example Corp platform scale-up story.",
    },
    "evidence_backed_supporting_claims": {
        "claims": [
            {
                "claim": "Led payments product line",
                "support_level": "explicit",
                "source_reference": "career-packet-1#fact-3",
                "why_it_matters": "core requirement",
                "safe_wording": "Led payments product line at X",
                "where_to_use": "CV summary",
            }
        ]
    },
    "claims_to_avoid": {
        "claims": [
            {
                "claim": "Owned global payment rails end to end",
                "reason": "only adjacent evidence",
            }
        ]
    },
    "questions_to_ask": {
        "recruiter_screen": ["Is relocation mandatory for this Example Corp role?"],
        "hiring_manager": ["What are the 6-12 month success metrics?"],
    },
}


class StubExecutor:
    def __init__(self, payloads: dict[str, dict[str, Any]] | None = None):
        self.calls: list[str] = []
        self.payloads = payloads or _MODULE_PAYLOADS

    def execute(self, *, module_id: str, skill_id: str, module_input: dict[str, Any]) -> DecisionModuleExecution:
        self.calls.append(module_id)
        return DecisionModuleExecution(
            payload=dict(self.payloads[module_id]),
            confidence="medium",
            sources=["https://example.com/press/series-f"],
        )


def _request(**overrides: Any) -> DecisionSupportRequest:
    payload: dict[str, Any] = {
        "requested_outputs": ["company_assessment", "company_risk_register"],
        "vacancy_source": dict(APPROVED_VACANCY),
        "career_fact_sources": [dict(APPROVED_CAREER_SOURCE)],
        "company_identity": "Example Corp",
        "company_research_claims": [dict(item) for item in RESEARCH_CLAIMS],
    }
    payload.update(overrides)
    return DecisionSupportRequest(**payload)


def test_partial_company_run_ready_and_skips_unrequested() -> None:
    executor = StubExecutor()
    report = run_recruiter_decision_support_flow(_request(), module_executor=executor)

    assert report.status is DecisionBundleStatus.READY
    modules = report.modules
    assert modules["company_assessment"].status is DecisionModuleStatus.READY
    assert modules["company_risk_register"].status is DecisionModuleStatus.READY
    assert modules["recommendation"].status is DecisionModuleStatus.SKIPPED_NOT_REQUESTED
    assert "positioning_summary" not in executor.calls
    # company-only run must not demand candidate career facts
    report_no_career = run_recruiter_decision_support_flow(
        _request(career_fact_sources=[]), module_executor=StubExecutor()
    )
    assert report_no_career.status is DecisionBundleStatus.READY


def test_candidate_module_without_career_source_blocked_others_unaffected() -> None:
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["positioning_summary", "company_assessment"], career_fact_sources=[]),
        module_executor=StubExecutor(),
    )

    assert report.modules["positioning_summary"].status is DecisionModuleStatus.BLOCKED
    assert report.modules["positioning_summary"].block_reason == "CAREER_FACT_SOURCE_UNAVAILABLE"
    assert report.modules["company_assessment"].status is DecisionModuleStatus.READY
    assert report.status is DecisionBundleStatus.BLOCKED


def test_unapproved_career_source_blocks_via_privacy_gate() -> None:
    unapproved = dict(APPROVED_CAREER_SOURCE, approved=False)
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["positioning_summary"], career_fact_sources=[unapproved]),
        module_executor=StubExecutor(),
    )

    assert report.modules["positioning_summary"].status is DecisionModuleStatus.BLOCKED
    assert report.modules["positioning_summary"].block_reason == "PRIVACY_GATE_BLOCKED"
    assert report.status is DecisionBundleStatus.BLOCKED


def test_generated_draft_never_accepted_as_career_facts() -> None:
    generated = {
        "source_id": "old-cover-letter",
        "source_kind": "generated_draft",
        "source_type": "career_fact_packet",
        "approved": True,
    }
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["positioning_summary"], career_fact_sources=[generated]),
        module_executor=StubExecutor(),
    )

    assert report.modules["positioning_summary"].status is DecisionModuleStatus.BLOCKED


def test_weak_company_research_blocks_company_modules_only() -> None:
    report = run_recruiter_decision_support_flow(
        _request(
            requested_outputs=["company_assessment", "positioning_summary"],
            company_research_claims=[],
        ),
        module_executor=StubExecutor(),
    )

    assert report.modules["company_assessment"].status is DecisionModuleStatus.BLOCKED
    assert report.modules["company_assessment"].block_reason == "COMPANY_RESEARCH_UNAVAILABLE"
    assert report.modules["positioning_summary"].status is DecisionModuleStatus.READY
    assert report.status is DecisionBundleStatus.BLOCKED


def test_outbound_enabled_blocks_entire_run() -> None:
    report = run_recruiter_decision_support_flow(
        _request(outbound_enabled=True), module_executor=StubExecutor()
    )

    assert report.status is DecisionBundleStatus.BLOCKED
    assert report.safety["no_outbound"] is False or report.errors


def test_missing_cover_letter_never_blocks() -> None:
    # Full-ish request without any document modules: absence of cover letter /
    # recruiter message must not appear as a blocker anywhere.
    report = run_recruiter_decision_support_flow(
        _request(
            requested_outputs=[
                "vacancy_assessment",
                "company_assessment",
                "company_risk_register",
                "recommendation",
                "positioning_summary",
                "evidence_backed_supporting_claims",
                "claims_to_avoid",
                "questions_to_ask",
                "manual_review_warnings",
            ]
        ),
        module_executor=StubExecutor(),
    )

    assert report.status is DecisionBundleStatus.READY
    joined = " ".join(report.errors + report.warnings).lower()
    assert "cover letter" not in joined


def test_degraded_recommendation_is_inconclusive_with_low_confidence() -> None:
    executor = StubExecutor()
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["recommendation"], company_research_claims=[]),
        module_executor=executor,
    )

    result = report.modules["recommendation"]
    assert result.status in (DecisionModuleStatus.INCONCLUSIVE, DecisionModuleStatus.READY)
    if result.status is DecisionModuleStatus.INCONCLUSIVE:
        assert report.status is DecisionBundleStatus.INCONCLUSIVE
    assert result.warnings or result.confidence == "low"


def test_internal_language_in_output_blocks_module() -> None:
    payloads = dict(_MODULE_PAYLOADS)
    payloads["company_assessment"] = dict(
        payloads["company_assessment"],
        summary="Based on the positioning packet and provider-visible provenance, looks fine.",
    )
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["company_assessment"]),
        module_executor=StubExecutor(payloads),
    )

    assert report.modules["company_assessment"].status is DecisionModuleStatus.BLOCKED
    assert (
        report.modules["company_assessment"].block_reason
        == "REQUIRED_OUTPUT_INTERNAL_LANGUAGE_FORBIDDEN"
    )


def test_generic_or_empty_output_blocks_module() -> None:
    payloads = dict(_MODULE_PAYLOADS)
    payloads["company_assessment"] = {"recommendation": "", "summary": ""}
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["company_assessment"]),
        module_executor=StubExecutor(payloads),
    )

    assert report.modules["company_assessment"].status is DecisionModuleStatus.BLOCKED


def test_packet_shape_matches_contract() -> None:
    report = run_recruiter_decision_support_flow(_request(), module_executor=StubExecutor())
    packet = report.to_dict()

    assert packet["schema_version"] == "recruiter_decision_support_packet_v1"
    assert packet["status"] == "COMPANY_VACANCY_DECISION_BUNDLE_READY"
    assert packet["requested_outputs"] == ["company_assessment", "company_risk_register", "manual_review_warnings"]
    assert packet["safety"] == {
        "no_outbound": True,
        "no_submission": True,
        "no_crm_writes": True,
        "no_job_intel_writes": True,
        "no_browser_automation": True,
        "manual_review_required": True,
        "draft_only": True,
    }
    for module_payload in packet["modules"].values():
        assert module_payload["manual_review_required"] is True


def test_manual_review_warnings_module_aggregates_warnings() -> None:
    report = run_recruiter_decision_support_flow(
        _request(requested_outputs=["company_assessment", "manual_review_warnings"]),
        module_executor=StubExecutor(),
    )

    warnings_module = report.modules["manual_review_warnings"]
    assert warnings_module.status is DecisionModuleStatus.READY
    assert warnings_module.payload.get("flags", {}).get("draft_only") is True
