from __future__ import annotations

from collections import Counter
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

    for name, patterns, is_leadership_signal in _ROLE_CLASSIFICATION_PATTERNS:
        if any(re.search(pattern, title_lower) or re.search(pattern, text_lower) for pattern in patterns):
            role_classification = name
            executive_detected = is_leadership_signal
            break

    if role_classification == "other":
        # Guardrail: leadership tokens alone are too noisy (e.g. "Head of Finance").
        # Require a product-domain signal in title or surrounding text.
        product_domain = any(
            token in title_lower or token in text_lower
            for token in (
                "product",
                "growth",
                "monetization",
                "strategy",
                "platform",
                "ecosystem",
                "subscription",
                "marketplace",
                "superapp",
                "digital products",
            )
        )
        if product_domain and any(token in title_lower for token in ("vp", "vice president", "director", "head of", "chief", "cpo", "gm", "general manager")):
            role_classification = "executive_product_leadership"
            executive_detected = True
        elif _has_any(text_lower, ["product strategy", "monetization", "growth", "platform", "ecosystem", "digital products", "superapp"]):
            role_classification = "product_strategy_and_growth"
            executive_detected = any(token in title_lower for token in ("lead", "director", "head", "vp", "chief", "gm"))

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


def score_vacancy(vacancy: Vacancy) -> Evaluation:
    cfg = _cfg()["scoring"]
    text = _text(vacancy)
    classification = classify_vacancy(vacancy)
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
    recommendation = "reject" if tier in {"reject", "weak_fit"} else tier
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
