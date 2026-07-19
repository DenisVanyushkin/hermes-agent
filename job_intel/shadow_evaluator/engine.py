"""Shadow Preference Evaluator — runtime engine (shadow/offline only).

Faithful executable translation of the Decision SoT v1.1.x. The engine
consumes ONLY the canonical Step 1 preference model and Step 2 vacancy
understanding record. It never imports legacy scoring, delivery, Slack, CRM
or write-store modules, never writes anywhere, and is not reachable from
production flows.

Stage numbering follows the decision graph (contract.evaluation_order).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional, Union

from job_intel.preference_model.model import (
    AntiPreferenceLevel,
    AntiPreferenceTier,
    EffectType,
    FeasibilityVerdict as P1Verdict,
    RuleStatus,
    condition_matches,
)
from job_intel.shadow_evaluator.contract import (
    Confidence,
    FeasibilityVerdict,
    FitBand,
    Lane,
    Recommendation,
)
from job_intel.shadow_evaluator.models import (
    Action,
    Clarification,
    Diagnostics,
    EvalMetadata,
    EvaluationError,
    Explanation,
    FeasibilityResult,
    FitResult,
    InteractionTraceEntry,
    ItemKind,
    OverallResult,
    ResultItem,
    Section,
    ShadowEvaluation,
    UnknownLedgerEntry,
)
from job_intel.shadow_evaluator.policy import (
    EVALUATOR_VERSION,
    RuntimePolicy,
    UnsupportedInputError,
    load_policy,
)
from job_intel.shadow_evaluator.signals import DerivedSignals, derive_signals
from job_intel.vacancy_understanding.model import VacancyUnderstanding

_CONF_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}

# Strong role/mandate preferences (Step 1 strength=strong) counted for the
# exceptional/strong band criteria. platform_as_the_business is annotated at
# mandate level in golden data (SoT §6 exemplars) and is included here.
_STRONG_MANDATE_PREFS = {"scope_breadth", "growth_mandate", "expansion_mandate",
                         "pnl_ownership", "platform_as_the_business"}
_MEDIUM_MANDATE_PREFS = {"monetization_core", "org_mandate", "executive_exposure",
                         "zero_to_one_mandate", "digital_business_ownership"}
# Role anti-preferences that are mismatch-grade when active (strong tier,
# critical-semantics conflict). narrow scope / risk / devex are weak-grade.
_MISMATCH_ROLE_ANTIS = {"internal_tools_backoffice"}
_WEAK_ROLE_SIGNALS = {"narrow_feature_scope", "fraud_risk_compliance_heavy",
                      "platform_engineering"}

_UNKNOWN_QUESTIONS = {
    "feasibility_facts.work_format": "Каков формат работы (remote/hybrid/onsite)?",
    "feasibility_facts.country_group": "В какой стране находится роль?",
    "feasibility_facts.sponsorship_stated": "Спонсирует ли работодатель визу/релокацию для этой роли?",
    "feasibility_facts.relocation_support": "Есть ли relocation-пакет?",
    "feasibility_facts.timezone_expectations": "Есть ли ожидания по рабочей timezone?",
    "mandate.scope_breadth": "Какова реальная широта мандата (фича/домен/бизнес-линия/регион/портфель)?",
    "mandate.revenue_proximity": "Насколько роль близка к выручке/P&L?",
    "mandate.pnl_ownership": "Роль владеет P&L или только влияет на выручку?",
    "mandate.digital_business_ownership": "Включает ли роль ownership цифрового бизнеса (продукт+коммерция)?",
    "mandate.platform_as_business": "«Platform/infrastructure» — клиентский продукт или внутренняя платформа?",
    "company.scale": "Каков операционный масштаб компании?",
    "company.brand_recognition": "Насколько узнаваем бренд компании?",
    "source_text": "Нужен полный текст вакансии — snapshot неполон.",
}


def _min_conf(*vals: str) -> str:
    known = [v for v in vals if v is not None]
    if not known:
        return "unknown"
    return min(known, key=lambda v: _CONF_ORDER[v])


def evaluate(
    vu: VacancyUnderstanding,
    *,
    policy: Optional[RuntimePolicy] = None,
    evaluated_at: Optional[datetime] = None,
) -> Union[ShadowEvaluation, EvaluationError]:
    policy = policy or load_policy()
    contract = policy.contract

    # ---- Stage 1: validate inputs -----------------------------------------
    pref = policy.preference_model
    try:
        policy.check_input_versions(
            pref.metadata.schema_version, vu.metadata.schema_version)
    except UnsupportedInputError as exc:
        return EvaluationError(
            vacancy_key=vu.metadata.vacancy_key, error=str(exc),
            decision_contract_version=contract.metadata.contract_version)
    if not vu.metadata.vacancy_key:
        return EvaluationError(
            vacancy_key=None, error="missing vacancy_key",
            decision_contract_version=contract.metadata.contract_version)

    sig = derive_signals(vu)
    items: list[ResultItem] = []
    trace: list[InteractionTraceEntry] = []
    ledger: list[UnknownLedgerEntry] = []
    clarifications: list[Clarification] = []
    warnings: list[str] = []
    cap_ids: list[str] = []

    def add_item(**kw) -> ResultItem:
        item = ResultItem(**kw)
        items.append(item)
        return item

    def add_unknown(policy_id: str, field: str, section: Section) -> None:
        entry = policy.unknown_policy[policy_id]
        ledger.append(UnknownLedgerEntry(
            policy_id=policy_id, field=field, section=section,
            cap=entry.cap.value, clarification_priority=entry.clarification_priority.value))
        if entry.cap.value == "promising":
            cap_ids.append("cap_critical_unknowns")
        if entry.clarification_priority.value in ("blocking", "recommendation_changing"):
            clarifications.append(Clarification(
                question=_UNKNOWN_QUESTIONS.get(field, f"Уточнить: {field}?"),
                reason=f"unknown {field} ({policy_id})",
                affected_section=section,
                affected_recommendation=entry.cap.value != "none",
                required_fact=field,
                priority=entry.clarification_priority.value))

    # ---- Stage 2: lane routing --------------------------------------------
    lane = Lane.core
    if sig.country_group is not None and sig.country_group.value == "kazakhstan" and sig.local_market:
        lane = Lane.fallback_local
        trace.append(InteractionTraceEntry(
            rule_id="ir_kz_fallback_lane", effect="route_to_fallback",
            target_ids=["local_market_fallback", "small_local_company"], produced="lane=fallback_local"))
    kz_local_lane = lane == Lane.fallback_local
    if sig.country_group is None:
        add_unknown("u_country", "feasibility_facts.country_group", Section.feasibility)

    # ---- Stage 3-4: feasibility matching + interactions + merge -----------
    verdict = FeasibilityVerdict.feasible
    matched_constraints: list[str] = []
    blocker_ids: list[str] = []
    feas_unknown_ids: list[str] = []
    scenario = sig.scenario

    # 'allow' interactions prevent target blockers from matching at all
    prevented: set[str] = set()
    for rule in sorted(pref.interaction_rules, key=lambda r: r.priority):
        if rule.status != RuleStatus.active or rule.effect.type != EffectType.allow:
            continue
        if condition_matches(rule.when, scenario):
            prevented.update(rule.effect.target_ids)
            trace.append(InteractionTraceEntry(
                rule_id=rule.id, effect="allow", target_ids=rule.effect.target_ids,
                produced="prevented"))

    for c in pref.feasibility_constraints.constraints:
        if c.status != RuleStatus.active:
            continue
        if c.id in prevented:
            continue
        if condition_matches(c.when, scenario):
            matched_constraints.append(c.id)
            if c.verdict.value != "feasible":
                new_verdict = FeasibilityVerdict(c.verdict.value)
                if _order(new_verdict) > _order(verdict):
                    verdict = new_verdict
                item = add_item(
                    id=f"blk_{c.id}", section=Section.feasibility,
                    kind=ItemKind.blocker if c.verdict == P1Verdict.infeasible else ItemKind.unknown,
                    preference_rule_id=c.id, vacancy_fact_path="feasibility_facts",
                    statement=c.statement.strip(), evidence_refs=_scenario_evidence(sig, c),
                    confidence=Confidence.high, impact=c.verdict.value)
                if c.verdict == P1Verdict.infeasible:
                    blocker_ids.append(item.id)
                else:
                    feas_unknown_ids.append(item.id)

    # feasibility unknown policy (uncertain-grade unknowns cap, verdict unchanged)
    if sig.work_format is None and not kz_local_lane:
        add_unknown("u_work_format_non_kz", "feasibility_facts.work_format", Section.feasibility)
    if "feasibility_facts.sponsorship_stated" in sig.unknown_fields:
        if sig.work_format is not None and sig.work_format.value in ("onsite", "hybrid") \
                and sig.country_group is not None and sig.country_group.value not in ("usa", "kazakhstan"):
            pass  # already produced fc_onsite_sponsorship_unknown -> uncertain
        elif not kz_local_lane and (sig.work_format is None or sig.work_format.value == "remote"):
            # optional-grade: no cap, no clarification pressure
            pass
    if sig.source_text_incomplete:
        add_unknown("u_incomplete_text", "source_text", Section.feasibility)
        cap_ids.append("cap_incomplete_text")

    feas_conf = _feasibility_confidence(sig, verdict)
    feasibility = FeasibilityResult(
        verdict=verdict, lane=lane, matched_constraints=matched_constraints,
        blockers=blocker_ids, unknowns=sorted(sig.unknown_fields & {
            "feasibility_facts.work_format", "feasibility_facts.country_group",
            "feasibility_facts.sponsorship_stated", "feasibility_facts.relocation_support",
            "feasibility_facts.timezone_expectations"}),
        confidence=Confidence(feas_conf),
        fallback_state="standby" if lane == Lane.fallback_local else None)

    # ---- Stages 6-9: mandate & company ------------------------------------
    terminal = verdict == FeasibilityVerdict.infeasible
    mandate = _mandate_fit(policy, sig, items, trace, add_item, decisioning=not terminal)
    company = _company_fit(policy, sig, lane, items, trace, add_item, decisioning=not terminal)

    # mandate/company unknown-policy entries
    if "mandate.scope_breadth" in sig.unknown_fields:
        add_unknown("u_scope_breadth", "mandate.scope_breadth", Section.mandate)
    if "mandate.revenue_proximity" in sig.unknown_fields:
        add_unknown("u_revenue_proximity", "mandate.revenue_proximity", Section.mandate)
    if company.band == FitBand.unknown:
        add_unknown("u_company_all", "company", Section.company)

    # ---- Stage 5 / 11: recommendation -------------------------------------
    if terminal:
        recommendation = Recommendation.not_recommended
        applied_caps: list[str] = []
        overall_conf = "high"  # confidence of the blocker (contract exception)
    else:
        base = policy.resolve_matrix(mandate.band, company.band)
        if verdict == FeasibilityVerdict.uncertain:
            cap_ids.append("cap_uncertain")
        # crypto cap (provisional shadow policy)
        if sig.flag("crypto_exchange_employer"):
            cap_ids.append("cap_crypto_employer")
        # Stage 10: confidence before cap_low_confidence
        overall_conf = _min_conf(feas_conf, mandate.confidence.value, company.confidence.value)
        if terminal:
            pass
        if overall_conf == "low":
            cap_ids.append("cap_low_confidence")
        # dedupe preserving order; cap_critical_unknowns synthetic id maps to
        # the strongest field-specific promising cap
        seen = set()
        ordered = [x for x in cap_ids if not (x in seen or seen.add(x))]
        ordered = [x for x in ordered if x in policy.caps or x == "cap_critical_unknowns"]
        real = [x for x in ordered if x in policy.caps]
        recommendation, applied_caps = policy.apply_caps(base, real)
        # critical-unknown promising ceiling (field-specific, from ledger)
        if "cap_critical_unknowns" in ordered and recommendation.value in ("strong", "exceptional"):
            recommendation = Recommendation.promising
            applied_caps.append("cap_critical_unknowns")

    if terminal:
        overall_conf_final = "high"
    else:
        overall_conf_final = overall_conf

    action = Action(policy.action_for(
        recommendation, overall_conf_final,
        feasibility_uncertain=verdict == FeasibilityVerdict.uncertain))

    overall = OverallResult(
        recommendation=recommendation, action=action,
        confidence=Confidence(overall_conf_final), lane=lane,
        applied_caps=applied_caps, exploration_axis=None)

    # ---- Stage 12: explanations + clarifications --------------------------
    explanation = _explain(items, trace, applied_caps, overall, feasibility, lane, policy)
    # unclear requires non-empty clarification
    if recommendation == Recommendation.unclear and not clarifications:
        for path in sorted(sig.unknown_fields)[:2] or ["mandate.scope_breadth"]:
            clarifications.append(Clarification(
                question=_UNKNOWN_QUESTIONS.get(path, f"Уточнить: {path}?"),
                reason=f"unknown {path} prevents classification",
                affected_section=Section.mandate, affected_recommendation=True,
                required_fact=path, priority="recommendation_changing"))
    # dedup clarifications by required_fact
    dedup: dict[str, Clarification] = {}
    for cl in clarifications:
        dedup.setdefault(cl.required_fact, cl)

    input_hash = hashlib.sha256(
        (vu.metadata.vacancy_key + (vu.metadata.source_content_hash or "")).encode()
    ).hexdigest()[:16]

    return ShadowEvaluation(
        metadata=EvalMetadata(
            decision_contract_version=contract.metadata.contract_version,
            preference_model_version=pref.metadata.model_version,
            vacancy_understanding_schema_version=vu.metadata.schema_version,
            evaluator_version=EVALUATOR_VERSION,
            evaluated_at=evaluated_at or datetime.now(timezone.utc),
            vacancy_key=vu.metadata.vacancy_key,
            input_content_hash=input_hash),
        feasibility=feasibility, mandate_fit=mandate, company_fit=company,
        overall=overall, items=items, explanations=explanation,
        clarifications=list(dedup.values()), interaction_trace=trace,
        unknown_ledger=ledger, diagnostics=Diagnostics(warnings=warnings))


def _order(v: FeasibilityVerdict) -> int:
    return {"feasible": 0, "uncertain": 1, "infeasible": 2}[v.value]


def _scenario_evidence(sig: DerivedSignals, constraint) -> list[str]:
    refs: list[str] = []
    for flag in (constraint.when.flags_all or []):
        s = sig.signals.get(flag)
        if s:
            refs.extend(s.evidence_refs)
    return refs or ["src_structured_fields"]


def _feasibility_confidence(sig: DerivedSignals, verdict) -> str:
    if verdict == FeasibilityVerdict.infeasible:
        return "high"
    critical = []
    if sig.work_format is None or "feasibility_facts.country_group" in sig.unknown_fields:
        return "low"
    if sig.sponsorship.value == "unknown" and sig.work_format.value in ("onsite", "hybrid"):
        critical.append("medium")
    return _min_conf("high", *critical) if critical else "high"


def _mandate_fit(policy, sig: DerivedSignals, items, trace, add_item, *, decisioning) -> FitResult:
    pref = policy.preference_model
    supports: list[str] = []
    concerns: list[str] = []
    blockers: list[str] = []
    scenario = sig.scenario

    def s_item(name: str, kind: ItemKind, section=Section.mandate, impact=None) -> ResultItem:
        s = sig.signals[name]
        return add_item(
            id=f"{kind.value}_{name}", section=section, kind=kind,
            preference_rule_id=name, vacancy_fact_path=s.fact_path,
            statement=s.statement, evidence_refs=s.evidence_refs or ["src_structured_fields"],
            confidence=Confidence(s.confidence.value), impact=impact)

    # supports
    for name in sorted(_STRONG_MANDATE_PREFS | _MEDIUM_MANDATE_PREFS):
        if sig.flag(name):
            supports.append(s_item(name, ItemKind.support, impact="support").id)
    # excluded_from: platform engineering never supports platform-as-business
    if sig.flag("platform_engineering"):
        rule = next(r for r in pref.interaction_rules
                    if r.id == "ir_platform_engineering_not_platform_business")
        removed = [i for i in supports if i.endswith("platform_as_the_business")]
        for rid in removed:
            it = next(i for i in items if i.id == rid)
            it.active = False
            it.suppressed_by = rule.id
            supports.remove(rid)
        trace.append(InteractionTraceEntry(
            rule_id=rule.id, effect="exclude_from",
            target_ids=["platform_as_the_business"],
            produced="excluded", input_item_ids=removed))

    # role-level anti-preferences → concerns/blockers
    for anti in pref.anti_preferences:
        if anti.status != RuleStatus.active or anti.level != AntiPreferenceLevel.role:
            continue
        if anti.when is None or not condition_matches(anti.when, scenario):
            continue
        # suppression by interaction rules
        suppressed_by = None
        for rule in sorted(pref.interaction_rules, key=lambda r: r.priority):
            if rule.status != RuleStatus.active or rule.effect.type != EffectType.suppress:
                continue
            if anti.id in rule.effect.target_ids and condition_matches(rule.when, scenario):
                suppressed_by = rule.id
                break
        item = add_item(
            id=f"anti_{anti.id}", section=Section.mandate,
            kind=ItemKind.blocker if anti.id in _MISMATCH_ROLE_ANTIS else ItemKind.concern,
            preference_rule_id=anti.id,
            vacancy_fact_path=sig.signals[next(iter(anti.when.flags_all))].fact_path
            if anti.when.flags_all and anti.when.flags_all[0] in sig.signals else None,
            statement=anti.statement.strip(),
            evidence_refs=(sig.signals[anti.when.flags_all[0]].evidence_refs
                           if anti.when.flags_all and anti.when.flags_all[0] in sig.signals
                           else []) or ["src_structured_fields"],
            confidence=Confidence.high if anti.confidence.value == "high" else Confidence.medium,
            impact="mismatch" if anti.id in _MISMATCH_ROLE_ANTIS else "concern",
            active=suppressed_by is None, suppressed_by=suppressed_by)
        if suppressed_by:
            trace.append(InteractionTraceEntry(
                rule_id=suppressed_by, effect="suppress", target_ids=[anti.id],
                produced="suppressed", input_item_ids=[item.id]))
        elif anti.id in _MISMATCH_ROLE_ANTIS:
            blockers.append(item.id)
        else:
            concerns.append(item.id)

    # function-based mismatch (diagnostic mirror of fc_function)
    if sig.flag("non_product_function") and not sig.flag("digital_business_ownership"):
        it = s_item("non_product_function", ItemKind.blocker, impact="mismatch")
        blockers.append(it.id)

    band, conf = _mandate_band(sig, supports, concerns, blockers, items)
    return FitResult(
        band=band, supports=supports, concerns=concerns, blockers=blockers,
        unknowns=sorted(sig.unknown_fields & {"mandate.scope_breadth", "mandate.revenue_proximity",
                                              "mandate.pnl_ownership"}),
        confidence=Confidence(conf), decisioning=decisioning)


def _mandate_band(sig: DerivedSignals, supports, concerns, blockers, items) -> tuple[FitBand, str]:
    scope = sig.signals.get("scope_breadth")
    scope_known = scope is not None
    scope_broad = bool(scope and scope.value)
    scope_conf = scope.confidence.value if scope else "unknown"
    revenue_unknown = "mandate.revenue_proximity" in sig.unknown_fields

    strong_high = [
        i for i in supports
        if i.removeprefix("support_") in _STRONG_MANDATE_PREFS
        and next(x for x in items if x.id == i).confidence.value == "high"
    ]
    material_concerns = [i for i in concerns]  # active, unsuppressed by construction

    # section confidence: least-confident critical fact (scope, revenue).
    # Unknown revenue proximity ceilings at medium (u_revenue_proximity),
    # it does not zero the section.
    revenue_conf = "medium" if revenue_unknown else (sig.revenue_conf or "high")
    conf = _min_conf(scope_conf if scope_known else "unknown", revenue_conf)
    if not scope_known and revenue_unknown and not supports and not blockers and not material_concerns:
        return FitBand.unknown, "unknown"
    if blockers:
        return FitBand.mismatch, "high"

    weak_signals = [n for n in _WEAK_ROLE_SIGNALS if sig.flag(n)]
    monetization_exception = sig.flag("monetization_core") and sig.flag("narrow_feature_scope")
    narrow_active = any(i.endswith("narrow_feature_scope") for i in material_concerns)

    if scope_known and scope_broad:
        if scope_conf == "high" and len(strong_high) >= 2 and not material_concerns and not revenue_unknown:
            return FitBand.exceptional, conf
        if strong_high and not material_concerns:
            return FitBand.strong, conf
        return FitBand.moderate, conf
    if monetization_exception and not narrow_active:
        # narrow scope suppressed by monetization exception
        if strong_high or sig.flag("monetization_core"):
            return (FitBand.strong, conf) if not material_concerns else (FitBand.moderate, conf)
    if weak_signals and (narrow_active or not supports):
        # a strong high-confidence axis (e.g. growth) keeps a narrow role at
        # moderate: transferable relevance with a strong preference match
        if strong_high:
            return FitBand.moderate, conf
        return FitBand.weak, _min_conf(conf, "high") if scope_known else "medium"
    if supports:
        return FitBand.moderate, conf
    if not scope_known:
        return FitBand.unknown, "unknown"
    return FitBand.weak, conf


def _company_fit(policy, sig: DerivedSignals, lane, items, trace, add_item, *, decisioning) -> FitResult:
    pref = policy.preference_model
    supports: list[str] = []
    concerns: list[str] = []
    blockers: list[str] = []
    scenario = sig.scenario

    # company anti-preferences
    for anti in pref.anti_preferences:
        if anti.status != RuleStatus.active or anti.level != AntiPreferenceLevel.company:
            continue
        if anti.when is None or not condition_matches(anti.when, scenario):
            continue
        suppressed_by = None
        moved = None
        if anti.id == "crypto_exchange_employer":
            # limit_to_company_fit: concern lives here only, never mandate
            rule = next(r for r in pref.interaction_rules
                        if r.id == "ir_crypto_employer_not_role_veto")
            moved = "company_fit"
            trace.append(InteractionTraceEntry(
                rule_id=rule.id, effect="limit_to_company_fit",
                target_ids=[anti.id], produced="moved_to:company_fit"))
        if anti.id == "small_local_company" and lane == Lane.fallback_local:
            suppressed_by = "ir_kz_fallback_lane"
            trace.append(InteractionTraceEntry(
                rule_id="ir_kz_fallback_lane", effect="suppress",
                target_ids=[anti.id], produced="suppressed"))
        sname = anti.when.flags_all[0] if anti.when.flags_all else None
        src = sig.signals.get(sname) if sname else None
        item = add_item(
            id=f"anti_{anti.id}", section=Section.company, kind=ItemKind.concern,
            preference_rule_id=anti.id,
            vacancy_fact_path=src.fact_path if src else None,
            statement=anti.statement.strip(),
            evidence_refs=(src.evidence_refs if src else []) or ["src_structured_fields"],
            confidence=Confidence.high, impact="company_concern",
            active=suppressed_by is None, suppressed_by=suppressed_by, moved_to=moved)
        if suppressed_by is None:
            concerns.append(item.id)

    # b2b suppression by platform-as-business
    if sig.flag("b2b_enterprise_context") and sig.flag("platform_as_the_business"):
        for i in list(concerns):
            it = next(x for x in items if x.id == i)
            if it.preference_rule_id == "b2b_enterprise_context":
                it.active = False
                it.suppressed_by = "ir_b2b_platform_business_exception"
                concerns.remove(i)
                trace.append(InteractionTraceEntry(
                    rule_id="ir_b2b_platform_business_exception", effect="suppress",
                    target_ids=["b2b_enterprise_context"], produced="suppressed",
                    input_item_ids=[i]))

    band, conf = _company_band(sig, concerns, lane)
    # positive company facts as supports
    vu_scale = sig.signals  # supports derived directly from company facts below
    return FitResult(
        band=band, supports=supports, concerns=concerns, blockers=blockers,
        unknowns=sorted(sig.unknown_fields & {"company.scale", "company.brand_recognition"}),
        confidence=Confidence(conf), decisioning=decisioning)


def _company_band(sig: DerivedSignals, concerns, lane) -> tuple[FitBand, str]:
    scale_unknown = "company.scale" in sig.unknown_fields
    brand_unknown = "company.brand_recognition" in sig.unknown_fields
    conf = "medium" if (scale_unknown or brand_unknown) else "high"

    mismatch = sig.flag("outsourcing_company") or (
        sig.flag("small_local_company") and lane == Lane.core)
    if mismatch:
        return FitBand.mismatch, "high"
    if sig.flag("crypto_exchange_employer"):
        return FitBand.weak, "high"
    if scale_unknown and brand_unknown:
        if concerns:
            return FitBand.weak, "medium"
        return FitBand.unknown, "unknown"
    return _company_band_from_facts(sig, concerns, conf)


def _company_band_from_facts(sig: DerivedSignals, concerns, conf) -> tuple[FitBand, str]:
    scale, brand = sig.company_scale, sig.company_brand
    if scale == "local":
        return FitBand.weak, conf
    if scale in ("global", "multi_region") and brand in ("known", "tier1_scaleup", "big_tech"):
        if not concerns:
            return FitBand.strong, conf
        return FitBand.moderate, conf
    return FitBand.moderate, conf


def _explain(items, trace, applied_caps, overall, feasibility, lane, policy) -> Explanation:
    attractive = [i.statement for i in items
                  if i.kind == ItemKind.support and i.active][:6]
    negative = [i.statement for i in items
                if i.kind in (ItemKind.concern, ItemKind.blocker) and i.active][:6]
    unknowns = [i.statement for i in items if i.kind == ItemKind.unknown][:6]
    for cap_id in applied_caps:
        cap = policy.caps.get(cap_id)
        negative.append(f"cap {cap_id}: {cap.rationale}" if cap
                        else f"cap {cap_id}: critical unknown field ceiling")
    summary = (
        f"{overall.recommendation.value} ({overall.action.value}, "
        f"confidence {overall.confidence.value}, lane {lane.value}, "
        f"feasibility {feasibility.verdict.value})"
    )
    return Explanation(
        verdict_summary=summary,
        why_attractive=attractive,
        why_may_not_work=negative,
        unknowns=unknowns,
        interactions_applied=[t.rule_id for t in trace],
        lane=lane,
    )
