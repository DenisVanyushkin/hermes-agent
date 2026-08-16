"""Strict, persistence-free domain contracts for Product Search.

This module owns vocabulary and immutable replay inputs only.  It deliberately
does not evaluate vacancies, read a database, or perform lifecycle transitions;
those behaviors belong to later Product Search tasks.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
LEGACY_BOUNDARY_VERSION = "shadow-evaluator-decision/1.1.0"


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
        if self.state is DimensionEvidenceState.EVIDENCE_AVAILABLE and not self.evidence_refs:
            raise ValueError("evidence_available requires evidence_refs")
        if self.state is DimensionEvidenceState.UNKNOWN and not self.unknown_reasons:
            raise ValueError("unknown requires unknown_reasons")
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
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    candidate_fact_pointers: tuple[str, ...] = Field(min_length=1)


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
    mandate_role_families: tuple[str, ...] = Field(min_length=1)
    transferable_patterns: tuple[str, ...] = Field(min_length=1)
    hard_gates: tuple[str, ...] = Field(min_length=1)
    feasibility_unknowns: tuple[str, ...] = Field(min_length=1)
    geography_policy: GeographyPolicy


class LegacyRecommendation(str, Enum):
    EXCEPTIONAL = "exceptional"
    STRONG = "strong"
    PROMISING = "promising"
    UNCLEAR = "unclear"
    NOT_RECOMMENDED = "not_recommended"


class LegacyAssessmentV1(_StrictFrozenModel):
    boundary_version: str
    recommendation: LegacyRecommendation
    exploration_axis: str | None


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
        if (record.exploration_axis or "").strip()
        else SelectionMode.CORE
    )
    return LegacyCompatibilityMapping(
        boundary_version=LEGACY_BOUNDARY_VERSION,
        source_recommendation=record.recommendation,
        target_system_verdict=verdict,
        target_selection_mode=selection_mode,
        requires_full_reassessment=True,
    )


def load_career_profile(path_or_payload: Path | str | Mapping[str, Any]) -> CareerProfileV2:
    if isinstance(path_or_payload, Mapping):
        payload = dict(path_or_payload)
    else:
        payload = yaml.safe_load(Path(path_or_payload).read_text(encoding="utf-8"))
    return CareerProfileV2.model_validate(payload)
