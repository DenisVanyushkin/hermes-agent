from __future__ import annotations

from collections import Counter
import os
import re
from typing import Any

from .config import DEFAULT_CONFIG, load_config_bundle
from .models import Evaluation, Vacancy

POSITIVE_KEYWORDS = {
    "product ownership": "product_ownership",
    "product strategy": "product_ownership",
    "monetization": "monetization_responsibility",
    "p&l": "PnL_ownership",
    "profit and loss": "PnL_ownership",
    "b2c": "B2C_platform",
    "subscription": "subscriptions",
    "retention": "product_ownership",
    "growth": "executive_visibility",
    "fintech": "fintech_or_telecom",
    "telecom": "fintech_or_telecom",
    "transformation": "org_transformation",
    "international": "international_team",
    "remote": "remote_friendly",
    "ai": "AI_or_modern_tech",
}

NEGATIVE_KEYWORDS = {
    "outsourcing": "outsourcing_company",
    "outstaffing": "outsourcing_company",
    "delivery": "delivery_only",
    "ticket processing": "delivery_only",
    "project manager": "pure_project_management",
    "scrum master": "pure_project_management",
    "bureaucracy": "enterprise_bureaucracy",
    "support operations": "delivery_only",
    "implementation partner": "outsourcing_company",
    "low autonomy": "low_autonomy",
    "weak product": "weak_product_culture",
}

REJECT_GEOS = {"russia", "belarus", "iran", "north korea", "syria", "crimea", "donetsk", "luhansk"}
REJECT_TITLES = {"scrum master", "project manager", "delivery manager", "product owner", "implementation manager"}

# V2: growth signal must be product-growth ownership, not generic growth/perf/BD.
GROWTH_PRODUCT_OWNERSHIP_TOKENS = (
    "growth product",
    "lifecycle",
    "activation",
    "retention",
    "engagement",
    "product-led growth",
    "plg",
    "monetization",
    "pricing",
    "subscription",
    "subscriptions",
    "marketplace",
    "cvm",
)
GROWTH_DISQUALIFIERS = (
    "performance",
    "analytics",
    "performance marketing",
    "marketing",
    "sales",
    "business development",
    "bd",
)

# V2: penalize non-product functions only when product-domain is absent.
NON_PRODUCT_FUNCTION_TOKENS = (
    "head of sales",
    "sales",
    "marketing",
    "cmo",
    "finance",
    "head of finance",
    "finance director",
    "business development",
    "bd",
    "partnerships",
    "account manager",
    "key account",
)

PRODUCT_DOMAIN_TOKENS = (
    "product",
    "platform",
    "ecosystem",
    "monetization",
    "growth product",
    "digital product",
    "digital products",
    "product strategy",
    "product management",
    "product manager",
)

TITLE_FUNCTION_BLOCKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "product_design_function",
        (
            r"\bhead of product design\b",
            r"\bdirector of product design\b",
            r"\bhead,\s*product design\b",
            r"\bdirector,\s*product design\b",
            r"\bvp product design\b",
            r"\bvp,\s*product design\b",
            r"\bvice president of product design\b",
            r"\bvice president,\s*product design\b",
            r"\bproduct design lead\b",
            r"\bproduct design manager\b",
            r"\bproduct designer\b",
        ),
    ),
    (
        "product_marketing_function",
        (
            r"\bhead of product marketing\b",
            r"\bdirector of product marketing\b",
            r"\bhead,\s*product marketing\b",
            r"\bdirector,\s*product marketing\b",
            r"\bvp product marketing\b",
            r"\bvp,\s*product marketing\b",
            r"\bvice president of product marketing\b",
            r"\bvice president,\s*product marketing\b",
            r"\bproduct marketing lead\b",
            r"\bproduct marketing manager\b",
            r"\bteam lead / head of product marketing\b",
            r"\bteam lead and head of product marketing\b",
        ),
    ),
    (
        "product_legal_function",
        (
            r"\bproduct legal\b",
            r"\bdirector,\s*product legal\b",
            r"\bproduct counsel\b",
            r"\bdirector,\s*product counsel\b",
            r"\bproduct lawyer\b",
        ),
    ),
    (
        "product_finance_function",
        (
            r"\bproduct finance\b",
            r"\bdirector,\s*product finance\b",
            r"\bproduct finance manager\b",
            r"\bproduct finance lead\b",
        ),
    ),
    (
        "product_analytics_ic_function",
        (
            r"\bproduct analyst\b",
            r"\bproduct analytics manager\b",
            r"\bproduct analytics analyst\b",
            r"\bproduct analytics specialist\b",
            r"\bproduct analytics associate\b",
        ),
    ),
    (
        "product_operations_non_ownership",
        (
            r"\bproduct operations manager\b",
            r"\bproduct operations specialist\b",
            r"\bproduct operations analyst\b",
            r"\bproduct operations coordinator\b",
            r"\bproduct ops manager\b",
            r"\bproduct ops specialist\b",
            r"\bproduct ops analyst\b",
            r"\bproduct ops coordinator\b",
        ),
    ),
)

TARGET_PRODUCT_LEADERSHIP_PATTERNS: tuple[str, ...] = (
    r"\bchief product officer\b",
    r"\bcpo\b",
    r"\bvp\b(?:[\s,/-]+.*)?\bproduct\b",
    r"\bvice president\b.*\bproduct\b",
    r"\bhead of\b.*\bproduct\b",
    r"\bdirector\b.*\bproduct\b",
    r"\bproduct director\b",
    r"\bgm\b.*\bproduct\b",
    r"\bgeneral manager\b.*\bproduct\b",
    r"\bproduct lead\b",
    r"\bhead of platform\b",
    r"\bhead of monetization\b",
    r"\bhead of ecosystem\b",
    r"\bhead of digital products?\b",
    r"\bhead of growth product\b",
    r"\bgrowth product lead\b",
)

PRODUCT_OPS_EXECUTIVE_AMBIGUOUS_PATTERNS: tuple[str, ...] = (
    r"\bhead of product operations\b",
    r"\bdirector of product operations\b",
    r"\bvp product operations\b",
    r"\bvice president of product operations\b",
    r"\bhead of product ops\b",
    r"\bdirector of product ops\b",
    r"\bvp product ops\b",
    r"\bvice president of product ops\b",
)


def _target_company_names() -> set[str]:
    cfg = _cfg().get("target_companies") or {}
    names: set[str] = set()
    for group in cfg.values():
        if isinstance(group, dict):
            iterables = group.values()
        elif isinstance(group, list):
            iterables = [group]
        else:
            continue
        for item_group in iterables:
            for item in item_group or []:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip().lower()
                    if name:
                        names.add(name)
    return names


def _company_signals(vacancy: Vacancy) -> dict[str, Any]:
    raw = vacancy.metadata if isinstance(vacancy.metadata, dict) else {}
    return raw


def _target_company_names() -> set[str]:
    cfg = _cfg().get("target_companies") or {}
    names: set[str] = set()
    for group in cfg.values():
        if isinstance(group, dict):
            iterables = group.values()
        elif isinstance(group, list):
            iterables = [group]
        else:
            continue
        for item_group in iterables:
            for item in item_group or []:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip().lower()
                    if name:
                        names.add(name)
    return names


def _company_signals(vacancy: Vacancy) -> dict[str, Any]:
    raw = vacancy.metadata if isinstance(vacancy.metadata, dict) else {}
    return raw


def _cfg() -> dict[str, Any]:
    return load_config_bundle() or DEFAULT_CONFIG


def _text(vacancy: Vacancy) -> str:
    return " ".join([vacancy.company, vacancy.title, vacancy.location, vacancy.description]).lower()


def _has_any(text: str, terms: list[str] | set[str]) -> bool:
    return any(term in text for term in terms)

def _product_domain(text: str) -> bool:
    return any(tok in text for tok in PRODUCT_DOMAIN_TOKENS)

def _executive_seniority_in_title(title: str) -> bool:
    t = (title or "").lower()
    return any(tok in t for tok in ("chief", "cpo", "vp", "vice president", "director", "head of", "gm", "general manager", "lead"))


def _title_function_blocker(title: str) -> str | None:
    title_l = re.sub(r"\s+", " ", (title or "").strip().lower())
    for blocker, patterns in TITLE_FUNCTION_BLOCKERS:
        if any(re.search(pattern, title_l, re.I) for pattern in patterns):
            return blocker
    return None


def _match_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _is_target_product_leadership_title(title: str) -> bool:
    return _match_any_pattern(title, TARGET_PRODUCT_LEADERSHIP_PATTERNS)


def _is_executive_product_ops_title(title: str) -> bool:
    return _match_any_pattern(title, PRODUCT_OPS_EXECUTIVE_AMBIGUOUS_PATTERNS)


def _blocked_title_function_evaluation(vacancy: Vacancy, blocker: str) -> Evaluation:
    return Evaluation(
        score=0,
        tier="reject",
        recommendation="reject",
        salary_tier=salary_tier_for(vacancy, 0),
        concerns=[blocker, "adjacent non-target product function in title"],
        reasons=[blocker, "adjacent_non_target_function", "title function outside target product leadership thesis"],
        raw_breakdown={blocker: -100, "adjacent_non_target_function": -100},
    )


_ROLE_CLASSIFICATION_PATTERNS: list[tuple[str, tuple[str, ...], bool]] = [
    ("chief_product_officer", (r"\bchief product officer\b", r"\bcpo\b"), True),
    ("vp_product", (r"\bvice president\b.*\bproduct\b", r"\bvp\b(?:[\s,/-]+(?:product|product & growth|product growth|growth product|products))?\b"), True),
    ("director_product", (r"\bdirector\b.*\bproduct\b", r"\bproduct director\b"), True),
    ("head_product", (r"\bhead of\b.*\bproduct\b", r"\bhead\b.*\bproduct\b"), True),
    ("product_strategy_lead", (r"\bproduct strategy\b", r"\bstrategy lead\b.*\bproduct\b"), True),
    ("growth_product_lead", (r"\bgrowth\b.*\bproduct\b", r"\bproduct growth\b"), True),
    ("monetization_product_lead", (r"\bmonetization\b.*\bproduct\b", r"\bproduct monetization\b"), True),
    ("consumer_product_lead", (r"\bconsumer\b.*\bproduct\b", r"\bb2c\b.*\bproduct\b"), True),
    ("platform_ecosystem_product_lead", (r"\bplatform\b.*\bproduct\b", r"\becosystem\b.*\bproduct\b", r"\bdigital products\b", r"\bsuperapp\b"), True),
    ("product_lead", (r"\bproduct lead\b", r"\blead product\b", r"\bproduct leader\b"), True),
    ("generic_product_manager", (r"\bproduct manager\b", r"\bsenior product manager\b", r"\bassociate product manager\b"), False),
]


def classify_vacancy(vacancy: Vacancy) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", vacancy.title or "").strip()
    text = _text(vacancy)
    title_lower = title.lower()
    text_lower = text.lower()

    role_classification = "other"
    executive_detected = False
    matched_signals: list[str] = []

    # Hard blocklist: sales/marketing/CS "Executive" roles must NOT be treated as exec product roles.
    if re.search(r"\b(account|sales|customer success|marketing|business development)\s+executive\b", title_lower):
        return {
            "raw_title": vacancy.title,
            "normalized_title": title,
            "classification": "non_product_executive_title",
            "executive_detected": False,
            "matched_signals": ["non_product_exec_title_blocked"],
        }

    blocker = _title_function_blocker(title)
    if blocker:
        return {
            "raw_title": vacancy.title,
            "normalized_title": title,
            "classification": blocker,
            "executive_detected": False,
            "matched_signals": [blocker],
        }

    # High-precision pattern-based classification.
    for name, patterns, is_leadership_signal in _ROLE_CLASSIFICATION_PATTERNS:
        if any(re.search(pattern, title_lower) or re.search(pattern, text_lower) for pattern in patterns):
            role_classification = name
            executive_detected = is_leadership_signal
            break

    if role_classification == "other" and _is_target_product_leadership_title(title_lower):
        role_classification = "target_product_leadership"
        executive_detected = True
        matched_signals.append("target_product_leadership_title")

    # Executive product leadership requires BOTH product-domain signal AND seniority signal.
    if role_classification == "other":
        product_domain = any(
            token in title_lower or token in text_lower
            for token in (
                "product",
                "platform",
                "ecosystem",
                "monetization",
                "growth product",
                "consumer product",
                "digital product",
                "digital products",
                "product strategy",
                "product management",
                "product manager",
            )
        )

        seniority_signal = any(
            re.search(pat, title_lower)
            for pat in (
                r"\bchief product officer\b",
                r"\bcpo\b",
                r"\bvp\b\s*product\b",
                r"\bvice president\b.*\bproduct\b",
                r"\bhead of\b.*\bproduct\b",
                r"\bproduct\s+director\b",
                r"\bdirector\b.*\bproduct\b",
                r"\bgm\b\s*product\b",
                r"\bgeneral manager\b.*\bproduct\b",
                r"\bproduct\s+lead\b",
            )
        )

        if product_domain and seniority_signal:
            role_classification = "executive_product_leadership"
            executive_detected = True
            matched_signals.append("product_domain")
            matched_signals.append("executive_seniority")

    # Transparency-only signals.
    if any(token in text_lower for token in ("monetization", "growth", "product strategy", "platform", "ecosystem", "digital products", "b2c", "consumer", "subscription", "marketplace")):
        matched_signals.append("strategic_product_scope")
    if any(token in text_lower for token in ("vp", "vice president", "director", "head of", "chief", "cpo", "gm", "general manager", "lead")):
        matched_signals.append("leadership_scope")

    return {
        "raw_title": vacancy.title,
        "normalized_title": title,
        "classification": role_classification,
        "executive_detected": executive_detected,
        "matched_signals": matched_signals,
    }


def tier_for_score(score: int) -> str:
    thresholds = _cfg()["scoring"]["thresholds"]
    if score >= thresholds["exceptional_fit"]:
        return "exceptional_fit"
    if score >= thresholds["strong_fit"]:
        return "strong_fit"
    if score >= thresholds["possible_fit"]:
        return "possible_fit"
    if score >= thresholds["weak_fit"]:
        return "weak_fit"
    return "reject"


def salary_tier_for(vacancy: Vacancy, score: int) -> str:
    title = vacancy.title.lower()
    if any(term in title for term in ["chief", "cpo", "vp", "vice president"]):
        return "executive"
    if any(term in title for term in ["director", "head of"]):
        return "senior_leadership"
    if score >= 75:
        return "senior_leadership"
    return "market"


def score_vacancy_v1(vacancy: Vacancy) -> Evaluation:
    cfg = _cfg()["scoring"]
    text = _text(vacancy)
    classification = classify_vacancy(vacancy)
    blocker = _title_function_blocker(vacancy.title)
    if blocker:
        return _blocked_title_function_evaluation(vacancy, blocker)
    score = 0
    matched: list[str] = []
    concerns: list[str] = []
    reasons: list[str] = []
    breakdown: Counter[str] = Counter()

    title = vacancy.title.lower()
    if classification["executive_detected"]:
        score += 20
        breakdown["executive_visibility"] += 20
        matched.append("executive-level leadership")
    if classification["classification"] in {
        "product_strategy_lead",
        "growth_product_lead",
        "monetization_product_lead",
        "platform_ecosystem_product_lead",
        "consumer_product_lead",
        "product_lead",
        "product_strategy_and_growth",
    }:
        score += 12
        breakdown["growth_signal"] += 12
        matched.append("growth/strategy/product leadership")
    if _has_any(text, ["product strategy", "product ownership"]):
        score += 20
        breakdown["product_ownership"] += 20
        matched.append("product ownership")
    if "monetization" in text:
        score += 25
        breakdown["monetization_responsibility"] += 25
        matched.append("monetization")
    if "p&l" in text or "profit and loss" in text:
        score += 25
        breakdown["PnL_ownership"] += 25
        matched.append("P&L ownership")
    if _has_any(text, ["b2c", "consumer", "subscription", "ecosystem", "platform"]):
        score += 15
        breakdown["B2C_platform"] += 15
        matched.append("B2C/platform environment")
    if _has_any(text, ["mobile", "app"]):
        score += 10
        breakdown["mobile_product"] += 10
        matched.append("mobile product")
    if _has_any(text, ["fintech", "telecom"]):
        score += 15
        breakdown["fintech_or_telecom"] += 15
        matched.append("telecom/fintech adjacency")
    if _has_any(text, ["transformation", "turnaround", "restructure"]):
        score += 20
        breakdown["org_transformation"] += 20
        matched.append("organizational transformation")
    if _has_any(text, ["international", "global", "cross-functional"]):
        score += 10
        breakdown["international_team"] += 10
        matched.append("international / cross-functional scope")
    if "remote" in text:
        score += 5
        breakdown["remote_friendly"] += 5
        matched.append("remote friendly")
    if "ai" in text or "machine learning" in text:
        score += 10
        breakdown["AI_or_modern_tech"] += 10
        matched.append("AI / modern tech")

    raw_metadata = vacancy.metadata if isinstance(vacancy.metadata, dict) else {}
    company_signals = _company_signals(vacancy)
    target_company_names = _target_company_names()
    if raw_metadata.get("target_company") or vacancy.company.lower() in target_company_names:
        score += 18
        breakdown["target_company"] += 18
        matched.append("target company")
    if company_signals.get("signals"):
        signal_count = len(company_signals.get("signals") or [])
        bonus = min(10, signal_count * 3)
        score += bonus
        breakdown["career_page_signal"] += bonus
        if bonus:
            matched.append("career page activity")
    if company_signals.get("opening_count"):
        openings = int(company_signals.get("opening_count") or 0)
        if openings:
            bonus = min(8, openings)
            score += bonus
            breakdown["career_page_signal"] += bonus
            matched.append("open leadership openings")

    for keyword, signal in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            penalty = abs(cfg["negative_signals"][signal])
            score -= penalty
            breakdown[signal] -= penalty
            concerns.append(keyword)
            reasons.append(f"negative signal: {keyword}")

    if _has_any(text, REJECT_TITLES):
        score -= 30
        concerns.append("delivery/PM title")
        reasons.append("role title is outside executive product path")

    if vacancy.source in {"remoteok", "remotive"} and not any(term in title for term in ["vp", "vice president", "director", "head of", "chief", "cpo", "gm", "general manager"]):
        score -= 25
        breakdown["generic_remote_noise"] -= 25
        concerns.append("generic remote noise")
        reasons.append("remote board result without executive title signal")

    if _has_any(text, REJECT_GEOS):
        score -= 60
        concerns.append("restricted geography")
        reasons.append("restricted geography or sanctions risk")

    if "russia" in text or "belarus" in text:
        score -= 60
        concerns.append("sanctions risk")

    tier = tier_for_score(score)

    # Review-mode buckets (user-facing). Near-miss must be visible in daily report, but must NOT alert.
    if score >= 75:
        recommendation = "strong_fit"
    elif score >= 60:
        recommendation = "potential_fit"
    elif score >= 40:
        recommendation = "near_miss"
    else:
        recommendation = "reject"
    if tier == "exceptional_fit":
        reasons.append("high-signal executive match")
    elif tier == "strong_fit":
        reasons.append("good strategic fit")
    elif tier == "possible_fit":
        reasons.append("some fit, manual review recommended")
    else:
        reasons.append("insufficient fit")

    return Evaluation(
        score=max(-100, min(100, score)),
        tier=tier,
        recommendation=recommendation,
        salary_tier=salary_tier_for(vacancy, score),
        matched_signals=matched,
        concerns=concerns,
        reasons=reasons,
        raw_breakdown=dict(breakdown),
    )


def score_vacancy_v2(vacancy: Vacancy) -> Evaluation:
    """Scoring V2 (experimental).

    Core assumptions:
    - Unknown fields do not subtract.
    - Company intelligence is neutral until implemented (company_score/hiring_likelihood not used).
    - P&L is a bonus only (not mandatory).
    - executive_visibility is gated by product-domain.
    - growth_signal is high only for product-growth ownership (not perf/analytics/marketing/sales/BD).
    """

    cfg = _cfg()["scoring"]
    text = _text(vacancy)
    classification = classify_vacancy(vacancy)
    blocker = _title_function_blocker(vacancy.title)
    if blocker:
        return _blocked_title_function_evaluation(vacancy, blocker)

    score = 0
    matched: list[str] = []
    concerns: list[str] = []
    reasons: list[str] = []
    breakdown: Counter[str] = Counter()

    title_lower = (vacancy.title or "").lower()
    product_domain = _product_domain(text)
    executive_seniority = _executive_seniority_in_title(vacancy.title)

    # V2 weights (from scoring-v2-simulation.md; keep here for explicit versioning).
    W_EXEC_VIS = 15
    W_HEAD_PRODUCT_TITLE = 10
    W_PRODUCT_LEAD_TITLE = 10
    W_FINTECH = 18
    W_B2C_PLATFORM = 10
    W_AI = 12
    W_REMOTE = 5
    W_GROWTH = 20
    W_PRODUCT_OWNERSHIP = 25
    W_MONETIZATION = 30
    W_PNL_BONUS = 10
    W_NON_PRODUCT_PENALTY = -35

    # Executive visibility: only if both seniority and product-domain.
    if executive_seniority and product_domain:
        score += W_EXEC_VIS
        breakdown["executive_visibility"] += W_EXEC_VIS
        matched.append("executive visibility (product-domain)")

    # Title boosts: help thesis roles reach review buckets without relying on unknown fields.
    if "head of product" in title_lower or ("head" in title_lower and "product" in title_lower):
        score += W_HEAD_PRODUCT_TITLE
        breakdown["head_of_product_title_bonus"] += W_HEAD_PRODUCT_TITLE
        matched.append("head of product (title)")
    if "product lead" in title_lower or ("lead" in title_lower and "product" in title_lower):
        score += W_PRODUCT_LEAD_TITLE
        breakdown["product_lead_title_bonus"] += W_PRODUCT_LEAD_TITLE
        matched.append("product lead (title)")

    # Industry adjacency.
    if _has_any(text, ["fintech", "telecom"]):
        score += W_FINTECH
        breakdown["fintech_or_telecom"] += W_FINTECH
        matched.append("telecom/fintech adjacency")

    # Environment: keep but reduce.
    if _has_any(text, ["b2c", "consumer", "subscription", "subscriptions", "ecosystem", "platform", "marketplace"]):
        score += W_B2C_PLATFORM
        breakdown["B2C_platform"] += W_B2C_PLATFORM
        matched.append("B2C/platform environment")

    if "remote" in text:
        score += W_REMOTE
        breakdown["remote_friendly"] += W_REMOTE
        matched.append("remote friendly")

    if "ai" in text or "machine learning" in text:
        score += W_AI
        breakdown["AI_or_modern_tech"] += W_AI
        matched.append("AI / modern tech")

    # Ownership / monetization.
    if _has_any(text, ["product strategy", "product ownership", "roadmap", "product vision"]):
        score += W_PRODUCT_OWNERSHIP
        breakdown["product_ownership"] += W_PRODUCT_OWNERSHIP
        matched.append("product ownership / strategy")

    # Monetization is high-weight but should still be in product context; require product-domain.
    if product_domain and _has_any(text, ["monetization", "pricing"]):
        score += W_MONETIZATION
        breakdown["monetization_responsibility"] += W_MONETIZATION
        matched.append("monetization responsibility")

    # P&L is bonus only.
    if "p&l" in text or "profit and loss" in text:
        score += W_PNL_BONUS
        breakdown["PnL_ownership"] += W_PNL_BONUS
        matched.append("P&L (bonus)")

    # Growth signal: only product-growth ownership; exclude perf/analytics/marketing/sales/BD.
    growth_ownership = any(tok in text for tok in GROWTH_PRODUCT_OWNERSHIP_TOKENS) or ("growth" in text and "product" in text)
    growth_disq = any(tok in text for tok in GROWTH_DISQUALIFIERS)
    if growth_ownership and not growth_disq:
        score += W_GROWTH
        breakdown["growth_signal"] += W_GROWTH
        matched.append("product-growth ownership")

    # Target company / company signals remain as-is (explicit evidence only).
    raw_metadata = vacancy.metadata if isinstance(vacancy.metadata, dict) else {}
    company_signals = _company_signals(vacancy)
    target_company_names = _target_company_names()
    if raw_metadata.get("target_company") or vacancy.company.lower() in target_company_names:
        score += 18
        breakdown["target_company"] += 18
        matched.append("target company")
    if company_signals.get("signals"):
        signal_count = len(company_signals.get("signals") or [])
        bonus = min(10, signal_count * 3)
        score += bonus
        breakdown["career_page_signal"] += bonus
        if bonus:
            matched.append("career page activity")
    if company_signals.get("opening_count"):
        openings = int(company_signals.get("opening_count") or 0)
        if openings:
            bonus = min(8, openings)
            score += bonus
            breakdown["career_page_signal"] += bonus
            matched.append("open leadership openings")

    # Explicit negatives (keep): these are real negative evidence, not unknown.
    for keyword, signal in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            penalty = abs(cfg["negative_signals"][signal])
            score -= penalty
            breakdown[signal] -= penalty
            concerns.append(keyword)
            reasons.append(f"negative signal: {keyword}")

    if _has_any(text, REJECT_TITLES):
        score -= 30
        concerns.append("delivery/PM title")
        reasons.append("role title is outside executive product path")

    # Non-product function penalty (only when product-domain is absent).
    if (not product_domain) and any(tok in text for tok in NON_PRODUCT_FUNCTION_TOKENS):
        score += W_NON_PRODUCT_PENALTY
        breakdown["non_product_function_penalty"] += W_NON_PRODUCT_PENALTY
        concerns.append("non-product function")
        reasons.append("non-product function without product-domain evidence")

    if vacancy.source in {"remoteok", "remotive"} and not any(term in title_lower for term in ["vp", "vice president", "director", "head of", "chief", "cpo", "gm", "general manager"]):
        score -= 25
        breakdown["generic_remote_noise"] -= 25
        concerns.append("generic remote noise")
        reasons.append("remote board result without executive title signal")

    if _has_any(text, REJECT_GEOS):
        score -= 60
        concerns.append("restricted geography")
        reasons.append("restricted geography or sanctions risk")

    if "russia" in text or "belarus" in text:
        score -= 60
        concerns.append("sanctions risk")

    tier = tier_for_score(score)

    # Recommendation buckets: unchanged thresholds.
    if score >= 75:
        recommendation = "strong_fit"
    elif score >= 60:
        recommendation = "potential_fit"
    elif score >= 40:
        recommendation = "near_miss"
    else:
        recommendation = "reject"

    if tier == "exceptional_fit":
        reasons.append("high-signal executive match")
    elif tier == "strong_fit":
        reasons.append("good strategic fit")
    elif tier == "possible_fit":
        reasons.append("some fit, manual review recommended")
    else:
        reasons.append("insufficient fit")

    return Evaluation(
        score=max(-100, min(100, score)),
        tier=tier,
        recommendation=recommendation,
        salary_tier=salary_tier_for(vacancy, score),
        matched_signals=matched,
        concerns=concerns,
        reasons=reasons,
        raw_breakdown=dict(breakdown),
    )


def _evaluation_from_v3_shadow(vacancy: Vacancy, payload: dict[str, Any]) -> Evaluation:
    recommendation = str(payload.get("recommendation") or "reject")
    score = int(payload.get("score") or 0)
    if recommendation == "strong_fit":
        tier = "strong_fit"
    elif recommendation in {"needs_review", "near_miss"}:
        tier = "possible_fit"
    else:
        tier = "reject"

    gates = payload.get("gates") or {}
    concerns = [
        f"{gate}:{details.get('reason')}"
        for gate, details in gates.items()
        if str(details.get("status") or "") == "FAIL"
    ]
    matched = [
        f"{gate}:{details.get('reason')}"
        for gate, details in gates.items()
        if str(details.get("status") or "") == "PASS"
    ]
    reasons = list(payload.get("reasons") or [])
    breakdown = dict(payload.get("raw_breakdown") or {})

    return Evaluation(
        score=max(-100, min(100, score)),
        tier=tier,
        recommendation=recommendation,
        salary_tier=salary_tier_for(vacancy, score),
        matched_signals=matched,
        concerns=concerns,
        reasons=reasons,
        raw_breakdown=breakdown,
    )


def score_vacancy(vacancy: Vacancy) -> Evaluation:
    """Versioned scorer wrapper.

    Environment:
    - SCORING_MODEL_VERSION=v1|v2|v3 (default v1)
    """
    version = (os.getenv("SCORING_MODEL_VERSION", "v1") or "v1").strip().lower()
    return score_vacancy_with_version(vacancy, version)


def score_vacancy_with_version(vacancy: Vacancy, version: str) -> Evaluation:
    v = (version or "v1").strip().lower()
    if v == "v3":
        return _evaluation_from_v3_shadow(vacancy, score_vacancy_v3_shadow(vacancy))
    if v == "v2":
        return score_vacancy_v2(vacancy)
    return score_vacancy_v1(vacancy)


def score_vacancy_v3_shadow(vacancy: Vacancy) -> dict[str, Any]:
    """Shadow-only Scoring V3.

    Important:
    - This function does not drive production recommendation decisions yet.
    - It returns an explicit gate trace with PASS/FAIL/UNKNOWN states.
    """
    text = _text(vacancy)
    title = (vacancy.title or "").strip()
    title_l = title.lower()
    blocker = _title_function_blocker(title)

    non_product_patterns = (
        r"\bvp\b.*\b(data|revenue|sales|operations)\b",
        r"\bvice president\b.*\b(data|revenue|sales|operations)\b",
        r"\brevenue strategy\b",
        r"\baccount management\b",
        r"\bcustomer success\b",
        r"\bsite reliability engineer\b",
        r"\bsoftware engineer\b",
        r"\bdata scientist\b",
    )
    core_product_patterns = TARGET_PRODUCT_LEADERSHIP_PATTERNS
    adjacent_patterns = PRODUCT_OPS_EXECUTIVE_AMBIGUOUS_PATTERNS

    gates: dict[str, dict[str, str]] = {}

    # G0: Core Product Function Required
    if blocker:
        g0 = ("FAIL", blocker)
        function_class = "adjacent_non_target_function"
    elif _match_any_pattern(title_l, non_product_patterns):
        g0 = ("FAIL", "non-product function")
        function_class = "non_product"
    elif _match_any_pattern(title_l, core_product_patterns):
        g0 = ("PASS", "core product title")
        function_class = "core_product"
    elif _match_any_pattern(title_l, adjacent_patterns):
        g0 = ("UNKNOWN", "product operations leadership ambiguous")
        function_class = "product_ops_executive_ambiguous"
    else:
        g0 = ("UNKNOWN", "function ambiguous")
        function_class = "unknown"
    gates["G0"] = {"status": g0[0], "reason": g0[1]}

    # G1: Product Leadership Required
    if _is_target_product_leadership_title(title_l):
        g1 = ("PASS", "explicit target product leadership title")
    elif _is_executive_product_ops_title(title_l):
        g1 = ("PASS", "executive product operations title")
    elif re.search(r"\b(chief|vp|vice president|head of|director|gm|general manager)\b", title_l) and "product" not in title_l:
        g1 = ("FAIL", "leadership without product function")
    elif re.search(r"\b(product lead|group product manager)\b", title_l):
        g1 = ("UNKNOWN", "leadership track possible, scope unclear")
    else:
        g1 = ("FAIL", "no product leadership signal")
    gates["G1"] = {"status": g1[0], "reason": g1[1]}

    # G2: Senior Leadership Scope Required
    if re.search(r"\b(chief|cpo|vp|vice president|head of|director|gm|general manager)\b", title_l):
        g2 = ("PASS", "seniority present")
    elif re.search(r"\b(product lead|group product manager)\b", title_l):
        g2 = ("UNKNOWN", "seniority may depend on company leveling")
    elif re.search(r"\b(staff|senior|principal)\b", title_l):
        g2 = ("FAIL", "individual contributor seniority")
    else:
        g2 = ("FAIL", "seniority not evident")
    gates["G2"] = {"status": g2[0], "reason": g2[1]}

    # G3: Executive Product Ownership Required
    ownership_terms = ("product ownership", "roadmap", "product strategy", "monetization", "p&l", "profit and loss")
    if "product" in text and any(t in text for t in ownership_terms):
        g3 = ("PASS", "product ownership/strategy evidence")
    elif "product" in text and re.search(r"\b(director|head|vp|chief|gm)\b", title_l):
        g3 = ("UNKNOWN", "product role present, ownership explicitness unclear")
    elif _is_target_product_leadership_title(title_l) or _is_executive_product_ops_title(title_l):
        g3 = ("UNKNOWN", "target leadership title present, ownership explicitness unclear")
    elif "product" not in text:
        g3 = ("FAIL", "no product ownership domain")
    else:
        g3 = ("FAIL", "ownership not executive-product")
    gates["G3"] = {"status": g3[0], "reason": g3[1]}

    gate_states = [gates[k]["status"] for k in ("G0", "G1", "G2", "G3")]

    title_l = title.lower()
    needs_review_title = bool(
        re.search(
            r"\b(product lead|group product manager|principal product manager|senior product manager|staff product manager)\b",
            title_l,
            re.I,
        )
    )

    # Company context: ranking-only signal for borderline product-management roles.
    # Never bypasses hard gate failure.
    company_context = 0
    target_company_names = _target_company_names()
    company_lower = (vacancy.company or "").strip().lower()
    md = vacancy.metadata if isinstance(vacancy.metadata, dict) else {}
    if company_lower and company_lower in target_company_names:
        company_context += 10
    if md.get("target_company"):
        company_context += 10
    opening_raw = md.get("opening_count")
    opening_count = int(opening_raw or 0) if isinstance(opening_raw, (int, float)) else 0
    if opening_count > 0:
        company_context += min(10, opening_count)

    if "FAIL" in gate_states:
        recommendation = "reject"
        score = 10
    elif "UNKNOWN" in gate_states:
        if needs_review_title:
            recommendation = "needs_review"
            score = 60 + min(15, company_context)
        else:
            recommendation = "near_miss"
            score = 55 + min(10, company_context)
    else:
        recommendation = "strong_fit"
        score = 85 + min(10, company_context)

    breakdown = {"hard_gate_model_v3": score, "company_context": company_context}
    reasons = [f"{k}:{v['status']}:{v['reason']}" for k, v in gates.items()]
    if blocker:
        reasons.insert(0, blocker)
        reasons.insert(1, "adjacent_non_target_function")

    return {
        "score": score,
        "recommendation": recommendation,
        "gates": gates,
        "function_class": function_class,
        "raw_breakdown": breakdown,
        "reasons": reasons,
    }
