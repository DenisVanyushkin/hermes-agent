"""Semantic providers (Step 4B).

DeterministicPhraseProvider — the first conformant implementation of the
Step 4A provider contract: pure, replayable, no network, no model calls.
Its rule table is this provider's "prompt" (implementation detail, Step 4A
§9); it emits ONLY observations with verbatim excerpts.

LLMProvider — a declared extension point. Live model calls require a
separate owner approval; the class exists so nothing provider-specific can
leak outside this module when it lands.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from job_intel.vacancy_understanding.semantic.runtime.models import (
    Observation,
    ObservationBasis,
)


@dataclass(frozen=True)
class SignalRule:
    pattern: str
    signal: str                    # "<fact_leaf>=<value>"
    basis: ObservationBasis
    scope: str = "body"            # body | title
    extra_signals: tuple[str, ...] = ()


_R = SignalRule
_EXP = ObservationBasis.explicit
_DIR = ObservationBasis.direct
_WEA = ObservationBasis.weak

# Qualifiers that make a strategy a FUNCTIONAL one — owning it is not an
# executive product mandate (owner decision 2026-08-06). Sampled from real
# firings: design, product marketing, commercial risk, regional events,
# Talent Acquisition, technical sales, global benefits, search.
_FUNCTIONAL_STRATEGY = (
    r"design|marketing|brand|risk|benefits?|events?|sales|talent|"
    r"recruit\w*|hiring|content|search|seo|technical|engineering|data|"
    r"security|compliance|finance|financial|communications?|people|hr|"
    r"media|channel|partnerships?|support|operations?|legal|tax|payroll|"
    r"procurement|workplace|community|social|quota|pipeline"
)
# Duty verb, then AT MOST four qualifier words none of which is functional,
# then the strategy noun. The word-by-word lookahead is what makes this a
# denylist that a free filler cannot slip past: round 1 used
# "[\w ,'-]{0,30}" here, which happily swallowed "the design ".
_STRATEGY_DUTY = (
    r"(?:[Dd]efine|[Dd]rive|[Ff]ormulate|[Ss]et|[Oo]wn)(?:s|ing)?"
    r"(?: and (?:execute|drive|define|own|shape|lead)(?:s|ing)?)?"
    rf"(?:\s+(?!(?i:{_FUNCTIONAL_STRATEGY})\b)[\w'’-]+){{0,4}}"
    r"\s+(?:strategy|strategic vision|product vision)\b"
)

RULES: list[SignalRule] = [
    # ---- scope breadth ----
    _R(r"[Oo]wn [\w' ]{0,30}growth and expansion (across|in) (all )?\w+[\w ]{0,20}", "scope_breadth=region", _DIR),
    _R(r"lead(ing)? (all of )?(the )?(APAC|EMEA|LATAM)[\w ]{0,15}(region|markets|business)", "scope_breadth=region", _DIR),
    _R(r"APAC|EMEA|LATAM", "scope_breadth=region", _WEA, scope="title"),
    _R(r"[Oo]wn the [\w ]{0,20}business line|[Bb]usiness [Bb]anking(?= business| for| across)", "scope_breadth=business_line", _DIR),
    _R(r"own product, growth and (the )?P&L", "scope_breadth=business_line", _DIR,
       extra_signals=("digital_business_ownership=true", "pnl_ownership=true")),
    _R(r"([Ii]mprove|[Oo]ptimi[sz]e) the onboarding flow( conversion)?", "scope_breadth=feature", _DIR),
    _R(r"[Dd]rive growth across acquisition and onboarding", "scope_breadth=domain", _WEA,
       extra_signals=("growth_mandate=true",)),
    _R(r"report into the feature team", "scope_breadth=feature", _DIR),
    # ---- growth / expansion ----
    _R(r"[Oo]wn (user |customer )?acquisition( and activation)?", "growth_mandate=true", _DIR),
    _R(r"[Mm]aintain the existing [\w ]{0,20}(stack|platform|process(es)?)", "growth_mandate=false", _DIR,
       extra_signals=("maintenance_only=true",)),
    _R(r"\bGrowth\b", "growth_mandate=true", _WEA, scope="title"),
    _R(r"purely compliance duties|compliance duties only", "growth_mandate=false", _DIR),
    _R(r"[Ll]aunch (and scale )?new markets( in [A-Z]\w+)?( while scaling the core)?", "expansion_mandate=true", _DIR,
       extra_signals=("market_entry_ownership=true", "transformation_phase=expand")),
    _R(r"lead (our|the) entry into [A-Z]\w+", "market_entry_ownership=true", _DIR,
       extra_signals=("expansion_mandate=true",)),
    _R(r"[Oo]perate the established [\w ]{0,20}business", "expansion_mandate=false", _DIR),
    _R(r"[Ee]xpansion", "expansion_mandate=true", _WEA, scope="title"),
    # ---- monetization ----
    _R(r"own (our |the )?pricing (engine|strategy)( and experimentation)?", "pricing_core=true", _DIR,
       extra_signals=("monetization_core=true",)),
    _R(r"own (our|the) (card )?acquiring product", "acquiring_core=true", _DIR,
       extra_signals=("monetization_core=true",)),
    _R(r"Pricing", "pricing_core=true", _WEA, scope="title",),
    _R(r"Acquiring", "acquiring_core=true", _WEA, scope="title"),
    _R(r"own the compliance product", "monetization_core=false", _DIR),
    # ---- P&L / strategy / org ----
    _R(r"full P&L ownership( of the [\w ]{0,20})?|own(ership of)? the P&L|own P&L\b", "pnl_ownership=true", _EXP),
    _R(r"finance owns the budget|budget managed centrally", "pnl_ownership=false", _DIR,
       extra_signals=("organization.budget_ownership=false",)),
    _R(r"own the (marketing |line )?budget", "organization.budget_ownership=true", _DIR),
    _R(r"[Yy]ou (will )?define the product strategy( for [\w ]{0,25})?", "strategy_ownership=true", _EXP),
    _R(r"[Ee]xecute the roadmap defined by", "strategy_ownership=false", _DIR),
    _R(r"redesign the [\w ]{0,30}operating model", "org_design_mandate=true", _DIR),
    _R(r"join an established team structure", "org_design_mandate=false", _DIR),
    _R(r"hire and grow a team( of \w+)?( from scratch)?|build (the|a) team from scratch",
       "team_build_mandate=true", _DIR),
    _R(r"lead an existing team of \d+", "team_build_mandate=false", _DIR),
    _R(r"present (quarterly )?to the executive (committee|team)", "executive_exposure=true", _EXP),
    _R(r"(prepare and )?present board updates", "board_exposure=true", _EXP),
    # ---- phases ----
    _R(r"(take|bring) [\w' ]{0,30}from zero to one|zero-to-one", "zero_to_one_mandate=true", _DIR,
       extra_signals=("transformation_phase=build",)),
    _R(r"reverse the decline( of [\w ]{0,25})?", "turnaround_mandate=true", _DIR,
       extra_signals=("transformation_phase=turnaround",)),
    _R(r"keep the (existing platform stable|lights on)", "maintenance_only=true", _DIR,
       extra_signals=("transformation_phase=maintain",)),
    _R(r"no new (scope|development) planned", "maintenance_only=true", _DIR),
    _R(r"scaling the core|scale the core [\w ]{0,15}", "transformation_phase=scale", _DIR),
    _R(r"deliver features from the central roadmap", "feature_delivery_only=true", _DIR),
    _R(r"define what we build and why", "feature_delivery_only=false", _DIR,
       extra_signals=("strategy_ownership=true",)),
    _R(r"carry the [\w ]{0,20}sales quota", "digital_business_ownership=false", _DIR),
    # ---- product shape ----
    _R(r"(payment|money) (rails|network) [\w ]{0,25}customers (move money through|rely on)|"
       r"customers move money through|moves? money for businesses( worldwide)?",
       "platform_as_business=true", _EXP, extra_signals=("company.platform_ecosystem=true",)),
    _R(r"internal (developer|compute) platform( for our engineers)?|developer productivity platform",
       "platform_engineering=true", _EXP),
    _R(r"internal HR and finance systems|used by our people team|back-office tooling",
       "internal_tools_backoffice=true", _EXP),
    _R(r"serving internal (teams|engineering) only", "platform_engineering=true", _DIR),
    _R(r"own fraud prevention( end to end)?", "risk_compliance_heavy=true", _DIR),
    _R(r"Payment Fraud|Financial Crime", "risk_compliance_heavy=true", _WEA, scope="title"),
    # ---- company (self-statements only) ----
    _R(r"\d+ offices around the globe|operate in \d+ (countries|markets)|serving \d+ markets",
       "company.scale=global", _EXP),
    _R(r"customers across Kazakhstan|serve [\w ]{0,15}Kazakhstan only", "company.scale=local", _EXP),
    _R(r"after our Series [C-F]|Series [C-F] funding", "company.stage=scaleup", _EXP),
    _R(r"publicly listed( company)?", "company.stage=public", _EXP),
    _R(r"family business established \d{4}", "company.stage=mature", _EXP),
    _R(r"\d+k? business customers|SMB banking", "company.customer_model=smb_mass", _DIR),
    _R(r"enterprise sales cycles|Fortune 500 (clients|customers)", "company.customer_model=b2b_enterprise", _DIR),
    _R(r"true ownership|founder(-like)? energy|zero-to-one energy", "company.product_culture_signal=true", _DIR),
    _R(r"follow established processes strictly", "company.product_culture_signal=false", _DIR),
    _R(r"sell a single desktop application", "company.platform_ecosystem=false", _DIR),
    _R(r"(is )?a leading crypto(currency)? exchange", "company.is_crypto_exchange=true", _EXP),
    _R(r"we are a cross-border payments company", "company.is_crypto_exchange=false", _EXP),
    # ---- requirements ----
    _R(r"must have \d\+? years deep [\w\- ]{0,40}expertise( and fluent \w+)?",
       "requirements.overall_transferability=non_transferable_barrier", _DIR),
    _R(r"[\w]+ experience a plus", "requirements.overall_transferability=transferable", _WEA),
]

# ---------------------------------------------------------------------------
# §7.2 round 1 — rules authored from REAL vacancy language (T5).
#
# Every pattern below was written against duty sentences mined from live
# target-population vacancies (mandate_mining.py, DEV slice only), NOT from
# the contract's own control phrases. That inversion is the whole point: the
# pre-existing rules matched the controls they were derived from and almost
# nothing in production text (median 1 observation on a ~7000-char vacancy).
#
# Precision discipline: the duty must be the CANDIDATE's. The owner-marked
# negative fixtures (mandate_negative_fixtures.json) guard against the
# company-boilerplate error these rules could otherwise reintroduce.
# ---------------------------------------------------------------------------

RULES += [
    # P&L: "You own a P&L", "owning the full P&L", "Own the Roadmap and P&L",
    # "own the outcomes of the P&L"
    _R(r"[Oo]wn(?:ing|s)?\b[\w ,'-]{0,40}\bP&L\b", "pnl_ownership=true", _DIR,
       extra_signals=("revenue_proximity=direct_pnl",)),
    _R(r"full ownership of[\w ,'-]{0,30}\bP&L\b", "pnl_ownership=true", _DIR,
       extra_signals=("revenue_proximity=direct_pnl",)),

    # Strategy: "drive strategy", "Define and drive the long-term product
    # vision, strategy", "Formulate and execute a strategic vision".
    #
    # Owner decision 2026-08-06: a FUNCTIONAL strategy is not an executive
    # product mandate — the negative fixture "Senior Director, Enterprise Risk
    # Strategy" already said so, but the round-1 rule let a free filler run up
    # to the noun, so "own the design strategy", "the product marketing
    # strategy" and "a Talent Acquisition strategy" all matched.
    #
    # Expressed as a denylist on the QUALIFIER rather than an allowlist on the
    # object: the flagship GPNI vacancy phrases it bare ("You'll drive
    # strategy, assess and integrate new financial partners"), and requiring a
    # product/business object would have dropped a role the acceptance
    # criterion requires in the top band. On a product role a bare "strategy"
    # is a product strategy by context; a qualified one names its function.
    _R(_STRATEGY_DUTY, "strategy_ownership=true", _DIR),

    # Team building: "build and lead a team of product managers",
    # "Lead, mentor, and scale a strong organization of Product Managers",
    # "hiring, managing, and developing product managers"
    _R(r"(?:[Bb]uild|[Gg]row|[Ss]cale)(?:s|ing)?\b[\w ,'-]{0,35}"
       r"\b(?:team|organi[sz]ation)\b", "team_build_mandate=true", _DIR),
    _R(r"\bhiring\b[\w ,'-]{0,30}\b(?:product managers?|PMs?|leaders?)\b",
       "team_build_mandate=true", _DIR),

    # Executive exposure: "collaborate with senior leadership",
    # "present results to senior leadership", "our C-level vision"
    _R(r"(?:[Pp]resent|[Rr]eport|[Cc]ollaborat|[Ee]ngag|[Pp]artner|[Ww]ork)"
       r"(?:s|ed|ing)?\b[\w ,'-]{0,30}\b(?:with|to)\b[\w ,'-]{0,20}"
       r"\b(?:C-level|senior leadership|executive team|executive leadership|"
       r"leadership team|ExCo)\b", "executive_exposure=true", _DIR),

    # Board exposure: "present results to senior leadership and the board"
    _R(r"(?:[Pp]resent|[Rr]eport)(?:s|ing)?\b[\w ,'-]{0,40}\bto\b"
       r"[\w ,'-]{0,30}\bthe board\b", "board_exposure=true", _DIR),

    # Org design: "Unify the Risk and Identity organizations",
    # "Lead the product organization across multiple product areas"
    _R(r"[Uu]nify(?:ing)?\b[\w ,'-]{0,35}\borgani[sz]ations?\b",
       "org_design_mandate=true", _DIR),
    _R(r"[Ll]ead(?:s|ing)? the product organi[sz]ation\b",
       "org_design_mandate=true", _DIR),

    # Scope: "Own the Card domain end-to-end" -> domain;
    # "oversees multiple product portfolios" / "across your portfolio"
    _R(r"[Oo]wn(?:s|ing)? the [\w ]{0,20}domain end.to.end",
       "scope_breadth=domain", _DIR),
    _R(r"(?:multiple product portfolios|across your portfolio)",
       "scope_breadth=portfolio", _DIR),
]


RULES += [
    # ---- revenue proximity ----
    _R(r"own revenue targets( for [\w ]{0,25})?", "revenue_proximity=direct_revenue", _DIR),
    _R(r"[Ss]upport the finance team with reporting", "revenue_proximity=support", _DIR),
    _R(r"no budget responsibility|report into [\w ]{0,30}without budget", "pnl_ownership=false", _EXP),
    # ---- cross-functional / org / team pairs ----
    _R(r"lead engineering, design and ops leaders[\w ]{0,25}", "organization.cross_functional_leadership=true", _DIR),
    _R(r"manage your PM team", "organization.cross_functional_leadership=false", _DIR),
    _R(r"lead the org\b", "organization.cross_functional_leadership=true", _DIR),
    _R(r"\bIC (position|role)\b", "organization.cross_functional_leadership=false", _DIR),
    _R(r"work within the delivery squad", "executive_exposure=false", _DIR),
    _R(r"execute the group plan", "strategy_ownership=false", _DIR),
    _R(r"headcount frozen[\w, ]{0,20}", "org_design_mandate=false", _DIR),
    _R(r"build the org\b", "org_design_mandate=true", _DIR),
    _R(r"build the team\b", "team_build_mandate=true", _DIR),
    _R(r"no hiring this year", "team_build_mandate=false", _DIR),
    _R(r"own (the )?budget\b", "organization.budget_ownership=true", _DIR),
    _R(r"finance approves all spend", "organization.budget_ownership=false", _DIR),
    # ---- phase negatives ----
    _R(r"[Oo]ptimi[sz]e existing markets", "market_entry_ownership=false", _DIR),
    _R(r"scale a fast-growing product", "turnaround_mandate=false", _DIR),
    _R(r"iterate on our mature core product", "zero_to_one_mandate=false", _DIR),
    _R(r"launch the new business line", "maintenance_only=false", _DIR),
    _R(r"maintenance duties", "expansion_mandate=false", _DIR),
    _R(r"maintain the legacy stack", "zero_to_one_mandate=false", _DIR),
    _R(r"\bdelivery role\b", "feature_delivery_only=true", _DIR),
    _R(r"own (the )?strategy\b", "feature_delivery_only=false", _DIR),
    _R(r"own the digital business", "digital_business_ownership=true", _DIR),
    _R(r"no product authority", "digital_business_ownership=false", _DIR),
    _R(r"no pricing authority", "pricing_core=false", _DIR,
       extra_signals=("monetization_core=false",)),
    # ---- product shape pairs ----
    _R(r"customer-facing rails", "platform_as_business=true", _DIR),
    _R(r"\binternal tools\b", "internal_tools_backoffice=true", _DIR),
    _R(r"external customers", "internal_tools_backoffice=false", _DIR),
    _R(r"\bfraud role\b", "risk_compliance_heavy=true", _DIR),
    _R(r"primarily growth duties", "risk_compliance_heavy=false", _DIR),
    # ---- company pairs (self-statements) ----
    _R(r"\bconsumer app\b", "company.customer_model=b2c", _DIR),
    _R(r"enterprise contracts only", "company.customer_model=b2b_enterprise", _DIR),
    _R(r"all decisions centrali[sz]ed", "company.product_culture_signal=false", _DIR),
    _R(r"single-market focus", "company.scale=local", _EXP),
    _R(r"^startup\b|we are a startup", "company.stage=seed", _EXP),
    _R(r"^exchange\b|(we are|is) (a |the )?(crypto )?exchange\b", "company.is_crypto_exchange=true", _EXP),
    _R(r"we are a bank\b", "company.is_crypto_exchange=false", _EXP),
]

# Signals whose truth depends on the sentence being about the CANDIDATE.
# Company-level prose mentioning the same words is not evidence for them.
_DUTY_SCOPED = (
    "team_build_mandate", "strategy_ownership", "org_design_mandate",
    "executive_exposure", "board_exposure", "pnl_ownership",
    "revenue_proximity", "scope_breadth", "expansion_mandate",
    "market_entry_ownership", "monetization_core", "pricing_core",
    "acquiring_core", "growth_mandate",
)


def _duty_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of sentences that state the CANDIDATE's duty.

    §7.2 round-2 fix. Rules used to regex the raw text, so company-culture
    prose matched: "we scale and solve them as a team" produced
    team_build_mandate on a Recruiter, a Data Engineer and an AI Engineer.
    Mandate patterns must only fire inside a sentence that actually assigns a
    duty to the candidate — the same discipline the mining stage already
    applies. Non-mandate signals are unaffected.
    """
    from job_intel.vacancy_understanding.semantic.runtime.mandate_mining import (
        responsibility_sentences,
    )

    spans = []
    for sent in responsibility_sentences(text):
        idx = text.find(sent[:60])
        if idx >= 0:
            spans.append((idx, idx + len(sent)))
    return spans


def _within(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b + 1 for a, b in spans)


_FACT_PREFIX = {"company.": "company.", "requirements.": "requirements.", "organization.": "organization."}


def _fact_id(leaf: str) -> str:
    for p in _FACT_PREFIX:
        if leaf.startswith(p):
            return leaf
    return f"mandate.{leaf}"


class DeterministicPhraseProvider:
    """First conformant provider: pure regex phrase rules -> observations."""

    provider_id = "deterministic-phrase"
    prompt_version = "rules-1.0.0"

    def extract_semantic_observations(self, *, title: str, text: str,
                                      structured: dict) -> list[Observation]:
        out: list[Observation] = []
        n = 0
        # Mandate patterns may only fire inside a sentence that assigns a duty
        # to the candidate (§7.2 round-2). Computed once per call.
        duty = _duty_spans(text) if text else []
        for rule in RULES:
            hay = title if rule.scope == "title" else text
            if not hay:
                continue
            m = None
            for cand in re.finditer(rule.pattern, hay):
                if (rule.scope != "title"
                        and rule.signal.startswith(_DUTY_SCOPED)
                        and not _within(cand.span(), duty)):
                    continue  # matched company prose, not a duty of the role
                m = cand
                break
            if not m:
                continue
            n += 1
            signals = (rule.signal,) + rule.extra_signals
            leaf, _ = rule.signal.split("=", 1)
            out.append(Observation(
                observation_id=f"obs_{n:03d}_{leaf.split('.')[-1]}",
                excerpt=m.group(0)[:400],
                location=rule.scope if rule.scope == "title" else "description",
                signal_type=rule.signal,
                interpretation=f"phrase rule {rule.pattern!r} matched",
                maps_to=[_fact_id(s.split('=', 1)[0]) for s in signals],
                basis=rule.basis,
            ))
            # extra signals become their own observations sharing the excerpt
            for extra in rule.extra_signals:
                n += 1
                eleaf = extra.split("=", 1)[0]
                out.append(Observation(
                    observation_id=f"obs_{n:03d}_{eleaf.split('.')[-1]}",
                    excerpt=m.group(0)[:400],
                    location=rule.scope if rule.scope == "title" else "description",
                    signal_type=extra,
                    interpretation=f"companion signal of {rule.signal}",
                    maps_to=[_fact_id(eleaf)],
                    basis=rule.basis,
                ))
        return out


class LLMProvider:
    """Extension point ONLY. Live model calls require separate owner
    approval; instantiating this provider without it must fail loudly."""

    provider_id = "llm-unapproved"
    prompt_version = "none"

    def extract_semantic_observations(self, *, title: str, text: str,
                                      structured: dict) -> list[Observation]:
        raise NotImplementedError(
            "LLM provider execution is gated behind a separate owner approval "
            "(Step 4A provider contract); use DeterministicPhraseProvider")
