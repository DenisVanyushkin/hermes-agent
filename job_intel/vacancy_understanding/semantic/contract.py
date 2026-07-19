"""Semantic Vacancy Understanding SoT — structural validation ONLY (Step 4A).

Validates `semantic-fact-contract.yaml`: the canonical, implementation-
independent policy for how semantic facts may be extracted from vacancy
content. NO runtime extraction lives here — this module mirrors the role of
job_intel/shadow_evaluator/contract.py for the decision SoT.

Guarantees enforced by this schema:
- every semantic fact has exactly one canonical definition, keyed by the
  canonical Step 2 field path;
- every fact declares evidence requirements, prohibited evidence, unknown
  policy and five synthetic controls;
- confidence is evidence-based (provider self-confidence is not a source);
- the contract is provider-independent (no model/vendor names — tested);
- facts cannot be written directly from raw text: the observation layer is
  mandatory for semantic origin.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PKG_DIR = Path(__file__).parent
CONTRACT_PATH = PKG_DIR / "semantic-fact-contract.yaml"
SCHEMA_PATH = PKG_DIR / "semantic-fact-contract.schema.json"

SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractionClass(str, Enum):
    deterministic_only = "deterministic_only"
    semantic_only = "semantic_only"
    hybrid = "hybrid"
    enrichment_only = "enrichment_only"   # semantic extraction PROHIBITED
    rule_produced = "rule_produced"       # produced by conflict rules, never inferred


class FactGroup(str, Enum):
    mandate = "mandate"
    product_shape = "product_shape"
    organization = "organization"
    company = "company"
    requirements = "requirements"
    risk = "risk"


class EvidenceLevel(str, Enum):
    explicit_text = "explicit_text"
    structured_source_field = "structured_source_field"
    deterministic_derivation = "deterministic_derivation"
    semantic_inference = "semantic_inference"
    human_gold = "human_gold"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class ClarificationPriority(str, Enum):
    blocking = "blocking"
    recommendation_changing = "recommendation_changing"
    confidence_improving = "confidence_improving"
    optional = "optional"


class SyntheticControls(_Strict):
    positive: str
    negative: str
    ambiguous: str
    unknown: str
    conflicting: str


class UnknownPolicy(_Strict):
    unknown_required_when: str
    inference_forbidden_when: str
    clarification_priority: ClarificationPriority


class SemanticFact(_Strict):
    id: str                              # canonical Step 2 field path
    group: FactGroup
    meaning: str
    values: str                          # enum/type reference into Step 2 model
    extraction_class: ExtractionClass
    evidence_required: str
    prohibited_evidence: list[str] = Field(default_factory=list)
    deterministic_relation: str
    evaluator_relation: str
    unknown: UnknownPolicy
    controls: Optional[SyntheticControls] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "SemanticFact":
        needs_controls = self.extraction_class in (
            ExtractionClass.semantic_only, ExtractionClass.hybrid)
        if needs_controls and self.controls is None:
            raise ValueError(f"{self.id}: semantic/hybrid facts require synthetic controls")
        if self.extraction_class == ExtractionClass.enrichment_only:
            if "vacancy_text" not in " ".join(self.prohibited_evidence) and \
               "semantic_inference" not in " ".join(self.prohibited_evidence):
                raise ValueError(f"{self.id}: enrichment_only must prohibit semantic inference")
        return self


class ObservationModel(_Strict):
    definition: str
    required_fields: list[str]
    facts_never_written_directly_from_text: bool
    min_observations_per_semantic_fact: int
    excerpt_must_be_verbatim: bool
    max_excerpt_len: int


class ProvenanceModel(_Strict):
    required_fields: list[str]
    reasoning_summary_style: str
    chain_of_thought_forbidden: bool


class EvidenceHierarchy(_Strict):
    levels: list[EvidenceLevel]          # strongest first
    level_definitions: dict[str, str]
    prohibited_sources: list[str]
    only_vacancy_evidence_produces_vacancy_facts: bool


class ConfidenceRule(_Strict):
    level: Confidence
    allowed_when: str


class ConfidencePolicy(_Strict):
    rules: list[ConfidenceRule]
    provider_confidence_is_not_a_source: bool


class ConflictRule(_Strict):
    id: str
    situation: str
    resolution: str


class ProviderContract(_Strict):
    provider_independent: bool
    canonical_output: str
    identical_output_requirement: str
    prompt_is_implementation_detail: bool
    extractor_version_composition: list[str]


class CalibrationContract(_Strict):
    golden_source: str
    metrics: list[str]
    disagreement_classes: list[str]
    no_aggregate_score: bool


class HumanReviewContract(_Strict):
    gold_annotation: str
    review_workflow: str
    correction_policy: str
    disagreement_resolution: str
    policy_changes_only_via_sot_amendment: bool


class ReplayIntegration(_Strict):
    fact_origin_classes: list[str]       # deterministic | semantic | missing
    missing_semantic_evidence_class: str
    statement: str


class Metadata(_Strict):
    contract_version: str = Field(pattern=SEMVER_PATTERN)
    status: str
    owner: str
    production_integration: bool
    no_silent_learning: bool
    human_sot: str
    step2_schema_compat: str
    decision_contract_compat: str


class SemanticFactContract(_Strict):
    metadata: Metadata
    evidence_hierarchy: EvidenceHierarchy
    observation_model: ObservationModel
    provenance_model: ProvenanceModel
    confidence_policy: ConfidencePolicy
    conflict_policy: list[ConflictRule]
    facts: list[SemanticFact]
    provider_contract: ProviderContract
    calibration_contract: CalibrationContract
    human_review: HumanReviewContract
    replay_integration: ReplayIntegration
    change_policy: dict[str, str]

    @model_validator(mode="after")
    def _validate(self) -> "SemanticFactContract":
        if self.metadata.production_integration:
            raise ValueError("production_integration must remain false")
        ids = [f.id for f in self.facts]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate fact ids: {sorted(dupes)} — exactly one canonical definition each")
        cids = [c.id for c in self.conflict_policy]
        if len(cids) != len(set(cids)):
            raise ValueError("duplicate conflict rule ids")

        if not self.observation_model.facts_never_written_directly_from_text:
            raise ValueError("facts must never be written directly from raw text")
        if self.observation_model.min_observations_per_semantic_fact < 1:
            raise ValueError("semantic facts require at least one observation")
        if not self.provenance_model.chain_of_thought_forbidden:
            raise ValueError("chain-of-thought must be forbidden in provenance")
        for req in ("origin", "provider", "prompt_version", "evidence_refs",
                    "reasoning_summary", "confidence"):
            if req not in self.provenance_model.required_fields:
                raise ValueError(f"provenance missing required field {req}")
        if not self.confidence_policy.provider_confidence_is_not_a_source:
            raise ValueError("provider confidence must not drive fact confidence")
        levels = {r.level for r in self.confidence_policy.rules}
        if levels != set(Confidence):
            raise ValueError("confidence rules must cover high/medium/low/unknown")
        if self.evidence_hierarchy.levels[0] != EvidenceLevel.explicit_text:
            raise ValueError("explicit text must be the strongest evidence level")
        for lvl in self.evidence_hierarchy.levels:
            if lvl.value not in self.evidence_hierarchy.level_definitions:
                raise ValueError(f"evidence level {lvl.value} lacks a definition")
        banned = {"company reputation", "recruiter intuition",
                  "historical assumptions", "previous evaluations"}
        joined = " | ".join(self.evidence_hierarchy.prohibited_sources).lower()
        for b in banned:
            if b not in joined:
                raise ValueError(f"prohibited sources must include: {b}")
        if not self.provider_contract.provider_independent:
            raise ValueError("contract must be provider independent")
        if not self.calibration_contract.no_aggregate_score:
            raise ValueError("no aggregate quality score allowed")
        if not self.human_review.policy_changes_only_via_sot_amendment:
            raise ValueError("manual review must not silently change policy")
        if self.replay_integration.missing_semantic_evidence_class != "insufficient_vacancy_evidence":
            raise ValueError("missing-evidence replay class must stay insufficient_vacancy_evidence")
        return self


def load_semantic_contract(path: Path | str | None = None) -> SemanticFactContract:
    import yaml

    p = Path(path) if path else CONTRACT_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return SemanticFactContract.model_validate(data["semantic_fact_contract"])


def export_json_schema() -> dict:
    schema = SemanticFactContract.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "semantic_fact_contract"
    return schema


def write_json_schema(path: Path | str | None = None) -> Path:
    p = Path(path) if path else SCHEMA_PATH
    p.write_text(
        json.dumps(export_json_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    return p


if __name__ == "__main__":
    print(write_json_schema())
