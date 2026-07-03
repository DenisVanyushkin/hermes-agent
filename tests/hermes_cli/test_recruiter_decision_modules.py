from __future__ import annotations

import pytest

from hermes_cli.recruiter_decision_modules import (
    BLOCK_REASON_MISSING_REQUIRED_INPUT,
    DECISION_MODULE_IDS,
    DECISION_MODULE_REGISTRY,
    DECISION_PRESETS,
    FULL_BUNDLE_PRESET_ID,
    DecisionBundleStatus,
    DecisionModuleResult,
    DecisionModuleStatus,
    missing_inputs_for_module,
    parse_requested_outputs,
    reduce_overall_status,
)


ALL_MODULES = [
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


class TestRegistry:
    def test_all_nine_modules_registered(self):
        assert sorted(DECISION_MODULE_IDS) == sorted(ALL_MODULES)
        assert set(DECISION_MODULE_REGISTRY) == set(ALL_MODULES)

    def test_positioning_summary_requires_vacancy_and_career_facts(self):
        spec = DECISION_MODULE_REGISTRY["positioning_summary"]
        assert spec.uses_candidate_facts is True
        missing = missing_inputs_for_module("positioning_summary", available_inputs=set())
        assert "vacancy_source" in missing
        assert "career_fact_source" in missing
        assert not missing_inputs_for_module(
            "positioning_summary",
            available_inputs={"vacancy_source", "career_fact_source"},
        )

    def test_company_assessment_accepts_company_identity_or_vacancy_source(self):
        spec = DECISION_MODULE_REGISTRY["company_assessment"]
        assert spec.uses_company_research is True
        assert not missing_inputs_for_module(
            "company_assessment",
            available_inputs={"company_identity", "public_research"},
        )
        assert not missing_inputs_for_module(
            "company_assessment",
            available_inputs={"vacancy_source", "public_research"},
        )
        missing = missing_inputs_for_module("company_assessment", available_inputs={"public_research"})
        assert missing

    def test_company_assessment_does_not_require_career_facts(self):
        assert not missing_inputs_for_module(
            "company_assessment",
            available_inputs={"company_identity", "public_research"},
        )

    def test_questions_to_ask_supports_degraded_mode(self):
        spec = DECISION_MODULE_REGISTRY["questions_to_ask"]
        assert spec.degraded_allowed is True

    def test_recommendation_supports_degraded_mode(self):
        assert DECISION_MODULE_REGISTRY["recommendation"].degraded_allowed is True

    def test_manual_review_warnings_has_no_required_inputs(self):
        assert not missing_inputs_for_module("manual_review_warnings", available_inputs=set())


class TestModuleResult:
    def test_result_defaults_manual_review_required(self):
        result = DecisionModuleResult(
            module_id="company_assessment",
            status=DecisionModuleStatus.READY,
        )
        assert result.manual_review_required is True
        payload = result.to_dict()
        assert payload["status"] == "READY"
        assert payload["manual_review_required"] is True

    def test_blocked_result_carries_block_reason(self):
        result = DecisionModuleResult(
            module_id="positioning_summary",
            status=DecisionModuleStatus.BLOCKED,
            block_reason=BLOCK_REASON_MISSING_REQUIRED_INPUT,
        )
        assert result.to_dict()["block_reason"] == "MISSING_REQUIRED_INPUT"


def _result(module_id: str, status: DecisionModuleStatus) -> DecisionModuleResult:
    return DecisionModuleResult(module_id=module_id, status=status)


class TestReducer:
    def test_all_requested_ready_is_ready(self):
        status = reduce_overall_status(
            requested=["company_assessment", "company_risk_register"],
            results={
                "company_assessment": _result("company_assessment", DecisionModuleStatus.READY),
                "company_risk_register": _result("company_risk_register", DecisionModuleStatus.READY),
            },
        )
        assert status is DecisionBundleStatus.READY
        assert status.value == "COMPANY_VACANCY_DECISION_BUNDLE_READY"

    def test_requested_blocked_makes_overall_blocked(self):
        status = reduce_overall_status(
            requested=["positioning_summary", "evidence_backed_supporting_claims"],
            results={
                "positioning_summary": _result("positioning_summary", DecisionModuleStatus.READY),
                "evidence_backed_supporting_claims": _result(
                    "evidence_backed_supporting_claims", DecisionModuleStatus.BLOCKED
                ),
            },
        )
        assert status is DecisionBundleStatus.BLOCKED
        assert status.value == "COMPANY_VACANCY_DECISION_BUNDLE_BLOCKED"

    def test_unrequested_modules_do_not_affect_overall(self):
        status = reduce_overall_status(
            requested=["company_assessment"],
            results={
                "company_assessment": _result("company_assessment", DecisionModuleStatus.READY),
                "recommendation": _result("recommendation", DecisionModuleStatus.SKIPPED_NOT_REQUESTED),
                "positioning_summary": _result("positioning_summary", DecisionModuleStatus.BLOCKED),
            },
        )
        assert status is DecisionBundleStatus.READY

    def test_inconclusive_without_blocked_is_inconclusive(self):
        status = reduce_overall_status(
            requested=["recommendation"],
            results={"recommendation": _result("recommendation", DecisionModuleStatus.INCONCLUSIVE)},
        )
        assert status is DecisionBundleStatus.INCONCLUSIVE
        assert status.value == "COMPANY_VACANCY_DECISION_BUNDLE_INCONCLUSIVE"

    def test_missing_result_for_requested_module_blocks(self):
        status = reduce_overall_status(requested=["company_assessment"], results={})
        assert status is DecisionBundleStatus.BLOCKED


class TestPresets:
    def test_full_bundle_preset_contains_all_modules(self):
        assert set(DECISION_PRESETS[FULL_BUNDLE_PRESET_ID]) == set(ALL_MODULES)

    def test_expected_presets_exist(self):
        for preset in (
            "quick_vacancy_screen",
            "company_diligence",
            "role_fit_only",
            "positioning_only",
            "interview_prep",
            "claims_review",
        ):
            assert preset in DECISION_PRESETS
            assert set(DECISION_PRESETS[preset]) <= set(ALL_MODULES)

    def test_company_diligence_preset(self):
        assert set(DECISION_PRESETS["company_diligence"]) >= {
            "company_assessment",
            "company_risk_register",
        }


class TestParseRequestedOutputs:
    def test_explicit_context_request_wins(self):
        parsed = parse_requested_outputs(
            "please analyse",
            context={"requested_outputs": ["company_assessment", "company_risk_register"]},
        )
        assert set(parsed.requested) >= {"company_assessment", "company_risk_register"}
        # manual review warnings are auto-added for any run
        assert "manual_review_warnings" in parsed.requested

    def test_unknown_output_names_rejected_report_safely(self):
        parsed = parse_requested_outputs(
            "analyse",
            context={"requested_outputs": ["company_assessment", "outbound_email"]},
        )
        assert "outbound_email" not in parsed.requested
        assert any("outbound_email" in warning for warning in parsed.warnings)

    def test_company_diligence_prompt_maps_to_company_modules(self):
        parsed = parse_requested_outputs("расскажи про компанию Airwallex, стоит ли с ней связываться")
        assert "company_assessment" in parsed.requested
        assert "company_risk_register" in parsed.requested
        assert "positioning_summary" not in parsed.requested

    def test_should_i_apply_maps_to_full_bundle(self):
        parsed = parse_requested_outputs(
            "Should I apply to this vacancy? https://example.com/jobs/42"
        )
        assert set(parsed.requested) == set(ALL_MODULES)
        assert parsed.preset_id == FULL_BUNDLE_PRESET_ID

    def test_questions_before_recruiter_screen(self):
        parsed = parse_requested_outputs("prepare questions to ask before the recruiter screen")
        assert "questions_to_ask" in parsed.requested
        assert "positioning_summary" not in parsed.requested

    def test_positioning_prompt(self):
        parsed = parse_requested_outputs("подготовь позиционирование под эту вакансию")
        assert "positioning_summary" in parsed.requested

    def test_claims_review_prompt(self):
        parsed = parse_requested_outputs("what claims should I avoid before editing my CV?")
        assert "claims_to_avoid" in parsed.requested

    def test_manual_review_warnings_always_included(self):
        parsed = parse_requested_outputs("оцени вакансию https://example.com/j/1")
        assert "manual_review_warnings" in parsed.requested

    def test_empty_explicit_request_raises(self):
        with pytest.raises(ValueError):
            parse_requested_outputs("analyse", context={"requested_outputs": []})
