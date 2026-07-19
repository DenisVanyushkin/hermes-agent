"""Vacancy Understanding Layer — canonical, candidate-independent contract.

Step 2 of the career-preference-system plan
(docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md,
task: step-2-vacancy-understanding-layer-agent-task.md).

This model describes WHAT A VACANCY MEANS — never whether it is good for a
candidate. It carries no preference weights, no bands, no apply/reject
verdicts. It must not import the career preference model at runtime, and no
production component may import this package until an explicitly approved
rollout step (enforced by tests).

Key semantics:
- ``unknown`` is a first-class value; missing data is NEVER collapsed to
  false (all tri-state facts default to ``unknown``).
- Every semantically inferred fact carries evidence, an extraction method and
  a confidence level.
- ``career-preference-model`` axes are deliberately absent here; a fact may
  exist because the preference model needs it, but its definition is
  candidate-independent (see feature-definitions.yaml).
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

PKG_DIR = Path(__file__).parent
SCHEMA_PATH = PKG_DIR / "vacancy-understanding.schema.json"

SCHEMA_VERSION = "1.0.0"
SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Core enums
# ---------------------------------------------------------------------------

class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class TriState(str, Enum):
    true = "true"
    false = "false"
    unknown = "unknown"


class ExtractionMethod(str, Enum):
    explicit_statement = "explicit_statement"
    deterministic_derivation = "deterministic_derivation"
    semantic_inference = "semantic_inference"
    company_enrichment = "company_enrichment"
    manual_gold_annotation = "manual_gold_annotation"
    none = "none"  # value is unknown — nothing was extracted


class EvidenceSourceType(str, Enum):
    vacancy_text = "vacancy_text"
    structured_source_field = "structured_source_field"
    company_enrichment = "company_enrichment"
    deterministic_derivation = "deterministic_derivation"
    semantic_inference = "semantic_inference"
    manual_gold_annotation = "manual_gold_annotation"


class TitleFamily(str, Enum):
    product = "product"
    growth = "growth"
    general_management = "general_management"
    commercial = "commercial"
    strategy = "strategy"
    operations = "operations"
    engineering = "engineering"
    sales = "sales"
    finance = "finance"
    project_delivery = "project_delivery"
    other = "other"
    unknown = "unknown"


class ManagementLevel(str, Enum):
    ic = "ic"
    manager = "manager"
    senior_manager = "senior_manager"
    director = "director"
    head_vp = "head_vp"
    c_level = "c_level"
    unknown = "unknown"


class EmploymentType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    unknown = "unknown"


class ScopeBreadth(str, Enum):
    feature = "feature"
    domain = "domain"
    business_line = "business_line"
    region = "region"
    portfolio = "portfolio"
    enterprise = "enterprise"
    unknown = "unknown"


class RevenueProximity(str, Enum):
    support = "support"
    enabling = "enabling"
    indirect = "indirect"
    direct_revenue = "direct_revenue"
    direct_pnl = "direct_pnl"
    unknown = "unknown"


class TransformationPhase(str, Enum):
    build = "build"
    scale = "scale"
    turnaround = "turnaround"
    expand = "expand"
    optimize = "optimize"
    maintain = "maintain"
    unknown = "unknown"


class CompanyScale(str, Enum):
    local = "local"
    regional = "regional"
    multi_region = "multi_region"
    global_ = "global"
    unknown = "unknown"


class CompanyStage(str, Enum):
    seed = "seed"
    growth = "growth"
    scaleup = "scaleup"
    public = "public"
    mature = "mature"
    unknown = "unknown"


class CustomerModel(str, Enum):
    b2c = "b2c"
    smb_mass = "smb_mass"
    b2b_enterprise = "b2b_enterprise"
    mixed = "mixed"
    unknown = "unknown"


class BrandRecognition(str, Enum):
    unknown = "unknown"
    niche = "niche"
    known = "known"
    tier1_scaleup = "tier1_scaleup"
    big_tech = "big_tech"


class WorkFormat(str, Enum):
    onsite = "onsite"
    hybrid = "hybrid"
    remote = "remote"
    unknown = "unknown"


class CountryGroup(str, Enum):
    usa = "usa"
    kazakhstan = "kazakhstan"
    sanctioned = "sanctioned"
    unstable = "unstable"
    africa = "africa"
    other = "other"
    unknown = "unknown"


class SponsorshipStated(str, Enum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class RelocationSupport(str, Enum):
    explicit = "explicit"
    implied = "implied"
    absent = "absent"
    unknown = "unknown"


class Transferability(str, Enum):
    transferable = "transferable"
    adjacent = "adjacent"
    specialized_but_learnable = "specialized_but_learnable"
    non_transferable_barrier = "non_transferable_barrier"
    unknown = "unknown"


class RiskKind(str, Enum):
    relocation_unclear = "relocation_unclear"
    sponsorship_absent = "sponsorship_absent"
    timezone_burden = "timezone_burden"
    title_scope_mismatch = "title_scope_mismatch"
    ambiguous_pnl = "ambiguous_pnl"
    company_facts_missing = "company_facts_missing"
    possible_repost = "possible_repost"
    source_text_incomplete = "source_text_incomplete"
    internal_contradiction = "internal_contradiction"
    low_extraction_confidence = "low_extraction_confidence"
    other = "other"


# ---------------------------------------------------------------------------
# Evidence and generic facts
# ---------------------------------------------------------------------------

MAX_EXCERPT_LEN = 400  # copyright: bounded excerpts only, never full postings


class Evidence(_StrictModel):
    source_id: str  # references SourceDocument.id in evidence_registry
    source_type: EvidenceSourceType
    excerpt: Optional[str] = Field(default=None, max_length=MAX_EXCERPT_LEN)
    location: Optional[str] = None  # e.g. "title", "description:requirements"
    rationale: Optional[str] = None


V = TypeVar("V")


class Fact(_StrictModel, Generic[V]):
    """A single extracted value with provenance.

    Invariants: a known value obtained by semantic inference or company
    enrichment must carry evidence; an unknown value must not pretend to be
    extracted (method=none, confidence=unknown).
    """

    value: V
    confidence: Confidence = Confidence.unknown
    method: ExtractionMethod = ExtractionMethod.none
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "Fact":
        raw = getattr(self.value, "value", self.value)
        if isinstance(raw, list):
            is_unknown = all(getattr(x, "value", x) == "unknown" for x in raw)
        else:
            is_unknown = raw is None or raw == "unknown"
        if is_unknown:
            if self.method not in (ExtractionMethod.none, ExtractionMethod.manual_gold_annotation):
                raise ValueError("unknown value must use method=none (nothing extracted)")
        else:
            if self.method == ExtractionMethod.none:
                raise ValueError("known value requires an extraction method")
            if (
                self.method in (ExtractionMethod.semantic_inference, ExtractionMethod.company_enrichment)
                and not self.evidence
            ):
                raise ValueError(f"{self.method.value} requires evidence")
        return self


BoolFact = Fact[TriState]
StrFact = Fact[Optional[str]]
IntFact = Fact[Optional[int]]


def unknown_bool() -> BoolFact:
    return BoolFact(value=TriState.unknown)


def _unknown(enum_cls):
    def factory():
        return Fact[enum_cls](value=enum_cls.unknown)
    return factory


def unknown_str() -> StrFact:
    return StrFact(value=None)


def unknown_int() -> IntFact:
    return IntFact(value=None)


class SourceDocument(_StrictModel):
    id: str
    source_type: EvidenceSourceType
    description: Optional[str] = None
    content_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

class Metadata(_StrictModel):
    schema_version: str = Field(pattern=SEMVER_PATTERN)
    extractor_version: str = Field(pattern=SEMVER_PATTERN)
    created_at: datetime
    vacancy_key: str
    source_system: str
    source_record_id: Optional[str] = None
    source_content_hash: Optional[str] = None
    language: Optional[str] = None
    is_synthetic_fixture: bool = False
    production_integration: bool = False


class RoleIdentity(_StrictModel):
    raw_title: str
    normalized_title: str
    # Hybrid roles keep every applicable family — no forced single category.
    title_families: list[TitleFamily] = Field(default_factory=list)
    function_families: list[TitleFamily] = Field(default_factory=list)
    employment_type: Fact[EmploymentType] = Field(default_factory=_unknown(EmploymentType))
    # Title is EVIDENCE about seniority, never final truth about scope.
    management_level_observed: Fact[ManagementLevel] = Field(default_factory=_unknown(ManagementLevel))


class Mandate(_StrictModel):
    scope_breadth: Fact[ScopeBreadth] = Field(default_factory=_unknown(ScopeBreadth))
    revenue_proximity: Fact[RevenueProximity] = Field(default_factory=_unknown(RevenueProximity))
    # Tri-state facts — default unknown, never false-by-absence.
    growth_mandate: BoolFact = Field(default_factory=unknown_bool)
    expansion_mandate: BoolFact = Field(default_factory=unknown_bool)
    monetization_core: BoolFact = Field(default_factory=unknown_bool)
    pricing_core: BoolFact = Field(default_factory=unknown_bool)
    acquiring_core: BoolFact = Field(default_factory=unknown_bool)
    pnl_ownership: BoolFact = Field(default_factory=unknown_bool)
    strategy_ownership: BoolFact = Field(default_factory=unknown_bool)
    org_design_mandate: BoolFact = Field(default_factory=unknown_bool)
    team_build_mandate: BoolFact = Field(default_factory=unknown_bool)
    executive_exposure: BoolFact = Field(default_factory=unknown_bool)
    board_exposure: BoolFact = Field(default_factory=unknown_bool)
    market_entry_ownership: BoolFact = Field(default_factory=unknown_bool)
    turnaround_mandate: BoolFact = Field(default_factory=unknown_bool)
    zero_to_one_mandate: BoolFact = Field(default_factory=unknown_bool)
    digital_business_ownership: BoolFact = Field(default_factory=unknown_bool)
    platform_as_business: BoolFact = Field(default_factory=unknown_bool)
    platform_engineering: BoolFact = Field(default_factory=unknown_bool)
    internal_tools_backoffice: BoolFact = Field(default_factory=unknown_bool)
    feature_delivery_only: BoolFact = Field(default_factory=unknown_bool)
    maintenance_only: BoolFact = Field(default_factory=unknown_bool)
    risk_compliance_heavy: BoolFact = Field(default_factory=unknown_bool)
    transformation_phase: Fact[list[TransformationPhase]] = Field(
        default_factory=lambda: Fact[list[TransformationPhase]](value=[TransformationPhase.unknown])
    )
    # Candidate-independent synthesis; evidence-backed; no recommendation.
    mandate_summary: StrFact = Field(default_factory=unknown_str)

    @model_validator(mode="after")
    def _platform_shape_consistency(self) -> "Mandate":
        # Both true simultaneously is an internal contradiction the extractor
        # must surface as a risk, not silently store.
        if (
            self.platform_as_business.value == TriState.true
            and self.platform_engineering.value == TriState.true
        ):
            raise ValueError(
                "platform_as_business and platform_engineering cannot both be "
                "true; record the dominant shape and an internal_contradiction risk"
            )
        return self


class Organization(_StrictModel):
    reports_to_level: Fact[ManagementLevel] = Field(default_factory=_unknown(ManagementLevel))
    reports_to_title: StrFact = Field(default_factory=unknown_str)
    direct_reports_estimate: IntFact = Field(default_factory=unknown_int)
    total_org_scope_estimate: IntFact = Field(default_factory=unknown_int)
    cross_functional_leadership: BoolFact = Field(default_factory=unknown_bool)
    hiring_authority: BoolFact = Field(default_factory=unknown_bool)
    budget_ownership: BoolFact = Field(default_factory=unknown_bool)
    org_design_authority: BoolFact = Field(default_factory=unknown_bool)
    decision_authority: StrFact = Field(default_factory=unknown_str)
    geographic_responsibility: StrFact = Field(default_factory=unknown_str)
    portfolio_responsibility: StrFact = Field(default_factory=unknown_str)


class CompanyFacts(_StrictModel):
    """Observable or enriched company FACTS — never company-fit verdicts."""

    name: str
    scale: Fact[CompanyScale] = Field(default_factory=_unknown(CompanyScale))
    stage: Fact[CompanyStage] = Field(default_factory=_unknown(CompanyStage))
    geographic_footprint: StrFact = Field(default_factory=unknown_str)
    business_model: StrFact = Field(default_factory=unknown_str)
    customer_model: Fact[CustomerModel] = Field(default_factory=_unknown(CustomerModel))
    platform_ecosystem: BoolFact = Field(default_factory=unknown_bool)
    is_crypto_exchange: BoolFact = Field(default_factory=unknown_bool)
    is_outsourcing: BoolFact = Field(default_factory=unknown_bool)
    local_only: BoolFact = Field(default_factory=unknown_bool)
    product_culture_signal: BoolFact = Field(default_factory=unknown_bool)
    emerging_markets_footprint: BoolFact = Field(default_factory=unknown_bool)
    brand_recognition: Fact[BrandRecognition] = Field(default_factory=_unknown(BrandRecognition))
    bureaucracy_signal: BoolFact = Field(default_factory=unknown_bool)
    regulatory_risk_signal: BoolFact = Field(default_factory=unknown_bool)


class LanguageRequirement(_StrictModel):
    language: str
    level: Optional[str] = None
    mandatory: TriState = TriState.unknown
    evidence: list[Evidence] = Field(default_factory=list)


class FeasibilityFacts(_StrictModel):
    """Facts a feasibility policy will need — the policy itself lives
    elsewhere. KZ local + sponsorship unknown is a VALID factual combination."""

    country: StrFact = Field(default_factory=unknown_str)
    city: StrFact = Field(default_factory=unknown_str)
    country_group: Fact[CountryGroup] = Field(default_factory=_unknown(CountryGroup))
    country_group_resolver_version: Optional[str] = None
    work_format: Fact[WorkFormat] = Field(default_factory=_unknown(WorkFormat))
    remote_geo_restrictions: StrFact = Field(default_factory=unknown_str)
    relocation_support: Fact[RelocationSupport] = Field(default_factory=_unknown(RelocationSupport))
    sponsorship_stated: Fact[SponsorshipStated] = Field(default_factory=_unknown(SponsorshipStated))
    work_authorization_required: BoolFact = Field(default_factory=unknown_bool)
    must_be_already_authorized: BoolFact = Field(default_factory=unknown_bool)
    timezone_expectations: StrFact = Field(default_factory=unknown_str)
    required_working_hours: StrFact = Field(default_factory=unknown_str)
    travel_requirement: StrFact = Field(default_factory=unknown_str)
    language_requirements: list[LanguageRequirement] = Field(default_factory=list)
    local_market_indicator: BoolFact = Field(default_factory=unknown_bool)


class EntryBarrier(_StrictModel):
    requirement: str
    transferability: Transferability
    why: str
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _barrier_needs_evidence(self) -> "EntryBarrier":
        if self.transferability == Transferability.non_transferable_barrier and not self.evidence:
            raise ValueError("a non-transferable barrier classification requires evidence")
        return self


class Requirements(_StrictModel):
    years_experience_min: IntFact = Field(default_factory=unknown_int)
    mandatory_industry_experience: list[str] = Field(default_factory=list)
    mandatory_domain_expertise: list[str] = Field(default_factory=list)
    regulatory_expertise: StrFact = Field(default_factory=unknown_str)
    technical_expertise: list[str] = Field(default_factory=list)
    education: StrFact = Field(default_factory=unknown_str)
    certifications: list[str] = Field(default_factory=list)
    # Crypto CONTEXT is never a barrier by itself: a barrier exists only when
    # deep prior expertise / language is mandatory (see feature dictionary).
    entry_barriers: list[EntryBarrier] = Field(default_factory=list)
    overall_transferability: Fact[Transferability] = Field(default_factory=_unknown(Transferability))


class Risk(_StrictModel):
    """Factual warning — never a preference penalty, never a rejection."""

    kind: RiskKind
    note: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractionDiagnostics(_StrictModel):
    warnings: list[str] = Field(default_factory=list)
    source_text_length: Optional[int] = None
    source_text_truncated: Optional[bool] = None
    deterministic_fact_count: Optional[int] = None
    semantic_fact_count: Optional[int] = None
    unknown_field_count: Optional[int] = None


class VacancyUnderstanding(_StrictModel):
    metadata: Metadata
    role_identity: RoleIdentity
    mandate: Mandate
    organization: Organization = Field(default_factory=Organization)
    company: CompanyFacts
    feasibility_facts: FeasibilityFacts = Field(default_factory=FeasibilityFacts)
    requirements: Requirements = Field(default_factory=Requirements)
    risks: list[Risk] = Field(default_factory=list)
    evidence_registry: list[SourceDocument] = Field(default_factory=list)
    extraction_diagnostics: ExtractionDiagnostics = Field(default_factory=ExtractionDiagnostics)

    @model_validator(mode="after")
    def _validate(self) -> "VacancyUnderstanding":
        if self.metadata.production_integration:
            raise ValueError("production_integration must remain false in Step 2")
        reg_ids = [d.id for d in self.evidence_registry]
        dupes = {i for i in reg_ids if reg_ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate evidence registry ids: {sorted(dupes)}")
        known = set(reg_ids)

        def walk(obj):
            if isinstance(obj, Evidence):
                if obj.source_id not in known:
                    raise ValueError(f"evidence references unknown source_id {obj.source_id!r}")
            elif isinstance(obj, BaseModel):
                for name in type(obj).model_fields:
                    walk(getattr(obj, name))
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        for section in (
            self.role_identity, self.mandate, self.organization, self.company,
            self.feasibility_facts, self.requirements, self.risks,
        ):
            walk(section)
        return self


# ---------------------------------------------------------------------------
# Loading / schema export
# ---------------------------------------------------------------------------

def load_understanding(path: Path | str) -> VacancyUnderstanding:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return VacancyUnderstanding.model_validate(data["vacancy_understanding"])


def export_json_schema() -> dict:
    schema = VacancyUnderstanding.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "vacancy_understanding"
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
