"""Shadow Evaluator — canonical output model (Step 3, shadow-only).

Strict Pydantic model for evaluator results. Semantic determinism:
``semantic_dump()`` excludes operational run metadata (evaluated_at,
evaluator run diagnostics timings) so identical inputs produce identical
semantic documents; ``evaluated_at`` is run metadata only.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from job_intel.shadow_evaluator.contract import (
    Confidence,
    FeasibilityVerdict,
    FitBand,
    Lane,
    Recommendation,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(str, Enum):
    apply = "apply"
    investigate = "investigate"
    save = "save"
    reject = "reject"


class ItemKind(str, Enum):
    support = "support"
    concern = "concern"
    blocker = "blocker"
    unknown = "unknown"
    interaction = "interaction"


class Section(str, Enum):
    feasibility = "feasibility"
    mandate = "mandate"
    company = "company"
    overall = "overall"


class ResultItem(_Strict):
    """Evidence-backed decision item. Active items require evidence refs
    unless they are purely structural interaction-trace entries that point to
    their input items."""

    id: str
    section: Section
    kind: ItemKind
    preference_rule_id: Optional[str] = None
    vacancy_fact_path: Optional[str] = None
    statement: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.unknown
    impact: Optional[str] = None
    active: bool = True
    suppressed_by: Optional[str] = None
    moved_to: Optional[str] = None


class InteractionTraceEntry(_Strict):
    rule_id: str
    effect: str
    target_ids: list[str] = Field(default_factory=list)
    produced: Optional[str] = None  # suppressed | moved_to:company_fit | prevented | lane | noop
    input_item_ids: list[str] = Field(default_factory=list)


class UnknownLedgerEntry(_Strict):
    policy_id: str
    field: str
    section: Section
    cap: str
    clarification_priority: str


class Clarification(_Strict):
    question: str
    reason: str
    affected_section: Section
    affected_recommendation: bool
    required_fact: str
    priority: str


class FeasibilityResult(_Strict):
    verdict: FeasibilityVerdict
    lane: Lane
    matched_constraints: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)      # ResultItem ids
    unknowns: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.unknown
    fallback_state: Optional[str] = None                    # standby when lane=fallback_local


class FitResult(_Strict):
    band: FitBand
    supports: list[str] = Field(default_factory=list)       # ResultItem ids
    concerns: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.unknown
    decisioning: bool = True   # False after terminal infeasible (diagnostic only)


class OverallResult(_Strict):
    recommendation: Recommendation
    action: Action
    confidence: Confidence
    lane: Lane
    applied_caps: list[str] = Field(default_factory=list)
    exploration_axis: Optional[str] = None


class Explanation(_Strict):
    verdict_summary: str
    why_attractive: list[str] = Field(default_factory=list)
    why_may_not_work: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    interactions_applied: list[str] = Field(default_factory=list)
    lane: Lane = Lane.core


class EvalMetadata(_Strict):
    decision_contract_version: str
    preference_model_version: str
    vacancy_understanding_schema_version: str
    evaluator_version: str
    evaluated_at: datetime            # run metadata — excluded from semantics
    vacancy_key: str
    input_content_hash: str
    shadow_only: bool = True
    production_integration: bool = False


class Diagnostics(_Strict):
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None       # set for error records (no verdict)


class ShadowEvaluation(_Strict):
    metadata: EvalMetadata
    feasibility: FeasibilityResult
    mandate_fit: FitResult
    company_fit: FitResult
    overall: OverallResult
    items: list[ResultItem] = Field(default_factory=list)
    explanations: Explanation
    clarifications: list[Clarification] = Field(default_factory=list)
    interaction_trace: list[InteractionTraceEntry] = Field(default_factory=list)
    unknown_ledger: list[UnknownLedgerEntry] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)

    def semantic_dump(self) -> dict:
        """Deterministic semantic document: run metadata excluded."""
        doc = json.loads(self.model_dump_json())
        doc["metadata"].pop("evaluated_at", None)
        return doc

    def semantic_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.semantic_dump(), sort_keys=True).encode()
        ).hexdigest()[:16]

    def item(self, item_id: str) -> ResultItem:
        return next(i for i in self.items if i.id == item_id)


class EvaluationError(_Strict):
    """Error record — no verdict emitted (e.g. unsupported input major)."""

    vacancy_key: Optional[str]
    error: str
    decision_contract_version: str
