"""Immutable, persistence-free company evidence contracts for Product Search.

The models in this module validate deterministic fixture/content-addressed
inputs only.  They do not fetch sources, create opportunities, persist state,
or perform owner actions.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Self

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from pydantic import field_validator
from pydantic import model_validator

from .contracts import ImmutableArtifactRef, SHA256_PATTERN


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanyIdentityResolutionState(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class CompanyEvidenceKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"


class CompanyEvidenceDimension(str, Enum):
    SCALE_STAGE = "scale_stage"
    TRAJECTORY = "trajectory"
    BUSINESS_MODEL = "business_model"
    EMPLOYER_RISK = "employer_risk"
    GEOGRAPHIC_CONTEXT = "geographic_context"
    CREDIBLE_NEED = "credible_need"
    SIGNAL_EVENT = "signal_event"


class CompanyEvidenceSourceType(str, Enum):
    COMPANY_WEBSITE = "company_website"
    REGULATORY_FILING = "regulatory_filing"
    NEWS_REPORT = "news_report"
    MARKET_RESEARCH = "market_research"
    EMPLOYER_REVIEW = "employer_review"


class EvidenceFreshnessState(str, Enum):
    CURRENT = "current"
    STALE = "stale"


class EvidenceContradictionState(str, Enum):
    UNOPPOSED = "unopposed"
    CONTRADICTED = "contradicted"


class EvidenceSufficiencyState(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"


class EvidenceSensitivity(str, Enum):
    PUBLIC = "public"


class EvidenceRedactionState(str, Enum):
    SHAREABLE_REDACTED = "shareable_redacted"


class CompanyIntelligenceProposedAction(str, Enum):
    RESEARCH = "research"
    MONITOR = "monitor"
    NETWORKING = "networking"
    OUTREACH = "outreach"


_REQUIRED_DIMENSIONS = frozenset(
    {
        CompanyEvidenceDimension.SCALE_STAGE,
        CompanyEvidenceDimension.TRAJECTORY,
        CompanyEvidenceDimension.BUSINESS_MODEL,
        CompanyEvidenceDimension.EMPLOYER_RISK,
        CompanyEvidenceDimension.GEOGRAPHIC_CONTEXT,
        CompanyEvidenceDimension.CREDIBLE_NEED,
    }
)
_PRIVATE_MARKERS = ("hermes-private://", "candidate facts", "user note")
CLAIM_ID_PATTERN = r"^claim:[a-z0-9][a-z0-9:-]*$"
MACHINE_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


def _clean_text(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    if cleaned != value:
        raise ValueError(f"{field_name} must be canonical whitespace")
    lowered = cleaned.casefold()
    if any(marker in lowered for marker in _PRIVATE_MARKERS):
        raise ValueError(f"{field_name} contains a prohibited private-data marker")
    return cleaned


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CompanyIdentityV1(_StrictFrozenModel):
    company_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    domains: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_canonical_identity(self) -> Self:
        _clean_text(self.company_id, "company_id")
        _clean_text(self.canonical_name, "canonical_name")
        normalized_names: list[str] = []
        for alias in self.aliases:
            _clean_text(alias, "alias")
            normalized_names.append(alias.casefold())
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("aliases must be unique case-insensitively")
        normalized_domains: list[str] = []
        for domain in self.domains:
            if domain != domain.strip().lower().rstrip("."):
                raise ValueError("domains must be lowercase canonical hostnames")
            if not domain or "://" in domain or "/" in domain or "." not in domain:
                raise ValueError("domains must be hostnames without scheme or path")
            normalized_domains.append(domain)
        if len(normalized_domains) != len(set(normalized_domains)):
            raise ValueError("domains must be unique")
        return self


class CompanyIdentityResolutionV1(_StrictFrozenModel):
    state: CompanyIdentityResolutionState
    company_id: str | None
    candidate_company_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> Self:
        candidates = self.candidate_company_ids
        if candidates != tuple(sorted(set(candidates))):
            raise ValueError("candidate_company_ids must be sorted and unique")
        if self.state is CompanyIdentityResolutionState.RESOLVED:
            if self.company_id is None or candidates != (self.company_id,):
                raise ValueError("resolved identity requires exactly its company_id")
        elif self.state is CompanyIdentityResolutionState.AMBIGUOUS:
            if self.company_id is not None or len(candidates) < 2:
                raise ValueError("ambiguous identity requires at least two candidates")
        elif self.company_id is not None or candidates:
            raise ValueError("unresolved identity cannot contain a company candidate")
        return self


def resolve_company_identity(
    identities: Sequence[CompanyIdentityV1],
    *,
    name: str | None = None,
    domain: str | None = None,
) -> CompanyIdentityResolutionV1:
    """Resolve only exact canonical names, declared aliases, and exact domains."""
    normalized_name = " ".join(name.split()).casefold() if name is not None else None
    normalized_domain = domain.strip().lower().rstrip(".") if domain is not None else None
    candidates: list[str] = []
    for identity in identities:
        names = {identity.canonical_name.casefold(), *(alias.casefold() for alias in identity.aliases)}
        if normalized_name is not None and normalized_name not in names:
            continue
        if normalized_domain is not None and normalized_domain not in identity.domains:
            continue
        if normalized_name is not None or normalized_domain is not None:
            candidates.append(identity.company_id)
    candidate_ids = tuple(sorted(set(candidates)))
    if len(candidate_ids) == 1:
        return CompanyIdentityResolutionV1(
            state=CompanyIdentityResolutionState.RESOLVED,
            company_id=candidate_ids[0],
            candidate_company_ids=candidate_ids,
        )
    if len(candidate_ids) > 1:
        return CompanyIdentityResolutionV1(
            state=CompanyIdentityResolutionState.AMBIGUOUS,
            company_id=None,
            candidate_company_ids=candidate_ids,
        )
    return CompanyIdentityResolutionV1(
        state=CompanyIdentityResolutionState.UNRESOLVED,
        company_id=None,
        candidate_company_ids=(),
    )


class CompanyEvidenceSourceV1(_StrictFrozenModel):
    source_id: str = Field(min_length=1)
    source_type: CompanyEvidenceSourceType
    source_uri: str = Field(min_length=1)
    artifact_ref: ImmutableArtifactRef
    captured_at: AwareDatetime
    published_at: AwareDatetime
    sensitivity: EvidenceSensitivity
    redaction_state: EvidenceRedactionState

    @field_validator("source_uri")
    @classmethod
    def require_public_https_source(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source_uri must be a public https URI")
        _clean_text(value, "source_uri")
        return value

    @model_validator(mode="after")
    def published_not_after_capture(self) -> Self:
        if self.published_at > self.captured_at:
            raise ValueError("published_at cannot be after captured_at")
        return self


class PublicCompanyEvidenceClaimV1(_StrictFrozenModel):
    """One redacted claim with a bounded quote and source locator."""

    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    dimension: CompanyEvidenceDimension
    value_codes: tuple[str, ...] = Field(min_length=1)
    quote: str = Field(min_length=1)
    locator: str = Field(min_length=1)

    @field_validator("value_codes")
    @classmethod
    def validate_machine_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("value_codes must be sorted and unique")
        for code in value:
            if not re.fullmatch(MACHINE_CODE_PATTERN, code):
                raise ValueError("value_codes must contain bounded machine codes")
        return value

    @field_validator("quote", "locator")
    @classmethod
    def validate_traceability_text(cls, value: str, info: Any) -> str:
        return _clean_text(value, info.field_name)


class PublicCompanyEvidenceArtifactV1(_StrictFrozenModel):
    """Closed public/redacted source artifact used by deterministic replay."""

    schema_version: Literal["1.0.0"]
    artifact_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    redaction_state: EvidenceRedactionState
    claims: tuple[PublicCompanyEvidenceClaimV1, ...] = Field(min_length=1)

    @field_validator("source_uri")
    @classmethod
    def require_public_https_source(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source_uri must be a public https URI")
        return value

    @model_validator(mode="after")
    def validate_claim_ids(self) -> Self:
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claims must not contain duplicate claim_id")
        return self


class CompanyEvidenceRecordV1(_StrictFrozenModel):
    evidence_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    evidence_kind: CompanyEvidenceKind
    dimension: CompanyEvidenceDimension
    statement: str = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)
    freshness_state: EvidenceFreshnessState
    fresh_until: AwareDatetime
    contradiction_state: EvidenceContradictionState
    redaction_state: EvidenceRedactionState
    supersedes_evidence_id: str | None = None
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        return _clean_text(value, "statement")

    @model_validator(mode="after")
    def validate_sources_and_hash(self) -> Self:
        if any(not source_id.strip() for source_id in self.source_ids):
            raise ValueError("source_ids must contain non-empty values")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must not contain duplicates")
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if _canonical_sha256(payload) != self.content_sha256:
            raise ValueError("content_sha256 does not match evidence content")
        return self


class CompanyEvidenceBundleV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    bundle_id: str = Field(min_length=1)
    company_identity: CompanyIdentityV1
    identity_resolution: CompanyIdentityResolutionV1
    as_of: AwareDatetime
    sources: tuple[CompanyEvidenceSourceV1, ...] = Field(min_length=1)
    evidence: tuple[CompanyEvidenceRecordV1, ...] = Field(min_length=1)
    sufficiency_state: EvidenceSufficiencyState
    vacancy_evidence_refs: tuple[ImmutableArtifactRef, ...] = ()
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        source_by_id = {source.source_id: source for source in self.sources}
        if len(source_by_id) != len(self.sources):
            raise ValueError("sources contain duplicate source_id")
        if any(source.captured_at > self.as_of for source in self.sources):
            raise ValueError("source captured_at cannot be after bundle as_of")
        evidence_by_id = {record.evidence_id: record for record in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("evidence contains duplicate evidence_id")

        if self.identity_resolution.state is not CompanyIdentityResolutionState.RESOLVED:
            if self.sufficiency_state is EvidenceSufficiencyState.SUFFICIENT:
                raise ValueError(
                    f"{self.identity_resolution.state.value} identity cannot satisfy evidence"
                )
        elif self.identity_resolution.company_id != self.company_identity.company_id:
            raise ValueError("resolved company_id must match company_identity")

        superseded_ids: set[str] = set()
        evidence_positions = {
            record.evidence_id: index for index, record in enumerate(self.evidence)
        }
        for record in self.evidence:
            if record.company_id != self.company_identity.company_id:
                raise ValueError("company evidence cannot cross company identities")
            missing_sources = set(record.source_ids) - set(source_by_id)
            if missing_sources:
                raise ValueError(f"evidence references unknown source_ids: {missing_sources}")
            expected_freshness = (
                EvidenceFreshnessState.CURRENT
                if record.fresh_until >= self.as_of
                else EvidenceFreshnessState.STALE
            )
            if record.freshness_state is not expected_freshness:
                raise ValueError(
                    f"freshness_state mismatch for {record.evidence_id}: "
                    f"expected {expected_freshness.value}"
                )
            target_id = record.supersedes_evidence_id
            if target_id is None:
                continue
            target = evidence_by_id.get(target_id)
            if target is None or target_id == record.evidence_id:
                raise ValueError("supersedes_evidence_id must reference earlier evidence")
            if evidence_positions[target_id] >= evidence_positions[record.evidence_id]:
                raise ValueError("supersedes_evidence_id must reference earlier evidence")
            if target.company_id != record.company_id or target.dimension is not record.dimension:
                raise ValueError("supersession must stay within company and dimension")
            superseded_ids.add(target_id)

        active = tuple(
            record for record in self.evidence if record.evidence_id not in superseded_ids
        )
        sufficient_dimensions = {
            record.dimension
            for record in active
            if record.freshness_state is EvidenceFreshnessState.CURRENT
            and record.contradiction_state is EvidenceContradictionState.UNOPPOSED
            and record.dimension is not CompanyEvidenceDimension.SIGNAL_EVENT
        }
        missing = _REQUIRED_DIMENSIONS - sufficient_dimensions
        identity_resolved = (
            self.identity_resolution.state is CompanyIdentityResolutionState.RESOLVED
        )
        actually_sufficient = identity_resolved and not missing
        if self.sufficiency_state is EvidenceSufficiencyState.SUFFICIENT and not actually_sufficient:
            missing_names = ", ".join(sorted(item.value for item in missing))
            if all(
                record.dimension is CompanyEvidenceDimension.SIGNAL_EVENT for record in active
            ):
                raise ValueError(f"signal alone is insufficient; missing {missing_names}")
            raise ValueError(f"sufficient evidence is missing dimensions: {missing_names}")
        if self.sufficiency_state is EvidenceSufficiencyState.INSUFFICIENT and actually_sufficient:
            raise ValueError("sufficiency_state must be sufficient for complete evidence")

        vacancy_keys = {
            (ref.artifact_id, ref.version, ref.sha256) for ref in self.vacancy_evidence_refs
        }
        if len(vacancy_keys) != len(self.vacancy_evidence_refs):
            raise ValueError("vacancy_evidence_refs must not contain duplicates")

        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if _canonical_sha256(payload) != self.content_sha256:
            raise ValueError("content_sha256 does not match company evidence bundle")
        return self


class CompanyThesisInputV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    thesis_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    evidence_bundle_ref: ImmutableArtifactRef
    supporting_evidence_ids: tuple[str, ...] = Field(min_length=1)
    fit_thesis: str = Field(min_length=1)
    proposed_action: CompanyIntelligenceProposedAction

    @field_validator("fit_thesis")
    @classmethod
    def validate_fit_thesis(cls, value: str) -> str:
        return _clean_text(value, "fit_thesis")

    @field_validator("supporting_evidence_ids")
    @classmethod
    def validate_supporting_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not evidence_id.strip() for evidence_id in value):
            raise ValueError("supporting_evidence_ids must contain non-empty values")
        if len(value) != len(set(value)):
            raise ValueError("supporting_evidence_ids must not contain duplicates")
        return value


class CompanyEvidenceContractV1(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    product_authority_id: Literal["PS-SOT-2026-08-10-v1"]
    required_dimensions: tuple[CompanyEvidenceDimension, ...]
    identity_states: tuple[CompanyIdentityResolutionState, ...]
    evidence_kinds: tuple[CompanyEvidenceKind, ...]
    freshness_states: tuple[EvidenceFreshnessState, ...]
    contradiction_states: tuple[EvidenceContradictionState, ...]
    sufficiency_states: tuple[EvidenceSufficiencyState, ...]
    private_inputs: Literal["prohibited"]
    persistence: Literal["prohibited"]
    weekly_intelligence_requires: tuple[
        Literal["company_evidence", "fit_thesis", "proposed_action"], ...
    ]

    @model_validator(mode="after")
    def validate_closed_vocabularies(self) -> Self:
        expected_sets: tuple[tuple[str, tuple[Enum, ...], set[Enum]], ...] = (
            ("required_dimensions", self.required_dimensions, set(_REQUIRED_DIMENSIONS)),
            ("identity_states", self.identity_states, set(CompanyIdentityResolutionState)),
            ("evidence_kinds", self.evidence_kinds, set(CompanyEvidenceKind)),
            ("freshness_states", self.freshness_states, set(EvidenceFreshnessState)),
            (
                "contradiction_states",
                self.contradiction_states,
                set(EvidenceContradictionState),
            ),
            ("sufficiency_states", self.sufficiency_states, set(EvidenceSufficiencyState)),
        )
        for field_name, values, expected in expected_sets:
            if len(values) != len(set(values)) or set(values) != expected:
                raise ValueError(f"{field_name} must contain its complete closed vocabulary")
        if set(self.weekly_intelligence_requires) != {
            "company_evidence",
            "fit_thesis",
            "proposed_action",
        }:
            raise ValueError("weekly_intelligence_requires must contain all three inputs")
        return self


def _load_yaml(path_or_payload: Path | str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(path_or_payload, Mapping):
        return dict(path_or_payload)
    payload = yaml.safe_load(Path(path_or_payload).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("company evidence YAML must contain a mapping")
    return payload


def load_company_evidence_bundle(
    path_or_payload: Path | str | Mapping[str, Any],
    *,
    artifacts_root: Path | str | None = None,
) -> CompanyEvidenceBundleV1:
    bundle = CompanyEvidenceBundleV1.model_validate(_load_yaml(path_or_payload))
    if isinstance(path_or_payload, Mapping):
        if artifacts_root is None:
            raise ValueError("artifacts_root is required for mapping company evidence")
        sources_root = Path(artifacts_root)
    else:
        if artifacts_root is not None:
            sources_root = Path(artifacts_root)
        else:
            bundle_path = Path(path_or_payload)
            bundle_parent = bundle_path.parent
            # Published bundles are content-addressed below <company>/ while
            # their source artifacts are shared by that company at <company>/sources.
            # Keep the flat fixture layout working, but do not make callers guess
            # the sibling source root with an out-of-band parameter.
            if re.fullmatch(r"[0-9a-f]{64}", bundle_parent.name):
                sources_root = bundle_parent.parent / "sources"
            else:
                sources_root = bundle_parent / "sources"
    for source in bundle.sources:
        artifact_path = sources_root / f"{source.artifact_ref.sha256}.json"
        try:
            source_bytes = artifact_path.read_bytes()
        except OSError as exc:
            raise ValueError("source artifact unavailable by content hash") from exc
        if hashlib.sha256(source_bytes).hexdigest() != source.artifact_ref.sha256:
            raise ValueError("source artifact sha256 does not match immutable reference")
        try:
            source_payload = json.loads(source_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError("source artifact must be valid JSON") from exc
        try:
            artifact = PublicCompanyEvidenceArtifactV1.model_validate(source_payload)
        except ValidationError as exc:
            raise ValueError(
                "source artifact violates closed public evidence artifact schema"
            ) from exc
        if (
            artifact.artifact_id != source.artifact_ref.artifact_id
            or artifact.company_id != bundle.company_identity.company_id
            or artifact.source_uri != source.source_uri
            or artifact.redaction_state is not source.redaction_state
        ):
            raise ValueError("source artifact provenance does not match bundle source")
        artifact_dimensions = {claim.dimension for claim in artifact.claims}
        referenced_dimensions = {
            record.dimension
            for record in bundle.evidence
            if source.source_id in record.source_ids
        }
        if not referenced_dimensions <= artifact_dimensions:
            raise ValueError("source artifact claims do not cover referenced evidence")
    return bundle


def load_company_thesis_input(
    path_or_payload: Path | str | Mapping[str, Any],
    *,
    evidence_bundle: CompanyEvidenceBundleV1,
) -> CompanyThesisInputV1:
    thesis = CompanyThesisInputV1.model_validate(_load_yaml(path_or_payload))
    if evidence_bundle.sufficiency_state is not EvidenceSufficiencyState.SUFFICIENT:
        raise ValueError("weekly company intelligence requires sufficient evidence")
    if thesis.company_id != evidence_bundle.company_identity.company_id:
        raise ValueError("thesis company_id does not match evidence bundle")
    expected_ref = (
        evidence_bundle.bundle_id,
        evidence_bundle.schema_version,
        evidence_bundle.content_sha256,
    )
    actual_ref = (
        thesis.evidence_bundle_ref.artifact_id,
        thesis.evidence_bundle_ref.version,
        thesis.evidence_bundle_ref.sha256,
    )
    if actual_ref != expected_ref:
        raise ValueError("evidence_bundle_ref does not match immutable bundle identity/hash")

    records = {record.evidence_id: record for record in evidence_bundle.evidence}
    superseded_ids = {
        record.supersedes_evidence_id
        for record in evidence_bundle.evidence
        if record.supersedes_evidence_id is not None
    }
    supporting = []
    for evidence_id in thesis.supporting_evidence_ids:
        if evidence_id in superseded_ids:
            raise ValueError(f"weekly intelligence cannot cite superseded evidence: {evidence_id}")
        try:
            supporting.append(records[evidence_id])
        except KeyError as exc:
            raise ValueError(f"unknown supporting evidence: {evidence_id}") from exc
    if all(
        record.dimension is CompanyEvidenceDimension.SIGNAL_EVENT for record in supporting
    ):
        raise ValueError("a company signal alone cannot form weekly intelligence")
    if any(
        record.freshness_state is not EvidenceFreshnessState.CURRENT
        or record.contradiction_state is not EvidenceContradictionState.UNOPPOSED
        for record in supporting
    ):
        raise ValueError("weekly intelligence supports only current unopposed evidence")
    return thesis


def load_company_evidence_contract(
    path_or_payload: Path | str | Mapping[str, Any],
) -> CompanyEvidenceContractV1:
    return CompanyEvidenceContractV1.model_validate(_load_yaml(path_or_payload))
