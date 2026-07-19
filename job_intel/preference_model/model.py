"""Career Preference Model — strict, versioned Source of Truth contract.

Step 1 of the career-preference-system plan
(docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md).

This module defines the *contract* for career-preference-model.yaml and a
minimal deterministic rule matcher used by contract-level tests. It is NOT an
evaluator: no scoring, no weights, no production integration. Production code
must not import this package until a later, explicitly approved step.

Design decisions:
- Pydantic v2 with ``extra="forbid"`` everywhere strictness is intended.
- ``career-preference-model.schema.json`` is generated from these models
  (see ``export_json_schema``); a test asserts the artifact is up to date.
- Rule conditions are structured (work format, country group, sponsorship,
  semantic flags), not prose, so policy scenarios are testable.
"""
from __future__ import annotations

import json
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_DIR = Path(__file__).parent
DEFAULT_MODEL_PATH = MODEL_DIR / "career-preference-model.yaml"
SCHEMA_PATH = MODEL_DIR / "career-preference-model.schema.json"


# --------------------------------------------------------------------------
# Enums (normalized; draft values medium_high/low_medium are intentionally
# rounded down to the nearest canonical value — original wording is preserved
# in provenance notes, see the draft-vs-normalized report).
# --------------------------------------------------------------------------

class Strength(str, Enum):
    critical = "critical"
    strong = "strong"
    medium = "medium"
    weak = "weak"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class SourceType(str, Enum):
    behavioral = "behavioral"
    explicit = "explicit"
    inferred = "inferred"


class RuleStatus(str, Enum):
    active = "active"
    hypothesis = "hypothesis"
    exploration = "exploration"
    inactive = "inactive"


class Polarity(str, Enum):
    positive = "positive"
    negative = "negative"


class FeasibilityVerdict(str, Enum):
    feasible = "feasible"
    uncertain = "uncertain"
    infeasible = "infeasible"


class Lane(str, Enum):
    core = "core"
    fallback_local = "fallback_local"


class WorkFormat(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"


class CountryGroup(str, Enum):
    usa = "usa"
    kazakhstan = "kazakhstan"
    sanctioned = "sanctioned"
    unstable = "unstable"
    africa = "africa"
    other = "other"


class SponsorshipStated(str, Enum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class AntiPreferenceLevel(str, Enum):
    company = "company"
    role = "role"


class AntiPreferenceTier(str, Enum):
    strong = "strong"  # overcome only by an explicit interaction rule
    soft = "soft"      # undesirable but acceptable


class EffectType(str, Enum):
    suppress = "suppress"                        # target rule does not apply
    limit_to_company_fit = "limit_to_company_fit"  # target may affect company_fit only
    gate = "gate"                                # documents a feasibility gate
    route_to_fallback = "route_to_fallback"      # send to local fallback lane
    exclude_from = "exclude_from"                # scenario must not inherit target positive
    allow = "allow"                              # explicitly permits despite target


class PreferenceTarget(str, Enum):
    role = "role"
    company = "company"


class PreferenceAxis(str, Enum):
    mandate_scope = "mandate_scope"
    revenue_proximity = "revenue_proximity"
    org_authority = "org_authority"
    transformation_phase = "transformation_phase"
    feasibility_signal = "feasibility_signal"
    company_scale = "company_scale"
    company_brand = "company_brand"
    company_stage = "company_stage"
    business_model = "business_model"
    company_culture = "company_culture"
    company_footprint = "company_footprint"
    # Forbidden as standalone active preferences (validator-enforced), listed
    # so the enum can express them for inactive/historical entries if needed.
    industry = "industry"
    country = "country"
    title = "title"


class EvidenceKind(str, Enum):
    document = "document"
    data = "data"
    verbatim = "verbatim"
    user_decision = "user_decision"


class ModelStatus(str, Enum):
    normalized_not_integrated = "normalized_not_integrated"
    shadow = "shadow"
    production = "production"


class TimezoneTreatment(str, Enum):
    risk_or_clarification = "risk_or_clarification"


class FallbackActivation(str, Enum):
    manual_by_user = "manual_by_user"


class FallbackState(str, Enum):
    standby = "standby"
    armed = "armed"
    active = "active"


SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"

# Preference axes that must never carry standalone active preference weight.
FORBIDDEN_STANDALONE_AXES = frozenset(
    {PreferenceAxis.industry, PreferenceAxis.country, PreferenceAxis.title}
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Provenance / evidence
# --------------------------------------------------------------------------

class EvidenceRef(_StrictModel):
    registry_id: str
    detail: Optional[str] = None


class Provenance(_StrictModel):
    source_type: SourceType
    evidence: list[EvidenceRef] = Field(default_factory=list)
    evidence_count: Optional[int] = None
    last_validated: date
    notes: Optional[str] = None


class Override(_StrictModel):
    allowed: bool
    conditions: Optional[str] = None


class EvidenceRegistryEntry(_StrictModel):
    id: str
    source: str
    kind: EvidenceKind
    description: Optional[str] = None


# --------------------------------------------------------------------------
# Structured conditions (the testable "when")
# --------------------------------------------------------------------------

class ScenarioCondition(_StrictModel):
    """All non-null fields must match for the condition to apply."""

    work_format: Optional[list[WorkFormat]] = None
    country_group: Optional[list[CountryGroup]] = None
    sponsorship_stated: Optional[list[SponsorshipStated]] = None
    local_market: Optional[bool] = None
    flags_all: Optional[list[str]] = None
    flags_none: Optional[list[str]] = None


class Scenario(_StrictModel):
    """A concrete vacancy situation used by contract tests (not production)."""

    work_format: Optional[WorkFormat] = None
    country_group: CountryGroup = CountryGroup.other
    sponsorship_stated: SponsorshipStated = SponsorshipStated.unknown
    local_market: bool = False
    flags: set[str] = Field(default_factory=set)


def condition_matches(cond: ScenarioCondition, s: Scenario) -> bool:
    if cond.work_format is not None:
        if s.work_format is None or s.work_format not in cond.work_format:
            return False
    if cond.country_group is not None and s.country_group not in cond.country_group:
        return False
    if (
        cond.sponsorship_stated is not None
        and s.sponsorship_stated not in cond.sponsorship_stated
    ):
        return False
    if cond.local_market is not None and s.local_market != cond.local_market:
        return False
    if cond.flags_all and not set(cond.flags_all) <= s.flags:
        return False
    if cond.flags_none and set(cond.flags_none) & s.flags:
        return False
    return True


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

class _RuleBase(_StrictModel):
    id: str
    statement: str
    strength: Strength
    confidence: Confidence
    status: RuleStatus
    provenance: Provenance
    override: Optional[Override] = None


class Motivation(_StrictModel):
    id: str
    statement: str
    confidence: Confidence
    status: RuleStatus
    provenance: Provenance


class FeasibilityConstraint(_RuleBase):
    when: ScenarioCondition
    verdict: FeasibilityVerdict
    lane: Lane = Lane.core


class TimezonePolicy(_StrictModel):
    hard_gate: bool
    treatment: TimezoneTreatment
    statement: str
    provenance: Provenance


class CompensationPolicy(_StrictModel):
    status: RuleStatus
    gating_effect: bool
    ranking_effect: bool
    missing_salary_is_negative: bool
    statement: str
    provenance: Provenance


class FeasibilitySection(_StrictModel):
    constraints: list[FeasibilityConstraint]
    timezone_policy: TimezonePolicy
    compensation_policy: CompensationPolicy


class Preference(_RuleBase):
    axis: PreferenceAxis
    polarity: Polarity = Polarity.positive
    applies_to: PreferenceTarget = PreferenceTarget.role
    values_order: Optional[list[str]] = None
    preferred_min: Optional[str] = None
    values: Optional[list[str]] = None
    anti_values: Optional[list[str]] = None


class AntiPreference(_RuleBase):
    level: AntiPreferenceLevel
    tier: AntiPreferenceTier
    when: Optional[ScenarioCondition] = None


class Effect(_StrictModel):
    type: EffectType
    target_ids: list[str]
    result: Optional[str] = None


class InteractionRule(_StrictModel):
    id: str
    statement: str
    priority: int  # lower number = applied earlier
    when: ScenarioCondition
    effect: Effect
    status: RuleStatus
    provenance: Provenance


class ExplorationAxis(_StrictModel):
    id: str
    axis: str
    values: list[str]
    note: Optional[str] = None
    requirement: Optional[str] = None
    status: RuleStatus = RuleStatus.exploration


class ExplorationPolicy(_StrictModel):
    rate: str
    rules: list[str]
    axes: list[ExplorationAxis]
    direct_questions_not_exploration: list[str] = Field(default_factory=list)


class LocalMarketFallbackPolicy(_StrictModel):
    id: str
    status: RuleStatus
    activation: FallbackActivation   # never automatic
    current_state: FallbackState
    statement: str
    separation: str          # how the lane is kept apart from core
    provenance: Provenance


class ChangePolicy(_StrictModel):
    versioning: str
    compatibility: str
    approval: str
    no_silent_learning: bool


class Metadata(_StrictModel):
    schema_version: str = Field(pattern=SEMVER_PATTERN)
    model_version: str = Field(pattern=SEMVER_PATTERN)
    subject: str
    generated_at: date
    status: ModelStatus
    production_integration: bool
    derived_from: list[str]
    data_basis: Optional[dict] = None
    reference_ideal_vacancy: Optional[str] = None


class CareerPreferenceModel(_StrictModel):
    metadata: Metadata
    motivations: list[Motivation]
    feasibility_constraints: FeasibilitySection
    mandate_preferences: list[Preference]
    company_preferences: list[Preference]
    anti_preferences: list[AntiPreference]
    interaction_rules: list[InteractionRule]
    exploration_policy: ExplorationPolicy
    local_market_fallback_policy: LocalMarketFallbackPolicy
    evidence_registry: list[EvidenceRegistryEntry]
    change_policy: ChangePolicy

    # ---------------- invariants ----------------

    def _all_rule_ids(self) -> list[str]:
        ids = [m.id for m in self.motivations]
        ids += [c.id for c in self.feasibility_constraints.constraints]
        ids += [p.id for p in self.mandate_preferences]
        ids += [p.id for p in self.company_preferences]
        ids += [a.id for a in self.anti_preferences]
        ids += [r.id for r in self.interaction_rules]
        ids += [a.id for a in self.exploration_policy.axes]
        ids.append(self.local_market_fallback_policy.id)
        return ids

    @model_validator(mode="after")
    def _validate_invariants(self) -> "CareerPreferenceModel":
        ids = self._all_rule_ids()
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate rule ids: {sorted(dupes)}")
        known = set(ids)

        registry_ids = [e.id for e in self.evidence_registry]
        reg_dupes = {i for i in registry_ids if registry_ids.count(i) > 1}
        if reg_dupes:
            raise ValueError(f"duplicate evidence registry ids: {sorted(reg_dupes)}")
        registry = set(registry_ids)

        def check_provenance(rule_id: str, prov: Provenance, status: RuleStatus) -> None:
            if status == RuleStatus.active and not prov.evidence:
                raise ValueError(f"active rule {rule_id} has no evidence")
            for ref in prov.evidence:
                if ref.registry_id not in registry:
                    raise ValueError(
                        f"rule {rule_id} references unknown evidence "
                        f"registry id {ref.registry_id!r}"
                    )

        for m in self.motivations:
            check_provenance(m.id, m.provenance, m.status)
        for c in self.feasibility_constraints.constraints:
            check_provenance(c.id, c.provenance, c.status)
        for p in self.mandate_preferences + self.company_preferences:
            check_provenance(p.id, p.provenance, p.status)
        for a in self.anti_preferences:
            check_provenance(a.id, a.provenance, a.status)
        for r in self.interaction_rules:
            check_provenance(r.id, r.provenance, r.status)
            for target in r.effect.target_ids:
                if target not in known:
                    raise ValueError(
                        f"interaction rule {r.id} targets unknown rule id {target!r}"
                    )
        check_provenance(
            self.local_market_fallback_policy.id,
            self.local_market_fallback_policy.provenance,
            self.local_market_fallback_policy.status,
        )
        check_provenance(
            "timezone_policy",
            self.feasibility_constraints.timezone_policy.provenance,
            RuleStatus.active,
        )
        check_provenance(
            "compensation_policy",
            self.feasibility_constraints.compensation_policy.provenance,
            RuleStatus.active,
        )

        # Forbidden standalone label axes.
        for p in self.mandate_preferences + self.company_preferences:
            if p.axis in FORBIDDEN_STANDALONE_AXES and p.status == RuleStatus.active:
                raise ValueError(
                    f"preference {p.id} carries standalone active weight on "
                    f"forbidden axis {p.axis!r}"
                )

        # Compensation must be inactive and effect-free.
        comp = self.feasibility_constraints.compensation_policy
        if comp.status != RuleStatus.inactive:
            raise ValueError("compensation_policy must be inactive")
        if comp.gating_effect or comp.ranking_effect or comp.missing_salary_is_negative:
            raise ValueError("compensation_policy must have zero gating/ranking effect")

        # Timezone must not be a hard gate.
        if self.feasibility_constraints.timezone_policy.hard_gate:
            raise ValueError("timezone_policy must not be a hard gate")

        # No feasibility constraint may condition on compensation/timezone flags.
        for c in self.feasibility_constraints.constraints:
            for flag in (c.when.flags_all or []) + (c.when.flags_none or []):
                if "compensation" in flag or "salary" in flag or "timezone" in flag:
                    raise ValueError(
                        f"constraint {c.id} must not gate on compensation/timezone"
                    )

        # USA remote must never be infeasible on geography alone.
        for c in self.feasibility_constraints.constraints:
            if (
                c.status == RuleStatus.active
                and c.verdict == FeasibilityVerdict.infeasible
                and c.when.country_group is not None
                and CountryGroup.usa in c.when.country_group
                and (c.when.work_format is None or WorkFormat.remote in c.when.work_format)
                and not c.when.flags_all
            ):
                raise ValueError(f"constraint {c.id} makes remote USA infeasible")

        # KZ feasibility must never depend on sponsorship: working in KZ needs
        # no visa/relocation path, so no non-feasible constraint may combine
        # kazakhstan with a sponsorship condition.
        for c in self.feasibility_constraints.constraints:
            if (
                c.status == RuleStatus.active
                and c.verdict != FeasibilityVerdict.feasible
                and c.when.country_group is not None
                and CountryGroup.kazakhstan in c.when.country_group
                and c.when.sponsorship_stated is not None
            ):
                raise ValueError(
                    f"constraint {c.id} makes Kazakhstan feasibility depend "
                    "on sponsorship"
                )

        if self.metadata.production_integration:
            raise ValueError("production_integration must be false in Step 1")
        return self


# --------------------------------------------------------------------------
# Loading and schema export
# --------------------------------------------------------------------------

def load_model(path: Path | str | None = None) -> CareerPreferenceModel:
    import yaml

    p = Path(path) if path else DEFAULT_MODEL_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return CareerPreferenceModel.model_validate(data["career_preference_model"])


def export_json_schema() -> dict:
    schema = CareerPreferenceModel.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "career_preference_model"
    return schema


def write_json_schema(path: Path | str | None = None) -> Path:
    p = Path(path) if path else SCHEMA_PATH
    p.write_text(
        json.dumps(export_json_schema(), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return p


# --------------------------------------------------------------------------
# Minimal deterministic matcher (contract tests only — NOT an evaluator)
# --------------------------------------------------------------------------

class FeasibilityResult(_StrictModel):
    verdict: FeasibilityVerdict
    lane: Lane
    matched_constraint_ids: list[str]
    risks: list[str]


_VERDICT_ORDER = {
    FeasibilityVerdict.feasible: 0,
    FeasibilityVerdict.uncertain: 1,
    FeasibilityVerdict.infeasible: 2,
}


def evaluate_feasibility(model: CareerPreferenceModel, s: Scenario) -> FeasibilityResult:
    """Deterministic constraint matching for contract tests."""
    verdict = FeasibilityVerdict.feasible
    lane = Lane.core
    matched: list[str] = []
    for c in model.feasibility_constraints.constraints:
        if c.status != RuleStatus.active:
            continue
        if condition_matches(c.when, s):
            matched.append(c.id)
            if _VERDICT_ORDER[c.verdict] > _VERDICT_ORDER[verdict]:
                verdict = c.verdict
            if c.lane == Lane.fallback_local:
                lane = Lane.fallback_local
    risks: list[str] = []
    tz = model.feasibility_constraints.timezone_policy
    if "timezone_gap_large" in s.flags and not tz.hard_gate:
        risks.append("timezone_gap")
    return FeasibilityResult(
        verdict=verdict, lane=lane, matched_constraint_ids=matched, risks=risks
    )


def _active_interaction_rules(
    model: CareerPreferenceModel, s: Scenario
) -> list[InteractionRule]:
    rules = [
        r
        for r in model.interaction_rules
        if r.status == RuleStatus.active and condition_matches(r.when, s)
    ]
    return sorted(rules, key=lambda r: r.priority)


def applicable_anti_preferences(
    model: CareerPreferenceModel, s: Scenario
) -> dict[str, AntiPreference]:
    """Anti-preferences triggered by the scenario after interaction rules."""
    triggered = {
        a.id: a
        for a in model.anti_preferences
        if a.status == RuleStatus.active
        and a.when is not None
        and condition_matches(a.when, s)
    }
    for rule in _active_interaction_rules(model, s):
        if rule.effect.type == EffectType.suppress:
            for target in rule.effect.target_ids:
                triggered.pop(target, None)
    return triggered


def role_level_vetoes(model: CareerPreferenceModel, s: Scenario) -> list[str]:
    """Anti-preferences that would veto the ROLE itself (not company concern)."""
    limited_to_company: set[str] = set()
    for rule in _active_interaction_rules(model, s):
        if rule.effect.type == EffectType.limit_to_company_fit:
            limited_to_company.update(rule.effect.target_ids)
    vetoes = []
    for aid, anti in applicable_anti_preferences(model, s).items():
        if anti.tier != AntiPreferenceTier.strong:
            continue
        if anti.level == AntiPreferenceLevel.company or aid in limited_to_company:
            continue
        vetoes.append(aid)
    return vetoes


def applicable_positive_preferences(
    model: CareerPreferenceModel, s: Scenario
) -> set[str]:
    """Positive preferences whose axis-flag is present, minus exclusions."""
    prefs = {
        p.id
        for p in model.mandate_preferences + model.company_preferences
        if p.status == RuleStatus.active and p.id in s.flags
    }
    for rule in _active_interaction_rules(model, s):
        if rule.effect.type == EffectType.exclude_from:
            prefs -= set(rule.effect.target_ids)
    return prefs


if __name__ == "__main__":  # regenerate the schema artifact
    print(write_json_schema())
