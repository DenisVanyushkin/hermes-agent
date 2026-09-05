"""Strict, persistence-free domain contracts for Product Search.

This module owns vocabulary and immutable replay inputs only.  It deliberately
does not evaluate vacancies, read a database, or perform lifecycle transitions;
those behaviors belong to later Product Search tasks.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
LEGACY_BOUNDARY_VERSION = "shadow-evaluator-decision/1.1.0"
DEFAULT_CANDIDATE_FACTS_PATH = Path(
    "/home/hermes/.hermes/private/career/denis_vanyushkin_structured_resume_v1_1.json"
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiscoveryOrigin(str, Enum):
    OPEN_MARKET = "Open Market"
    STRATEGIC_WATCHLIST = "Strategic Watchlist"


class SelectionMode(str, Enum):
    CORE = "Core"
    EXPLORATION = "Exploration"


class WatchlistStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEPRIORITIZED = "deprioritized"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReviewState(str, Enum):
    CURRENT = "current"
    REVIEW_DUE = "review_due"


class CompanyAction(str, Enum):
    NOMINATE = "nominate"
    PROMOTE = "promote"
    RETAIN = "retain"
    DEPRIORITIZE = "deprioritize"
    REJECT = "reject"
    EXPIRE = "expire"


class SystemVerdict(str, Enum):
    PRIORITY = "Priority"
    INVESTIGATE = "Investigate"
    SAVE = "Save"
    REJECT = "Reject"


class UserDecision(str, Enum):
    PURSUE = "Pursue"
    INVESTIGATE = "Investigate"
    SAVE_FOR_LATER = "Save for later"
    NOT_INTERESTING = "Not interesting"
    NOT_FEASIBLE = "Not feasible"
    WRONG_OR_STALE_DATA = "Wrong or stale data"


class RecommendedActionKind(str, Enum):
    RESEARCH = "research"
    FEASIBILITY = "feasibility"
    NETWORKING = "networking"
    OUTREACH = "outreach"
    REFERRAL = "referral"
    APPLICATION = "application"


class DimensionEvidenceState(str, Enum):
    EVIDENCE_AVAILABLE = "evidence_available"
    UNKNOWN = "unknown"


class CompanyAuthorityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MandateRoleFamily(str, Enum):
    EXECUTIVE_PRODUCT = "executive_product"
    DIGITAL_BUSINESS = "digital_business"
    CUSTOMER_GROWTH_COMMERCIAL_HYBRID = "customer_growth_commercial_hybrid"
    PRODUCT_BUSINESS_UNIT = "product_business_unit"
    GENERAL_MANAGEMENT = "general_management"
    GROWTH_MONETIZATION = "growth_monetization"
    TRANSFORMATION_BUILDER = "transformation_builder"
    HYBRID_EXECUTIVE_EXPLORATION = "hybrid_executive_exploration"


class TransferablePattern(str, Enum):
    BUSINESS_AND_PNL_OWNERSHIP = "business_and_pnl_ownership"
    MONETIZATION_AND_GROWTH = "monetization_and_growth"
    PORTFOLIO_AND_GO_TO_MARKET = "portfolio_and_go_to_market"
    ORGANIZATION_BUILDING = "organization_building"
    OPERATING_MODEL_TRANSFORMATION = "operating_model_transformation"
    TURNAROUND = "turnaround"
    NEW_BUSINESS_LAUNCH = "new_business_launch"
    EXECUTIVE_STAKEHOLDER_LEADERSHIP = "executive_stakeholder_leadership"


class HardGate(str, Enum):
    SANCTIONED_OR_UNSTABLE = "sanctioned_or_clearly_unstable_environment"
    AFRICA_PROACTIVE = "africa_as_proactive_search_region"
    US_ONSITE_SPONSORSHIP = "us_onsite_or_hybrid_without_explicit_sponsorship"
    NO_WORK_AUTHORIZATION_PATH = (
        "onsite_or_hybrid_with_explicitly_no_viable_work_authorization_path"
    )
    BELOW_EXECUTIVE_SCOPE = "below_minimum_executive_scope"
    NON_PRODUCT_WITHOUT_OWNERSHIP = (
        "non_product_function_without_real_digital_business_ownership"
    )
    NON_TRANSFERABLE_DOMAIN_OR_LANGUAGE = (
        "non_transferable_required_domain_or_language"
    )
    PURE_DELIVERY = "pure_delivery_project_or_program_ownership"
    INTERNAL_INFRA_WITHOUT_BUSINESS_SCOPE = (
        "internal_tools_infrastructure_or_back_office_without_business_scope"
    )


class FeasibilityUnknown(str, Enum):
    SPONSORSHIP_OUTSIDE_ONSITE_US = "sponsorship_outside_onsite_us"
    REPORTING_LINE = "reporting_line"
    COMPENSATION = "compensation"
    TIMEZONE = "timezone"


class CandidateClaimId(str, Enum):
    LEADERSHIP_SCOPE = "leadership_scope"
    DIRECT_AND_MATRIX_LEADERSHIP = "direct_and_matrix_leadership"
    PNL_AND_BUSINESS_OWNERSHIP = "pnl_and_business_ownership"
    GROWTH_AND_MONETIZATION = "growth_and_monetization"
    PORTFOLIO_AND_TRANSFORMATION = "portfolio_and_transformation"
    LAUNCH_PIVOT_STOP = "launch_pivot_stop"
    EXECUTIVE_CONTEXT = "executive_context"


class LegacyExplorationAxis(str, Enum):
    INDUSTRY = "exp_industry"
    INDUSTRY_RETURN_TELECOM = "exp_industry_return_telecom"
    COMPANY_TYPE = "exp_company_type"
    ROLE_FAMILY = "exp_role_family"
    WORK_FORMAT = "exp_work_format"


class ImmutableArtifactRef(_StrictFrozenModel):
    """A content-addressed input suitable for deterministic replay."""

    artifact_id: str = Field(min_length=1)
    version: str = Field(pattern=SEMVER_PATTERN)
    sha256: str = Field(pattern=SHA256_PATTERN)


class AssessmentReferences(_StrictFrozenModel):
    profile_ref: ImmutableArtifactRef
    candidate_facts_ref: ImmutableArtifactRef
    semantic_contract_ref: ImmutableArtifactRef
    search_contract_ref: ImmutableArtifactRef
    policy_ref: ImmutableArtifactRef
    evidence_snapshot_ref: ImmutableArtifactRef


class DimensionEvidenceInput(_StrictFrozenModel):
    """Evidence presented to one Decision dimension, before Decision v2."""

    state: DimensionEvidenceState
    evidence_refs: tuple[str, ...] = ()
    unknown_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_explicit_evidence_or_unknown(self) -> Self:
        for field_name, values in (
            ("evidence_refs", self.evidence_refs),
            ("unknown_reasons", self.unknown_reasons),
        ):
            canonical = tuple(value.strip() for value in values)
            if any(not value for value in canonical):
                raise ValueError(f"{field_name} must contain non-empty values")
            if values != canonical:
                raise ValueError(f"{field_name} must not contain surrounding whitespace")
            if len(canonical) != len(set(canonical)):
                raise ValueError(f"{field_name} must not contain duplicates")
        if self.state is DimensionEvidenceState.EVIDENCE_AVAILABLE and not self.evidence_refs:
            raise ValueError("evidence_available requires evidence_refs")
        if self.state is DimensionEvidenceState.EVIDENCE_AVAILABLE and self.unknown_reasons:
            raise ValueError("evidence_available requires unknown_reasons to be empty")
        if self.state is DimensionEvidenceState.UNKNOWN and not self.unknown_reasons:
            raise ValueError("unknown requires unknown_reasons")
        if self.state is DimensionEvidenceState.UNKNOWN and self.evidence_refs:
            raise ValueError("unknown requires evidence_refs to be empty")
        return self


class DecisionDimensionsInput(_StrictFrozenModel):
    feasibility: DimensionEvidenceInput
    mandate_fit: DimensionEvidenceInput
    company_fit: DimensionEvidenceInput
    transferability: DimensionEvidenceInput
    career_value: DimensionEvidenceInput
    evidence_confidence: DimensionEvidenceInput


class AssessmentInputV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    assessment_id: str = Field(min_length=1)
    references: AssessmentReferences
    dimensions: DecisionDimensionsInput


class AssessmentInputV2(_StrictFrozenModel):
    """Additive benchmark input; v1 remains the company-bundle contract."""

    schema_version: Literal["2.0.0"]
    assessment_id: str = Field(min_length=1)
    references: AssessmentReferences
    dimensions: DecisionDimensionsInput
    company_authority_status: CompanyAuthorityStatus

    @model_validator(mode="after")
    def keep_unavailable_company_authority_unknown(self) -> Self:
        if (
            self.company_authority_status is CompanyAuthorityStatus.UNAVAILABLE
            and self.dimensions.company_fit.state is not DimensionEvidenceState.UNKNOWN
        ):
            raise ValueError(
                "unavailable company authority requires company_fit to remain unknown"
            )
        return self


class ProductDecisionFields(_StrictFrozenModel):
    """Independent product fields; no field derives or mutates another."""

    discovery_origin: DiscoveryOrigin
    selection_mode: SelectionMode
    system_verdict: SystemVerdict | None = None
    user_decision: UserDecision | None = None
    recommended_action: RecommendedActionKind | None = None
    company_action: CompanyAction | None = None

    @field_validator(
        "discovery_origin",
        "selection_mode",
        "system_verdict",
        "user_decision",
        "recommended_action",
        "company_action",
        mode="before",
    )
    @classmethod
    def reject_foreign_enum_instances(cls, value: Any, info: Any) -> Any:
        expected = {
            "discovery_origin": DiscoveryOrigin,
            "selection_mode": SelectionMode,
            "system_verdict": SystemVerdict,
            "user_decision": UserDecision,
            "recommended_action": RecommendedActionKind,
            "company_action": CompanyAction,
        }[info.field_name]
        if isinstance(value, Enum) and not isinstance(value, expected):
            raise ValueError(f"{info.field_name} rejects implicit enum conversion")
        return value


class DiscoveryOriginFacts(_StrictFrozenModel):
    watchlist_status_before_discovery: WatchlistStatus
    watchlist_monitoring_formed_canonical_candidate: bool
    existing_primary_origin: DiscoveryOrigin | None = None
    later_watchlist_status: WatchlistStatus | None = None
    later_watchlist_rediscovery: bool = False


def resolve_discovery_origin(facts: DiscoveryOriginFacts) -> DiscoveryOrigin:
    """Resolve once from discovery-time facts; an existing origin is immutable."""
    if facts.existing_primary_origin is not None:
        return facts.existing_primary_origin
    if (
        facts.watchlist_status_before_discovery is WatchlistStatus.ACTIVE
        and facts.watchlist_monitoring_formed_canonical_candidate
    ):
        return DiscoveryOrigin.STRATEGIC_WATCHLIST
    return DiscoveryOrigin.OPEN_MARKET


class CandidateFactClaim(_StrictFrozenModel):
    claim_id: CandidateClaimId
    statement: str = Field(min_length=1)
    candidate_fact_pointers: tuple[str, ...] = Field(min_length=1)
    candidate_fact_value_sha256s: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_hashes_align_with_pointers(self) -> Self:
        if len(self.candidate_fact_pointers) != len(self.candidate_fact_value_sha256s):
            raise ValueError(
                "candidate_fact_value_sha256s must align with candidate_fact_pointers"
            )
        if any(not pointer.startswith("/") for pointer in self.candidate_fact_pointers):
            raise ValueError("candidate_fact_pointers must use absolute JSON pointers")
        if any(
            len(value_hash) != 64
            or any(char not in "0123456789abcdef" for char in value_hash)
            for value_hash in self.candidate_fact_value_sha256s
        ):
            raise ValueError("candidate fact value hashes must be lowercase sha256")
        return self


class CandidateFactPolicy(_StrictFrozenModel):
    broadening: Literal["prohibited"]
    derived_fields: Literal["must_remain_labeled_derived"]
    conflict_resolution: Literal["candidate_facts_win_or_fail_closed"]


class CareerProfileAuthorities(_StrictFrozenModel):
    product_sot_ref: ImmutableArtifactRef
    candidate_facts_ref: ImmutableArtifactRef
    preference_model_ref: ImmutableArtifactRef
    search_contract_ref: ImmutableArtifactRef


class KazakhstanPolicy(_StrictFrozenModel):
    eligible_market: Literal[True]
    fallback: Literal[False]
    minimum_delivery: None
    lowered_bar: Literal[False]


class OtherCentralAsiaPolicy(_StrictFrozenModel):
    independent_by_country: Literal[True]
    inherits_kazakhstan_policy: Literal[False]


class GeographyPolicy(_StrictFrozenModel):
    kazakhstan: KazakhstanPolicy
    other_central_asia: OtherCentralAsiaPolicy


class CareerProfileV2(_StrictFrozenModel):
    schema_version: Literal["2.0.0"]
    profile_id: Literal["career-profile-v2"]
    product_authority_id: Literal["PS-SOT-2026-08-10-v1"]
    authorities: CareerProfileAuthorities
    core_positioning: str = Field(min_length=1)
    short_positioning: str = Field(min_length=1)
    candidate_fact_policy: CandidateFactPolicy
    candidate_fact_claims: tuple[CandidateFactClaim, ...] = Field(min_length=1)
    mandate_role_families: tuple[MandateRoleFamily, ...] = Field(min_length=1)
    transferable_patterns: tuple[TransferablePattern, ...] = Field(min_length=1)
    hard_gates: tuple[HardGate, ...] = Field(min_length=1)
    feasibility_unknowns: tuple[FeasibilityUnknown, ...] = Field(min_length=1)
    geography_policy: GeographyPolicy

    @model_validator(mode="after")
    def require_complete_authority_sets_and_cited_claims(self) -> Self:
        required_sets: tuple[tuple[str, tuple[Enum, ...], type[Enum]], ...] = (
            ("mandate_role_families", self.mandate_role_families, MandateRoleFamily),
            ("transferable_patterns", self.transferable_patterns, TransferablePattern),
            ("hard_gates", self.hard_gates, HardGate),
            ("feasibility_unknowns", self.feasibility_unknowns, FeasibilityUnknown),
        )
        for field_name, values, enum_type in required_sets:
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} contains duplicates")
            if set(values) != set(enum_type):
                raise ValueError(f"{field_name} must contain the complete authority set")

        claims_by_id = {claim.claim_id: claim for claim in self.candidate_fact_claims}
        if len(claims_by_id) != len(self.candidate_fact_claims):
            raise ValueError("candidate_fact_claims contains duplicate claim_id")
        if set(claims_by_id) != set(CandidateClaimId):
            raise ValueError("candidate_fact_claims must contain the complete approved set")
        for claim_id, (statement, pointers) in _CANDIDATE_CLAIM_CONTRACT.items():
            claim = claims_by_id[claim_id]
            if claim.statement != statement:
                raise ValueError(f"candidate claim {claim_id.value} statement is not approved")
            if claim.candidate_fact_pointers != pointers:
                raise ValueError(
                    f"candidate claim {claim_id.value} candidate_fact_pointers are not approved"
                )
        return self


class LegacyRecommendation(str, Enum):
    EXCEPTIONAL = "exceptional"
    STRONG = "strong"
    PROMISING = "promising"
    UNCLEAR = "unclear"
    NOT_RECOMMENDED = "not_recommended"


class LegacyAssessmentV1(_StrictFrozenModel):
    boundary_version: str
    recommendation: LegacyRecommendation
    exploration_axis: LegacyExplorationAxis | None


class LegacyCompatibilityMapping(_StrictFrozenModel):
    boundary_version: Literal["shadow-evaluator-decision/1.1.0"]
    source_recommendation: LegacyRecommendation
    target_system_verdict: SystemVerdict
    target_selection_mode: SelectionMode
    requires_full_reassessment: Literal[True]


class AmbiguousLegacyMappingError(ValueError):
    pass


def map_legacy_assessment(record: LegacyAssessmentV1) -> LegacyCompatibilityMapping:
    """Map only deterministic legacy values at the named compatibility boundary.

    The result is deliberately not a Product Search assessment.  It records a
    compatibility hint and still requires reassessment against all v2 inputs.
    """
    if record.boundary_version != LEGACY_BOUNDARY_VERSION:
        raise ValueError(f"unsupported boundary_version: {record.boundary_version}")
    verdicts = {
        LegacyRecommendation.EXCEPTIONAL: SystemVerdict.PRIORITY,
        LegacyRecommendation.STRONG: SystemVerdict.PRIORITY,
        LegacyRecommendation.NOT_RECOMMENDED: SystemVerdict.REJECT,
    }
    try:
        verdict = verdicts[record.recommendation]
    except KeyError as exc:
        raise AmbiguousLegacyMappingError(
            f"legacy {record.recommendation.value} requires Product Search reassessment"
        ) from exc
    selection_mode = (
        SelectionMode.EXPLORATION
        if record.exploration_axis is not None
        else SelectionMode.CORE
    )
    return LegacyCompatibilityMapping(
        boundary_version=LEGACY_BOUNDARY_VERSION,
        source_recommendation=record.recommendation,
        target_system_verdict=verdict,
        target_selection_mode=selection_mode,
        requires_full_reassessment=True,
    )


def _resolve_json_pointer(document: Any, pointer: str) -> Any:
    value = document
    for raw_token in pointer.removeprefix("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"candidate_fact pointer does not exist: {pointer}") from exc
    return value


def _canonical_value_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_candidate_fact_evidence(
    profile: CareerProfileV2,
    candidate_facts_path: Path,
) -> None:
    try:
        source_bytes = candidate_facts_path.read_bytes()
    except OSError as exc:
        raise ValueError("candidate facts source unavailable") from exc
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != profile.authorities.candidate_facts_ref.sha256:
        raise ValueError("candidate facts sha256 does not match pinned profile reference")
    try:
        candidate_facts = json.loads(source_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("candidate facts source is not valid JSON") from exc
    for claim in profile.candidate_fact_claims:
        for pointer, expected_hash in zip(
            claim.candidate_fact_pointers,
            claim.candidate_fact_value_sha256s,
            strict=True,
        ):
            value = _resolve_json_pointer(candidate_facts, pointer)
            if _canonical_value_sha256(value) != expected_hash:
                raise ValueError(
                    f"candidate fact value hash mismatch for approved claim {claim.claim_id.value}"
                )


def load_career_profile(
    path_or_payload: Path | str | Mapping[str, Any],
    *,
    candidate_facts_path: Path | str = DEFAULT_CANDIDATE_FACTS_PATH,
) -> CareerProfileV2:
    if isinstance(path_or_payload, Mapping):
        payload = dict(path_or_payload)
    else:
        payload = yaml.safe_load(Path(path_or_payload).read_text(encoding="utf-8"))
    profile = CareerProfileV2.model_validate(payload)
    _validate_candidate_fact_evidence(profile, Path(candidate_facts_path))
    return profile


_CANDIDATE_CLAIM_CONTRACT: dict[
    CandidateClaimId,
    tuple[str, tuple[str, ...]],
] = {
    CandidateClaimId.LEADERSHIP_SCOPE: (
        "Leadership of product and business organizations of approximately 50-90 FTE, with broader commercial organizational responsibility of 170+ employees.",
        ("/metrics_index/0", "/metrics_index/1", "/metrics_index/2", "/metrics_index/4"),
    ),
    CandidateClaimId.DIRECT_AND_MATRIX_LEADERSHIP: (
        "Direct management of 10 reports in the SuperApp tribe and matrix leadership across product organizations.",
        (
            "/experience/0/roles/0/team_scope/direct_reports",
            "/experience/1/roles/0/team_scope/matrix_leadership_across_product_tribes",
        ),
    ),
    CandidateClaimId.PNL_AND_BUSINESS_OWNERSHIP: (
        "Explicit P&L and business-unit responsibility.",
        ("/professional_summary/positioning_tags/7", "/core_competencies/2"),
    ),
    CandidateClaimId.GROWTH_AND_MONETIZATION: (
        "Acquisition, retention, customer lifecycle, pricing, and monetization experience.",
        (
            "/core_competencies/1",
            "/core_competencies/7",
            "/metrics_index/6",
            "/experience/5/roles/0/responsibilities/1",
        ),
    ),
    CandidateClaimId.PORTFOLIO_AND_TRANSFORMATION: (
        "Product portfolio, go-to-market, organization design, operating-model transformation, and turnaround experience.",
        (
            "/experience/0/roles/1/function",
            "/core_competencies/6",
            "/experience/0/roles/1/achievements/2/statement",
            "/core_competencies/4",
            "/experience/0/roles/1/achievements/0/statement",
        ),
    ),
    CandidateClaimId.LAUNCH_PIVOT_STOP: (
        "New-product and new-business-line launches, strategic pivots, and decisions to stop non-viable initiatives.",
        (
            "/experience/2/roles/0/responsibilities/0",
            "/experience/0/roles/0/achievements/4",
            "/experience/2/roles/0/achievements/4",
        ),
    ),
    CandidateClaimId.EXECUTIVE_CONTEXT: (
        "Executive and board-level stakeholder work in B2C digital products and telecom/banking partner environments.",
        (
            "/experience/3/roles/0/responsibilities/2",
            "/candidate/headline",
            "/experience/1/roles/0/achievements/1/statement",
        ),
    ),
}
