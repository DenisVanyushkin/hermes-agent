"""Semantic extractor runtime — data models (Step 4B).

Faithful execution layer for the approved Step 4A contract. No business
policy lives here; the runtime never sees candidate preferences, never
produces recommendations and never enriches companies.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

RUNTIME_VERSION = "0.1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationBasis(str, Enum):
    """Evidence quality of the observation — drives confidence (Step 4A §6).
    NEVER provider self-confidence."""

    explicit = "explicit"        # near-paraphrase assertion in the body
    direct = "direct"            # direct responsibility/mandate language
    weak = "weak"                # title-only / boilerplate-adjacent


class Observation(_Strict):
    """Step 4A observation: the ONLY thing a provider may emit.

    signal_type convention (contract-driven, no hidden mappings):
    ``<fact_leaf>=<value>`` where the fact leaf resolves to a contract fact id
    and the value belongs to the Step 2 enum of that fact.
    """

    observation_id: str
    excerpt: str = Field(max_length=400)
    location: str                      # "title" | "description"
    signal_type: str
    interpretation: str
    maps_to: list[str]                 # canonical contract fact ids
    basis: ObservationBasis


class RejectedObservation(_Strict):
    observation: dict
    reason: str                        # classified failure


class FactProvenance(_Strict):
    origin: str                        # semantic_inference
    provider: str
    prompt_version: str
    observation_ids: list[str]
    reasoning_summary: str
    confidence: str


class ConflictRecord(_Strict):
    rule_id: str                       # cf_* from the contract
    fact_id: str
    detail: str
    observation_ids: list[str]


class ClarificationOut(_Strict):
    fact_id: str
    priority: str                      # from the contract unknown policy
    question: str


class ExtractionDiagnosticsOut(_Strict):
    provider: str
    prompt_version: str
    semantic_contract_version: str
    runtime_version: str
    observations_total: int
    observations_rejected: int
    facts_emitted: int
    facts_unknown: int
    warnings: list[str] = Field(default_factory=list)


class SemanticExtraction(_Strict):
    """Stage 10 output: canonical Step 2 semantic fragment + full audit."""

    vacancy_key: str
    fragment: dict                     # validated Step 2 VacancyUnderstanding doc
    observations: list[Observation]
    rejected_observations: list[RejectedObservation]
    provenance: dict[str, FactProvenance]   # fact_id -> provenance
    conflicts: list[ConflictRecord]
    clarifications: list[ClarificationOut]
    diagnostics: ExtractionDiagnosticsOut

    def semantic_dump(self) -> dict:
        """Deterministic, byte-stable semantic document (no run metadata)."""
        import json
        return json.loads(self.model_dump_json())


class SemanticProvider(Protocol):
    """Provider abstraction (Step 4A §9). Providers emit ONLY observations —
    never canonical facts. Prompts/rules live inside the implementation."""

    provider_id: str
    prompt_version: str

    def extract_semantic_observations(
        self, *, title: str, text: str, structured: dict
    ) -> list[Observation]: ...
