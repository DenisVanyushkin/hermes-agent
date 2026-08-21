"""Conservative, company-neutral Gate B evidence projection (v3)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import lru_cache
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    model_validator,
)
import yaml

from job_intel.product_search.contracts import (
    AssessmentInputV2,
    AssessmentReferences,
    CompanyAuthorityStatus,
    DecisionDimensionsInput,
    DimensionEvidenceInput,
    DimensionEvidenceState,
    ImmutableArtifactRef,
)
from job_intel.product_search.evidence_synthesis import (
    AllowedEvidenceClaimV1,
    CompanyAuthorityUnavailableV2,
    EvidenceClaimStatus,
    EvidenceDimension,
    EvidenceFragmentV1,
    EvidenceSourceKind,
    EvidenceSynthesisInputV2,
    EvidenceSynthesisStatus,
    VacancyEvidenceArtifactFragmentV1,
    VacancyEvidenceArtifactV1,
    validate_provider_payload_v3 as validate_provider_payload_v3_contract,
)
from job_intel.product_search.gate_b_benchmark_policy_v3 import (
    DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH,
    GateBBenchmarkPolicyV3,
    load_gate_b_benchmark_policy_v3,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewedFragmentDecisionV3(StrEnum):
    ALLOW_ROLE_RESPONSIBILITY = "allow_role_responsibility"
    ALLOW_ROLE_REQUIREMENT = "allow_role_requirement"
    EXCLUDE_COMPANY_FACT = "exclude_company_fact"
    EXCLUDE_AMBIGUOUS = "exclude_ambiguous"


class ReviewedFragmentEntryV3(_StrictFrozenModel):
    selection_key: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    vacancy_artifact_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]
    source_locator: Annotated[
        str, StringConstraints(pattern=r"^/description#[0-9]{3}$")
    ]
    text_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    decision: ReviewedFragmentDecisionV3
    reviewer_role: Literal["independent_gate_b_evidence_reviewer"]
    reviewed_at: AwareDatetime


class ReviewedFragmentAllowlistV3(_StrictFrozenModel):
    schema_version: Literal["3.0.0"]
    gate_a_run_id: Literal["gate-a-20260816T141344Z"]
    gate_b_corpus_sha256: Literal[
        "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
    ]
    entries: tuple[ReviewedFragmentEntryV3, ...]

    @model_validator(mode="after")
    def validate_unique_entries(self) -> Self:
        identities = {
            (
                entry.selection_key,
                entry.vacancy_artifact_sha256,
                entry.source_locator,
                entry.text_sha256,
            )
            for entry in self.entries
        }
        if len(identities) != len(self.entries):
            raise ValueError("reviewed fragment allowlist entries must be unique")
        return self


def load_reviewed_fragment_allowlist_v3(
    path: Path | str,
) -> ReviewedFragmentAllowlistV3:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ReviewedFragmentAllowlistV3.model_validate(payload)


def load_gate_b_evidence_policy_v3(
    path: Path | str = DEFAULT_GATE_B_BENCHMARK_POLICY_V3_PATH,
) -> GateBBenchmarkPolicyV3:
    return load_gate_b_benchmark_policy_v3(path)


DIRECT_FIELDS = frozenset({"title", "location", "salary", "posted_at"})
ALLOWED_SECTIONS = frozenset({
    "responsibilities",
    "what_you_will_do",
    "requirements",
    "qualifications",
    "skills",
    "experience",
})
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_PATH = _REPOSITORY_ROOT / "config/product_search/career_profile.v2.yaml"
_SEMANTIC_CONTRACT_PATH = (
    _REPOSITORY_ROOT
    / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml"
)
_GATE_A_RUN_ID = "gate-a-20260816T141344Z"
_GATE_B_CORPUS_SHA256 = (
    "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
)
_SECTION_NORMALIZATION = {
    "responsibilities": "responsibilities",
    "what_you_will_do": "what_you_will_do",
    "requirements": "requirements",
    "qualifications": "qualifications",
    "skills": "skills",
    "experience": "experience",
}
_DIRECT_DIMENSIONS = {
    "title": (EvidenceDimension.MANDATE_FIT, EvidenceDimension.EVIDENCE_CONFIDENCE),
    "location": (EvidenceDimension.FEASIBILITY, EvidenceDimension.EVIDENCE_CONFIDENCE),
    "salary": (EvidenceDimension.FEASIBILITY, EvidenceDimension.EVIDENCE_CONFIDENCE),
    "posted_at": (EvidenceDimension.EVIDENCE_CONFIDENCE,),
}
_RESPONSIBILITY_SECTIONS = frozenset({"responsibilities", "what_you_will_do"})
_ROLE_DIMENSIONS = {
    "responsibilities": (
        EvidenceDimension.MANDATE_FIT,
        EvidenceDimension.CAREER_VALUE,
    ),
    "what_you_will_do": (
        EvidenceDimension.MANDATE_FIT,
        EvidenceDimension.CAREER_VALUE,
    ),
    "requirements": (EvidenceDimension.MANDATE_FIT,),
    "qualifications": (EvidenceDimension.MANDATE_FIT,),
    "skills": (EvidenceDimension.MANDATE_FIT,),
    "experience": (EvidenceDimension.MANDATE_FIT,),
}
_UNKNOWN_REASONS = {
    EvidenceDimension.FEASIBILITY: "feasibility_not_stated_in_vacancy",
    EvidenceDimension.MANDATE_FIT: "mandate_not_stated_in_vacancy",
    EvidenceDimension.COMPANY_FIT: (
        "company_authority_unavailable:unresolved_company_identity"
    ),
    EvidenceDimension.TRANSFERABILITY: "candidate_profile_evidence_not_materialized",
    EvidenceDimension.CAREER_VALUE: "career_value_not_stated_in_vacancy",
    EvidenceDimension.EVIDENCE_CONFIDENCE: "evidence_confidence_not_established",
}


class ProjectionBlockedV3(ValueError):
    """The closed v3 projection cannot prove a safe provider input."""


class CandidateTupleV3(_StrictFrozenModel):
    """Hash-only candidate tuple handed to an independent reviewer."""

    selection_key: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    vacancy_artifact_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]
    source_locator: Annotated[
        str, StringConstraints(pattern=r"^/description#[0-9]{3}$")
    ]
    text_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    section: Literal[
        "responsibilities",
        "what_you_will_do",
        "requirements",
        "qualifications",
        "skills",
        "experience",
    ]


def serialize_candidate_tuple_table_v3(
    *,
    records: int,
    entries: Sequence[CandidateTupleV3],
) -> bytes:
    """Return canonical hash-only bytes without a terminal newline."""
    if records != 48:
        raise ProjectionBlockedV3("candidate tuple table must bind exactly 48 records")
    ordered_entries = sorted(
        entries,
        key=lambda entry: (
            entry.selection_key,
            entry.source_locator,
            entry.text_sha256,
        ),
    )
    payload = {
        "records": records,
        "candidate_occurrences": len(ordered_entries),
        "entries": [entry.model_dump(mode="json") for entry in ordered_entries],
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class DescriptionCandidateV3(_StrictFrozenModel):
    source_locator: Annotated[
        str, StringConstraints(pattern=r"^/description#[0-9]{3}$")
    ]
    section: Literal[
        "responsibilities",
        "what_you_will_do",
        "requirements",
        "qualifications",
        "skills",
        "experience",
    ]
    text: str
    text_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ProjectionAuditV3(_StrictFrozenModel):
    selection_key: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    vacancy_artifact_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]
    description_candidates_total: int
    reviewed_candidates_total: int
    direct_fields_admitted: int
    role_candidates_admitted: int
    company_fact_candidates_excluded: int
    ambiguous_fragments_excluded: int
    company_fact_candidates_admitted: int
    ambiguous_fragments_admitted: int


class VacancyProjectionCandidatesV3(_StrictFrozenModel):
    selection_key: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    vacancy_artifact_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]
    vacancy_evidence: VacancyEvidenceArtifactV1
    description_candidates: tuple[DescriptionCandidateV3, ...]


def serialize_provider_payload_v3(
    synthesis_input: EvidenceSynthesisInputV3,
) -> dict[str, Any]:
    """Serialize only v3-admitted provider evidence and its immutable identity."""
    if not isinstance(synthesis_input, EvidenceSynthesisInputV3):
        raise TypeError("v3-admitted evidence input is required")
    return {
        "schema_version": synthesis_input.schema_version,
        "assessment_input": synthesis_input.assessment_input.model_dump(mode="json"),
        "company_authority": synthesis_input.company_authority.model_dump(mode="json"),
        "vacancy_evidence_ref": synthesis_input.vacancy_evidence_ref.model_dump(
            mode="json"
        ),
        "fragments": [
            fragment.model_dump(mode="json") for fragment in synthesis_input.fragments
        ],
    }


class EvidenceSynthesisInputV3(EvidenceSynthesisInputV2):
    """V3 input preserves the local artifact but dispatches only admitted evidence."""

    def provider_payload(self) -> dict[str, Any]:
        return serialize_provider_payload_v3(self)


class _DescriptionBlock:
    __slots__ = ("section", "text")

    def __init__(self, section: str | None, text: str) -> None:
        self.section = section
        self.text = text


def _canonical_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _classify_section(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not normalized:
        return None
    if "responsibilit" in normalized:
        return "responsibilities"
    if "what_you" in normalized and ("do" in normalized or "work" in normalized):
        return "what_you_will_do"
    if "who_you_are" in normalized:
        return "qualifications"
    if (
        "what_you" in normalized and "bring" in normalized
    ) or "who_are_you" in normalized:
        return "requirements"
    if "requirement" in normalized:
        return "requirements"
    if "qualification" in normalized:
        return "qualifications"
    if "skill" in normalized:
        return "skills"
    if "experience" in normalized:
        return "experience"
    if "about" in normalized or "company" in normalized:
        return "company"
    return None


def _split_inline_section(value: str) -> tuple[str | None, str]:
    match = re.match(
        (
            r"^(responsibilities?|what\s+you(?:'|’)ll\s+do|what\s+you\s+will\s+do|"
            r"requirements?|qualifications?|skills?|experience)\s*(?::|-)?\s*"
        ),
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None, value
    return _classify_section(match.group(1)), value[match.end() :]


class _SectionedDescriptionParser(HTMLParser):
    _BLOCK_TAGS = frozenset({"p", "div", "li", "br"})
    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_DescriptionBlock] = []
        self._section: str | None = None
        self._buffer: list[str] = []
        self._heading_buffer: list[str] | None = None
        self._active_block_tag: str | None = None
        self._block_prior_section: str | None = None
        self._emphasis_depth = 0
        self._block_has_text = False
        self._block_has_non_emphasis_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.casefold()
        if lowered in self._HEADING_TAGS:
            self._flush()
            self._heading_buffer = []
        elif lowered in self._BLOCK_TAGS:
            self._flush()
            if lowered != "br":
                self._active_block_tag = lowered
                self._block_prior_section = self._section
                self._block_has_text = False
                self._block_has_non_emphasis_text = False
        elif lowered in {"b", "strong", "em"}:
            self._emphasis_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self._HEADING_TAGS:
            heading = _canonical_text(" ".join(self._heading_buffer or ()))
            self._section = _classify_section(heading)
            self._heading_buffer = None
        elif lowered in {"p", "div", "li"}:
            text = _canonical_text(" ".join(self._buffer))
            prior_section = self._block_prior_section
            inline_section, _ = _split_inline_section(text)
            structural_heading = (
                self._is_emphasized_block_heading() and inline_section is None
            )
            self._flush()
            if structural_heading:
                heading_section = _classify_section(text)
                if heading_section is not None:
                    self._section = (
                        heading_section if prior_section in ALLOWED_SECTIONS else None
                    )
            self._active_block_tag = None
            self._block_prior_section = None
            self._block_has_text = False
            self._block_has_non_emphasis_text = False
        elif lowered in {"b", "strong", "em"} and self._emphasis_depth:
            self._emphasis_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._heading_buffer is not None:
            self._heading_buffer.append(data)
        else:
            self._buffer.append(data)
            if _canonical_text(data):
                self._block_has_text = True
                if self._emphasis_depth == 0:
                    self._block_has_non_emphasis_text = True

    def finish(self) -> tuple[_DescriptionBlock, ...]:
        self._flush()
        return tuple(self.blocks)

    def _is_emphasized_block_heading(self) -> bool:
        return (
            self._active_block_tag is not None
            and self._block_has_text
            and not self._block_has_non_emphasis_text
        )

    def _flush(self) -> None:
        text = _canonical_text(" ".join(self._buffer))
        self._buffer = []
        if not text:
            return
        inline_section, body = _split_inline_section(text)
        if inline_section is not None:
            self._section = inline_section
            text = _canonical_text(body)
        if text:
            self.blocks.append(_DescriptionBlock(self._section, text))


def _decode_description(value: object) -> str:
    decoded = str(value or "")
    for _ in range(3):
        next_value = unescape(decoded)
        if next_value == decoded:
            return next_value
        decoded = next_value
    return decoded


def _exact_sentence_or_bullet_fragments(value: str) -> tuple[str, ...]:
    pieces: list[str] = []
    for bullet in re.split(r"(?:\s*[•▪●]\s*|\s+-\s+)", value):
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", bullet):
            text = _canonical_text(sentence)
            if not text:
                continue
            while text:
                if len(text) <= 500:
                    pieces.append(text)
                    break
                boundary = text.rfind(" ", 0, 501)
                if boundary <= 0:
                    raise ProjectionBlockedV3(
                        "description sentence has no bounded immutable continuation"
                    )
                pieces.append(text[:boundary])
                text = text[boundary + 1 :]
    return tuple(pieces)


def _description_blocks(raw_description: object) -> tuple[_DescriptionBlock, ...]:
    decoded = _decode_description(raw_description)
    parser = _SectionedDescriptionParser()
    parser.feed(decoded)
    parser.close()
    blocks = parser.finish()
    if blocks:
        return blocks
    text = _canonical_text(decoded)
    return () if not text else (_DescriptionBlock(None, text),)


def _selection_key(record: Mapping[str, Any]) -> str:
    value = record.get("selection_key")
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProjectionBlockedV3("record selection_key must be an exact SHA-256")
    return value


def _authority_references(vacancy_ref: ImmutableArtifactRef) -> AssessmentReferences:
    try:
        profile = yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8"))
        authorities = profile["authorities"]
    except (OSError, TypeError, KeyError) as exc:
        raise ProjectionBlockedV3(
            "candidate and profile authority is unavailable"
        ) from exc
    return AssessmentReferences(
        profile_ref=ImmutableArtifactRef(
            artifact_id="career-profile-v2",
            version="2.0.0",
            sha256=hashlib.sha256(_PROFILE_PATH.read_bytes()).hexdigest(),
        ),
        candidate_facts_ref=ImmutableArtifactRef.model_validate(
            authorities["candidate_facts_ref"]
        ),
        semantic_contract_ref=ImmutableArtifactRef(
            artifact_id="semantic-fact-contract",
            version="1.0.0",
            sha256=hashlib.sha256(_SEMANTIC_CONTRACT_PATH.read_bytes()).hexdigest(),
        ),
        search_contract_ref=ImmutableArtifactRef.model_validate(
            authorities["search_contract_ref"]
        ),
        policy_ref=ImmutableArtifactRef.model_validate(authorities["product_sot_ref"]),
        evidence_snapshot_ref=vacancy_ref,
    )


def build_vacancy_projection_candidates_v3(
    record: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> VacancyProjectionCandidatesV3:
    """Build immutable candidate fragments without admitting provider evidence."""
    selection_key = _selection_key(record)
    artifact_fragments: list[VacancyEvidenceArtifactFragmentV1] = []
    description_candidates: list[DescriptionCandidateV3] = []
    for field_name in ("title", "location", "salary", "posted_at"):
        text = _canonical_text(raw.get(field_name))
        if text:
            artifact_fragments.append(
                VacancyEvidenceArtifactFragmentV1(
                    source_locator=f"/{field_name}#000",
                    text=text,
                )
            )
    description_index = 0
    for block in _description_blocks(raw.get("description")):
        for text in _exact_sentence_or_bullet_fragments(block.text):
            locator = f"/description#{description_index:03d}"
            description_index += 1
            artifact_fragments.append(
                VacancyEvidenceArtifactFragmentV1(source_locator=locator, text=text)
            )
            if block.section in ALLOWED_SECTIONS:
                description_candidates.append(
                    DescriptionCandidateV3(
                        source_locator=locator,
                        section=block.section,
                        text=text,
                        text_sha256=_sha256_text(text),
                    )
                )
    if not artifact_fragments:
        raise ProjectionBlockedV3("vacancy has no immutable admissible fragment")
    artifact = VacancyEvidenceArtifactV1(
        schema_version="1.0.0",
        artifact_id=f"gate-b-v3-vacancy:{selection_key}",
        artifact_version="3.0.0",
        redaction_state="shareable_redacted",
        fragments=tuple(artifact_fragments),
    )
    artifact_sha256 = _canonical_json_sha256(artifact.model_dump(mode="json"))
    return VacancyProjectionCandidatesV3(
        selection_key=selection_key,
        vacancy_artifact_sha256=artifact_sha256,
        vacancy_evidence=artifact,
        description_candidates=tuple(description_candidates),
    )


@lru_cache(maxsize=1)
def _compiled_company_fact_deny_patterns(
    patterns: tuple[str, ...],
) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def _entry_key(
    *,
    selection_key: str,
    vacancy_artifact_sha256: str,
    source_locator: str,
    text_sha256: str,
) -> tuple[str, str, str, str]:
    return (selection_key, vacancy_artifact_sha256, source_locator, text_sha256)


def _review_entries(
    candidates: VacancyProjectionCandidatesV3,
    allowlist: ReviewedFragmentAllowlistV3,
) -> dict[tuple[str, str, str, str], ReviewedFragmentEntryV3]:
    if (
        allowlist.gate_a_run_id != _GATE_A_RUN_ID
        or allowlist.gate_b_corpus_sha256 != _GATE_B_CORPUS_SHA256
    ):
        raise ProjectionBlockedV3("reviewed allowlist does not bind the pinned corpus")
    entries = {
        _entry_key(
            selection_key=entry.selection_key,
            vacancy_artifact_sha256=entry.vacancy_artifact_sha256,
            source_locator=entry.source_locator,
            text_sha256=entry.text_sha256,
        ): entry
        for entry in allowlist.entries
        if entry.selection_key == candidates.selection_key
    }
    candidate_keys = {
        _entry_key(
            selection_key=candidates.selection_key,
            vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
            source_locator=candidate.source_locator,
            text_sha256=candidate.text_sha256,
        )
        for candidate in candidates.description_candidates
    }
    entry_keys = set(entries)
    if candidate_keys - entry_keys:
        raise ProjectionBlockedV3(
            "reviewed decision is missing for a description candidate"
        )
    if entry_keys - candidate_keys:
        raise ProjectionBlockedV3("reviewed allowlist contains a non-candidate entry")
    return entries


def _allowed_claims(
    text: str,
    *,
    field_name: str,
    dimensions: tuple[EvidenceDimension, ...],
) -> tuple[AllowedEvidenceClaimV1, ...]:
    return tuple(
        AllowedEvidenceClaimV1(
            claim_code=f"vacancy_{field_name}_{dimension.value}_explicit",
            dimension=dimension,
            status=EvidenceClaimStatus.EXPLICIT,
            statement=text,
        )
        for dimension in dimensions
    )


def _build_projection_v3(
    record: Mapping[str, Any],
    raw: Mapping[str, Any],
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
    *,
    policy: GateBBenchmarkPolicyV3,
) -> tuple[EvidenceSynthesisInputV2, ProjectionAuditV3]:
    candidates = build_vacancy_projection_candidates_v3(record, raw)
    review_entries = _review_entries(candidates, reviewed_allowlist)
    compiled_deny_patterns = _compiled_company_fact_deny_patterns(
        policy.company_fact_deny_patterns
    )
    artifact = candidates.vacancy_evidence
    artifact_ref = ImmutableArtifactRef(
        artifact_id=artifact.artifact_id,
        version=artifact.artifact_version,
        sha256=candidates.vacancy_artifact_sha256,
    )
    candidate_by_locator = {
        candidate.source_locator: candidate
        for candidate in candidates.description_candidates
    }
    fragments: list[EvidenceFragmentV1] = []
    dimension_refs: dict[EvidenceDimension, list[str]] = defaultdict(list)
    prohibited_company_hashes: list[str] = []
    direct_fields_admitted = 0
    role_candidates_admitted = 0
    company_fact_candidates_excluded = 0
    ambiguous_fragments_excluded = 0
    sequence = 0

    def add_vacancy_fragment(
        *,
        source_locator: str,
        text: str,
        field_name: str,
        dimensions: tuple[EvidenceDimension, ...],
    ) -> None:
        nonlocal sequence
        fragment_id = f"vacancy-v3:{candidates.selection_key[:16]}:{sequence:03d}"
        sequence += 1
        fragments.append(
            EvidenceFragmentV1(
                fragment_id=fragment_id,
                artifact_ref=artifact_ref,
                source_kind=EvidenceSourceKind.VACANCY,
                source_locator=source_locator,
                permitted_dimensions=dimensions,
                text=text,
                text_sha256=_sha256_text(text),
                allowed_claims=_allowed_claims(
                    text,
                    field_name=field_name,
                    dimensions=dimensions,
                ),
            )
        )
        for dimension in dimensions:
            dimension_refs[dimension].append(fragment_id)

    for item in artifact.fragments:
        if item.source_locator.startswith("/description#"):
            candidate = candidate_by_locator.get(item.source_locator)
            if candidate is None:
                continue
            key = _entry_key(
                selection_key=candidates.selection_key,
                vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
                source_locator=candidate.source_locator,
                text_sha256=candidate.text_sha256,
            )
            entry = review_entries[key]
            denied = any(
                pattern.search(candidate.text) for pattern in compiled_deny_patterns
            )
            if (
                denied
                or entry.decision is ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT
            ):
                if (
                    entry.decision
                    is not ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT
                ):
                    raise ProjectionBlockedV3(
                        "company or marketing candidate has an unsafe review decision"
                    )
                prohibited_company_hashes.append(candidate.text_sha256)
                company_fact_candidates_excluded += 1
                continue
            if entry.decision is ReviewedFragmentDecisionV3.EXCLUDE_AMBIGUOUS:
                ambiguous_fragments_excluded += 1
                continue
            expected_decision = (
                ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY
                if candidate.section in _RESPONSIBILITY_SECTIONS
                else ReviewedFragmentDecisionV3.ALLOW_ROLE_REQUIREMENT
            )
            if entry.decision is not expected_decision:
                raise ProjectionBlockedV3(
                    "review decision does not bind the candidate's role section"
                )
            add_vacancy_fragment(
                source_locator=candidate.source_locator,
                text=candidate.text,
                field_name=f"description_{candidate.section}",
                dimensions=_ROLE_DIMENSIONS[candidate.section],
            )
            role_candidates_admitted += 1
            continue
        field_name = item.source_locator.split("#", 1)[0].removeprefix("/")
        if field_name not in DIRECT_FIELDS:
            raise ProjectionBlockedV3("immutable artifact has an unknown direct field")
        if any(pattern.search(item.text) for pattern in compiled_deny_patterns):
            prohibited_company_hashes.append(_sha256_text(item.text))
            continue
        add_vacancy_fragment(
            source_locator=item.source_locator,
            text=item.text,
            field_name=field_name,
            dimensions=_DIRECT_DIMENSIONS[field_name],
        )
        direct_fields_admitted += 1

    for dimension in EvidenceDimension:
        refs = tuple(dimension_refs.get(dimension, ()))
        force_unknown = dimension in {
            EvidenceDimension.COMPANY_FIT,
            EvidenceDimension.TRANSFERABILITY,
        }
        if refs and not force_unknown:
            continue
        reason = _UNKNOWN_REASONS[dimension]
        fragment_id = f"unknown-v3:{candidates.selection_key[:16]}:{dimension.value}"
        fragments.append(
            EvidenceFragmentV1(
                fragment_id=fragment_id,
                artifact_ref=artifact_ref,
                source_kind=EvidenceSourceKind.ASSESSMENT_UNKNOWN,
                source_locator=reason,
                permitted_dimensions=(dimension,),
                text=reason,
                text_sha256=_sha256_text(reason),
                allowed_claims=(
                    AllowedEvidenceClaimV1(
                        claim_code=f"{dimension.value}_unknown",
                        dimension=dimension,
                        status=EvidenceClaimStatus.UNKNOWN,
                        statement=reason,
                    ),
                ),
            )
        )
    dimensions_payload = {
        dimension.value: (
            DimensionEvidenceInput(
                state=DimensionEvidenceState.EVIDENCE_AVAILABLE,
                evidence_refs=tuple(dimension_refs[dimension]),
            )
            if dimension_refs[dimension]
            and dimension
            not in {EvidenceDimension.COMPANY_FIT, EvidenceDimension.TRANSFERABILITY}
            else DimensionEvidenceInput(
                state=DimensionEvidenceState.UNKNOWN,
                unknown_reasons=(_UNKNOWN_REASONS[dimension],),
            )
        )
        for dimension in EvidenceDimension
    }
    assessment = AssessmentInputV2(
        schema_version="2.0.0",
        assessment_id=f"gate-b-v3:{candidates.selection_key}",
        references=_authority_references(artifact_ref),
        dimensions=DecisionDimensionsInput(**dimensions_payload),
        company_authority_status=CompanyAuthorityStatus.UNAVAILABLE,
    )
    result = EvidenceSynthesisInputV3(
        schema_version="2.0.0",
        assessment_input=assessment,
        company_authority=CompanyAuthorityUnavailableV2(
            status="unavailable",
            reason="unresolved_company_identity",
        ),
        vacancy_evidence_ref=artifact_ref,
        vacancy_evidence=artifact,
        prohibited_company_claim_text_sha256s=tuple(
            dict.fromkeys(prohibited_company_hashes)
        ),
        fragments=tuple(fragments),
    )
    audit = ProjectionAuditV3(
        selection_key=candidates.selection_key,
        vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
        description_candidates_total=len(candidates.description_candidates),
        reviewed_candidates_total=len(review_entries),
        direct_fields_admitted=direct_fields_admitted,
        role_candidates_admitted=role_candidates_admitted,
        company_fact_candidates_excluded=company_fact_candidates_excluded,
        ambiguous_fragments_excluded=ambiguous_fragments_excluded,
        company_fact_candidates_admitted=0,
        ambiguous_fragments_admitted=0,
    )
    return result, audit


def project_vacancy_evidence_v3(
    record: Mapping[str, Any],
    raw: Mapping[str, Any],
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
) -> EvidenceSynthesisInputV2:
    result, _ = _build_projection_v3(
        record,
        raw,
        reviewed_allowlist,
        policy=load_gate_b_evidence_policy_v3(),
    )
    return result


def audit_vacancy_projection_v3(
    record: Mapping[str, Any],
    raw: Mapping[str, Any],
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
) -> ProjectionAuditV3:
    _, audit = _build_projection_v3(
        record,
        raw,
        reviewed_allowlist,
        policy=load_gate_b_evidence_policy_v3(),
    )
    return audit


def validate_provider_payload_v3(
    raw_payload: object,
    *,
    synthesis_input: EvidenceSynthesisInputV2,
    reviewed_allowlist: ReviewedFragmentAllowlistV3,
) -> EvidenceSynthesisStatus | None:
    """Bind generic v3 provider validation to this selection's exact review."""
    selection_key = synthesis_input.vacancy_evidence.artifact_id.removeprefix(
        "gate-b-v3-vacancy:"
    )
    if re.fullmatch(r"[0-9a-f]{64}", selection_key) is None:
        return EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    reviewed_description_claims = {
        (entry.source_locator, entry.text_sha256): entry.decision.value
        for entry in reviewed_allowlist.entries
        if entry.selection_key == selection_key
        and entry.vacancy_artifact_sha256 == synthesis_input.vacancy_evidence_ref.sha256
    }
    return validate_provider_payload_v3_contract(
        raw_payload,
        synthesis_input=synthesis_input,
        reviewed_description_claims=reviewed_description_claims,
    )
