"""Bounded provider-assisted evidence synthesis for Product Search.

The provider may select only pre-authorized, cited evidence claims.  This
module never computes product decisions and has no live network client; its
offline adapter reuses the Semantic Contract runtime's recording store.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from job_intel.product_search.company_evidence import CompanyEvidenceBundleV1
from job_intel.product_search.contracts import (
    AssessmentInputV1,
    CareerProfileV2,
    DimensionEvidenceState,
    ImmutableArtifactRef,
    SHA256_PATTERN,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    GovernedPricingSchedule,
    GovernedStructuredRequest,
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore,
    StructuredCallCapability,
    build_live_llm_provider,
)


DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/product_search/evidence_synthesis.v1.yaml"
)
OUTPUT_SCHEMA_VERSION = "1.0.0"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_PRIVATE_MARKERS = ("hermes-private://", "user note", "private resume")
_TASK10_SYSTEM_PROMPT_BASE = """You synthesize bounded Product Search evidence.
Return only a JSON object matching the supplied schema. Select claims only from
the allowed_claims attached to the supplied evidence fragments, cite every
claim with its exact fragment_id, and use only the supplied question templates.
Cover all six dimensions. You must not decide fit, verdict, selection mode,
urgency, delivery, CRM state, hard gates, or company actions. Do not use any
knowledge outside the supplied redacted input."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceDimension(str, Enum):
    FEASIBILITY = "feasibility"
    MANDATE_FIT = "mandate_fit"
    COMPANY_FIT = "company_fit"
    TRANSFERABILITY = "transferability"
    CAREER_VALUE = "career_value"
    EVIDENCE_CONFIDENCE = "evidence_confidence"


class EvidenceClaimStatus(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class EvidenceSourceKind(str, Enum):
    VACANCY = "vacancy"
    COMPANY = "company"
    CANDIDATE_PROFILE = "candidate_profile"
    ASSESSMENT_UNKNOWN = "assessment_unknown"


class EvidenceSynthesisStatus(str, Enum):
    DELIVERABLE = "deliverable"
    FORBIDDEN_FIELD = "forbidden_field"
    INVALID_SCHEMA = "invalid_schema"
    MISSING_CITATION = "missing_citation"
    FOREIGN_CITATION = "foreign_citation"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    INCOMPLETE_DIMENSIONS = "incomplete_dimensions"
    BOUNDS_EXCEEDED = "bounds_exceeded"
    REFUSAL = "refusal"
    TIMEOUT = "timeout"
    PROVIDER_OUTAGE = "provider_outage"
    PROVIDER_ERROR = "provider_error"
    RECORDING_MISSING = "recording_missing"
    PROVIDER_METADATA_MISMATCH = "provider_metadata_mismatch"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_text(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    if cleaned != value:
        raise ValueError(f"{field_name} must use canonical whitespace")
    if any(marker in cleaned.casefold() for marker in _PRIVATE_MARKERS):
        raise ValueError(f"{field_name} contains prohibited private text")
    return cleaned


class AllowedEvidenceClaimV1(_StrictFrozenModel):
    claim_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")
    dimension: EvidenceDimension
    status: EvidenceClaimStatus
    statement: str = Field(min_length=1, max_length=500)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        return _canonical_text(value, "statement")


class EvidenceFragmentV1(_StrictFrozenModel):
    fragment_id: str = Field(min_length=1, max_length=160)
    artifact_ref: ImmutableArtifactRef
    source_kind: EvidenceSourceKind
    source_locator: str = Field(min_length=1, max_length=200)
    permitted_dimensions: tuple[EvidenceDimension, ...] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)
    text_sha256: str = Field(pattern=SHA256_PATTERN)
    allowed_claims: tuple[AllowedEvidenceClaimV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fragment(self) -> Self:
        _canonical_text(self.fragment_id, "fragment_id")
        _canonical_text(self.source_locator, "source_locator")
        _canonical_text(self.text, "text")
        if _sha256_text(self.text) != self.text_sha256:
            raise ValueError("text_sha256 does not match fragment text")
        if len(self.permitted_dimensions) != len(set(self.permitted_dimensions)):
            raise ValueError("permitted_dimensions must not contain duplicates")
        claim_keys = {
            (claim.claim_code, claim.dimension, claim.status, claim.statement)
            for claim in self.allowed_claims
        }
        if len(claim_keys) != len(self.allowed_claims):
            raise ValueError("allowed_claims must not contain duplicates")
        for claim in self.allowed_claims:
            if claim.dimension not in self.permitted_dimensions:
                raise ValueError("allowed claim uses a foreign dimension")
            if claim.statement != self.text:
                raise ValueError("allowed claim statement must equal its cited fragment text")
        return self


class VacancyEvidenceArtifactFragmentV1(_StrictFrozenModel):
    source_locator: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_redacted_fragment(self) -> Self:
        _canonical_text(self.source_locator, "source_locator")
        _canonical_text(self.text, "text")
        return self


class VacancyEvidenceArtifactV1(_StrictFrozenModel):
    """Closed redacted vacancy artifact resolved by an immutable byte hash."""

    schema_version: Literal["1.0.0"]
    artifact_id: str = Field(min_length=1)
    artifact_version: str
    redaction_state: Literal["shareable_redacted"]
    fragments: tuple[VacancyEvidenceArtifactFragmentV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_locators(self) -> Self:
        locators = [fragment.source_locator for fragment in self.fragments]
        if len(locators) != len(set(locators)):
            raise ValueError("vacancy artifact fragments contain duplicate locators")
        return self


class EvidenceSynthesisInputV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    assessment_input: AssessmentInputV1
    career_profile: CareerProfileV2
    company_evidence_bundle: CompanyEvidenceBundleV1
    fragments: tuple[EvidenceFragmentV1, ...] = Field(min_length=1)
    vacancy_artifacts_root: Path

    @property
    def company_evidence_ref(self) -> ImmutableArtifactRef:
        bundle = self.company_evidence_bundle
        return ImmutableArtifactRef(
            artifact_id=bundle.bundle_id,
            version=bundle.schema_version,
            sha256=bundle.content_sha256,
        )

    @model_validator(mode="after")
    def validate_authoritative_inputs(self) -> Self:
        refs = self.assessment_input.references
        if refs.candidate_facts_ref != self.career_profile.authorities.candidate_facts_ref:
            raise ValueError("candidate facts reference does not match career profile")
        if refs.search_contract_ref != self.career_profile.authorities.search_contract_ref:
            raise ValueError("search contract reference does not match career profile")
        if refs.policy_ref != self.career_profile.authorities.product_sot_ref:
            raise ValueError("policy reference does not match career profile")

        fragments_by_id = {fragment.fragment_id: fragment for fragment in self.fragments}
        if len(fragments_by_id) != len(self.fragments):
            raise ValueError("fragments must not contain duplicate fragment_id")

        company_records = {
            record.evidence_id: record for record in self.company_evidence_bundle.evidence
        }
        superseded_ids = {
            record.supersedes_evidence_id
            for record in self.company_evidence_bundle.evidence
            if record.supersedes_evidence_id is not None
        }
        profile_claims = {
            claim.claim_id.value: claim for claim in self.career_profile.candidate_fact_claims
        }
        vacancy_refs = set(self.company_evidence_bundle.vacancy_evidence_refs)
        vacancy_artifacts: dict[
            ImmutableArtifactRef, dict[str, VacancyEvidenceArtifactFragmentV1]
        ] = {}
        for vacancy_ref in vacancy_refs:
            artifact_path = self.vacancy_artifacts_root / f"{vacancy_ref.sha256}.json"
            try:
                artifact_bytes = artifact_path.read_bytes()
            except OSError as exc:
                raise ValueError("vacancy evidence artifact is unavailable") from exc
            if hashlib.sha256(artifact_bytes).hexdigest() != vacancy_ref.sha256:
                raise ValueError("vacancy evidence artifact sha256 does not match reference")
            try:
                artifact_payload = json.loads(artifact_bytes)
                artifact = VacancyEvidenceArtifactV1.model_validate(artifact_payload)
            except Exception as exc:
                raise ValueError("vacancy evidence artifact violates closed schema") from exc
            if (
                artifact.artifact_id != vacancy_ref.artifact_id
                or artifact.artifact_version != vacancy_ref.version
            ):
                raise ValueError("vacancy evidence artifact identity does not match reference")
            vacancy_artifacts[vacancy_ref] = {
                item.source_locator: item for item in artifact.fragments
            }
        authorized_fragment_ids: set[str] = set()
        unknown_locators: dict[EvidenceDimension, set[str]] = {}

        for dimension in EvidenceDimension:
            dimension_input = getattr(self.assessment_input.dimensions, dimension.value)
            if dimension_input.state is DimensionEvidenceState.EVIDENCE_AVAILABLE:
                for fragment_id in dimension_input.evidence_refs:
                    fragment = fragments_by_id.get(fragment_id)
                    if fragment is None:
                        raise ValueError(
                            f"assessment references unavailable fragment: {fragment_id}"
                        )
                    if dimension not in fragment.permitted_dimensions:
                        raise ValueError(
                            f"assessment reference is foreign to {dimension.value}: {fragment_id}"
                        )
                    authorized_fragment_ids.add(fragment_id)
            else:
                unknown_locators[dimension] = set(dimension_input.unknown_reasons)

        for fragment in self.fragments:
            if fragment.source_kind is EvidenceSourceKind.VACANCY:
                if fragment.artifact_ref not in vacancy_refs:
                    raise ValueError("vacancy fragment weakens immutable vacancy reference")
                artifact_fragment = vacancy_artifacts[fragment.artifact_ref].get(
                    fragment.source_locator
                )
                if artifact_fragment is None or artifact_fragment.text != fragment.text:
                    raise ValueError("vacancy evidence artifact fragment is mismatched")
            elif fragment.source_kind is EvidenceSourceKind.COMPANY:
                if fragment.artifact_ref != self.company_evidence_ref:
                    raise ValueError("company fragment weakens company evidence bundle hash")
                record = company_records.get(fragment.source_locator)
                if (
                    record is None
                    or fragment.source_locator in superseded_ids
                    or record.statement != fragment.text
                ):
                    raise ValueError("company evidence fragment is unavailable or mismatched")
            elif fragment.source_kind is EvidenceSourceKind.CANDIDATE_PROFILE:
                if fragment.artifact_ref != refs.profile_ref:
                    raise ValueError("candidate profile fragment weakens immutable profile reference")
                claim = profile_claims.get(fragment.source_locator)
                if claim is None or claim.statement != fragment.text:
                    raise ValueError("candidate profile fragment is unavailable or broader")
            else:
                if fragment.artifact_ref != refs.evidence_snapshot_ref:
                    raise ValueError("unknown fragment weakens evidence snapshot reference")
                matching = [
                    dimension
                    for dimension in fragment.permitted_dimensions
                    if fragment.source_locator in unknown_locators.get(dimension, set())
                ]
                if len(matching) != len(fragment.permitted_dimensions):
                    raise ValueError("unknown fragment is not an AssessmentInput unknown reason")
                authorized_fragment_ids.add(fragment.fragment_id)

        unreferenced = set(fragments_by_id) - authorized_fragment_ids
        if unreferenced:
            raise ValueError(f"unreferenced fragments are prohibited: {sorted(unreferenced)}")
        return self

    def provider_payload(self) -> dict[str, Any]:
        """Return the only redacted, bounded payload exposed to a provider."""
        return {
            "schema_version": self.schema_version,
            "assessment_id": self.assessment_input.assessment_id,
            "references": self.assessment_input.references.model_dump(mode="json"),
            "dimensions": self.assessment_input.dimensions.model_dump(mode="json"),
            "company_evidence_ref": self.company_evidence_ref.model_dump(mode="json"),
            "vacancy_evidence_refs": [
                ref.model_dump(mode="json")
                for ref in self.company_evidence_bundle.vacancy_evidence_refs
            ],
            "fragments": [fragment.model_dump(mode="json") for fragment in self.fragments],
        }


class QuestionTemplateV1(_StrictFrozenModel):
    question_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")
    question: str = Field(min_length=1, max_length=300)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        return _canonical_text(value, "question")


class EvidenceSynthesisPolicyV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    product_authority_id: Literal["PS-SOT-2026-08-10-v1"]
    provider_runtime: Literal["llm-observation"]
    provider_adapter_version: Literal["product-search-evidence-replay/1.0"]
    semantic_prompt_version: Literal["llm-obs-1.0.0"]
    model_id: Literal["openai/gpt-5-mini"]
    prompt_version: Literal["product-search-evidence-synthesis-1.0.0"]
    output_schema_version: Literal["1.0.0"]
    dimensions: tuple[EvidenceDimension, ...]
    claim_statuses: tuple[EvidenceClaimStatus, ...]
    max_claims_per_dimension: int = Field(ge=1, le=10)
    max_conflicts: int = Field(ge=0, le=10)
    max_questions_total: int = Field(ge=0, le=12)
    max_questions_per_dimension: int = Field(ge=0, le=4)
    conflict_codes: tuple[Literal["input_evidence_conflict"], ...]
    question_templates: dict[EvidenceDimension, tuple[QuestionTemplateV1, ...]]
    forbidden_fields: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_closed_policy(self) -> Self:
        if self.dimensions != tuple(EvidenceDimension):
            raise ValueError("dimensions must be the ordered six-dimension vocabulary")
        if len(self.claim_statuses) != len(set(self.claim_statuses)) or set(
            self.claim_statuses
        ) != set(EvidenceClaimStatus):
            raise ValueError("claim_statuses must contain the complete closed vocabulary")
        if self.conflict_codes != ("input_evidence_conflict",):
            raise ValueError("conflict_codes must remain closed")
        if set(self.question_templates) != set(EvidenceDimension):
            raise ValueError("question_templates must cover all six dimensions")
        for templates in self.question_templates.values():
            codes = [template.question_code for template in templates]
            if len(codes) != len(set(codes)):
                raise ValueError("question template codes must be unique per dimension")
        required_forbidden = {
            "hard_gate",
            "hard_gate_outcome",
            "system_verdict",
            "selection_mode",
            "company_transition",
            "company_action",
            "urgency",
            "selection",
            "delivery_instruction",
            "crm",
            "user_decision",
            "stage_4",
        }
        if not required_forbidden.issubset(self.forbidden_fields):
            raise ValueError("forbidden_fields omits normative authority")
        if len(self.forbidden_fields) != len(set(self.forbidden_fields)):
            raise ValueError("forbidden_fields must not contain duplicates")
        return self


def load_evidence_synthesis_policy(
    path_or_payload: Path | str | Mapping[str, Any] = DEFAULT_POLICY_PATH,
) -> EvidenceSynthesisPolicyV1:
    if isinstance(path_or_payload, Mapping):
        payload = dict(path_or_payload)
    else:
        payload = yaml.safe_load(Path(path_or_payload).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("evidence synthesis policy must contain a mapping")
    return EvidenceSynthesisPolicyV1.model_validate(payload)


def build_task10_prompt(policy: EvidenceSynthesisPolicyV1) -> str:
    """Build the pinned Task 10 prompt from its closed policy vocabulary."""
    bounded_policy = {
        "dimensions": [item.value for item in policy.dimensions],
        "claim_statuses": [item.value for item in policy.claim_statuses],
        "max_claims_per_dimension": policy.max_claims_per_dimension,
        "max_conflicts": policy.max_conflicts,
        "max_questions_total": policy.max_questions_total,
        "max_questions_per_dimension": policy.max_questions_per_dimension,
        "conflict_codes": list(policy.conflict_codes),
        "question_templates": {
            dimension.value: [item.model_dump(mode="json") for item in templates]
            for dimension, templates in policy.question_templates.items()
        },
        "forbidden_fields": list(policy.forbidden_fields),
    }
    return (
        _TASK10_SYSTEM_PROMPT_BASE
        + "\n\nExact bounded Task 10 policy:\n"
        + json.dumps(bounded_policy, ensure_ascii=False, indent=2, sort_keys=True)
    )


def task10_prompt_sha256(policy: EvidenceSynthesisPolicyV1) -> str:
    return _sha256_text(build_task10_prompt(policy))


class EvidenceClaimV1(_StrictFrozenModel):
    claim_id: str = Field(min_length=1, max_length=160)
    dimension: EvidenceDimension
    status: EvidenceClaimStatus
    claim_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")
    statement: str = Field(min_length=1, max_length=500)
    citations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_claim_shape(self) -> Self:
        _canonical_text(self.statement, "statement")
        if len(self.citations) != len(set(self.citations)):
            raise ValueError("claim citations must not contain duplicates")
        return self


class EvidenceConflictV1(_StrictFrozenModel):
    conflict_id: str = Field(min_length=1, max_length=160)
    dimension: EvidenceDimension
    conflict_code: Literal["input_evidence_conflict"]
    claim_ids: tuple[str, ...] = Field(min_length=2)
    citations: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_conflict_shape(self) -> Self:
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("conflict claim_ids must not contain duplicates")
        if len(self.citations) != len(set(self.citations)):
            raise ValueError("conflict citations must not contain duplicates")
        return self


class EvidenceQuestionCandidateV1(_StrictFrozenModel):
    question_id: str = Field(min_length=1, max_length=160)
    dimension: EvidenceDimension
    question_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")
    question: str = Field(min_length=1, max_length=300)
    citations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_question_shape(self) -> Self:
        _canonical_text(self.question, "question")
        if len(self.citations) != len(set(self.citations)):
            raise ValueError("question citations must not contain duplicates")
        return self


class ProviderEvidencePayloadV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    claims: tuple[EvidenceClaimV1, ...]
    conflicts: tuple[EvidenceConflictV1, ...]
    question_candidates: tuple[EvidenceQuestionCandidateV1, ...]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        for field_name, id_field, records in (
            ("claims", "claim_id", self.claims),
            ("conflicts", "conflict_id", self.conflicts),
            ("question_candidates", "question_id", self.question_candidates),
        ):
            ids = [getattr(record, id_field) for record in records]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{field_name} must not contain duplicate ids")
        return self


class EvidenceSynthesisMetadataV1(_StrictFrozenModel):
    provider_id: str
    provider_version: str
    model_id: str
    semantic_prompt_version: str
    prompt_version: str
    schema_version: Literal["1.0.0"]
    latency_ms: int = Field(ge=0)
    cost_usd: str | None = Field(default=None, pattern=r"^\d+(?:\.\d+)?$")
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    output_sha256: str = Field(pattern=SHA256_PATTERN)


class EvidenceSynthesisResultV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    status: EvidenceSynthesisStatus
    deliverable: bool
    claims: tuple[EvidenceClaimV1, ...]
    conflicts: tuple[EvidenceConflictV1, ...]
    question_candidates: tuple[EvidenceQuestionCandidateV1, ...]
    failure_reason: str | None
    metadata: EvidenceSynthesisMetadataV1

    @model_validator(mode="after")
    def validate_deliverability(self) -> Self:
        if self.status is EvidenceSynthesisStatus.DELIVERABLE:
            if not self.deliverable or self.failure_reason is not None:
                raise ValueError("deliverable status must be successful")
        elif self.deliverable or self.claims or self.conflicts or self.question_candidates:
            raise ValueError("non-deliverable result cannot expose provider synthesis")
        return self


def synthesis_input_sha256(
    input_payload: dict[str, Any], *, provider: RecordedEvidenceSynthesisProvider
) -> str:
    envelope = {
        "provider_id": provider.provider_id,
        "provider_version": provider.provider_version,
        "model_id": provider.model_id,
        "semantic_prompt_version": provider.semantic_prompt_version,
        "prompt_version": provider.prompt_version,
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "input": input_payload,
    }
    return _sha256_text(_canonical_json(envelope))


def provider_output_schema_sha256() -> str:
    """Stable identity of the exact Task 10 structured-output schema."""
    return _sha256_text(_canonical_json(ProviderEvidencePayloadV1.model_json_schema()))


class RecordedEvidenceSynthesisProvider:
    """Task 10 adapter over the governed Semantic record/replay transport."""

    def __init__(
        self,
        *,
        semantic_provider: LLMObservationProvider,
        policy: EvidenceSynthesisPolicyV1,
        pricing: GovernedPricingSchedule | None = None,
        record_capability: StructuredCallCapability | None = None,
        run_identity_sha256: str | None = None,
    ) -> None:
        if not isinstance(semantic_provider, LLMObservationProvider):
            raise TypeError("governed Semantic provider must be LLMObservationProvider")
        if (
            semantic_provider.provider_id != policy.provider_runtime
            or semantic_provider.prompt_version != policy.semantic_prompt_version
            or semantic_provider.model_id != policy.model_id
        ):
            raise ValueError("governed Semantic provider identity does not match policy")
        if semantic_provider.mode == "record" and record_capability is None:
            raise ValueError("Task 10 record mode requires a runner-issued capability")
        if pricing is None and record_capability is not None:
            pricing = record_capability.pricing
        if pricing is None:
            raise ValueError("governed pricing schedule is required")
        if record_capability is not None and record_capability.pricing != pricing:
            raise ValueError("runner-issued capability pricing does not match adapter")
        if record_capability is not None:
            run_identity_sha256 = record_capability.run_identity_sha256
        if not isinstance(run_identity_sha256, str) or not re.fullmatch(
            SHA256_PATTERN, run_identity_sha256
        ):
            raise ValueError("run_identity_sha256 is required")
        self.semantic_provider = semantic_provider
        self.store = semantic_provider.store
        self.provider_id = semantic_provider.provider_id
        self.provider_version = policy.provider_adapter_version
        self.model_id = semantic_provider.model_id
        self.semantic_prompt_version = semantic_provider.prompt_version
        self.prompt_version = policy.prompt_version
        self._task10_prompt = build_task10_prompt(policy)
        self.pricing = pricing
        self.record_capability = record_capability
        self.run_identity_sha256 = run_identity_sha256
        self.last_call_metadata: dict[str, Any] = {}

    def synthesize_evidence(self, *, input_payload: dict[str, Any]) -> object:
        input_hash = synthesis_input_sha256(input_payload, provider=self)
        if self.semantic_provider.mode == "record":
            existing = self.store.path_for(input_hash)
            if not existing.exists():
                return self._record_call(input_hash, input_payload)
        record = self.store.load(input_hash)
        return self._replay_record(
            record, expected_input_hash=input_hash, input_payload=input_payload
        )

    def _expected_record_identity(
        self, input_hash: str, input_payload: dict[str, Any]
    ) -> tuple[tuple[str, Any], ...]:
        return (
            ("input_hash", input_hash),
            ("input_payload_sha256", _sha256_text(_canonical_json(input_payload))),
            ("provider_id", self.provider_id),
            ("model_id", self.model_id),
            ("semantic_prompt_version", self.semantic_prompt_version),
            ("semantic_prompt_sha256", self.semantic_provider.semantic_prompt_sha256),
            ("structured_prompt_sha256", _sha256_text(self._task10_prompt)),
            ("response_schema_sha256", provider_output_schema_sha256()),
            ("pricing_sha256", self.pricing.identity_sha256),
            ("max_output_tokens", self.pricing.max_output_tokens),
        )

    def _governance_identity(self) -> dict[str, Any]:
        return {
            "run_identity_sha256": self.run_identity_sha256,
            "provider_version": self.provider_version,
            "prompt_version": self.prompt_version,
            "task10_prompt_sha256": _sha256_text(self._task10_prompt),
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "output_schema_sha256": provider_output_schema_sha256(),
            "pricing_sha256": self.pricing.identity_sha256,
        }

    def _replay_record(
        self,
        record: dict[str, Any],
        *,
        expected_input_hash: str,
        input_payload: dict[str, Any],
    ) -> object:
        if self.record_capability is None:
            raise LLMProviderError("provider_metadata_mismatch", "metadata_seal")
        self.record_capability.verify_record(record)
        for field, expected in self._expected_record_identity(
            expected_input_hash, input_payload
        ):
            if record.get(field) != expected:
                raise LLMProviderError("provider_metadata_mismatch", field)
        if record.get("input") != input_payload:
            raise LLMProviderError("provider_metadata_mismatch", "input")
        if record.get("governance_identity") != self._governance_identity():
            raise LLMProviderError("provider_metadata_mismatch", "governance_identity")
        if record.get("pricing") != self.pricing.as_record():
            raise LLMProviderError("provider_metadata_mismatch", "pricing")
        if record.get("decoding_parameters") != {"temperature": 0}:
            raise LLMProviderError("provider_metadata_mismatch", "decoding_parameters")
        status = record.get("status")
        if status not in {"success", "failure"}:
            raise LLMProviderError("provider_metadata_mismatch", "status")
        failure_code = record.get("failure_code")
        if (status == "success") == bool(failure_code):
            raise LLMProviderError("provider_metadata_mismatch", "status/failure_code")
        latency_ms = record.get("latency_ms")
        if not isinstance(latency_ms, int) or latency_ms < 0:
            raise LLMProviderError("provider_metadata_mismatch", "latency_ms")
        if record.get("retry_count", 0) != 0:
            raise LLMProviderError("provider_metadata_mismatch", "retry_count")
        if status == "success":
            response_model = record.get("response_model")
            if response_model != self.model_id:
                raise LLMProviderError(
                    "provider_metadata_mismatch", "response_model"
                )
            try:
                usage = self.pricing.validate_usage(record.get("usage"))
                cost = self.pricing.cost(
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                )
            except Exception as exc:
                raise LLMProviderError(
                    "provider_metadata_mismatch", "usage"
                ) from exc
            if str(cost) != record.get("cost_usd"):
                raise LLMProviderError("provider_metadata_mismatch", "cost_usd")
        self.last_call_metadata = {
            "latency_ms": record.get("latency_ms", 0),
            "cost_usd": record.get("cost_usd"),
        }
        if failure_code:
            recorded_reason = str(failure_code)
            if recorded_reason not in {
                "refusal",
                "timeout",
                "provider_outage",
                "transport_error",
                "schema_invalid",
                "invalid_json",
                "usage_invalid",
                "provider_metadata_mismatch",
                "forbidden_response_marker",
            }:
                recorded_reason = "recorded_call_failed"
            diagnostic = str(record.get("failure_diagnostic") or "")
            raise LLMProviderError(recorded_reason, diagnostic)
        try:
            return json.loads(record["raw_response_text"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMProviderError("schema_invalid", "recorded JSON is invalid") from exc

    def _record_call(self, input_hash: str, input_payload: dict[str, Any]) -> object:
        if self.record_capability is None:
            raise LLMProviderError("structured_capability_required")
        try:
            result = self.semantic_provider.governed_structured_call(
                request=GovernedStructuredRequest(
                    input_hash=input_hash,
                    system_prompt=self._task10_prompt,
                    user_payload=input_payload,
                    schema_name="product_search_evidence_synthesis",
                    response_schema=ProviderEvidencePayloadV1.model_json_schema(),
                    governance_identity=self._governance_identity(),
                    forbidden_markers=_PRIVATE_MARKERS,
                ),
                capability=self.record_capability,
            )
        finally:
            self.last_call_metadata = dict(self.semantic_provider.last_call_metadata)
        try:
            return json.loads(result.raw_response_text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMProviderError("schema_invalid", "structured JSON invalid") from exc


def build_live_evidence_synthesis_provider(
    *,
    store_dir: Path | str,
    policy: EvidenceSynthesisPolicyV1,
    pricing: GovernedPricingSchedule,
    record_capability: StructuredCallCapability,
) -> RecordedEvidenceSynthesisProvider:
    """Build Task 10 record mode only through the Semantic spend-gated factory."""
    semantic_provider = build_live_llm_provider(
        store_dir=store_dir,
        model_id=policy.model_id,
        prompt_version=policy.semantic_prompt_version,
    )
    return RecordedEvidenceSynthesisProvider(
        semantic_provider=semantic_provider,
        policy=policy,
        pricing=pricing,
        record_capability=record_capability,
    )


def _normalized_key(value: str) -> str:
    """Canonical key space: exact alphanumerics, case/separators ignored."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _find_forbidden_field(value: object, forbidden_fields: tuple[str, ...]) -> str | None:
    forbidden = {_normalized_key(item) for item in forbidden_fields}
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = _normalized_key(str(key))
            if normalized in forbidden:
                return str(key)
            found = _find_forbidden_field(nested, forbidden_fields)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_forbidden_field(nested, forbidden_fields)
            if found is not None:
                return found
    return None


def _safe_output_sha256(value: object | None) -> str:
    if value is None:
        return _EMPTY_SHA256
    try:
        return _sha256_text(_canonical_json(value))
    except (TypeError, ValueError):
        return _sha256_text(type(value).__qualname__)


def _metadata(
    *,
    provider: RecordedEvidenceSynthesisProvider,
    input_hash: str,
    output_hash: str,
    elapsed_ms: int,
) -> EvidenceSynthesisMetadataV1:
    call_metadata = getattr(provider, "last_call_metadata", {}) or {}
    latency = call_metadata.get("latency_ms")
    if not isinstance(latency, int) or latency < 0:
        latency = elapsed_ms
    cost = call_metadata.get("cost_usd")
    if cost is not None:
        cost = str(cost)
    return EvidenceSynthesisMetadataV1(
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        model_id=provider.model_id,
        semantic_prompt_version=provider.semantic_prompt_version,
        prompt_version=provider.prompt_version,
        schema_version=OUTPUT_SCHEMA_VERSION,
        latency_ms=latency,
        cost_usd=cost,
        input_sha256=input_hash,
        output_sha256=output_hash,
    )


def _failure(
    status: EvidenceSynthesisStatus,
    *,
    provider: RecordedEvidenceSynthesisProvider,
    input_hash: str,
    output_hash: str,
    elapsed_ms: int,
) -> EvidenceSynthesisResultV1:
    return EvidenceSynthesisResultV1(
        schema_version=OUTPUT_SCHEMA_VERSION,
        status=status,
        deliverable=False,
        claims=(),
        conflicts=(),
        question_candidates=(),
        failure_reason=status.value,
        metadata=_metadata(
            provider=provider,
            input_hash=input_hash,
            output_hash=output_hash,
            elapsed_ms=elapsed_ms,
        ),
    )


def _validate_citations(
    citations: tuple[str, ...],
    *,
    dimension: EvidenceDimension,
    fragments_by_id: dict[str, EvidenceFragmentV1],
) -> EvidenceSynthesisStatus | None:
    for citation in citations:
        fragment = fragments_by_id.get(citation)
        if fragment is None:
            return EvidenceSynthesisStatus.MISSING_CITATION
        if dimension not in fragment.permitted_dimensions:
            return EvidenceSynthesisStatus.FOREIGN_CITATION
    return None


def _validate_provider_payload(
    payload: ProviderEvidencePayloadV1,
    *,
    synthesis_input: EvidenceSynthesisInputV1,
    policy: EvidenceSynthesisPolicyV1,
) -> EvidenceSynthesisStatus | None:
    dimensions = {claim.dimension for claim in payload.claims}
    if dimensions != set(EvidenceDimension):
        return EvidenceSynthesisStatus.INCOMPLETE_DIMENSIONS
    claim_counts = Counter(claim.dimension for claim in payload.claims)
    if any(count > policy.max_claims_per_dimension for count in claim_counts.values()):
        return EvidenceSynthesisStatus.BOUNDS_EXCEEDED
    if len(payload.conflicts) > policy.max_conflicts:
        return EvidenceSynthesisStatus.BOUNDS_EXCEEDED
    if len(payload.question_candidates) > policy.max_questions_total:
        return EvidenceSynthesisStatus.BOUNDS_EXCEEDED
    question_counts = Counter(question.dimension for question in payload.question_candidates)
    if any(count > policy.max_questions_per_dimension for count in question_counts.values()):
        return EvidenceSynthesisStatus.BOUNDS_EXCEEDED

    fragments_by_id = {
        fragment.fragment_id: fragment for fragment in synthesis_input.fragments
    }
    claims_by_id = {claim.claim_id: claim for claim in payload.claims}
    for claim in payload.claims:
        citation_failure = _validate_citations(
            claim.citations,
            dimension=claim.dimension,
            fragments_by_id=fragments_by_id,
        )
        if citation_failure is not None:
            return citation_failure
        supported = all(
            any(
                allowed.claim_code == claim.claim_code
                and allowed.dimension is claim.dimension
                and allowed.status is claim.status
                and allowed.statement == claim.statement
                for allowed in fragments_by_id[citation].allowed_claims
            )
            for citation in claim.citations
        )
        if not supported:
            return EvidenceSynthesisStatus.UNSUPPORTED_CLAIM

    for conflict in payload.conflicts:
        citation_failure = _validate_citations(
            conflict.citations,
            dimension=conflict.dimension,
            fragments_by_id=fragments_by_id,
        )
        if citation_failure is not None:
            return citation_failure
        conflict_claims = [claims_by_id.get(claim_id) for claim_id in conflict.claim_ids]
        if any(
            claim is None or claim.dimension is not conflict.dimension
            for claim in conflict_claims
        ):
            return EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
        cited_by_claims = {
            citation
            for claim in conflict_claims
            if claim is not None
            for citation in claim.citations
        }
        if not set(conflict.citations).issubset(cited_by_claims):
            return EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
        if any(
            not set(claim.citations).intersection(conflict.citations)
            for claim in conflict_claims
            if claim is not None
        ):
            return EvidenceSynthesisStatus.UNSUPPORTED_CLAIM

    templates = {
        dimension: {
            template.question_code: template.question
            for template in policy.question_templates[dimension]
        }
        for dimension in EvidenceDimension
    }
    for question in payload.question_candidates:
        citation_failure = _validate_citations(
            question.citations,
            dimension=question.dimension,
            fragments_by_id=fragments_by_id,
        )
        if citation_failure is not None:
            return citation_failure
        if templates[question.dimension].get(question.question_code) != question.question:
            return EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    return None


def _status_for_provider_error(error: Exception) -> EvidenceSynthesisStatus:
    if isinstance(error, TimeoutError):
        return EvidenceSynthesisStatus.TIMEOUT
    if isinstance(error, ConnectionError):
        return EvidenceSynthesisStatus.PROVIDER_OUTAGE
    if isinstance(error, LLMProviderError):
        return {
            "refusal": EvidenceSynthesisStatus.REFUSAL,
            "timeout": EvidenceSynthesisStatus.TIMEOUT,
            "schema_invalid": EvidenceSynthesisStatus.INVALID_SCHEMA,
            "invalid_json": EvidenceSynthesisStatus.INVALID_SCHEMA,
            "provider_outage": EvidenceSynthesisStatus.PROVIDER_OUTAGE,
            "transport_error": EvidenceSynthesisStatus.PROVIDER_OUTAGE,
            "transport_unavailable": EvidenceSynthesisStatus.PROVIDER_OUTAGE,
            "recording_missing": EvidenceSynthesisStatus.RECORDING_MISSING,
            "provider_metadata_mismatch": EvidenceSynthesisStatus.PROVIDER_METADATA_MISMATCH,
        }.get(error.reason, EvidenceSynthesisStatus.PROVIDER_ERROR)
    return EvidenceSynthesisStatus.PROVIDER_ERROR


def run_evidence_synthesis(
    *,
    synthesis_input: EvidenceSynthesisInputV1,
    provider: RecordedEvidenceSynthesisProvider,
    policy: EvidenceSynthesisPolicyV1 | None = None,
) -> EvidenceSynthesisResultV1:
    """Run one bounded synthesis; every failure returns non-deliverable data."""
    policy = policy or load_evidence_synthesis_policy()
    if not isinstance(provider, RecordedEvidenceSynthesisProvider):
        raise TypeError(
            "governed Semantic provider requires RecordedEvidenceSynthesisProvider"
        )
    input_payload = synthesis_input.provider_payload()
    input_hash = synthesis_input_sha256(input_payload, provider=provider)
    started = time.monotonic()

    if (
        provider.provider_id != policy.provider_runtime
        or provider.provider_version != policy.provider_adapter_version
        or provider.semantic_prompt_version != policy.semantic_prompt_version
        or provider.model_id != policy.model_id
        or provider.prompt_version != policy.prompt_version
    ):
        return _failure(
            EvidenceSynthesisStatus.PROVIDER_METADATA_MISMATCH,
            provider=provider,
            input_hash=input_hash,
            output_hash=_EMPTY_SHA256,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    try:
        raw_payload = provider.synthesize_evidence(input_payload=input_payload)
    except Exception as error:  # provider boundary: convert to a closed result
        return _failure(
            _status_for_provider_error(error),
            provider=provider,
            input_hash=input_hash,
            output_hash=_EMPTY_SHA256,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    output_hash = _safe_output_sha256(raw_payload)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    forbidden = _find_forbidden_field(raw_payload, policy.forbidden_fields)
    if forbidden is not None:
        return _failure(
            EvidenceSynthesisStatus.FORBIDDEN_FIELD,
            provider=provider,
            input_hash=input_hash,
            output_hash=output_hash,
            elapsed_ms=elapsed_ms,
        )
    try:
        payload = ProviderEvidencePayloadV1.model_validate(raw_payload)
    except Exception:
        return _failure(
            EvidenceSynthesisStatus.INVALID_SCHEMA,
            provider=provider,
            input_hash=input_hash,
            output_hash=output_hash,
            elapsed_ms=elapsed_ms,
        )
    validation_failure = _validate_provider_payload(
        payload,
        synthesis_input=synthesis_input,
        policy=policy,
    )
    if validation_failure is not None:
        return _failure(
            validation_failure,
            provider=provider,
            input_hash=input_hash,
            output_hash=output_hash,
            elapsed_ms=elapsed_ms,
        )
    return EvidenceSynthesisResultV1(
        schema_version=OUTPUT_SCHEMA_VERSION,
        status=EvidenceSynthesisStatus.DELIVERABLE,
        deliverable=True,
        claims=payload.claims,
        conflicts=payload.conflicts,
        question_candidates=payload.question_candidates,
        failure_reason=None,
        metadata=_metadata(
            provider=provider,
            input_hash=input_hash,
            output_hash=output_hash,
            elapsed_ms=elapsed_ms,
        ),
    )
