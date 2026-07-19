"""Shadow Evaluator Decision Contract — structural validation ONLY.

Step 3A. This module validates `decision-contract.yaml` (the machine-readable
policy companion of docs/job-intel-improvements/jul19/shadow-evaluator-
decision-sot.md). It deliberately contains NO runtime evaluation: no verdicts
are computed here, and no production component may import this package until
an explicitly approved rollout step (test-enforced).

The YAML is policy, not executable code; this schema keeps it strict:
unknown fields rejected, closed vocabularies, complete recommendation matrix,
no numeric preference weights.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PKG_DIR = Path(__file__).parent
CONTRACT_PATH = PKG_DIR / "decision-contract.yaml"
SCHEMA_PATH = PKG_DIR / "decision-contract.schema.json"

SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
MAJOR_RANGE_PATTERN = r"^\d+\.x$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeasibilityVerdict(str, Enum):
    feasible = "feasible"
    uncertain = "uncertain"
    infeasible = "infeasible"


class Lane(str, Enum):
    core = "core"
    fallback_local = "fallback_local"


class FitBand(str, Enum):
    exceptional = "exceptional"
    strong = "strong"
    moderate = "moderate"
    weak = "weak"
    mismatch = "mismatch"
    unknown = "unknown"


class Recommendation(str, Enum):
    exceptional = "exceptional"
    strong = "strong"
    promising = "promising"
    unclear = "unclear"
    not_recommended = "not_recommended"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class ResultKind(str, Enum):
    support = "support"
    concern = "concern"
    blocker = "blocker"
    unknown = "unknown"
    interaction = "interaction"


class ClarificationPriority(str, Enum):
    blocking = "blocking"
    recommendation_changing = "recommendation_changing"
    confidence_improving = "confidence_improving"
    optional = "optional"


class InteractionEffect(str, Enum):
    suppress = "suppress"
    limit_to_company_fit = "limit_to_company_fit"
    gate = "gate"
    route_to_fallback = "route_to_fallback"
    exclude_from = "exclude_from"
    allow = "allow"


class UnknownCap(str, Enum):
    none = "none"
    no_exceptional = "no_exceptional"
    no_strong_or_exceptional = "no_strong_or_exceptional"
    promising = "promising"
    terminal = "terminal"


class Metadata(_Strict):
    contract_version: str = Field(pattern=SEMVER_PATTERN)
    status: str
    owner: str
    production_integration: bool
    no_silent_learning: bool
    human_sot: str


class ActionMappingEntry(_Strict):
    recommendation: Recommendation
    action: str  # apply | investigate | save | reject
    low_confidence_or_uncertain_action: Optional[str] = None
    requires_clarification: Optional[bool] = None


class ActionVocabulary(_Strict):
    mapping: list[ActionMappingEntry]
    process_sot_amendment: str


class SupportedInputs(_Strict):
    preference_model: str = Field(pattern=MAJOR_RANGE_PATTERN)
    vacancy_understanding: str = Field(pattern=MAJOR_RANGE_PATTERN)
    on_unsupported_major: str


class EvaluationNode(_Strict):
    id: str
    title: str
    terminal: bool
    on_failure: str


class Precedence(_Strict):
    verdict_merge: list[FeasibilityVerdict]  # strongest first
    lane_independent_of_verdict: bool
    rule_order: list[str]
    interaction_ordering: str
    later_rules_may_reverse: bool
    evidence_hierarchy: list[str]
    evidence_conflict_same_level: str
    manual_gold_is_runtime_evidence: bool


class UnknownPolicyEntry(_Strict):
    id: str
    field: str
    condition: Optional[str] = None
    verdict_effect: str
    confidence_effect: str
    clarification_priority: ClarificationPriority
    cap: UnknownCap


class FitBandDef(_Strict):
    band: FitBand
    criteria: str
    exemplars: list[str] = Field(default_factory=list)


class ResultTypeDef(_Strict):
    kind: ResultKind
    suppressible: bool
    overridable: bool
    aggregation: str


class MatrixCell(_Strict):
    mandate: FitBand
    company: FitBand
    recommendation: Recommendation


class RecommendationMatrix(_Strict):
    terminal_rules: list[str]
    feasible_matrix: list[MatrixCell]
    uncertain_transform: str
    fallback_lane_uses_same_matrix: bool


class Cap(_Strict):
    id: str
    condition: str
    ceiling: Recommendation
    rationale: str
    status: Optional[str] = None        # e.g. provisional_shadow_policy (O4)
    review_after: Optional[str] = None


class InteractionSemantics(_Strict):
    effect: InteractionEffect
    semantics: str
    trace_visibility: str
    idempotent: bool


class ConfidencePolicy(_Strict):
    section_rule: str
    critical_facts: dict[str, list[str]]
    title_only_cap: Confidence
    conflict_result: Confidence
    exceptional_coverage: str
    overall_rule: str
    confidence_is_not_a_score: bool


class ClarificationPolicy(_Strict):
    required_fields: list[str]
    priorities: list[ClarificationPriority]
    fact_seeking_only: bool
    dedup_key: str


class ExplanationPolicy(_Strict):
    item_fields: list[str]
    top_level_fields: list[str]
    numeric_scores_forbidden: bool
    evidence_required: bool


class FallbackPolicy(_Strict):
    lane: Lane
    same_vocabulary_with_marker: bool
    rationale: str
    activation: str
    current_state: str
    excluded_from_core_metrics: bool
    sponsorship_unknown_is_not_uncertain: bool
    feedback_never_recalibrates_core: bool
    production_delivery: str


class ExplorationPolicy(_Strict):
    eligible_axes: list[str]
    ineligible_axes: list[str]
    ineligible_axes_policy: Optional[str] = None  # O5: neutral, no anti/support/cap
    one_axis_at_a_time: bool
    hard_gates_always_apply: bool
    max_rate: str
    min_mandate_band: FitBand
    marker_field: str
    excluded_from_precision_metrics: bool


class ReplayProtocol(_Strict):
    cohort_exclusions: list[str]
    per_case_outputs: list[str]
    disagreement_taxonomy: list[str]
    metrics: list[str]
    feedback_is_ground_truth: bool
    prerequisites: list[str]


class ChangePolicy(_Strict):
    versioning: str
    approval: str
    no_silent_learning: bool


class DecisionContract(_Strict):
    metadata: Metadata
    action_vocabulary: ActionVocabulary
    supported_input_versions: SupportedInputs
    evaluation_order: list[EvaluationNode]
    precedence: Precedence
    unknown_policy: list[UnknownPolicyEntry]
    result_types: list[ResultTypeDef]
    mandate_fit_bands: list[FitBandDef]
    company_fit_bands: list[FitBandDef]
    recommendation_matrix: RecommendationMatrix
    caps: list[Cap]
    interaction_effects: list[InteractionSemantics]
    confidence_policy: ConfidencePolicy
    clarification_policy: ClarificationPolicy
    explanation_contract: ExplanationPolicy
    fallback_policy: FallbackPolicy
    exploration_policy: ExplorationPolicy
    replay_protocol: ReplayProtocol
    change_policy: ChangePolicy

    @model_validator(mode="after")
    def _validate(self) -> "DecisionContract":
        if self.metadata.production_integration:
            raise ValueError("production_integration must remain false")
        if not self.metadata.no_silent_learning:
            raise ValueError("no_silent_learning must be true")

        # unique ids
        for name, ids in (
            ("evaluation_order", [n.id for n in self.evaluation_order]),
            ("unknown_policy", [u.id for u in self.unknown_policy]),
            ("caps", [c.id for c in self.caps]),
        ):
            dupes = {i for i in ids if ids.count(i) > 1}
            if dupes:
                raise ValueError(f"duplicate ids in {name}: {sorted(dupes)}")

        # matrix completeness: every mandate×company combination exactly once
        cells = {(c.mandate, c.company) for c in self.recommendation_matrix.feasible_matrix}
        expected = {(m, c) for m in FitBand for c in FitBand}
        if cells != expected:
            missing = expected - cells
            extra = cells - expected
            raise ValueError(f"matrix incomplete: missing={sorted(missing)} extra={sorted(extra)}")
        if len(self.recommendation_matrix.feasible_matrix) != len(cells):
            raise ValueError("duplicate matrix cells")

        # architectural invariants
        by_cell = {(c.mandate, c.company): c.recommendation
                   for c in self.recommendation_matrix.feasible_matrix}
        for company in FitBand:
            if by_cell[(FitBand.mismatch, company)] != Recommendation.not_recommended:
                raise ValueError("mandate mismatch must always be not_recommended")
            if by_cell[(FitBand.weak, company)] != Recommendation.not_recommended:
                raise ValueError("mandate weak must always be not_recommended")
        for mandate in FitBand:
            rec = by_cell[(mandate, FitBand.mismatch)]
            if rec not in (Recommendation.not_recommended,):
                raise ValueError("company mismatch must be not_recommended")
        if "infeasible" not in " ".join(self.recommendation_matrix.terminal_rules):
            raise ValueError("terminal rule for infeasible missing")

        # every interaction effect defined exactly once, covering the enum
        effects = [e.effect for e in self.interaction_effects]
        if sorted(e.value for e in effects) != sorted(e.value for e in InteractionEffect):
            raise ValueError("interaction_effects must define every effect exactly once")

        # verdict merge = strongest first
        if self.precedence.verdict_merge != [
            FeasibilityVerdict.infeasible, FeasibilityVerdict.uncertain, FeasibilityVerdict.feasible
        ]:
            raise ValueError("verdict_merge must be infeasible > uncertain > feasible")
        if self.precedence.later_rules_may_reverse:
            raise ValueError("later rules must not reverse earlier effects")
        if self.precedence.manual_gold_is_runtime_evidence:
            raise ValueError("manual gold annotation is test truth, not runtime evidence")
        if not self.precedence.lane_independent_of_verdict:
            raise ValueError("lane must be independent of verdict")
        if not self.fallback_policy.sponsorship_unknown_is_not_uncertain:
            raise ValueError("KZ sponsorship-unknown must not create uncertainty")
        if self.explanation_contract.numeric_scores_forbidden is not True:
            raise ValueError("explanations must forbid numeric scores")
        if self.replay_protocol.feedback_is_ground_truth:
            raise ValueError("feedback is evidence, not ground truth")

        # O1: every recommendation label maps to exactly one action entry
        mapped = [m.recommendation for m in self.action_vocabulary.mapping]
        if sorted(m.value for m in mapped) != sorted(r.value for r in Recommendation):
            raise ValueError("action_vocabulary must map every recommendation exactly once")
        for m in self.action_vocabulary.mapping:
            if m.action not in {"apply", "investigate", "save", "reject"}:
                raise ValueError(f"unknown action {m.action!r}")

        # O6: no counting arithmetic in concern aggregation
        for rt in self.result_types:
            if rt.kind == ResultKind.concern:
                import re as _re
                if _re.search(r">=?\s*\d|\d\s*concerns", rt.aggregation):
                    raise ValueError("concern aggregation must not count concerns (O6)")
        return self


def load_contract(path: Path | str | None = None) -> DecisionContract:
    import yaml

    p = Path(path) if path else CONTRACT_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return DecisionContract.model_validate(data["shadow_evaluator_decision_contract"])


def export_json_schema() -> dict:
    schema = DecisionContract.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "shadow_evaluator_decision_contract"
    return schema


def write_json_schema(path: Path | str | None = None) -> Path:
    p = Path(path) if path else SCHEMA_PATH
    p.write_text(
        json.dumps(export_json_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return p


if __name__ == "__main__":
    print(write_json_schema())
