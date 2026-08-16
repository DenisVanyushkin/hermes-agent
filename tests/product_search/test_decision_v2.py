from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from itertools import permutations
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from job_intel.product_search.contracts import (
    CompanyAction,
    HardGate,
    RecommendedActionKind,
    ReviewState,
    SelectionMode,
    SystemVerdict,
    WatchlistStatus,
)
from job_intel.product_search.decision_v2 import (
    CompanyActionRequestV2,
    DecisionImmutableReferencesV2,
    DecisionRequestV2,
    DecisionRunStatus,
    DimensionOutcome,
    ExplorationRequestV2,
    StageEvidenceV2,
    UrgencyEvidenceKind,
    UrgencyEvidenceV2,
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


def _references(**overrides: str) -> DecisionImmutableReferencesV2:
    loaded = load_decision_policy(POLICY_PATH)
    values = {
        **loaded.policy.authority_hashes.model_dump(),
        "decision_contract_sha256": loaded.source_sha256,
        "semantic_contract_sha256": "b" * 64,
        "evidence_snapshot_sha256": "e" * 64,
        "company_evidence_bundle_sha256": "d" * 64,
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
        "synthesis": synthesis,
        "selection": ExplorationRequestV2(
            requested_mode=SelectionMode.CORE,
            qualifies_under_core_policy=True,
        ),
        "company_action": None,
        "urgency_evidence": None,
        "daily_digest_at": NOW,
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
        if claim.dimension is not EvidenceDimension.EVIDENCE_CONFIDENCE
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
        if claim.dimension is not EvidenceDimension.EVIDENCE_CONFIDENCE
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


def test_save_daily_slot_requires_qualified_exploration_information_value() -> None:
    """Mutation caught: ordinary Save or unqualified exploration consumes a daily slot."""
    weak_claims = tuple(
        claim
        for claim in _strong_claims()
        if claim.dimension is not EvidenceDimension.EVIDENCE_CONFIDENCE
    ) + (
        _claim(
            EvidenceDimension.EVIDENCE_CONFIDENCE,
            "evidence_strength_unknown",
            status=EvidenceClaimStatus.UNKNOWN,
        ),
    )
    exploration = ExplorationRequestV2(
        requested_mode=SelectionMode.EXPLORATION,
        qualifies_under_core_policy=False,
        hypothesis_id="hypothesis:industry-adjacency",
        axes=("industry",),
        information_value="Tests transferability into an adjacent scaled industry.",
        daily_slot_available=True,
    )
    assessment = _run(
        synthesis=_synthesis(claims=weak_claims), selection=exploration
    ).assessment
    assert assessment.system_verdict is SystemVerdict.SAVE
    assert assessment.selection_mode is SelectionMode.EXPLORATION
    assert assessment.daily_digest_eligible is True

    core = _run(synthesis=_synthesis(claims=weak_claims)).assessment
    assert core.system_verdict is SystemVerdict.SAVE
    assert core.daily_digest_eligible is False


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
        ExplorationRequestV2(
            requested_mode=SelectionMode.EXPLORATION,
            qualifies_under_core_policy=False,
            axes=("industry",),
            information_value="Useful.",
            daily_slot_available=True,
        )
    with pytest.raises(ValidationError, match="multi-axis exception"):
        ExplorationRequestV2(
            requested_mode=SelectionMode.EXPLORATION,
            qualifies_under_core_policy=False,
            hypothesis_id="hypothesis:two-axis",
            axes=("industry", "geography"),
            information_value="Useful.",
            daily_slot_available=True,
        )
    allowed = ExplorationRequestV2(
        requested_mode=SelectionMode.EXPLORATION,
        qualifies_under_core_policy=False,
        hypothesis_id="hypothesis:two-axis",
        axes=("industry", "geography"),
        multi_axis_exception_id="owner-exception:2026-08-16",
        information_value="Tests a named cross-axis thesis.",
        daily_slot_available=True,
    )
    assessment = _run(selection=allowed).assessment
    assert assessment.selection_mode is SelectionMode.EXPLORATION
    assert assessment.single_reaction_updates_hypothesis is False


def test_core_qualified_role_cannot_be_relabelled_exploration() -> None:
    """Mutation caught: unfamiliar context changes an already-Core opportunity's mode."""
    request = ExplorationRequestV2(
        requested_mode=SelectionMode.EXPLORATION,
        qualifies_under_core_policy=True,
        hypothesis_id="hypothesis:new-industry",
        axes=("industry",),
        information_value="Unfamiliar industry.",
        daily_slot_available=True,
    )
    assert _run(selection=request).assessment.selection_mode is SelectionMode.CORE


@pytest.mark.parametrize(
    ("action", "current", "review", "target"),
    [
        (CompanyAction.NOMINATE, None, ReviewState.CURRENT, WatchlistStatus.CANDIDATE),
        (CompanyAction.PROMOTE, WatchlistStatus.CANDIDATE, ReviewState.CURRENT, WatchlistStatus.ACTIVE),
        (CompanyAction.RETAIN, WatchlistStatus.ACTIVE, ReviewState.CURRENT, WatchlistStatus.ACTIVE),
        (CompanyAction.DEPRIORITIZE, WatchlistStatus.ACTIVE, ReviewState.CURRENT, WatchlistStatus.DEPRIORITIZED),
        (CompanyAction.REJECT, WatchlistStatus.CANDIDATE, ReviewState.CURRENT, WatchlistStatus.REJECTED),
        (CompanyAction.EXPIRE, WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE, WatchlistStatus.EXPIRED),
    ],
)
def test_company_action_validates_exact_transition_preconditions_independently(
    action: CompanyAction,
    current: WatchlistStatus | None,
    review: ReviewState,
    target: WatchlistStatus,
) -> None:
    """Mutation caught: vacancy verdict drives or bypasses a watchlist precondition."""
    request = CompanyActionRequestV2(
        action=action,
        current_status=current,
        review_state=review,
        evidence_sufficient=True,
        fit_thesis="The company has a cited strategic fit thesis.",
        proposed_action=action,
    )
    company = _run(company_action=request).assessment.company_action
    assert company.action is action
    assert company.target_status is target
    assert company.state_mutated is False
    assert _run(company_action=request).assessment.system_verdict is SystemVerdict.PRIORITY


def test_rejected_vacancy_can_independently_nominate_company_without_mutation() -> None:
    """Mutation caught: vacancy Reject suppresses or applies a company-level action."""
    company_request = CompanyActionRequestV2(
        action=CompanyAction.NOMINATE,
        current_status=None,
        review_state=ReviewState.CURRENT,
        evidence_sufficient=True,
        fit_thesis="A cited company thesis remains useful independent of this vacancy.",
        proposed_action=CompanyAction.NOMINATE,
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
        current_status=WatchlistStatus.ACTIVE,
        review_state=ReviewState.CURRENT,
        evidence_sufficient=True,
        fit_thesis="A cited thesis.",
        proposed_action=CompanyAction.PROMOTE,
    )
    result = _run(company_action=request)
    assert result.status is DecisionRunStatus.FAIL_CLOSED
    assert result.assessment is None
    assert result.failure_reason == "invalid_company_action_preconditions"


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
    assert trace.canonical_sha256 == (
        "ca2fdca945076064feb7948489edd7461f3e6fb7d57d5d3be09d2f83484c2e34"
    )


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
