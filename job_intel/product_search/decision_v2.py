"""Deterministic Product Search Decision Contract v2.

Task 10 output is evidence only.  This module is the first Product Search
component that may emit canonical funnel stage 4 and owns every normative
decision derived from the pinned policy below.  It performs no persistence,
delivery, user-decision, CRM, or company-lifecycle mutation.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from job_intel.product_search.contracts import (
    AssessmentReferences,
    CompanyAction,
    HardGate,
    RecommendedActionKind,
    ReviewState,
    SelectionMode,
    SHA256_PATTERN,
    SystemVerdict,
    WatchlistStatus,
    ImmutableArtifactRef,
)
from job_intel.product_search.company_evidence import (
    CompanyEvidenceDimension,
    CompanyEvidenceBundleV1,
    CompanyIdentityResolutionState,
    CompanyThesisInputV1,
    EvidenceContradictionState,
    EvidenceFreshnessState,
    EvidenceSufficiencyState,
)
from job_intel.product_search.evidence_synthesis import (
    EvidenceClaimStatus,
    EvidenceClaimV1,
    EvidenceDimension,
    EvidenceQuestionCandidateV1,
    EvidenceSynthesisResultV1,
    EvidenceSynthesisStatus,
)


DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/decision_contract.v2.yaml"
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionRunStatus(str, Enum):
    ASSESSED = "assessed"
    FAIL_CLOSED = "fail_closed"


class DimensionOutcome(str, Enum):
    POSITIVE = "positive"
    MIXED = "mixed"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class UrgencyEvidenceKind(str, Enum):
    EXPLICIT_CLOSING_WITHIN_48_HOURS = "explicit_closing_date_within_48_hours"
    CONFIRMED_SHORT_REFERRAL_WINDOW = "confirmed_short_referral_or_outreach_window"
    RECRUITER_DEADLINE = "recruiter_deadline"
    LIMITED_INTAKE = "limited_intake"
    RAPIDLY_CLOSING_SHORTLIST = "confirmed_rapidly_closing_shortlist"
    RECENCY_ONLY = "recency_only"
    PROVIDER_CONFIDENCE_ONLY = "provider_confidence_only"


class AuthorityHashesV2(_StrictFrozenModel):
    product_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    career_profile_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_facts_sha256: str = Field(pattern=SHA256_PATTERN)
    search_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    product_sot_sha256: str = Field(pattern=SHA256_PATTERN)
    company_evidence_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_synthesis_contract_sha256: str = Field(pattern=SHA256_PATTERN)


class DecisionImmutableReferencesV2(AuthorityHashesV2):
    decision_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    company_evidence_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_input_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_output_sha256: str = Field(pattern=SHA256_PATTERN)


class DecisionAuthorityInputsV2(_StrictFrozenModel):
    """Exact Task 8/9 immutable objects whose hashes Decision v2 traces."""

    assessment_references: AssessmentReferences
    company_evidence_bundle_ref: ImmutableArtifactRef


class StageEvidenceV2(_StrictFrozenModel):
    raw_observed: bool
    identity_resolved: bool
    duplicates_consolidated: bool
    freshness_confirmed: bool
    role_identified: bool
    company_identified: bool
    location_and_work_format_identified: bool
    material_responsibilities_identified: bool
    known_feasibility_constraints_identified: bool

    def stages_1_to_3_complete(self) -> bool:
        return all(self.model_dump().values())


class ExplorationAxis(str, Enum):
    INDUSTRY = "industry"
    GEOGRAPHY = "geography"
    BUSINESS_MODEL = "business_model"
    ROLE_FAMILY = "role_family"
    WORK_FORMAT = "work_format"
    COMPANY_TYPE = "company_type"


class MultiAxisExceptionV2(_StrictFrozenModel):
    exception_id: str = Field(min_length=1)
    axes: tuple[ExplorationAxis, ...] = Field(min_length=2)
    authority_ref: ImmutableArtifactRef

    @model_validator(mode="after")
    def validate_exception(self) -> Self:
        if self.exception_id != " ".join(self.exception_id.split()):
            raise ValueError("exception_id must use canonical whitespace")
        if self.axes != tuple(sorted(set(self.axes), key=lambda item: item.value)):
            raise ValueError("multi-axis exception axes must be sorted and unique")
        if self.authority_ref.artifact_id != self.exception_id:
            raise ValueError("multi-axis exception authority must match exception_id")
        return self


class SelectionEvidenceV2(_StrictFrozenModel):
    hypothesis_id: str | None = None
    hypothesis_claim_ids: tuple[str, ...] = ()
    multi_axis_exception: MultiAxisExceptionV2 | None = None
    information_value: str | None = None
    daily_slot_available: bool = False

    @model_validator(mode="after")
    def validate_selection_evidence(self) -> Self:
        for field_name, value in (
            ("hypothesis_id", self.hypothesis_id),
            ("information_value", self.information_value),
        ):
            if value is not None and value != " ".join(value.split()):
                raise ValueError(f"{field_name} must use canonical whitespace")
        if len(self.hypothesis_claim_ids) != len(set(self.hypothesis_claim_ids)):
            raise ValueError("hypothesis_claim_ids must be unique")
        if any(not claim_id.strip() for claim_id in self.hypothesis_claim_ids):
            raise ValueError("hypothesis_claim_ids must be nonblank")
        if self.hypothesis_claim_ids:
            if not self.hypothesis_id or not self.hypothesis_id.strip():
                raise ValueError("Exploration requires a named hypothesis")
            if not self.information_value or not self.information_value.strip():
                raise ValueError("information_value must be canonical nonblank text")
        elif self.hypothesis_id or self.information_value or self.multi_axis_exception:
            raise ValueError("hypothesis metadata requires structured hypothesis claims")
        return self


class CompanyDecisionSnapshotV2(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    snapshot_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    evidence_bundle_ref: ImmutableArtifactRef
    evidence_sufficiency_state: Literal["sufficient"]
    identity_resolution_state: Literal["resolved"]
    thesis_id: str = Field(min_length=1)
    thesis_input_sha256: str = Field(pattern=SHA256_PATTERN)
    current_status: WatchlistStatus | None
    review_state: ReviewState | None
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.current_status is None and self.review_state is not None:
            raise ValueError("absent company cannot have review state")
        if self.current_status is not None and self.review_state is None:
            raise ValueError("existing company requires review state")
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if hashlib.sha256(_canonical_json_bytes(payload)).hexdigest() != self.content_sha256:
            raise ValueError("company decision snapshot content hash mismatch")
        return self


def build_company_decision_snapshot(
    *,
    evidence_bundle: CompanyEvidenceBundleV1,
    thesis_input: CompanyThesisInputV1,
    current_status: WatchlistStatus | None,
    review_state: ReviewState | None,
    snapshot_id: str,
) -> CompanyDecisionSnapshotV2:
    if evidence_bundle.sufficiency_state is not EvidenceSufficiencyState.SUFFICIENT:
        raise ValueError("company evidence must be sufficient")
    if (
        evidence_bundle.identity_resolution.state
        is not CompanyIdentityResolutionState.RESOLVED
    ):
        raise ValueError("company identity must be resolved")
    expected_bundle_ref = ImmutableArtifactRef(
        artifact_id=evidence_bundle.bundle_id,
        version=evidence_bundle.schema_version,
        sha256=evidence_bundle.content_sha256,
    )
    if thesis_input.evidence_bundle_ref != expected_bundle_ref:
        raise ValueError("thesis does not bind the exact company evidence bundle")
    if thesis_input.company_id != evidence_bundle.company_identity.company_id:
        raise ValueError("thesis company does not match evidence bundle")
    records = {record.evidence_id: record for record in evidence_bundle.evidence}
    superseded = {
        record.supersedes_evidence_id
        for record in evidence_bundle.evidence
        if record.supersedes_evidence_id is not None
    }
    try:
        supporting = tuple(records[item] for item in thesis_input.supporting_evidence_ids)
    except KeyError as exc:
        raise ValueError("thesis references unknown company evidence") from exc
    if any(
        record.evidence_id in superseded
        or record.freshness_state is not EvidenceFreshnessState.CURRENT
        or record.contradiction_state is not EvidenceContradictionState.UNOPPOSED
        for record in supporting
    ):
        raise ValueError("thesis support must be current, unopposed, and non-superseded")
    if all(
        record.dimension is CompanyEvidenceDimension.SIGNAL_EVENT
        for record in supporting
    ):
        raise ValueError("company signal alone cannot support a decision snapshot")
    thesis_hash = hashlib.sha256(
        _canonical_json_bytes(thesis_input.model_dump(mode="json"))
    ).hexdigest()
    payload = {
        "schema_version": "1.0.0",
        "snapshot_id": snapshot_id,
        "company_id": evidence_bundle.company_identity.company_id,
        "evidence_bundle_ref": expected_bundle_ref.model_dump(mode="json"),
        "evidence_sufficiency_state": "sufficient",
        "identity_resolution_state": "resolved",
        "thesis_id": thesis_input.thesis_id,
        "thesis_input_sha256": thesis_hash,
        "current_status": current_status.value if current_status else None,
        "review_state": review_state.value if review_state else None,
    }
    payload["content_sha256"] = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return CompanyDecisionSnapshotV2.model_validate(payload)


class CompanyActionRequestV2(_StrictFrozenModel):
    action: CompanyAction
    snapshot: CompanyDecisionSnapshotV2


class CompanyActionConclusionV2(_StrictFrozenModel):
    action: CompanyAction
    current_status: WatchlistStatus | None
    target_status: WatchlistStatus
    review_state: ReviewState | None
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    thesis_input_sha256: str = Field(pattern=SHA256_PATTERN)
    state_mutated: Literal[False] = False


class UrgencyEvidenceV2(_StrictFrozenModel):
    kind: UrgencyEvidenceKind
    external_evidence_ref: str = Field(min_length=1)
    learned_at: datetime
    deadline_at: datetime | None = None

    @field_validator("learned_at", "deadline_at")
    @classmethod
    def normalize_urgency_clock(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("urgency timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.kind is UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS:
            if self.deadline_at is None:
                raise ValueError("explicit closing-date urgency requires deadline_at")
            if self.deadline_at <= self.learned_at:
                raise ValueError("closing deadline must be after discovery")
        return self


class DecisionRequestV2(_StrictFrozenModel):
    schema_version: Literal["2.0.0"]
    assessment_id: str = Field(min_length=1)
    stages: StageEvidenceV2
    references: DecisionImmutableReferencesV2
    authority_inputs: DecisionAuthorityInputsV2
    synthesis: EvidenceSynthesisResultV1
    selection: SelectionEvidenceV2
    company_action: CompanyActionRequestV2 | None
    urgency_evidence: UrgencyEvidenceV2 | None
    daily_digest_at: datetime
    assessed_at: datetime
    evaluated_at: datetime

    @field_validator("daily_digest_at", "assessed_at", "evaluated_at")
    @classmethod
    def normalize_decision_clock(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_clock(self) -> Self:
        if self.evaluated_at < self.daily_digest_at:
            raise ValueError("evaluated_at cannot precede daily_digest_at")
        if self.assessed_at > self.evaluated_at:
            raise ValueError("assessed_at cannot follow evaluated_at")
        if (
            self.urgency_evidence is not None
            and self.urgency_evidence.learned_at > self.evaluated_at
        ):
            raise ValueError("urgency evidence cannot be learned after evaluation")
        return self


class QuestionRuleV2(_StrictFrozenModel):
    question_code: str
    dimension: EvidenceDimension
    unknown_claim_code: str
    question: str
    action_kind: RecommendedActionKind
    material: Literal[True]
    bounded: Literal[True]
    realistically_resolvable: Literal[True]


class DecisionPolicyV2(_StrictFrozenModel):
    schema_version: Literal["2.0.0"]
    product_authority_id: Literal["PS-SOT-2026-08-10-v1"]
    policy_version: Literal["product-search-decision-v2.0.0"]
    authority_hashes: AuthorityHashesV2
    provider_identity: dict[str, str]
    positive_claims: dict[EvidenceDimension, tuple[str, ...]]
    negative_claims: dict[EvidenceDimension, tuple[str, ...]]
    hard_gate_claims: dict[str, HardGate]
    warning_claims: dict[str, str]
    qualified_questions: tuple[QuestionRuleV2, ...]
    urgency_claims: dict[UrgencyEvidenceKind, str]
    monetization_exception_claims: tuple[str, str]
    core_policy_requirements: dict[EvidenceDimension, tuple[str, ...]]
    exploration_claim_axes: dict[str, ExplorationAxis]

    @model_validator(mode="after")
    def validate_closed_policy(self) -> Self:
        if set(self.positive_claims) != set(EvidenceDimension):
            raise ValueError("positive_claims must cover all six dimensions")
        if set(self.negative_claims) != set(EvidenceDimension):
            raise ValueError("negative_claims must cover all six dimensions")
        if set(self.provider_identity) != {
            "provider_id",
            "provider_version",
            "model_id",
            "semantic_prompt_version",
            "prompt_version",
            "schema_version",
        }:
            raise ValueError("provider_identity vocabulary is closed")
        if len(self.qualified_questions) != len(
            {item.question_code for item in self.qualified_questions}
        ):
            raise ValueError("qualified question codes must be unique")
        if len(self.qualified_questions) != len(
            {item.unknown_claim_code for item in self.qualified_questions}
        ):
            raise ValueError("qualified question unknown claims must be unique")
        if set(self.urgency_claims) != {
            UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
            UrgencyEvidenceKind.CONFIRMED_SHORT_REFERRAL_WINDOW,
            UrgencyEvidenceKind.RECRUITER_DEADLINE,
            UrgencyEvidenceKind.LIMITED_INTAKE,
            UrgencyEvidenceKind.RAPIDLY_CLOSING_SHORTLIST,
        }:
            raise ValueError("urgency claims must cover only the five external SoT facts")
        if (
            set(self.core_policy_requirements) != set(EvidenceDimension)
            or any(
                not requirements
                or not set(requirements).issubset(self.positive_claims[dimension])
                for dimension, requirements in self.core_policy_requirements.items()
            )
        ):
            raise ValueError(
                "Core requirements must cover all dimensions with positive evidence codes"
            )
        if (
            set(self.exploration_claim_axes.values()) != set(ExplorationAxis)
            or len(self.exploration_claim_axes)
            != len(set(self.exploration_claim_axes.values()))
        ):
            raise ValueError("exploration claims must map one-to-one to exact axes")
        return self


class LoadedDecisionPolicyV2(_StrictFrozenModel):
    policy: DecisionPolicyV2
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    effective_sha256: str = Field(pattern=SHA256_PATTERN)


def _effective_policy_sha256(policy: DecisionPolicyV2) -> str:
    payload = policy.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def load_decision_policy(
    path_or_payload: Path | str | Mapping[str, Any] = DEFAULT_POLICY_PATH,
) -> LoadedDecisionPolicyV2:
    if isinstance(path_or_payload, Mapping):
        canonical = json.dumps(
            dict(path_or_payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        payload = dict(path_or_payload)
        source_hash = hashlib.sha256(canonical).hexdigest()
    else:
        source = Path(path_or_payload).read_bytes()
        payload = yaml.safe_load(source)
        source_hash = hashlib.sha256(source).hexdigest()
    if not isinstance(payload, Mapping):
        raise ValueError("Decision Contract v2 must be a mapping")
    policy = DecisionPolicyV2.model_validate(payload)
    return LoadedDecisionPolicyV2(
        policy=policy,
        source_sha256=source_hash,
        effective_sha256=_effective_policy_sha256(policy),
    )


class DimensionConclusionV2(_StrictFrozenModel):
    outcome: DimensionOutcome
    reason_codes: tuple[str, ...]
    evidence_pointers: tuple[str, ...]
    unknown_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]


class SixDimensionConclusionsV2(_StrictFrozenModel):
    feasibility: DimensionConclusionV2
    mandate_fit: DimensionConclusionV2
    company_fit: DimensionConclusionV2
    transferability: DimensionConclusionV2
    career_value: DimensionConclusionV2
    evidence_confidence: DimensionConclusionV2


class RecommendedQuestionV2(_StrictFrozenModel):
    question_id: str
    dimension: EvidenceDimension
    question_code: str
    question: str
    evidence_pointers: tuple[str, ...]
    material: Literal[True] = True
    bounded: Literal[True] = True
    realistically_resolvable: Literal[True] = True


class DecisionStepTraceV2(_StrictFrozenModel):
    ordinal: int = Field(ge=1, le=8)
    name: str
    outcome: str


class DecisionClockTraceV2(_StrictFrozenModel):
    assessed_at: datetime
    evaluated_at: datetime
    daily_digest_at: datetime
    urgency_learned_at: datetime | None
    urgency_deadline_at: datetime | None

    @field_validator(
        "assessed_at",
        "evaluated_at",
        "daily_digest_at",
        "urgency_learned_at",
        "urgency_deadline_at",
    )
    @classmethod
    def normalize_trace_clock(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamps must be timezone-aware")
        return value.astimezone(UTC)


class DecisionTraceV2(_StrictFrozenModel):
    policy_version: str
    references: DecisionImmutableReferencesV2
    authority_inputs: DecisionAuthorityInputsV2
    normalized_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    steps: tuple[DecisionStepTraceV2, ...] = Field(min_length=8, max_length=8)
    evaluated_at: datetime
    clock: DecisionClockTraceV2
    canonical_sha256: str = Field(pattern=SHA256_PATTERN)


class DecisionAssessmentV2(_StrictFrozenModel):
    schema_version: Literal["2.0.0"]
    assessment_id: str
    hard_gate_eligible: bool
    dimensions: SixDimensionConclusionsV2
    system_verdict: SystemVerdict
    selection_mode: SelectionMode
    exploration_hypothesis_id: str | None
    exploration_axes: tuple[str, ...]
    single_reaction_updates_hypothesis: bool
    company_action: CompanyActionConclusionV2 | None
    recommended_action_kind: RecommendedActionKind | None
    recommended_question: RecommendedQuestionV2 | None
    daily_digest_eligible: bool
    urgent_eligible: bool
    destinations: tuple[str, ...]
    blockers: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    unknowns: tuple[str, ...]
    evidence_pointers: tuple[str, ...]
    trace: DecisionTraceV2


class DecisionResultV2(_StrictFrozenModel):
    schema_version: Literal["2.0.0"] = "2.0.0"
    status: DecisionRunStatus
    failure_reason: str | None
    assessment: DecisionAssessmentV2 | None

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.status is DecisionRunStatus.ASSESSED:
            if self.assessment is None or self.failure_reason is not None:
                raise ValueError("assessed result requires exactly one assessment")
        elif self.assessment is not None or self.failure_reason is None:
            raise ValueError("fail-closed result cannot expose an assessment")
        return self


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_decision_bytes(result: DecisionResultV2) -> bytes:
    return _canonical_json_bytes(result.model_dump(mode="json"))


def _failure(reason: str) -> DecisionResultV2:
    return DecisionResultV2(
        status=DecisionRunStatus.FAIL_CLOSED,
        failure_reason=reason,
        assessment=None,
    )


def _validate_references(
    request: DecisionRequestV2,
    loaded: LoadedDecisionPolicyV2,
) -> str | None:
    references = request.references
    expected = {
        **loaded.policy.authority_hashes.model_dump(),
        "decision_contract_sha256": loaded.source_sha256,
        "provider_input_sha256": request.synthesis.metadata.input_sha256,
        "provider_output_sha256": request.synthesis.metadata.output_sha256,
    }
    for field_name, expected_value in expected.items():
        if getattr(references, field_name) != expected_value:
            return f"immutable_reference_mismatch:{field_name}"
    task8 = request.authority_inputs.assessment_references
    linked = {
        "career_profile_sha256": task8.profile_ref.sha256,
        "candidate_facts_sha256": task8.candidate_facts_ref.sha256,
        "semantic_contract_sha256": task8.semantic_contract_ref.sha256,
        "search_contract_sha256": task8.search_contract_ref.sha256,
        "product_sot_sha256": task8.policy_ref.sha256,
        "evidence_snapshot_sha256": task8.evidence_snapshot_ref.sha256,
        "company_evidence_bundle_sha256": (
            request.authority_inputs.company_evidence_bundle_ref.sha256
        ),
    }
    for field_name, actual_hash in linked.items():
        if getattr(references, field_name) != actual_hash:
            return f"immutable_reference_mismatch:{field_name}"
    if (
        request.company_action is not None
        and request.company_action.snapshot.evidence_bundle_ref
        != request.authority_inputs.company_evidence_bundle_ref
    ):
        return "immutable_reference_mismatch:company_evidence_bundle_sha256"
    metadata = request.synthesis.metadata
    for field_name, expected_value in loaded.policy.provider_identity.items():
        if str(getattr(metadata, field_name)) != expected_value:
            return f"immutable_reference_mismatch:provider_{field_name}"
    return None


def _sorted_claims(claims: tuple[EvidenceClaimV1, ...]) -> tuple[EvidenceClaimV1, ...]:
    return tuple(
        sorted(
            claims,
            key=lambda item: (
                item.dimension.value,
                item.claim_code,
                item.status.value,
                item.claim_id,
                item.citations,
            ),
        )
    )


def _normalized_evidence_sha256(request: DecisionRequestV2) -> str:
    synthesis = request.synthesis
    payload = {
        "claims": [item.model_dump(mode="json") for item in _sorted_claims(synthesis.claims)],
        "conflicts": [
            item.model_dump(mode="json")
            for item in sorted(synthesis.conflicts, key=lambda item: item.conflict_id)
        ],
        "questions": [
            item.model_dump(mode="json")
            for item in sorted(
                synthesis.question_candidates,
                key=lambda item: (item.question_code, item.question_id),
            )
        ],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _provider_output_sha256(synthesis: EvidenceSynthesisResultV1) -> str:
    payload = {
        "schema_version": synthesis.schema_version,
        "claims": [item.model_dump(mode="json") for item in synthesis.claims],
        "conflicts": [item.model_dump(mode="json") for item in synthesis.conflicts],
        "question_candidates": [
            item.model_dump(mode="json") for item in synthesis.question_candidates
        ],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _hard_gate_findings(
    claims: tuple[EvidenceClaimV1, ...], policy: DecisionPolicyV2
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                policy.hard_gate_claims[claim.claim_code].value
                for claim in claims
                if claim.status is not EvidenceClaimStatus.UNKNOWN
                and claim.claim_code in policy.hard_gate_claims
            }
        )
    )


def _dimension_conclusion(
    dimension: EvidenceDimension,
    claims: tuple[EvidenceClaimV1, ...],
    *,
    policy: DecisionPolicyV2,
    blockers: tuple[str, ...],
    has_conflict: bool,
) -> DimensionConclusionV2:
    selected = tuple(claim for claim in claims if claim.dimension is dimension)
    codes = {claim.claim_code for claim in selected}
    known_codes = {
        claim.claim_code
        for claim in selected
        if claim.status is not EvidenceClaimStatus.UNKNOWN
    }
    positives = known_codes.intersection(policy.positive_claims[dimension])
    negatives = known_codes.intersection(policy.negative_claims[dimension])
    if dimension is EvidenceDimension.MANDATE_FIT and set(
        policy.monetization_exception_claims
    ).issubset(known_codes):
        positives.add("narrow_monetization_exception")
    if dimension in {EvidenceDimension.FEASIBILITY, EvidenceDimension.MANDATE_FIT}:
        dimension_gates = {
            policy.hard_gate_claims[claim.claim_code].value
            for claim in selected
            if claim.claim_code in policy.hard_gate_claims
        }
        if dimension_gates.intersection(blockers):
            negatives.add("known_hard_gate")
    unknown_codes = tuple(
        sorted(
            claim.claim_code
            for claim in selected
            if claim.status is EvidenceClaimStatus.UNKNOWN
        )
    )
    if has_conflict or (positives and negatives):
        outcome = DimensionOutcome.MIXED
    elif negatives:
        outcome = DimensionOutcome.NEGATIVE
    elif unknown_codes:
        outcome = DimensionOutcome.UNKNOWN
    elif positives:
        outcome = DimensionOutcome.POSITIVE
    else:
        outcome = DimensionOutcome.UNKNOWN
    warning_codes = tuple(
        sorted(
            policy.warning_claims[code]
            for code in known_codes
            if code in policy.warning_claims
        )
    )
    pointers = tuple(sorted({pointer for claim in selected for pointer in claim.citations}))
    return DimensionConclusionV2(
        outcome=outcome,
        reason_codes=tuple(sorted(positives | negatives)),
        evidence_pointers=pointers,
        unknown_codes=unknown_codes,
        warning_codes=warning_codes,
    )


def _qualified_questions(
    questions: tuple[EvidenceQuestionCandidateV1, ...],
    claims: tuple[EvidenceClaimV1, ...],
    policy: DecisionPolicyV2,
) -> tuple[tuple[EvidenceQuestionCandidateV1, QuestionRuleV2], ...]:
    unknown_claims = tuple(
        claim for claim in claims if claim.status is EvidenceClaimStatus.UNKNOWN
    )
    if len(questions) != 1 or len(unknown_claims) != 1:
        return ()
    unknown_claim = unknown_claims[0]
    rules = {item.question_code: item for item in policy.qualified_questions}
    result = []
    for question in questions:
        rule = rules.get(question.question_code)
        if (
            rule is not None
            and question.dimension is rule.dimension
            and question.question == rule.question
            and unknown_claim.dimension is rule.dimension
            and unknown_claim.claim_code == rule.unknown_claim_code
            and set(question.citations) == set(unknown_claim.citations)
        ):
            result.append((question, rule))
    return tuple(sorted(result, key=lambda item: item[0].question_id))


def _selection_mode(
    selection: SelectionEvidenceV2,
    claims: tuple[EvidenceClaimV1, ...],
    policy: DecisionPolicyV2,
) -> tuple[SelectionMode, tuple[ExplorationAxis, ...]] | None:
    claims_by_id = {claim.claim_id: claim for claim in claims}
    known_codes_by_dimension = {
        dimension: {
            claim.claim_code
            for claim in claims
            if claim.dimension is dimension
            and claim.status is not EvidenceClaimStatus.UNKNOWN
        }
        for dimension in EvidenceDimension
    }
    core_qualified = all(
        set(requirements).issubset(known_codes_by_dimension[dimension])
        for dimension, requirements in policy.core_policy_requirements.items()
    )
    if core_qualified or not selection.hypothesis_claim_ids:
        return SelectionMode.CORE, ()
    axes: set[ExplorationAxis] = set()
    for claim_id in selection.hypothesis_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None or claim.status is not EvidenceClaimStatus.UNKNOWN:
            return None
        axis = policy.exploration_claim_axes.get(claim.claim_code)
        if axis is None:
            return None
        axes.add(axis)
    ordered_axes = tuple(sorted(axes, key=lambda item: item.value))
    if len(ordered_axes) != len(selection.hypothesis_claim_ids):
        return None
    if len(ordered_axes) == 1:
        if selection.multi_axis_exception is not None:
            return None
    else:
        exception = selection.multi_axis_exception
        if exception is None or exception.axes != ordered_axes:
            return None
    return SelectionMode.EXPLORATION, ordered_axes


def _company_action(
    request: CompanyActionRequestV2 | None,
) -> CompanyActionConclusionV2 | None | bool:
    if request is None:
        return None
    snapshot = request.snapshot
    snapshot_payload = snapshot.model_dump(mode="json", exclude={"content_sha256"})
    if (
        hashlib.sha256(_canonical_json_bytes(snapshot_payload)).hexdigest()
        != snapshot.content_sha256
    ):
        return False
    if snapshot.current_status in {WatchlistStatus.REJECTED, WatchlistStatus.EXPIRED}:
        return False
    allowed: dict[
        CompanyAction,
        tuple[set[tuple[WatchlistStatus | None, ReviewState | None]], WatchlistStatus],
    ] = {
        CompanyAction.NOMINATE: ({(None, None)}, WatchlistStatus.CANDIDATE),
        CompanyAction.PROMOTE: (
            {
                (WatchlistStatus.CANDIDATE, ReviewState.CURRENT),
                (WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE),
            },
            WatchlistStatus.ACTIVE,
        ),
        CompanyAction.RETAIN: (
            {
                (WatchlistStatus.ACTIVE, ReviewState.CURRENT),
                (WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE),
            },
            WatchlistStatus.ACTIVE,
        ),
        CompanyAction.DEPRIORITIZE: (
            {
                (WatchlistStatus.CANDIDATE, ReviewState.CURRENT),
                (WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE),
                (WatchlistStatus.ACTIVE, ReviewState.CURRENT),
                (WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE),
            },
            WatchlistStatus.DEPRIORITIZED,
        ),
        CompanyAction.REJECT: (
            {
                (status, review)
                for status in (
                    WatchlistStatus.CANDIDATE,
                    WatchlistStatus.ACTIVE,
                    WatchlistStatus.DEPRIORITIZED,
                )
                for review in (ReviewState.CURRENT, ReviewState.REVIEW_DUE)
            },
            WatchlistStatus.REJECTED,
        ),
        CompanyAction.EXPIRE: (
            {
                (WatchlistStatus.CANDIDATE, ReviewState.REVIEW_DUE),
                (WatchlistStatus.ACTIVE, ReviewState.REVIEW_DUE),
                (WatchlistStatus.DEPRIORITIZED, ReviewState.REVIEW_DUE),
            },
            WatchlistStatus.EXPIRED,
        ),
    }
    permitted, target = allowed[request.action]
    if (snapshot.current_status, snapshot.review_state) not in permitted:
        return False
    return CompanyActionConclusionV2(
        action=request.action,
        current_status=snapshot.current_status,
        target_status=target,
        review_state=snapshot.review_state,
        snapshot_sha256=snapshot.content_sha256,
        thesis_input_sha256=snapshot.thesis_input_sha256,
    )


def _urgent_eligible(
    *,
    request: DecisionRequestV2,
    verdict: SystemVerdict,
    policy: DecisionPolicyV2,
) -> bool:
    evidence = request.urgency_evidence
    accepted = {
        UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
        UrgencyEvidenceKind.CONFIRMED_SHORT_REFERRAL_WINDOW,
        UrgencyEvidenceKind.RECRUITER_DEADLINE,
        UrgencyEvidenceKind.LIMITED_INTAKE,
        UrgencyEvidenceKind.RAPIDLY_CLOSING_SHORTLIST,
    }
    expected_claim_code = (
        policy.urgency_claims.get(evidence.kind) if evidence is not None else None
    )
    externally_supported = bool(
        evidence is not None
        and expected_claim_code
        and any(
            claim.claim_code == expected_claim_code
            and claim.status is EvidenceClaimStatus.EXPLICIT
            and evidence.external_evidence_ref in claim.citations
            for claim in request.synthesis.claims
        )
    )
    return bool(
        verdict is SystemVerdict.PRIORITY
        and evidence is not None
        and evidence.kind in accepted
        and externally_supported
        and evidence.learned_at > request.daily_digest_at
        and evidence.learned_at <= request.evaluated_at
        and (
            evidence.deadline_at is None
            or timedelta(0)
            < evidence.deadline_at - request.evaluated_at
            <= timedelta(hours=48)
        )
        and evidence.external_evidence_ref.strip()
    )


def _trace_hash_payload(assessment: DecisionAssessmentV2) -> dict[str, Any]:
    payload = assessment.model_dump(mode="json")
    payload["trace"].pop("canonical_sha256")
    return payload


def run_decision_v2(
    request: DecisionRequestV2,
    *,
    policy: LoadedDecisionPolicyV2 | None = None,
) -> DecisionResultV2:
    """Evaluate one pinned assessment without side effects or fallback."""
    loaded = policy or load_decision_policy()
    if _effective_policy_sha256(loaded.policy) != loaded.effective_sha256:
        return _failure("policy_effective_hash_mismatch")
    if not request.stages.stages_1_to_3_complete():
        return _failure("stages_1_to_3_incomplete")
    mismatch = _validate_references(request, loaded)
    if mismatch is not None:
        return _failure(mismatch)
    if (
        request.synthesis.status is not EvidenceSynthesisStatus.DELIVERABLE
        or not request.synthesis.deliverable
    ):
        return _failure(f"synthesis_not_deliverable:{request.synthesis.status.value}")
    if _provider_output_sha256(request.synthesis) != request.synthesis.metadata.output_sha256:
        return _failure("provider_output_content_hash_mismatch")
    claim_ids = [claim.claim_id for claim in request.synthesis.claims]
    if len(claim_ids) != len(set(claim_ids)) or {
        claim.dimension for claim in request.synthesis.claims
    } != set(EvidenceDimension):
        return _failure("synthesis_contract_mismatch:dimension_coverage_or_claim_ids")

    claims = _sorted_claims(request.synthesis.claims)
    blockers = _hard_gate_findings(claims, loaded.policy)
    conflicts = {item.dimension for item in request.synthesis.conflicts}
    conclusions = SixDimensionConclusionsV2(
        **{
            dimension.value: _dimension_conclusion(
                dimension,
                claims,
                policy=loaded.policy,
                blockers=blockers,
                has_conflict=dimension in conflicts,
            )
            for dimension in EvidenceDimension
        }
    )
    qualified_questions = _qualified_questions(
        request.synthesis.question_candidates,
        claims,
        loaded.policy,
    )
    critical = (
        conclusions.feasibility,
        conclusions.mandate_fit,
        conclusions.company_fit,
        conclusions.transferability,
        conclusions.career_value,
    )
    all_conclusions = (*critical, conclusions.evidence_confidence)
    if blockers or any(item.outcome is DimensionOutcome.NEGATIVE for item in critical):
        verdict = SystemVerdict.REJECT
    elif all(
        item.outcome is DimensionOutcome.POSITIVE
        for item in all_conclusions
    ):
        verdict = SystemVerdict.PRIORITY
    elif (
        len(qualified_questions) == 1
        and sum(
            item.outcome is DimensionOutcome.UNKNOWN for item in all_conclusions
        )
        == 1
        and all(
            item.outcome in {DimensionOutcome.POSITIVE, DimensionOutcome.UNKNOWN}
            for item in all_conclusions
        )
    ):
        verdict = SystemVerdict.INVESTIGATE
    else:
        verdict = SystemVerdict.SAVE

    selection = _selection_mode(request.selection, claims, loaded.policy)
    if selection is None:
        return _failure("selection_evidence_invalid")
    selection_mode, exploration_axes = selection
    company_action = _company_action(request.company_action)
    if company_action is False:
        return _failure("invalid_company_action_preconditions")

    recommended_question = None
    recommended_action = None
    if verdict is SystemVerdict.PRIORITY:
        recommended_action = RecommendedActionKind.APPLICATION
    elif verdict is SystemVerdict.INVESTIGATE and len(qualified_questions) == 1:
        question, rule = qualified_questions[0]
        recommended_action = rule.action_kind
        recommended_question = RecommendedQuestionV2(
            question_id=question.question_id,
            dimension=question.dimension,
            question_code=question.question_code,
            question=question.question,
            evidence_pointers=tuple(sorted(question.citations)),
        )

    daily_eligible = (
        verdict is SystemVerdict.PRIORITY
        or (verdict is SystemVerdict.INVESTIGATE and len(qualified_questions) == 1)
        or (
            verdict is SystemVerdict.SAVE
            and selection_mode is SelectionMode.EXPLORATION
            and bool(request.selection.hypothesis_id)
            and bool(request.selection.information_value)
            and request.selection.daily_slot_available
        )
    )
    if verdict is SystemVerdict.REJECT:
        daily_eligible = False
    urgent_eligible = _urgent_eligible(
        request=request,
        verdict=verdict,
        policy=loaded.policy,
    )
    destinations: set[str] = set()
    if verdict is SystemVerdict.REJECT:
        destinations.add("rejection_ledger")
    elif urgent_eligible:
        destinations.add("urgent_exception")
    elif daily_eligible:
        destinations.add("daily_digest")
    else:
        destinations.add("saved_set")
    if company_action is not None:
        destinations.add("weekly_company_section")

    unknowns = tuple(
        sorted(
            {
                claim.claim_code
                for claim in claims
                if claim.status is EvidenceClaimStatus.UNKNOWN
            }
        )
    )
    warnings = tuple(
        sorted(
            {
                warning
                for conclusion in conclusions
                for warning in conclusion[1].warning_codes
            }
        )
    )
    reasons = tuple(
        sorted(
            {
                code
                for conclusion in conclusions
                for code in conclusion[1].reason_codes
            }
        )
    )
    evidence_pointers = tuple(
        sorted({pointer for claim in claims for pointer in claim.citations})
    )
    steps = (
        DecisionStepTraceV2(ordinal=1, name="verify_identity_freshness_minimum_evidence", outcome="passed"),
        DecisionStepTraceV2(ordinal=2, name="apply_hard_feasibility_function_scope_gates", outcome="eligible" if not blockers else "blocked"),
        DecisionStepTraceV2(ordinal=3, name="evaluate_mandate", outcome=conclusions.mandate_fit.outcome.value),
        DecisionStepTraceV2(ordinal=4, name="evaluate_company_context", outcome=conclusions.company_fit.outcome.value),
        DecisionStepTraceV2(ordinal=5, name="evaluate_transferability_and_career_value", outcome=f"{conclusions.transferability.outcome.value}:{conclusions.career_value.outcome.value}"),
        DecisionStepTraceV2(ordinal=6, name="assign_verdict_and_evidence_confidence", outcome=f"{verdict.value}:{conclusions.evidence_confidence.outcome.value}"),
        DecisionStepTraceV2(ordinal=7, name="assign_selection_mode_and_company_action_independently", outcome=selection_mode.value),
        DecisionStepTraceV2(ordinal=8, name="assign_destination_and_delivery_eligibility", outcome=",".join(sorted(destinations))),
    )
    trace = DecisionTraceV2(
        policy_version=loaded.policy.policy_version,
        references=request.references,
        authority_inputs=request.authority_inputs,
        normalized_evidence_sha256=_normalized_evidence_sha256(request),
        steps=steps,
        evaluated_at=request.evaluated_at,
        clock=DecisionClockTraceV2(
            assessed_at=request.assessed_at,
            evaluated_at=request.evaluated_at,
            daily_digest_at=request.daily_digest_at,
            urgency_learned_at=(
                request.urgency_evidence.learned_at
                if request.urgency_evidence is not None
                else None
            ),
            urgency_deadline_at=(
                request.urgency_evidence.deadline_at
                if request.urgency_evidence is not None
                else None
            ),
        ),
        canonical_sha256="0" * 64,
    )
    assessment = DecisionAssessmentV2(
        schema_version="2.0.0",
        assessment_id=request.assessment_id,
        hard_gate_eligible=not blockers,
        dimensions=conclusions,
        system_verdict=verdict,
        selection_mode=selection_mode,
        exploration_hypothesis_id=(
            request.selection.hypothesis_id
            if selection_mode is SelectionMode.EXPLORATION
            else None
        ),
        exploration_axes=(
            tuple(axis.value for axis in exploration_axes)
            if selection_mode is SelectionMode.EXPLORATION
            else ()
        ),
        single_reaction_updates_hypothesis=(
            selection_mode is SelectionMode.EXPLORATION
            and len(exploration_axes) == 1
        ),
        company_action=company_action,
        recommended_action_kind=recommended_action,
        recommended_question=recommended_question,
        daily_digest_eligible=daily_eligible,
        urgent_eligible=urgent_eligible,
        destinations=tuple(sorted(destinations)),
        blockers=blockers,
        reasons=reasons,
        warnings=warnings,
        unknowns=unknowns,
        evidence_pointers=evidence_pointers,
        trace=trace,
    )
    canonical_hash = hashlib.sha256(
        _canonical_json_bytes(_trace_hash_payload(assessment))
    ).hexdigest()
    assessment = assessment.model_copy(
        update={"trace": trace.model_copy(update={"canonical_sha256": canonical_hash})}
    )
    return DecisionResultV2(
        status=DecisionRunStatus.ASSESSED,
        failure_reason=None,
        assessment=assessment,
    )
