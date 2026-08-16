from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from itertools import permutations
import json
from pathlib import Path
from functools import lru_cache

import pytest
from pydantic import ValidationError

from job_intel.product_search.contracts import (
    AssessmentReferences,
    CompanyAction,
    HardGate,
    RecommendedActionKind,
    ReviewState,
    SelectionMode,
    SystemVerdict,
    WatchlistStatus,
    ImmutableArtifactRef,
)
from job_intel.product_search.company_evidence import (
    load_company_evidence_bundle,
    load_company_thesis_input,
)
from job_intel.product_search.decision_v2 import (
    CompanyActionRequestV2,
    DecisionAuthorityInputsV2,
    ExplorationAxis,
    DecisionImmutableReferencesV2,
    DecisionRequestV2,
    DecisionRunStatus,
    DimensionOutcome,
    MultiAxisExceptionV2,
    SelectionEvidenceV2,
    StageEvidenceV2,
    UrgencyEvidenceKind,
    UrgencyEvidenceV2,
    build_company_decision_snapshot,
    canonical_decision_bytes,
    load_decision_policy,
    run_decision_v2,
)
from job_intel.product_search.evidence_synthesis import (
    EvidenceClaimStatus,
    EvidenceClaimV1,
    EvidenceConflictV1,
    EvidenceDimension,
    EvidenceQuestionCandidateV1,
    EvidenceSynthesisMetadataV1,
    EvidenceSynthesisResultV1,
    EvidenceSynthesisStatus,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config/product_search/decision_contract.v2.yaml"
FIXTURES = ROOT / "tests/product_search/fixtures/decision_v2/decision-cases.v2.yaml"
COMPANY_FIXTURES = ROOT / "tests/product_search/fixtures/company_evidence"
NOW = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)


def _claim(
    dimension: EvidenceDimension,
    code: str,
    *,
    status: EvidenceClaimStatus = EvidenceClaimStatus.EXPLICIT,
    pointer: str | None = None,
) -> EvidenceClaimV1:
    pointer = pointer or f"evidence:{code}"
    return EvidenceClaimV1(
        claim_id=f"claim:{code}",
        dimension=dimension,
        status=status,
        claim_code=code,
        statement=code.replace("_", " "),
        citations=(pointer,),
    )


def _strong_claims() -> tuple[EvidenceClaimV1, ...]:
    return (
        _claim(EvidenceDimension.FEASIBILITY, "feasible_work_arrangement_explicit"),
        _claim(EvidenceDimension.MANDATE_FIT, "material_business_ownership_explicit"),
        _claim(EvidenceDimension.COMPANY_FIT, "useful_company_context_explicit"),
        _claim(EvidenceDimension.TRANSFERABILITY, "transferable_strengths_supported"),
        _claim(EvidenceDimension.CAREER_VALUE, "career_scope_improves_explicit"),
        _claim(EvidenceDimension.EVIDENCE_CONFIDENCE, "evidence_complete_explicit"),
    )


def _synthesis(
    *,
    claims: tuple[EvidenceClaimV1, ...] | None = None,
    questions: tuple[EvidenceQuestionCandidateV1, ...] = (),
    conflicts: tuple[EvidenceConflictV1, ...] = (),
    status: EvidenceSynthesisStatus = EvidenceSynthesisStatus.DELIVERABLE,
) -> EvidenceSynthesisResultV1:
    deliverable = status is EvidenceSynthesisStatus.DELIVERABLE
    result_claims = (claims if claims is not None else _strong_claims()) if deliverable else ()
    result_conflicts = conflicts if deliverable else ()
    result_questions = questions if deliverable else ()
    output_payload = {
        "schema_version": "1.0.0",
        "claims": [item.model_dump(mode="json") for item in result_claims],
        "conflicts": [item.model_dump(mode="json") for item in result_conflicts],
        "question_candidates": [item.model_dump(mode="json") for item in result_questions],
    }
    output_hash = hashlib.sha256(
        json.dumps(
            output_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return EvidenceSynthesisResultV1(
        schema_version="1.0.0",
        status=status,
        deliverable=deliverable,
        claims=result_claims,
        conflicts=result_conflicts,
        question_candidates=result_questions,
        failure_reason=None if deliverable else status.value,
        metadata=EvidenceSynthesisMetadataV1(
            provider_id="llm-observation",
            provider_version="product-search-evidence-replay/1.0",
            model_id="openai/gpt-5-mini",
            semantic_prompt_version="llm-obs-1.0.0",
            prompt_version="product-search-evidence-synthesis-1.0.0",
            schema_version="1.0.0",
            latency_ms=17,
            cost_usd="0.000100",
            input_sha256="1" * 64,
            output_sha256=output_hash,
        ),
    )


def _authority_inputs(**overrides: str) -> DecisionAuthorityInputsV2:
    values = {
        "assessment_references": AssessmentReferences.model_validate(
            {
                "profile_ref": {
                    "artifact_id": "career-profile-v2",
                    "version": "2.0.0",
                    "sha256": "19d63f738bf5317ef51ee676851c50e0085c970269a3b25e3df9e86c1f6d7651",
                },
                "candidate_facts_ref": {
                    "artifact_id": "candidate-facts-structured-resume-v1.1.0",
                    "version": "1.1.0",
                    "sha256": "7219eea2fbf04c92291254f83a76b8d2d1ef53e6004ac64ff4601c726eb9fac9",
                },
                "semantic_contract_ref": {
                    "artifact_id": "semantic-fact-contract",
                    "version": "1.0.0",
                    "sha256": overrides.get("semantic_contract_sha256", "b" * 64),
                },
                "search_contract_ref": {
                    "artifact_id": "product-search-contract-v1",
                    "version": "1.0.0",
                    "sha256": "faf9a81564d29b3b71b67908f47e54d2c6bbbf416db19914176f410e24df4ab1",
                },
                "policy_ref": {
                    "artifact_id": "PS-SOT-2026-08-10-v1",
                    "version": "1.0.0",
                    "sha256": "430340de2613ee733926d73ce276c93676fe64b1841bb2f68f3f9303b61fc3a8",
                },
                "evidence_snapshot_ref": {
                    "artifact_id": "evidence-snapshot:redacted-001",
                    "version": "1.0.0",
                    "sha256": overrides.get("evidence_snapshot_sha256", "e" * 64),
                },
            }
        ),
        "company_evidence_bundle_ref": ImmutableArtifactRef(
            artifact_id="company-evidence:northstar-commerce:2026-08-10",
            version="1.0.0",
            sha256=overrides.get(
                "company_evidence_bundle_sha256",
                "340c47d5408893612575f4ba6cee440074e84a8bc427888aba7862501933fa8a",
            ),
        ),
    }
    return DecisionAuthorityInputsV2.model_validate(values)


def _references(**overrides: str) -> DecisionImmutableReferencesV2:
    loaded = load_decision_policy(POLICY_PATH)
    values = {
        **loaded.policy.authority_hashes.model_dump(),
        "decision_contract_sha256": loaded.source_sha256,
        "semantic_contract_sha256": "b" * 64,
        "evidence_snapshot_sha256": "e" * 64,
        "company_evidence_bundle_sha256": "340c47d5408893612575f4ba6cee440074e84a8bc427888aba7862501933fa8a",
        "provider_input_sha256": "1" * 64,
        "provider_output_sha256": _synthesis().metadata.output_sha256,
    }
    values.update(overrides)
    return DecisionImmutableReferencesV2.model_validate(values)


def _request(**overrides: object) -> DecisionRequestV2:
    synthesis = overrides.pop("synthesis", _synthesis())
    references = overrides.pop(
        "references",
        _references(provider_output_sha256=synthesis.metadata.output_sha256),
    )
    values: dict[str, object] = {
        "schema_version": "2.0.0",
        "assessment_id": "assessment-redacted-001",
        "stages": StageEvidenceV2(
            raw_observed=True,
            identity_resolved=True,
            duplicates_consolidated=True,
            freshness_confirmed=True,
            role_identified=True,
            company_identified=True,
            location_and_work_format_identified=True,
            material_responsibilities_identified=True,
            known_feasibility_constraints_identified=True,
        ),
        "references": references,
        "authority_inputs": _authority_inputs(),
        "synthesis": synthesis,
        "selection": SelectionEvidenceV2(),
        "company_action": None,
        "urgency_evidence": None,
        "daily_digest_at": NOW,
        "assessed_at": NOW + timedelta(minutes=45),
        "evaluated_at": NOW + timedelta(hours=1),
    }
    values.update(overrides)
    return DecisionRequestV2.model_validate(values)


def _run(**overrides: object):
    return run_decision_v2(_request(**overrides), policy=load_decision_policy(POLICY_PATH))


def test_exact_eight_step_decision_order_is_immutable() -> None:
    """Mutation caught: reordering or skipping a normative SoT decision step."""
    result = _run()
    assert [step.name for step in result.assessment.trace.steps] == [
        "verify_identity_freshness_minimum_evidence",
        "apply_hard_feasibility_function_scope_gates",
        "evaluate_mandate",
        "evaluate_company_context",
        "evaluate_transferability_and_career_value",
        "assign_verdict_and_evidence_confidence",
        "assign_selection_mode_and_company_action_independently",
        "assign_destination_and_delivery_eligibility",
    ]


def test_authority_leaking_caller_flags_are_absent_from_v2_inputs() -> None:
    """Mutation caught: caller booleans/free text regain Decision authority."""
    assert "qualifies_under_core_policy" not in SelectionEvidenceV2.model_fields
    assert "core_policy_claim_id" not in SelectionEvidenceV2.model_fields
    assert "requested_mode" not in SelectionEvidenceV2.model_fields
    assert "axes" not in SelectionEvidenceV2.model_fields
    assert "evidence_sufficient" not in CompanyActionRequestV2.model_fields
    assert "fit_thesis" not in CompanyActionRequestV2.model_fields
    assert "current_status" not in CompanyActionRequestV2.model_fields


@lru_cache
def _company_models():
    bundle = load_company_evidence_bundle(
        COMPANY_FIXTURES / "company-evidence-bundle.v1.yaml"
    )
    thesis = load_company_thesis_input(
        COMPANY_FIXTURES / "company-thesis-input.v1.yaml",
        evidence_bundle=bundle,
    )
    return bundle, thesis


def _company_snapshot(
    current: WatchlistStatus | None,
    review: ReviewState | None,
):
    bundle, thesis = _company_models()
    return build_company_decision_snapshot(
        evidence_bundle=bundle,
        thesis_input=thesis,
        current_status=current,
        review_state=review,
        snapshot_id=f"snapshot:{current.value if current else 'absent'}:{review.value if review else 'none'}",
    )


@pytest.mark.parametrize(
    "missing_stage_field",
    [
        "raw_observed",
        "identity_resolved",
        "duplicates_consolidated",
        "freshness_confirmed",
        "role_identified",
        "company_identified",
        "location_and_work_format_identified",
        "material_responsibilities_identified",
        "known_feasibility_constraints_identified",
    ],
)
def test_stage4_requires_every_stage_1_to_3_fact(missing_stage_field: str) -> None:
    """Mutation caught: canonical stage 4 emitted from an incomplete funnel prefix."""
    payload = _request().stages.model_dump()
    payload[missing_stage_field] = False
    result = _run(stages=StageEvidenceV2.model_validate(payload))
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == "stages_1_to_3_incomplete"


def test_known_hard_gate_blocks_stage4_while_unknown_stays_unknown() -> None:
    """Mutation caught: ignoring a known gate or converting an unknown gate to rejection."""
    blocked = _run(
        synthesis=_synthesis(
            claims=_strong_claims()
            + (_claim(EvidenceDimension.FEASIBILITY, "sanctioned_environment_explicit"),)
        )
    )
    assert blocked.assessment.hard_gate_eligible is False
    assert blocked.assessment.system_verdict is SystemVerdict.REJECT
    assert blocked.assessment.blockers == (
        HardGate.SANCTIONED_OR_UNSTABLE.value,
    )

    unknown_claim = _claim(
        EvidenceDimension.FEASIBILITY,
        "sponsorship_outside_onsite_us_unknown",
        status=EvidenceClaimStatus.UNKNOWN,
    )
    unknown = _run(synthesis=_synthesis(claims=_strong_claims() + (unknown_claim,)))
    assert unknown.assessment.hard_gate_eligible is True
    assert "sponsorship_outside_onsite_us_unknown" in unknown.assessment.unknowns
    assert unknown.assessment.system_verdict is not SystemVerdict.REJECT


@pytest.mark.parametrize(
    ("case_name", "extra_claims", "removed_code", "expected_verdict", "warning"),
    [
        (
            "narrow_monetization_exception",
            (
                _claim(EvidenceDimension.MANDATE_FIT, "material_revenue_authority_explicit"),
                _claim(EvidenceDimension.MANDATE_FIT, "strategic_leverage_explicit"),
            ),
            "material_business_ownership_explicit",
            SystemVerdict.PRIORITY,
            None,
        ),
        (
            "b2b_is_not_negative",
            (_claim(EvidenceDimension.COMPANY_FIT, "b2b_model_observed"),),
            None,
            SystemVerdict.PRIORITY,
            None,
        ),
        (
            "remote_us_does_not_inherit_onsite_gate",
            (_claim(EvidenceDimension.FEASIBILITY, "remote_us_country_eligible_explicit"),),
            "feasible_work_arrangement_explicit",
            SystemVerdict.PRIORITY,
            None,
        ),
        (
            "crypto_is_company_warning_not_role_veto",
            (_claim(EvidenceDimension.COMPANY_FIT, "crypto_employer_observed"),),
            None,
            SystemVerdict.PRIORITY,
            "crypto_employer_company_risk_review",
        ),
        (
            "gm_adjacent_with_business_ownership_passes",
            (_claim(EvidenceDimension.MANDATE_FIT, "gm_digital_business_ownership_explicit"),),
            "material_business_ownership_explicit",
            SystemVerdict.PRIORITY,
            None,
        ),
    ],
)
def test_non_regression_positive_distinctions(
    case_name: str,
    extra_claims: tuple[EvidenceClaimV1, ...],
    removed_code: str | None,
    expected_verdict: SystemVerdict,
    warning: str | None,
) -> None:
    """Mutation caught: one of SoT 9.5 positive distinctions becomes a veto."""
    del case_name
    claims = tuple(
        claim for claim in _strong_claims() if claim.claim_code != removed_code
    ) + extra_claims
    assessment = _run(synthesis=_synthesis(claims=claims)).assessment
    assert assessment.system_verdict is expected_verdict
    assert assessment.hard_gate_eligible is True
    if warning:
        assert warning in assessment.warnings


def test_platform_engineering_without_business_ownership_is_not_platform_business() -> None:
    """Mutation caught: title/platform wording bypasses the technical-infrastructure gate."""
    claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.claim_code != "material_business_ownership_explicit"
    ) + (_claim(EvidenceDimension.MANDATE_FIT, "platform_engineering_without_business_ownership"),)
    assessment = _run(synthesis=_synthesis(claims=claims)).assessment
    assert assessment.system_verdict is SystemVerdict.REJECT
    assert HardGate.INTERNAL_INFRA_WITHOUT_BUSINESS_SCOPE.value in assessment.blockers


@pytest.mark.parametrize(
    "unknown_code",
    [
        "timezone_unknown",
        "compensation_unknown",
        "reporting_line_unknown",
        "pnl_unknown",
        "team_size_unknown",
    ],
)
def test_unknown_never_silently_becomes_strong_or_reject(unknown_code: str) -> None:
    """Mutation caught: unknown evidence is scored as either positive or negative."""
    claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.claim_code != "evidence_complete_explicit"
    ) + (
        _claim(
            EvidenceDimension.EVIDENCE_CONFIDENCE,
            unknown_code,
            status=EvidenceClaimStatus.UNKNOWN,
        ),
    )
    assessment = _run(synthesis=_synthesis(claims=claims)).assessment
    assert assessment.system_verdict is SystemVerdict.SAVE
    assert assessment.dimensions.evidence_confidence.outcome is DimensionOutcome.UNKNOWN
    assert unknown_code in assessment.unknowns


def test_unknown_status_cannot_activate_a_negative_policy_code() -> None:
    """Mutation caught: a negative-looking claim code overrides its unknown status."""
    claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.dimension is not EvidenceDimension.TRANSFERABILITY
    ) + (
        _claim(
            EvidenceDimension.TRANSFERABILITY,
            "non_transferable_gap_explicit",
            status=EvidenceClaimStatus.UNKNOWN,
        ),
    )
    assessment = _run(synthesis=_synthesis(claims=claims)).assessment
    assert assessment.dimensions.transferability.outcome is DimensionOutcome.UNKNOWN
    assert assessment.system_verdict is SystemVerdict.SAVE


def test_six_dimension_conclusions_are_separate_and_cited() -> None:
    """Mutation caught: collapsing a dimension into verdict or uncited free text."""
    assessment = _run().assessment
    dimensions = assessment.dimensions.model_dump(mode="json")
    assert tuple(dimensions) == (
        "feasibility",
        "mandate_fit",
        "company_fit",
        "transferability",
        "career_value",
        "evidence_confidence",
    )
    assert all(item["outcome"] == "positive" for item in dimensions.values())
    assert all(item["evidence_pointers"] for item in dimensions.values())
    assert assessment.system_verdict is SystemVerdict.PRIORITY


def _question(code: str = "clarify_reporting_line") -> EvidenceQuestionCandidateV1:
    is_work_format = code == "clarify_work_format"
    return EvidenceQuestionCandidateV1(
        question_id=f"question:{code}",
        dimension=(
            EvidenceDimension.FEASIBILITY
            if is_work_format
            else EvidenceDimension.EVIDENCE_CONFIDENCE
        ),
        question_code=code,
        question=(
            "Is the role remote or office-based?"
            if is_work_format
            else "What is the role's reporting line?"
        ),
        citations=(
            "evidence:feasible_work_arrangement_explicit"
            if is_work_format
            else "evidence:reporting_line_unknown",
        ),
    )


def test_investigate_daily_delivery_requires_exactly_one_governed_question() -> None:
    """Mutation caught: vague, multiple, or policy-unknown questions consume attention."""
    claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.claim_code != "evidence_complete_explicit"
    ) + (
        _claim(
            EvidenceDimension.EVIDENCE_CONFIDENCE,
            "reporting_line_unknown",
            status=EvidenceClaimStatus.UNKNOWN,
        ),
    )
    one = _run(synthesis=_synthesis(claims=claims, questions=(_question(),))).assessment
    assert one.system_verdict is SystemVerdict.INVESTIGATE
    assert one.daily_digest_eligible is True
    assert one.recommended_action_kind is RecommendedActionKind.RESEARCH
    assert one.recommended_question.question_code == "clarify_reporting_line"

    none = _run(synthesis=_synthesis(claims=claims)).assessment
    assert none.system_verdict is SystemVerdict.SAVE
    assert none.daily_digest_eligible is False

    two = _run(
        synthesis=_synthesis(
            claims=claims,
            questions=(_question(), _question("clarify_work_format")),
        )
    ).assessment
    assert two.daily_digest_eligible is False


@pytest.mark.parametrize(
    "case",
    ["multiple_material_unknowns", "unsupported_citations", "question_on_non_unknown"],
)
def test_investigate_question_binds_one_specific_unknown_fact(case: str) -> None:
    """Mutation caught: code/text match substitutes for unknown-claim provenance."""
    claims = tuple(
        claim for claim in _strong_claims() if claim.claim_code != "evidence_complete_explicit"
    )
    question = _question()
    if case == "question_on_non_unknown":
        synthesis = _synthesis(claims=_strong_claims(), questions=(question,))
    else:
        claims += (
            _claim(
                EvidenceDimension.EVIDENCE_CONFIDENCE,
                "reporting_line_unknown",
                status=EvidenceClaimStatus.UNKNOWN,
            ),
        )
        if case == "multiple_material_unknowns":
            claims += (
                _claim(
                    EvidenceDimension.FEASIBILITY,
                    "work_format_unknown",
                    status=EvidenceClaimStatus.UNKNOWN,
                ),
            )
        else:
            question = question.model_copy(update={"citations": ("evidence:unrelated",)})
        synthesis = _synthesis(claims=claims, questions=(question,))
    assessment = _run(synthesis=synthesis).assessment
    assert assessment.system_verdict is not SystemVerdict.INVESTIGATE
    assert assessment.daily_digest_eligible is (
        assessment.system_verdict is SystemVerdict.PRIORITY
    )


def test_save_daily_slot_requires_qualified_exploration_information_value() -> None:
    """Mutation caught: ordinary Save or unqualified exploration consumes a daily slot."""
    weak_claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.claim_code
        != "evidence_complete_explicit"
    ) + (
        _claim(
            EvidenceDimension.EVIDENCE_CONFIDENCE,
            "exploration_industry_hypothesis_unknown",
            status=EvidenceClaimStatus.UNKNOWN,
        ),
    )
    exploration = SelectionEvidenceV2(
        hypothesis_id="hypothesis:industry-adjacency",
        hypothesis_claim_ids=("claim:exploration_industry_hypothesis_unknown",),
        information_value="Tests transferability into an adjacent scaled industry.",
        daily_slot_available=True,
    )
    assessment = _run(
        synthesis=_synthesis(claims=weak_claims), selection=exploration
    ).assessment
    assert assessment.system_verdict is SystemVerdict.SAVE
    assert assessment.selection_mode is SelectionMode.EXPLORATION
    assert assessment.daily_digest_eligible is True

    no_selection = _run(
        synthesis=_synthesis(claims=weak_claims),
        selection=SelectionEvidenceV2(),
    )
    assert no_selection.status is DecisionRunStatus.ASSESSED
    assert no_selection.assessment.selection_mode is SelectionMode.CORE
    assert no_selection.assessment.daily_digest_eligible is False


def test_reject_never_delivers_as_an_opportunity() -> None:
    """Mutation caught: rejected vacancy leaks into daily or urgent opportunity delivery."""
    assessment = _run(
        synthesis=_synthesis(
            claims=_strong_claims()
            + (_claim(EvidenceDimension.MANDATE_FIT, "pure_delivery_scope_explicit"),)
        )
    ).assessment
    assert assessment.system_verdict is SystemVerdict.REJECT
    assert assessment.daily_digest_eligible is False
    assert assessment.urgent_eligible is False
    assert assessment.destinations == ("rejection_ledger",)


def test_exploration_requires_named_axis_and_multi_axis_exception() -> None:
    """Mutation caught: unnamed or casual multi-axis exploration is admitted."""
    with pytest.raises(ValidationError, match="hypothesis"):
        SelectionEvidenceV2(
            hypothesis_claim_ids=("claim:exploration_industry_hypothesis_unknown",),
            information_value="Useful.",
            daily_slot_available=True,
        )
    multi_claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.claim_code
        != "evidence_complete_explicit"
    ) + (
        _claim(
            EvidenceDimension.EVIDENCE_CONFIDENCE,
            "exploration_industry_hypothesis_unknown",
            status=EvidenceClaimStatus.UNKNOWN,
        ),
        _claim(
            EvidenceDimension.EVIDENCE_CONFIDENCE,
            "exploration_geography_hypothesis_unknown",
            status=EvidenceClaimStatus.UNKNOWN,
        ),
    )
    no_exception = SelectionEvidenceV2(
        hypothesis_id="hypothesis:two-axis",
        hypothesis_claim_ids=(
            "claim:exploration_industry_hypothesis_unknown",
            "claim:exploration_geography_hypothesis_unknown",
        ),
        information_value="Tests a named cross-axis thesis.",
        daily_slot_available=True,
    )
    result = _run(synthesis=_synthesis(claims=multi_claims), selection=no_exception)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.failure_reason == "selection_evidence_invalid"

    with pytest.raises(ValidationError, match="canonical"):
        SelectionEvidenceV2(
            hypothesis_id="hypothesis:two-axis",
            hypothesis_claim_ids=("claim:exploration_industry_hypothesis_unknown",),
            information_value="   ",
            daily_slot_available=True,
        )

    allowed = SelectionEvidenceV2(
        hypothesis_id="hypothesis:two-axis",
        hypothesis_claim_ids=(
            "claim:exploration_industry_hypothesis_unknown",
            "claim:exploration_geography_hypothesis_unknown",
        ),
        multi_axis_exception=MultiAxisExceptionV2(
            exception_id="owner-exception:2026-08-16",
            axes=(ExplorationAxis.GEOGRAPHY, ExplorationAxis.INDUSTRY),
            authority_ref=ImmutableArtifactRef(
                artifact_id="owner-exception:2026-08-16",
                version="1.0.0",
                sha256="9" * 64,
            ),
        ),
        information_value="Tests a named cross-axis thesis.",
        daily_slot_available=True,
    )
    assessment = _run(
        synthesis=_synthesis(claims=multi_claims), selection=allowed
    ).assessment
    assert assessment.selection_mode is SelectionMode.EXPLORATION
    assert assessment.exploration_axes == ("geography", "industry")
    assert assessment.single_reaction_updates_hypothesis is False


def test_core_qualified_role_cannot_be_relabelled_exploration() -> None:
    """Mutation caught: unfamiliar context changes an already-Core opportunity's mode."""
    hypothesis_claim = _claim(
        EvidenceDimension.EVIDENCE_CONFIDENCE,
        "exploration_industry_hypothesis_unknown",
        status=EvidenceClaimStatus.UNKNOWN,
    )
    request = SelectionEvidenceV2(
        hypothesis_id="hypothesis:new-industry",
        hypothesis_claim_ids=(hypothesis_claim.claim_id,),
        information_value="Unfamiliar industry.",
        daily_slot_available=True,
    )
    assert _run(
        selection=request,
        synthesis=_synthesis(claims=_strong_claims() + (hypothesis_claim,)),
    ).assessment.selection_mode is SelectionMode.CORE


def test_unfamiliar_context_alone_cannot_create_exploration() -> None:
    """Mutation caught: unfamiliar industry/company/geography becomes authority."""
    unfamiliar = _claim(
        EvidenceDimension.EVIDENCE_CONFIDENCE,
        "unfamiliar_industry_observed",
        status=EvidenceClaimStatus.UNKNOWN,
    )
    selection = SelectionEvidenceV2(
        hypothesis_id="hypothesis:unfamiliar-only",
        hypothesis_claim_ids=(unfamiliar.claim_id,),
        information_value="Tests unfamiliarity only.",
        daily_slot_available=True,
    )
    result = _run(
        selection=selection,
        synthesis=_synthesis(
            claims=tuple(
                claim
                for claim in _strong_claims()
                if claim.claim_code != "evidence_complete_explicit"
            )
            + (unfamiliar,)
        ),
    )
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.failure_reason == "selection_evidence_invalid"


@pytest.mark.parametrize(
    ("action", "current", "review", "target"),
    [
        (CompanyAction.NOMINATE, None, None, WatchlistStatus.CANDIDATE),
        (CompanyAction.PROMOTE, WatchlistStatus.CANDIDATE, ReviewState.CURRENT, WatchlistStatus.ACTIVE),
        (CompanyAction.PROMOTE, WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE, WatchlistStatus.ACTIVE),
        (CompanyAction.RETAIN, WatchlistStatus.ACTIVE, ReviewState.CURRENT, WatchlistStatus.ACTIVE),
        (CompanyAction.RETAIN, WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE, WatchlistStatus.ACTIVE),
        (CompanyAction.DEPRIORITIZE, WatchlistStatus.CANDIDATE, ReviewState.CURRENT, WatchlistStatus.DEPRIORITIZED),
        (CompanyAction.DEPRIORITIZE, WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE, WatchlistStatus.DEPRIORITIZED),
        (CompanyAction.DEPRIORITIZE, WatchlistStatus.ACTIVE, ReviewState.CURRENT, WatchlistStatus.DEPRIORITIZED),
        (CompanyAction.DEPRIORITIZE, WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE, WatchlistStatus.DEPRIORITIZED),
        (CompanyAction.REJECT, WatchlistStatus.CANDIDATE, ReviewState.CURRENT, WatchlistStatus.REJECTED),
        (CompanyAction.REJECT, WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE, WatchlistStatus.REJECTED),
        (CompanyAction.REJECT, WatchlistStatus.ACTIVE, ReviewState.CURRENT, WatchlistStatus.REJECTED),
        (CompanyAction.REJECT, WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE, WatchlistStatus.REJECTED),
        (CompanyAction.REJECT, WatchlistStatus.DEPRIORITIZED, ReviewState.CURRENT, WatchlistStatus.REJECTED),
        (CompanyAction.REJECT, WatchlistStatus.DEPRIORITIZED, ReviewState.REVIEW_DUE, WatchlistStatus.REJECTED),
        (CompanyAction.EXPIRE, WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE, WatchlistStatus.EXPIRED),
        (CompanyAction.EXPIRE, WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE, WatchlistStatus.EXPIRED),
        (CompanyAction.EXPIRE, WatchlistStatus.DEPRIORITIZED, ReviewState.REVIEW_DUE, WatchlistStatus.EXPIRED),
    ],
)
def test_company_action_validates_exact_transition_preconditions_independently(
    action: CompanyAction,
    current: WatchlistStatus | None,
    review: ReviewState | None,
    target: WatchlistStatus,
) -> None:
    """Mutation caught: vacancy verdict drives or bypasses a watchlist precondition."""
    request = CompanyActionRequestV2(
        action=action,
        snapshot=_company_snapshot(current, review),
    )
    company = _run(company_action=request).assessment.company_action
    assert company.action is action
    assert company.target_status is target
    assert company.state_mutated is False
    assert company.snapshot_sha256 == request.snapshot.content_sha256
    assert company.thesis_input_sha256 == request.snapshot.thesis_input_sha256
    assert _run(company_action=request).assessment.system_verdict is SystemVerdict.PRIORITY


def test_rejected_vacancy_can_independently_nominate_company_without_mutation() -> None:
    """Mutation caught: vacancy Reject suppresses or applies a company-level action."""
    company_request = CompanyActionRequestV2(
        action=CompanyAction.NOMINATE,
        snapshot=_company_snapshot(None, None),
    )
    claims = _strong_claims() + (
        _claim(EvidenceDimension.MANDATE_FIT, "pure_delivery_scope_explicit"),
    )
    assessment = _run(
        company_action=company_request,
        synthesis=_synthesis(claims=claims),
    ).assessment
    assert assessment.system_verdict is SystemVerdict.REJECT
    assert assessment.company_action.action is CompanyAction.NOMINATE
    assert assessment.company_action.state_mutated is False
    assert set(assessment.destinations) == {
        "rejection_ledger",
        "weekly_company_section",
    }


def test_invalid_company_transition_fails_closed_without_mutation() -> None:
    """Mutation caught: terminal/mismatched lifecycle state is treated as actionable."""
    request = CompanyActionRequestV2(
        action=CompanyAction.PROMOTE,
        snapshot=_company_snapshot(WatchlistStatus.ACTIVE, ReviewState.CURRENT),
    )
    result = _run(company_action=request)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == "invalid_company_action_preconditions"


def test_absent_company_cannot_be_review_due_or_act_without_exact_snapshot() -> None:
    """Mutation caught: nominate trusts caller review/evidence booleans without snapshot."""
    with pytest.raises(ValueError, match="absent company"):
        _company_snapshot(None, ReviewState.REVIEW_DUE)
    with pytest.raises(ValidationError):
        CompanyActionRequestV2.model_validate({"action": "nominate"})
    snapshot = _company_snapshot(WatchlistStatus.ACTIVE, ReviewState.CURRENT)
    tampered = snapshot.model_copy(
        update={"current_status": WatchlistStatus.CANDIDATE}
    )
    with pytest.raises(ValidationError, match="content hash mismatch"):
        CompanyActionRequestV2(
            action=CompanyAction.PROMOTE,
            snapshot=tampered,
        )


def test_urgent_requires_priority_external_time_sensitivity_learned_after_digest() -> None:
    """Mutation caught: recency/confidence or pre-digest timing makes an item urgent."""
    urgent = UrgencyEvidenceV2(
        kind=UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
        external_evidence_ref="vacancy:closing-date",
        learned_at=NOW + timedelta(minutes=30),
        deadline_at=NOW + timedelta(hours=40),
    )
    assert _run(urgency_evidence=urgent).assessment.urgent_eligible is False
    timing_claim = _claim(
        EvidenceDimension.EVIDENCE_CONFIDENCE,
        "closing_within_48_hours_explicit",
        pointer="vacancy:closing-date",
    )
    assessment = _run(
        urgency_evidence=urgent,
        synthesis=_synthesis(claims=_strong_claims() + (timing_claim,)),
    ).assessment
    assert assessment.urgent_eligible is True
    assert assessment.destinations == ("urgent_exception",)

    old = urgent.model_copy(update={"learned_at": NOW - timedelta(minutes=1)})
    assert _run(
        urgency_evidence=old,
        synthesis=_synthesis(claims=_strong_claims() + (timing_claim,)),
    ).assessment.urgent_eligible is False

    assert _run(
        urgency_evidence=urgent,
        evaluated_at=NOW + timedelta(hours=41),
        synthesis=_synthesis(claims=_strong_claims() + (timing_claim,)),
    ).assessment.urgent_eligible is False

    recency_only = UrgencyEvidenceV2(
        kind=UrgencyEvidenceKind.RECENCY_ONLY,
        external_evidence_ref="vacancy:posted-at",
        learned_at=NOW + timedelta(minutes=30),
    )
    assert _run(urgency_evidence=recency_only).assessment.urgent_eligible is False


def test_urgent_48_hour_boundary_is_measured_from_evaluated_at() -> None:
    """Mutation caught: 48h window is measured from learned_at instead of evaluation."""
    timing_claim = _claim(
        EvidenceDimension.EVIDENCE_CONFIDENCE,
        "closing_within_48_hours_explicit",
        pointer="vacancy:closing-date",
    )
    evaluated = NOW + timedelta(hours=2)
    exact = UrgencyEvidenceV2(
        kind=UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
        external_evidence_ref="vacancy:closing-date",
        learned_at=NOW + timedelta(minutes=30),
        deadline_at=evaluated + timedelta(hours=48),
    )
    claims = _strong_claims() + (timing_claim,)
    assert _run(
        urgency_evidence=exact,
        evaluated_at=evaluated,
        synthesis=_synthesis(claims=claims),
    ).assessment.urgent_eligible is True
    too_late = exact.model_copy(
        update={"deadline_at": evaluated + timedelta(hours=48, microseconds=1)}
    )
    assert _run(
        urgency_evidence=too_late,
        evaluated_at=evaluated,
        synthesis=_synthesis(claims=claims),
    ).assessment.urgent_eligible is False
    learned_at_digest = exact.model_copy(update={"learned_at": NOW})
    assert _run(
        urgency_evidence=learned_at_digest,
        evaluated_at=evaluated,
        synthesis=_synthesis(claims=claims),
    ).assessment.urgent_eligible is False
    with pytest.raises(ValidationError, match="after evaluation"):
        _request(
            urgency_evidence=exact.model_copy(
                update={"learned_at": evaluated + timedelta(seconds=1)}
            ),
            evaluated_at=evaluated,
            synthesis=_synthesis(claims=claims),
        )


@pytest.mark.parametrize(
    "mismatch",
    [
        "decision_contract_sha256",
        "product_contract_sha256",
        "career_profile_sha256",
        "candidate_facts_sha256",
        "search_contract_sha256",
        "product_sot_sha256",
        "company_evidence_contract_sha256",
        "evidence_synthesis_contract_sha256",
        "provider_input_sha256",
        "provider_output_sha256",
    ],
)
def test_every_contract_and_provider_hash_mismatch_fails_closed(mismatch: str) -> None:
    """Mutation caught: one immutable input can drift without suppressing assessment."""
    refs = _references(**{mismatch: "f" * 64})
    result = _run(references=refs)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == f"immutable_reference_mismatch:{mismatch}"


@pytest.mark.parametrize(
    "mismatch",
    [
        "semantic_contract_sha256",
        "evidence_snapshot_sha256",
        "company_evidence_bundle_sha256",
    ],
)
def test_traced_dynamic_hashes_must_match_authoritative_task_objects(
    mismatch: str,
) -> None:
    """Mutation caught: trace-only hash is accepted without Task8/9 object agreement."""
    authority = _authority_inputs(**{mismatch: "f" * 64})
    result = _run(authority_inputs=authority)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == f"immutable_reference_mismatch:{mismatch}"


@pytest.mark.parametrize(
    "family",
    [
        "authority_hashes",
        "provider_identity",
        "positive_claims",
        "negative_claims",
        "hard_gate_claims",
        "warning_claims",
        "qualified_questions",
        "urgency_claims",
        "monetization_exception_claims",
        "exploration_claim_axes",
        "core_policy_requirements",
    ],
)
def test_effective_policy_hash_blocks_every_nested_policy_mutation(family: str) -> None:
    """Mutation caught: frozen wrapper allows nested policy drift under source hash."""
    loaded = load_decision_policy(POLICY_PATH)
    source_hash = loaded.source_sha256
    if family == "authority_hashes":
        object.__setattr__(
            loaded.policy.authority_hashes,
            "product_contract_sha256",
            "f" * 64,
        )
    elif family == "provider_identity":
        loaded.policy.provider_identity["model_id"] = "mutated-model"
    elif family == "positive_claims":
        loaded.policy.positive_claims[EvidenceDimension.FEASIBILITY] = ()
    elif family == "negative_claims":
        loaded.policy.negative_claims[EvidenceDimension.COMPANY_FIT] = ()
    elif family == "hard_gate_claims":
        loaded.policy.hard_gate_claims["mutated_gate"] = HardGate.AFRICA_PROACTIVE
    elif family == "warning_claims":
        loaded.policy.warning_claims["mutated_warning"] = "changed"
    elif family == "qualified_questions":
        object.__setattr__(loaded.policy, "qualified_questions", ())
    elif family == "urgency_claims":
        loaded.policy.urgency_claims[
            UrgencyEvidenceKind.RECRUITER_DEADLINE
        ] = "mutated"
    elif family == "monetization_exception_claims":
        object.__setattr__(loaded.policy, "monetization_exception_claims", ("a", "b"))
    elif family == "exploration_claim_axes":
        loaded.policy.exploration_claim_axes[
            "mutated_exploration_claim"
        ] = ExplorationAxis.INDUSTRY
    else:
        loaded.policy.core_policy_requirements[
            EvidenceDimension.FEASIBILITY
        ] = ("mutated",)
    assert loaded.source_sha256 == source_hash
    result = run_decision_v2(_request(), policy=loaded)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.failure_reason == "policy_effective_hash_mismatch"


@pytest.mark.parametrize(
    "status",
    [
        EvidenceSynthesisStatus.TIMEOUT,
        EvidenceSynthesisStatus.INVALID_SCHEMA,
        EvidenceSynthesisStatus.MISSING_CITATION,
        EvidenceSynthesisStatus.REFUSAL,
        EvidenceSynthesisStatus.PROVIDER_OUTAGE,
    ],
)
def test_synthesis_failure_emits_no_stage4_or_legacy_fallback(
    status: EvidenceSynthesisStatus,
) -> None:
    """Mutation caught: provider failure falls back to legacy assessment or stage 4."""
    result = _run(synthesis=_synthesis(status=status))
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == f"synthesis_not_deliverable:{status.value}"


def test_provider_output_content_must_match_its_task10_hash() -> None:
    """Mutation caught: claims change after Task 10 while its output hash stays pinned."""
    synthesis = _synthesis()
    changed = synthesis.model_copy(
        update={"claims": tuple(reversed(synthesis.claims))}
    )
    result = _run(synthesis=changed)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == "provider_output_content_hash_mismatch"


def test_evidence_order_does_not_change_decision_semantics() -> None:
    """Mutation caught: provider tuple order changes deterministic policy output."""
    claims = _strong_claims()
    first = _run(synthesis=_synthesis(claims=claims))
    expected = first.assessment.model_dump(exclude={"trace"})
    expected_evidence_hash = first.assessment.trace.normalized_evidence_sha256
    for prefix in permutations(claims[:4]):
        replay = _run(synthesis=_synthesis(claims=prefix + claims[4:]))
        assert replay.assessment.model_dump(exclude={"trace"}) == expected
        assert replay.assessment.trace.normalized_evidence_sha256 == expected_evidence_hash


def test_replay_is_byte_stable_and_trace_carries_all_hashes() -> None:
    """Mutation caught: clock/order/default serialization leaks into replay output."""
    first = _run()
    second = _run()
    assert canonical_decision_bytes(first) == canonical_decision_bytes(second)
    trace = first.assessment.trace
    assert trace.policy_version == "product-search-decision-v2.0.0"
    assert trace.references == _references()
    assert trace.authority_inputs == _authority_inputs()
    assert trace.canonical_sha256 == (
        "58122bc5e404e13f960fa3ece92771c35c2dbc5cb3826bbb047d1961b5aee363"
    )


def test_equivalent_timezone_instants_have_identical_bytes_and_trace_hash() -> None:
    """Mutation caught: equivalent offset spelling leaks into canonical hash."""
    timing_claim = _claim(
        EvidenceDimension.EVIDENCE_CONFIDENCE,
        "closing_within_48_hours_explicit",
        pointer="vacancy:closing-date",
    )
    synthesis = _synthesis(claims=_strong_claims() + (timing_claim,))
    urgency = UrgencyEvidenceV2(
        kind=UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
        external_evidence_ref="vacancy:closing-date",
        learned_at=NOW + timedelta(minutes=30),
        deadline_at=NOW + timedelta(hours=40),
    )
    utc_result = _run(urgency_evidence=urgency, synthesis=synthesis)
    plus_six = timezone(timedelta(hours=6))
    offset_result = _run(
        daily_digest_at=NOW.astimezone(plus_six),
        assessed_at=(NOW + timedelta(minutes=45)).astimezone(plus_six),
        evaluated_at=(NOW + timedelta(hours=1)).astimezone(plus_six),
        urgency_evidence=UrgencyEvidenceV2(
            kind=urgency.kind,
            external_evidence_ref=urgency.external_evidence_ref,
            learned_at=urgency.learned_at.astimezone(plus_six),
            deadline_at=urgency.deadline_at.astimezone(plus_six),
        ),
        synthesis=synthesis,
    )
    assert canonical_decision_bytes(utc_result) == canonical_decision_bytes(offset_result)
    assert (
        utc_result.assessment.trace.canonical_sha256
        == offset_result.assessment.trace.canonical_sha256
    )
    clock = offset_result.assessment.trace.clock
    assert all(
        value is None or value.utcoffset() == timedelta(0)
        for value in (
            clock.assessed_at,
            clock.evaluated_at,
            clock.daily_digest_at,
            clock.urgency_learned_at,
            clock.urgency_deadline_at,
        )
    )


@pytest.mark.parametrize(
    "field",
    ["daily_digest_at", "assessed_at", "evaluated_at", "learned_at", "deadline_at"],
)
def test_every_canonical_clock_input_rejects_naive_datetime(field: str) -> None:
    """Mutation caught: one naive nested clock value enters canonical serialization."""
    naive = datetime(2026, 8, 16, 10, 0)
    if field in {"learned_at", "deadline_at"}:
        payload = {
            "kind": UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
            "external_evidence_ref": "vacancy:closing-date",
            "learned_at": NOW + timedelta(minutes=30),
            "deadline_at": NOW + timedelta(hours=40),
        }
        payload[field] = naive
        with pytest.raises(ValidationError):
            UrgencyEvidenceV2.model_validate(payload)
    else:
        with pytest.raises(ValidationError):
            _request(**{field: naive})


def test_assessment_clock_cannot_follow_deterministic_evaluation_clock() -> None:
    """Mutation caught: a future assessment is hashed as already evaluated."""
    with pytest.raises(ValidationError, match="assessed_at cannot follow evaluated_at"):
        _request(assessed_at=NOW + timedelta(hours=2))


def test_fixture_declares_all_sot_invariant_cases() -> None:
    """Mutation caught: adversarial replay fixture silently drops a SoT invariant."""
    import yaml

    payload = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "2.0.0"
    assert [case["id"] for case in payload["cases"]] == [
        "narrow_monetization_exception",
        "b2b_not_negative",
        "remote_us_not_onsite_gate",
        "crypto_company_concern_only",
        "platform_engineering_not_platform_business",
        "adjacent_executive_function_can_pass",
        "timezone_compensation_not_hard_gates",
        "unknown_not_negative",
    ]
    for case in payload["cases"]:
        removed = set(case["remove_claims"])
        claims = tuple(
            claim for claim in _strong_claims() if claim.claim_code not in removed
        ) + tuple(
            _claim(
                EvidenceDimension(item[0]),
                item[1],
                status=EvidenceClaimStatus(item[2]),
            )
            for item in case["add_claims"]
        )
        assessment = _run(synthesis=_synthesis(claims=claims)).assessment
        expected = case["expected"]
        assert assessment.system_verdict.value == expected["verdict"], case["id"]
        assert assessment.hard_gate_eligible is expected["hard_gate_eligible"], case["id"]
        if warning := expected.get("warning"):
            assert warning in assessment.warnings, case["id"]
        if blocker := expected.get("blocker"):
            assert blocker in assessment.blockers, case["id"]
