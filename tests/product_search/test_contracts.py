from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from job_intel.product_search.contracts import (
    AmbiguousLegacyMappingError,
    AssessmentInputV1,
    AssessmentReferences,
    CompanyAction,
    DecisionDimensionsInput,
    DimensionEvidenceInput,
    DimensionEvidenceState,
    DiscoveryOrigin,
    DiscoveryOriginFacts,
    ImmutableArtifactRef,
    LegacyAssessmentV1,
    ProductDecisionFields,
    RecommendedActionKind,
    ReviewState,
    SelectionMode,
    SystemVerdict,
    UserDecision,
    WatchlistStatus,
    load_career_profile,
    map_legacy_assessment,
    resolve_discovery_origin,
)
from job_intel.product_search.search_contract import (
    SelectionMode as SearchContractSelectionMode,
    resolve_selection_mode,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/product_search/fixtures/contracts"
PROFILE_PATH = ROOT / "config/product_search/career_profile.v2.yaml"


def _ref(artifact_id: str) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_id=artifact_id,
        version="1.0.0",
        sha256="a" * 64,
    )


def _dimensions() -> DecisionDimensionsInput:
    available = DimensionEvidenceInput(
        state=DimensionEvidenceState.EVIDENCE_AVAILABLE,
        evidence_refs=("snapshot:fact-1",),
    )
    unknown = DimensionEvidenceInput(
        state=DimensionEvidenceState.UNKNOWN,
        unknown_reasons=("evidence_not_published",),
    )
    return DecisionDimensionsInput(
        feasibility=available,
        mandate_fit=available,
        company_fit=unknown,
        transferability=available,
        career_value=unknown,
        evidence_confidence=available,
    )


def _assessment_payload() -> dict[str, object]:
    refs = {
        name: _ref(name).model_dump(mode="json")
        for name in (
            "profile_ref",
            "candidate_facts_ref",
            "semantic_contract_ref",
            "search_contract_ref",
            "policy_ref",
            "evidence_snapshot_ref",
        )
    }
    return {
        "schema_version": "1.0.0",
        "assessment_id": "assessment-redacted-001",
        "references": refs,
        "dimensions": _dimensions().model_dump(mode="json"),
    }


def test_product_vocabularies_are_closed_to_exact_sot_values() -> None:
    """Mutation caught: accepting aliases or synthetic lifecycle values."""
    assert {item.value for item in DiscoveryOrigin} == {
        "Open Market",
        "Strategic Watchlist",
    }
    assert {item.value for item in SelectionMode} == {"Core", "Exploration"}
    assert {item.value for item in WatchlistStatus} == {
        "candidate",
        "active",
        "deprioritized",
        "rejected",
        "expired",
    }
    assert {item.value for item in ReviewState} == {"current", "review_due"}
    assert {item.value for item in CompanyAction} == {
        "nominate",
        "promote",
        "retain",
        "deprioritize",
        "reject",
        "expire",
    }
    assert {item.value for item in SystemVerdict} == {
        "Priority",
        "Investigate",
        "Save",
        "Reject",
    }
    assert {item.value for item in UserDecision} == {
        "Pursue",
        "Investigate",
        "Save for later",
        "Not interesting",
        "Not feasible",
        "Wrong or stale data",
    }
    assert {item.value for item in RecommendedActionKind} == {
        "research",
        "feasibility",
        "networking",
        "outreach",
        "referral",
        "application",
    }

    for enum_type, invalid_values in (
        (SelectionMode, ("core", "Unknown", "None")),
        (WatchlistStatus, ("None", "nominated", "remove")),
        (ReviewState, ("None", "defer", "expired")),
        (CompanyAction, ("None", "defer", "remove", "nominated")),
    ):
        for value in invalid_values:
            with pytest.raises(ValueError):
                enum_type(value)


def test_selection_mode_has_one_canonical_type_and_ignores_unfamiliar_context() -> None:
    """Mutation caught: a second enum class or unfamiliar context forcing Exploration."""
    assert SearchContractSelectionMode is SelectionMode
    assert resolve_selection_mode(
        core_qualified=True,
        uncertain_hypothesis=None,
        unfamiliar_company=True,
        unfamiliar_geography=True,
        unfamiliar_industry=True,
    ) is SelectionMode.CORE
    with pytest.raises(ValueError, match="named uncertain hypothesis"):
        resolve_selection_mode(core_qualified=False, uncertain_hypothesis=None)


def test_decision_dimensions_and_product_decisions_remain_independent() -> None:
    """Mutation caught: coercing user intent into verdict/action/company state."""
    dimensions = _dimensions()
    fields = ProductDecisionFields(
        discovery_origin=DiscoveryOrigin.OPEN_MARKET,
        selection_mode=SelectionMode.EXPLORATION,
        system_verdict=SystemVerdict.PRIORITY,
        user_decision=UserDecision.NOT_FEASIBLE,
        recommended_action=RecommendedActionKind.RESEARCH,
        company_action=CompanyAction.NOMINATE,
    )

    assert set(dimensions.model_dump()) == {
        "feasibility",
        "mandate_fit",
        "company_fit",
        "transferability",
        "career_value",
        "evidence_confidence",
    }
    assert fields.system_verdict is SystemVerdict.PRIORITY
    assert fields.user_decision is UserDecision.NOT_FEASIBLE
    assert fields.recommended_action is RecommendedActionKind.RESEARCH
    assert fields.company_action is CompanyAction.NOMINATE

    with pytest.raises(ValidationError, match="system_verdict"):
        ProductDecisionFields(
            discovery_origin=DiscoveryOrigin.OPEN_MARKET,
            selection_mode=SelectionMode.CORE,
            system_verdict=UserDecision.INVESTIGATE,
        )
    with pytest.raises(ValidationError, match="user_decision"):
        ProductDecisionFields(
            discovery_origin=DiscoveryOrigin.OPEN_MARKET,
            selection_mode=SelectionMode.CORE,
            system_verdict=SystemVerdict.INVESTIGATE,
            user_decision=SystemVerdict.INVESTIGATE,
        )


def test_dimension_unknown_is_explicit_and_known_evidence_is_cited() -> None:
    """Mutation caught: treating absent evidence as a negative or a known conclusion."""
    with pytest.raises(ValidationError, match="unknown_reasons"):
        DimensionEvidenceInput(state=DimensionEvidenceState.UNKNOWN)
    with pytest.raises(ValidationError, match="evidence_refs"):
        DimensionEvidenceInput(state=DimensionEvidenceState.EVIDENCE_AVAILABLE)

    unknown = DimensionEvidenceInput(
        state=DimensionEvidenceState.UNKNOWN,
        unknown_reasons=("sponsorship_not_stated",),
    )
    assert unknown.evidence_refs == ()
    assert unknown.unknown_reasons == ("sponsorship_not_stated",)


@pytest.mark.parametrize(
    ("status_before", "monitoring_formed", "expected"),
    [
        (WatchlistStatus.ACTIVE, True, DiscoveryOrigin.STRATEGIC_WATCHLIST),
        (WatchlistStatus.ACTIVE, False, DiscoveryOrigin.OPEN_MARKET),
        (WatchlistStatus.CANDIDATE, True, DiscoveryOrigin.OPEN_MARKET),
        (WatchlistStatus.DEPRIORITIZED, True, DiscoveryOrigin.OPEN_MARKET),
    ],
)
def test_discovery_origin_uses_only_pre_discovery_status_and_canonical_formation(
    status_before: WatchlistStatus,
    monitoring_formed: bool,
    expected: DiscoveryOrigin,
) -> None:
    """Mutation caught: candidate/current watchlist membership granting Strategic origin."""
    facts = DiscoveryOriginFacts(
        watchlist_status_before_discovery=status_before,
        watchlist_monitoring_formed_canonical_candidate=monitoring_formed,
    )
    assert resolve_discovery_origin(facts) is expected


def test_later_promotion_or_rediscovery_cannot_rewrite_primary_origin() -> None:
    """Mutation caught: recomputing immutable origin from later lifecycle events."""
    facts = DiscoveryOriginFacts(
        watchlist_status_before_discovery=WatchlistStatus.CANDIDATE,
        watchlist_monitoring_formed_canonical_candidate=False,
        existing_primary_origin=DiscoveryOrigin.OPEN_MARKET,
        later_watchlist_status=WatchlistStatus.ACTIVE,
        later_watchlist_rediscovery=True,
    )
    assert resolve_discovery_origin(facts) is DiscoveryOrigin.OPEN_MARKET


def test_assessment_input_requires_all_immutable_replay_references() -> None:
    """Mutation caught: assessment accepted without one immutable input authority."""
    assessment = AssessmentInputV1.model_validate(_assessment_payload())
    assert assessment.references.evidence_snapshot_ref.sha256 == "a" * 64

    for field in AssessmentReferences.model_fields:
        payload = _assessment_payload()
        del payload["references"][field]  # type: ignore[index]
        with pytest.raises(ValidationError):
            AssessmentInputV1.model_validate(payload)

    payload = _assessment_payload()
    payload["references"]["profile_ref"]["sha256"] = "mutable"  # type: ignore[index]
    with pytest.raises(ValidationError, match="sha256"):
        AssessmentInputV1.model_validate(payload)


def test_strict_contracts_reject_extra_fields_and_wrong_versions() -> None:
    """Mutation caught: permissive parsing hides schema drift."""
    payload = _assessment_payload()
    payload["legacy_score"] = 97
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AssessmentInputV1.model_validate(payload)

    payload = _assessment_payload()
    payload["schema_version"] = "legacy"
    with pytest.raises(ValidationError, match="schema_version"):
        AssessmentInputV1.model_validate(payload)


def test_career_profile_v2_is_cited_bounded_and_preserves_product_rules() -> None:
    """Mutation caught: uncited claims, narrowed mandate, or KZ fallback regression."""
    profile = load_career_profile(PROFILE_PATH)

    assert profile.schema_version == "2.0.0"
    assert profile.product_authority_id == "PS-SOT-2026-08-10-v1"
    assert profile.authorities.candidate_facts_ref.sha256 == (
        "7219eea2fbf04c92291254f83a76b8d2d1ef53e6004ac64ff4601c726eb9fac9"
    )
    assert profile.candidate_fact_policy.broadening == "prohibited"
    assert profile.candidate_fact_policy.derived_fields == "must_remain_labeled_derived"
    assert all(claim.candidate_fact_pointers for claim in profile.candidate_fact_claims)
    assert set(profile.mandate_role_families) == {
        "executive_product",
        "digital_business",
        "customer_growth_commercial_hybrid",
        "product_business_unit",
        "general_management",
        "growth_monetization",
        "transformation_builder",
        "hybrid_executive_exploration",
    }
    assert set(profile.transferable_patterns) == {
        "business_and_pnl_ownership",
        "monetization_and_growth",
        "portfolio_and_go_to_market",
        "organization_building",
        "operating_model_transformation",
        "turnaround",
        "new_business_launch",
        "executive_stakeholder_leadership",
    }
    assert set(profile.feasibility_unknowns) == {
        "sponsorship_outside_onsite_us",
        "reporting_line",
        "compensation",
        "timezone",
    }
    assert profile.geography_policy.kazakhstan.eligible_market is True
    assert profile.geography_policy.kazakhstan.fallback is False
    assert profile.geography_policy.kazakhstan.minimum_delivery is None
    assert profile.geography_policy.kazakhstan.lowered_bar is False
    assert profile.geography_policy.other_central_asia.independent_by_country is True
    assert profile.geography_policy.other_central_asia.inherits_kazakhstan_policy is False


def test_uncited_candidate_claim_and_profile_extra_fail_closed() -> None:
    """Mutation caught: profile silently broadens Candidate Facts."""
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["candidate_fact_claims"][0]["candidate_fact_pointers"] = []
    with pytest.raises(ValidationError, match="candidate_fact_pointers"):
        load_career_profile(payload)

    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["invented_experience"] = ["global launch ownership"]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_career_profile(payload)


def test_legacy_compatibility_is_explicit_versioned_and_fail_closed() -> None:
    """Mutation caught: silently treating ambiguous current output as Product Search."""
    accepted = LegacyAssessmentV1.model_validate_json(
        (FIXTURES / "legacy-strong.v1.json").read_text(encoding="utf-8")
    )
    mapping = map_legacy_assessment(accepted)
    assert mapping.boundary_version == "shadow-evaluator-decision/1.1.0"
    assert mapping.target_system_verdict is SystemVerdict.PRIORITY
    assert mapping.target_selection_mode is SelectionMode.CORE
    assert mapping.requires_full_reassessment is True
    assert not isinstance(mapping, AssessmentInputV1)

    ambiguous = LegacyAssessmentV1.model_validate_json(
        (FIXTURES / "legacy-promising.v1.json").read_text(encoding="utf-8")
    )
    with pytest.raises(AmbiguousLegacyMappingError, match="promising"):
        map_legacy_assessment(ambiguous)

    wrong_version = accepted.model_copy(
        update={"boundary_version": "shadow-evaluator-decision/1.0.0"}
    )
    with pytest.raises(ValueError, match="boundary_version"):
        map_legacy_assessment(wrong_version)


def test_schema_summary_snapshot_locks_required_fields_and_strictness() -> None:
    """Mutation caught: a replay reference becomes optional or extras become accepted."""
    schema = AssessmentInputV1.model_json_schema()
    refs_schema = schema["$defs"]["AssessmentReferences"]
    actual = {
        "schema_version_const": schema["properties"]["schema_version"]["const"],
        "assessment_required": schema["required"],
        "assessment_additional_properties": schema["additionalProperties"],
        "references_required": refs_schema["required"],
        "references_additional_properties": refs_schema["additionalProperties"],
    }
    expected = json.loads(
        (FIXTURES / "assessment-input.schema-summary.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert actual == expected


def test_authority_manifest_pins_profile_and_candidate_facts_hashes() -> None:
    """Mutation caught: authority points at mutable or mismatched profile bytes."""
    manifest = yaml.safe_load(
        (ROOT / "docs/authority-manifest.yaml").read_text(encoding="utf-8")
    )
    profile_record = manifest["technical_contracts"]["career_profile_v2"]
    candidate_record = manifest["candidate_facts"]["artifact"]

    assert profile_record["version"] == "2.0.0"
    assert profile_record["subordinate_to"] == "PS-SOT-2026-08-10-v1"
    assert hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest() == profile_record["sha256"]
    assert candidate_record == {
        "id": "candidate-facts-structured-resume-v1.1.0",
        "version": "1.1.0",
        "source_uri": "hermes-private://career/denis_vanyushkin_structured_resume_v1_1.json",
        "sha256": "7219eea2fbf04c92291254f83a76b8d2d1ef53e6004ac64ff4601c726eb9fac9",
    }


def test_profile_rejects_nonexistent_candidate_fact_pointer() -> None:
    """Mutation caught: an invented path is treated as Candidate Facts evidence."""
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["candidate_fact_claims"][0]["candidate_fact_pointers"][0] = (
        "/experience/99/roles/0/invented"
    )

    with pytest.raises(ValidationError, match="candidate_fact"):
        load_career_profile(payload)


def test_profile_rejects_candidate_facts_hash_mismatch() -> None:
    """Mutation caught: a profile is validated against different Candidate Facts bytes."""
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["authorities"]["candidate_facts_ref"]["sha256"] = "b" * 64

    with pytest.raises(ValueError, match="candidate facts sha256"):
        load_career_profile(payload)


def test_profile_rejects_statement_broader_than_its_cited_candidate_value() -> None:
    """Mutation caught: pointers exist but the authored claim adds unsupported scope."""
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["candidate_fact_claims"][1]["statement"] = (
        "Direct management across five countries and global product launch ownership."
    )

    with pytest.raises(ValidationError, match="statement"):
        load_career_profile(payload)


@pytest.mark.parametrize(
    ("field", "unknown_value"),
    [
        ("mandate_role_families", "invented_executive"),
        ("transferable_patterns", "global_launch_experience"),
        ("hard_gates", "title_is_not_cpo"),
        ("feasibility_unknowns", "industry_unfamiliar"),
    ],
)
def test_profile_authority_vocabularies_reject_unknown_values(
    field: str,
    unknown_value: str,
) -> None:
    """Mutation caught: free-form profile policy silently expands authority."""
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    payload[field][-1] = unknown_value

    with pytest.raises(ValidationError):
        load_career_profile(payload)


@pytest.mark.parametrize(
    "field",
    [
        "mandate_role_families",
        "transferable_patterns",
        "hard_gates",
        "feasibility_unknowns",
    ],
)
def test_profile_authority_vocabularies_reject_omissions_and_duplicates(
    field: str,
) -> None:
    """Mutation caught: an authoritative rule disappears or is double-counted."""
    missing = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    missing[field].pop()
    with pytest.raises(ValidationError, match=field):
        load_career_profile(missing)

    duplicate = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    duplicate[field][-1] = duplicate[field][0]
    with pytest.raises(ValidationError, match=field):
        load_career_profile(duplicate)


def test_profile_contains_complete_authority_derived_policy_sets() -> None:
    """Mutation caught: hard gates or unknown semantics drift from approved authority."""
    profile = load_career_profile(PROFILE_PATH)

    assert {item.value for item in profile.hard_gates} == {
        "sanctioned_or_clearly_unstable_environment",
        "africa_as_proactive_search_region",
        "us_onsite_or_hybrid_without_explicit_sponsorship",
        "onsite_or_hybrid_with_explicitly_no_viable_work_authorization_path",
        "below_minimum_executive_scope",
        "non_product_function_without_real_digital_business_ownership",
        "non_transferable_required_domain_or_language",
        "pure_delivery_project_or_program_ownership",
        "internal_tools_infrastructure_or_back_office_without_business_scope",
    }
    assert {item.value for item in profile.feasibility_unknowns} == {
        "sponsorship_outside_onsite_us",
        "reporting_line",
        "compensation",
        "timezone",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "state": "evidence_available",
            "evidence_refs": (" ",),
            "unknown_reasons": (),
        },
        {
            "state": "unknown",
            "evidence_refs": (),
            "unknown_reasons": ("",),
        },
        {
            "state": "evidence_available",
            "evidence_refs": ("snapshot:fact-1",),
            "unknown_reasons": ("contradiction",),
        },
        {
            "state": "unknown",
            "evidence_refs": ("snapshot:fact-1",),
            "unknown_reasons": ("not_published",),
        },
    ],
)
def test_dimension_evidence_rejects_blank_or_opposite_state_data(
    payload: dict[str, object],
) -> None:
    """Mutation caught: blank/contradictory evidence passes an explicit state."""
    with pytest.raises(ValidationError):
        DimensionEvidenceInput.model_validate(payload)


@pytest.mark.parametrize(
    "axis",
    ["unfamiliar_company", "unfamiliar_industry", "unfamiliar_geography", "nominated", "other"],
)
def test_legacy_compatibility_rejects_unrecognized_exploration_axes(axis: str) -> None:
    """Mutation caught: any nonblank legacy marker becomes Exploration."""
    with pytest.raises(ValidationError, match="exploration_axis"):
        LegacyAssessmentV1(
            boundary_version="shadow-evaluator-decision/1.1.0",
            recommendation="strong",
            exploration_axis=axis,
        )


def test_authoritative_legacy_axis_maps_explicitly_to_exploration() -> None:
    """Mutation caught: removing the named approved compatibility axis."""
    record = LegacyAssessmentV1(
        boundary_version="shadow-evaluator-decision/1.1.0",
        recommendation="strong",
        exploration_axis="exp_industry",
    )
    assert map_legacy_assessment(record).target_selection_mode is SelectionMode.EXPLORATION
