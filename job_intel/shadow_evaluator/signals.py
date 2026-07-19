"""Derivation of Step 1 rule signals from a canonical Step 2 record.

Pure translation layer: no new vacancy facts are inferred here — a signal is
set only when the corresponding canonical fact is known. Unknown never
becomes false. Each derived signal keeps a pointer to its source fact path
and evidence refs for the explanation contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from job_intel.preference_model.model import (
    CountryGroup as P1Country,
    Scenario,
    SponsorshipStated as P1Sponsorship,
    WorkFormat as P1WorkFormat,
)
from job_intel.vacancy_understanding.model import (
    Confidence,
    CountryGroup,
    Fact,
    SponsorshipStated,
    Transferability,
    TriState,
    VacancyUnderstanding,
    WorkFormat,
)

_NON_PRODUCT_FAMILIES = {"sales", "finance", "project_delivery"}


@dataclass
class Signal:
    """A named boolean signal derived from exactly one canonical fact."""

    name: str
    value: bool
    fact_path: str
    confidence: Confidence
    evidence_refs: list[str] = field(default_factory=list)
    statement: str = ""


@dataclass
class DerivedSignals:
    scenario: Scenario                 # Step 1 structured scenario
    signals: dict[str, Signal]         # only KNOWN signals appear here
    unknown_fields: set[str]           # canonical field paths that are unknown
    source_text_incomplete: bool
    work_format: Optional[WorkFormat]
    country_group: Optional[CountryGroup]
    sponsorship: SponsorshipStated
    local_market: bool
    company_scale: Optional[str] = None
    company_brand: Optional[str] = None
    revenue_conf: Optional[str] = None  # confidence of known revenue_proximity

    def flag(self, name: str) -> bool:
        s = self.signals.get(name)
        return bool(s and s.value)


def _ev_refs(fact) -> list[str]:
    return [e.source_id for e in getattr(fact, "evidence", [])]


def _tri(fact: Fact) -> Optional[bool]:
    if fact.value == TriState.true:
        return True
    if fact.value == TriState.false:
        return False
    return None


def derive_signals(vu: VacancyUnderstanding) -> DerivedSignals:
    signals: dict[str, Signal] = {}
    unknown: set[str] = set()

    def add_tri(name: str, fact, path: str, statement: str) -> None:
        val = _tri(fact)
        if val is None:
            unknown.add(path)
        else:
            signals[name] = Signal(name, val, path, fact.confidence, _ev_refs(fact), statement)

    m, c, f = vu.mandate, vu.company, vu.feasibility_facts

    # --- mandate facts -----------------------------------------------------
    add_tri("growth_mandate", m.growth_mandate, "mandate.growth_mandate", "growth is core to the mandate")
    add_tri("expansion_mandate", m.expansion_mandate, "mandate.expansion_mandate", "expansion/market-entry mandate")
    add_tri("monetization_core", m.monetization_core, "mandate.monetization_core", "monetization/pricing at the core")
    add_tri("pnl_ownership", m.pnl_ownership, "mandate.pnl_ownership", "P&L ownership")
    add_tri("org_mandate", m.org_design_mandate, "mandate.org_design_mandate", "org design mandate")
    add_tri("executive_exposure", m.executive_exposure, "mandate.executive_exposure", "executive exposure")
    add_tri("zero_to_one_mandate", m.zero_to_one_mandate, "mandate.zero_to_one_mandate", "zero-to-one mandate")
    add_tri("platform_as_the_business", m.platform_as_business, "mandate.platform_as_business", "platform IS the business")
    add_tri("platform_engineering", m.platform_engineering, "mandate.platform_engineering", "internal platform engineering")
    add_tri("internal_tools_backoffice", m.internal_tools_backoffice, "mandate.internal_tools_backoffice", "internal tools / back-office product")
    add_tri("fraud_risk_compliance_heavy", m.risk_compliance_heavy, "mandate.risk_compliance_heavy", "risk/compliance-heavy domain")
    add_tri("digital_business_ownership", m.digital_business_ownership, "mandate.digital_business_ownership", "digital business ownership")

    # scope breadth (ordinal) → named signals
    scope = m.scope_breadth
    if scope.value.value == "unknown":
        unknown.add("mandate.scope_breadth")
    else:
        broad = scope.value.value in ("business_line", "region", "portfolio", "enterprise")
        signals["scope_breadth"] = Signal(
            "scope_breadth", broad, "mandate.scope_breadth", scope.confidence,
            _ev_refs(scope), f"scope breadth = {scope.value.value}")
        signals["narrow_feature_scope"] = Signal(
            "narrow_feature_scope", scope.value.value in ("feature", "domain"),
            "mandate.scope_breadth", scope.confidence, _ev_refs(scope),
            f"narrow scope ({scope.value.value})")
    if m.revenue_proximity.value.value == "unknown":
        unknown.add("mandate.revenue_proximity")

    # --- function family ---------------------------------------------------
    fams = {x.value for x in vu.role_identity.function_families}
    if fams and fams != {"unknown"}:
        non_product = bool(fams & _NON_PRODUCT_FAMILIES) and "product" not in fams
        if non_product:
            signals["non_product_function"] = Signal(
                "non_product_function", True, "role_identity.function_families",
                Confidence.medium, [], f"non-product function family: {sorted(fams & _NON_PRODUCT_FAMILIES)}")

    # --- requirements barriers ----------------------------------------------
    barrier = any(
        b.transferability == Transferability.non_transferable_barrier
        for b in vu.requirements.entry_barriers
    ) or vu.requirements.overall_transferability.value == Transferability.non_transferable_barrier
    if barrier:
        first = next((b for b in vu.requirements.entry_barriers
                      if b.transferability == Transferability.non_transferable_barrier), None)
        signals["non_transferable_domain_barrier"] = Signal(
            "non_transferable_domain_barrier", True, "requirements.entry_barriers",
            first.confidence if first else Confidence.medium,
            [e.source_id for e in (first.evidence if first else [])],
            first.requirement if first else "non-transferable entry barrier")

    # --- company facts -----------------------------------------------------
    add_tri("crypto_exchange_employer", c.is_crypto_exchange, "company.is_crypto_exchange", "employer operates a crypto exchange")
    add_tri("outsourcing_company", c.is_outsourcing, "company.is_outsourcing", "outsourcing/agency employer")
    add_tri("small_local_company", c.local_only, "company.local_only", "local-only company")
    if c.customer_model.value.value == "b2b_enterprise":
        signals["b2b_enterprise_context"] = Signal(
            "b2b_enterprise_context", True, "company.customer_model",
            c.customer_model.confidence, _ev_refs(c.customer_model), "B2B enterprise customer model")
    for path, fact in (("company.scale", c.scale), ("company.brand_recognition", c.brand_recognition)):
        if fact.value.value == "unknown":
            unknown.add(path)

    # --- feasibility facts --------------------------------------------------
    wf = None if f.work_format.value == WorkFormat.unknown else f.work_format.value
    if wf is None:
        unknown.add("feasibility_facts.work_format")
    cg = None if f.country_group.value == CountryGroup.unknown else f.country_group.value
    if cg is None:
        unknown.add("feasibility_facts.country_group")
    sponsorship = f.sponsorship_stated.value
    if sponsorship == SponsorshipStated.unknown:
        unknown.add("feasibility_facts.sponsorship_stated")
    local_market = f.local_market_indicator.value == TriState.true
    if f.relocation_support.value.value == "unknown":
        unknown.add("feasibility_facts.relocation_support")
    if f.timezone_expectations.value is None:
        unknown.add("feasibility_facts.timezone_expectations")

    incomplete = any(r.kind.value == "source_text_incomplete" for r in vu.risks)

    # --- Step 1 scenario ----------------------------------------------------
    flags = {name for name, s in signals.items() if s.value}
    scenario = Scenario(
        work_format=P1WorkFormat(wf.value) if wf else None,
        country_group=P1Country(cg.value) if cg else P1Country.other,
        sponsorship_stated=P1Sponsorship(sponsorship.value),
        local_market=local_market,
        flags=flags,
    )
    return DerivedSignals(
        scenario=scenario,
        signals=signals,
        unknown_fields=unknown,
        source_text_incomplete=incomplete,
        work_format=wf,
        country_group=cg,
        sponsorship=sponsorship,
        local_market=local_market,
        revenue_conf=None if m.revenue_proximity.value.value == "unknown"
        else m.revenue_proximity.confidence.value,
        company_scale=None if c.scale.value.value == "unknown" else c.scale.value.value,
        company_brand=None if c.brand_recognition.value.value == "unknown" else c.brand_recognition.value.value,
    )
