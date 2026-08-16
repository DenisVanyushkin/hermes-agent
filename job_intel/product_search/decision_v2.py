"""Deterministic Product Search Decision Contract v2.

Task 10 output is evidence only.  This module is the first Product Search
component that may emit canonical funnel stage 4 and owns every normative
decision derived from the pinned policy below.  It performs no persistence,
delivery, user-decision, CRM, or company-lifecycle mutation.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from job_intel.product_search.contracts import (
    CompanyAction,
    HardGate,
    RecommendedActionKind,
    ReviewState,
    SelectionMode,
    SHA256_PATTERN,
    SystemVerdict,
    WatchlistStatus,
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


class ExplorationRequestV2(_StrictFrozenModel):
    requested_mode: SelectionMode
    qualifies_under_core_policy: bool
    hypothesis_id: str | None = None
    axes: tuple[str, ...] = ()
    multi_axis_exception_id: str | None = None
    information_value: str | None = None
    daily_slot_available: bool = False

    @model_validator(mode="after")
    def validate_exploration(self) -> Self:
        if self.requested_mode is SelectionMode.CORE:
            if not self.qualifies_under_core_policy:
                raise ValueError("Core requires qualification under Core policy")
            if self.hypothesis_id or self.axes or self.multi_axis_exception_id:
                raise ValueError("Core cannot carry Exploration state")
            return self
        if not self.hypothesis_id or not self.hypothesis_id.strip():
            raise ValueError("Exploration requires a named hypothesis")
        if not self.axes or any(not axis.strip() for axis in self.axes):
            raise ValueError("Exploration requires at least one named axis")
        if len(self.axes) != len(set(self.axes)):
            raise ValueError("Exploration axes must be unique")
        if len(self.axes) > 1 and not self.multi_axis_exception_id:
            raise ValueError("multi-axis exception is required")
        if len(self.axes) == 1 and self.multi_axis_exception_id:
            raise ValueError("single-axis Exploration cannot claim a multi-axis exception")
        return self


class CompanyActionRequestV2(_StrictFrozenModel):
    action: CompanyAction
    current_status: WatchlistStatus | None
    review_state: ReviewState
    evidence_sufficient: bool
    fit_thesis: str = Field(min_length=1)
    proposed_action: CompanyAction

    @field_validator("fit_thesis")
    @classmethod
    def canonical_thesis(cls, value: str) -> str:
        if value != " ".join(value.split()):
            raise ValueError("fit_thesis must use canonical whitespace")
        return value


class CompanyActionConclusionV2(_StrictFrozenModel):
    action: CompanyAction
    current_status: WatchlistStatus | None
    target_status: WatchlistStatus
    review_state: ReviewState
    state_mutated: Literal[False] = False


class UrgencyEvidenceV2(_StrictFrozenModel):
    kind: UrgencyEvidenceKind
    external_evidence_ref: str = Field(min_length=1)
    learned_at: datetime
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.learned_at.tzinfo is None or self.learned_at.utcoffset() is None:
            raise ValueError("learned_at must be timezone-aware")
        if self.deadline_at is not None:
            if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
                raise ValueError("deadline_at must be timezone-aware")
        if self.kind is UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS:
            if self.deadline_at is None:
                raise ValueError("explicit closing-date urgency requires deadline_at")
            if not timedelta(0) < self.deadline_at - self.learned_at <= timedelta(hours=48):
                raise ValueError("closing deadline must be within 48 hours of discovery")
        return self


class DecisionRequestV2(_StrictFrozenModel):
    schema_version: Literal["2.0.0"]
    assessment_id: str = Field(min_length=1)
    stages: StageEvidenceV2
    references: DecisionImmutableReferencesV2
    synthesis: EvidenceSynthesisResultV1
    selection: ExplorationRequestV2
    company_action: CompanyActionRequestV2 | None
    urgency_evidence: UrgencyEvidenceV2 | None
    daily_digest_at: datetime
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_clock(self) -> Self:
        for name, value in (
            ("daily_digest_at", self.daily_digest_at),
            ("evaluated_at", self.evaluated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.evaluated_at < self.daily_digest_at:
            raise ValueError("evaluated_at cannot precede daily_digest_at")
        if (
            self.urgency_evidence is not None
            and self.urgency_evidence.learned_at > self.evaluated_at
        ):
            raise ValueError("urgency evidence cannot be learned after evaluation")
        return self


class QuestionRuleV2(_StrictFrozenModel):
    question_code: str
    dimension: EvidenceDimension
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
        if set(self.urgency_claims) != {
            UrgencyEvidenceKind.EXPLICIT_CLOSING_WITHIN_48_HOURS,
            UrgencyEvidenceKind.CONFIRMED_SHORT_REFERRAL_WINDOW,
            UrgencyEvidenceKind.RECRUITER_DEADLINE,
            UrgencyEvidenceKind.LIMITED_INTAKE,
            UrgencyEvidenceKind.RAPIDLY_CLOSING_SHORTLIST,
        }:
            raise ValueError("urgency claims must cover only the five external SoT facts")
        return self


class LoadedDecisionPolicyV2(_StrictFrozenModel):
    policy: DecisionPolicyV2
    source_sha256: str = Field(pattern=SHA256_PATTERN)


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
    return LoadedDecisionPolicyV2(
        policy=DecisionPolicyV2.model_validate(payload),
        source_sha256=source_hash,
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


class DecisionTraceV2(_StrictFrozenModel):
    policy_version: str
    references: DecisionImmutableReferencesV2
    normalized_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    steps: tuple[DecisionStepTraceV2, ...] = Field(min_length=8, max_length=8)
    evaluated_at: datetime
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
    policy: DecisionPolicyV2,
) -> tuple[tuple[EvidenceQuestionCandidateV1, QuestionRuleV2], ...]:
    rules = {item.question_code: item for item in policy.qualified_questions}
    result = []
    for question in questions:
        rule = rules.get(question.question_code)
        if (
            rule is not None
            and question.dimension is rule.dimension
            and question.question == rule.question
        ):
            result.append((question, rule))
    return tuple(sorted(result, key=lambda item: item[0].question_id))


def _company_action(
    request: CompanyActionRequestV2 | None,
) -> CompanyActionConclusionV2 | None | bool:
    if request is None:
        return None
    if (
        not request.evidence_sufficient
        or request.action is not request.proposed_action
        or not request.fit_thesis.strip()
        or request.current_status in {WatchlistStatus.REJECTED, WatchlistStatus.EXPIRED}
    ):
        return False
    allowed: dict[CompanyAction, tuple[set[WatchlistStatus | None], WatchlistStatus]] = {
        CompanyAction.NOMINATE: ({None}, WatchlistStatus.CANDIDATE),
        CompanyAction.PROMOTE: ({WatchlistStatus.CANDIDATE}, WatchlistStatus.ACTIVE),
        CompanyAction.RETAIN: ({WatchlistStatus.ACTIVE}, WatchlistStatus.ACTIVE),
        CompanyAction.DEPRIORITIZE: (
            {WatchlistStatus.CANDIDATE, WatchlistStatus.ACTIVE},
            WatchlistStatus.DEPRIORITIZED,
        ),
        CompanyAction.REJECT: (
            {
                WatchlistStatus.CANDIDATE,
                WatchlistStatus.ACTIVE,
                WatchlistStatus.DEPRIORITIZED,
            },
            WatchlistStatus.REJECTED,
        ),
        CompanyAction.EXPIRE: (
            {
                WatchlistStatus.CANDIDATE,
                WatchlistStatus.ACTIVE,
                WatchlistStatus.DEPRIORITIZED,
            },
            WatchlistStatus.EXPIRED,
        ),
    }
    permitted, target = allowed[request.action]
    if request.current_status not in permitted:
        return False
    if request.action is CompanyAction.EXPIRE and request.review_state is not ReviewState.REVIEW_DUE:
        return False
    return CompanyActionConclusionV2(
        action=request.action,
        current_status=request.current_status,
        target_status=target,
        review_state=request.review_state,
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
            or evidence.deadline_at > request.evaluated_at
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
        request.synthesis.question_candidates, loaded.policy
    )
    critical = (
        conclusions.feasibility,
        conclusions.mandate_fit,
        conclusions.company_fit,
        conclusions.transferability,
        conclusions.career_value,
    )
    if blockers or any(item.outcome is DimensionOutcome.NEGATIVE for item in critical):
        verdict = SystemVerdict.REJECT
    elif all(
        item.outcome is DimensionOutcome.POSITIVE
        for item in (*critical, conclusions.evidence_confidence)
    ):
        verdict = SystemVerdict.PRIORITY
    elif len(qualified_questions) == 1 and all(
        item.outcome is not DimensionOutcome.NEGATIVE for item in critical
    ):
        verdict = SystemVerdict.INVESTIGATE
    else:
        verdict = SystemVerdict.SAVE

    selection_mode = (
        SelectionMode.CORE
        if request.selection.qualifies_under_core_policy
        else request.selection.requested_mode
    )
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
        normalized_evidence_sha256=_normalized_evidence_sha256(request),
        steps=steps,
        evaluated_at=request.evaluated_at,
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
            tuple(sorted(request.selection.axes))
            if selection_mode is SelectionMode.EXPLORATION
            else ()
        ),
        single_reaction_updates_hypothesis=(
            selection_mode is SelectionMode.EXPLORATION
            and len(request.selection.axes) == 1
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
