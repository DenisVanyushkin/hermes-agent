from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
SEED_DIR = PACKAGE_DIR / "seed"

DEFAULT_CONFIG = {
    "candidate": {
        "name": "Denis Vanyushkin",
        "positioning": {
            "title": "Product & Business Transformation Executive",
            "summary": (
                "Senior product and monetization leader with 15+ years of experience in telecom, "
                "fintech, digital ecosystems, B2C platforms, and organizational transformation."
            ),
        },
        "seniority": {"level": ["VP", "Director", "C-level"]},
        "industries": {
            "preferred": [
                "telecom",
                "fintech",
                "SaaS",
                "AI products",
                "subscription platforms",
                "mobile applications",
                "digital ecosystems",
            ]
        },
        "geography": {"preferred": ["remote", "Europe", "UAE", "Singapore"], "acceptable": ["relocation"]},
        "work_preferences": {"remote_first": True, "international_environment": True, "english_first": "preferred"},
        "compensation": {"requires_executive_level": True, "equity_preferred": True},
        "avoid": ["outsourcing companies", "feature factories", "low-autonomy environments", "delivery-only roles", "heavy bureaucracy", "non-product organizations"],
    },
    "search_criteria": {
        "role_titles": {
            "high_priority": [
                "VP of Product",
                "Head of Product",
                "Chief Product Officer",
                "Director of Product",
                "Product & Growth Director",
                "Product Transformation Lead",
                "Director of Digital Products",
                "GM Digital",
                "Head of Monetization",
            ]
        },
        "business_models": {"preferred": ["B2C", "subscriptions", "mobile-first", "ecosystems", "platforms", "fintech", "SaaS"]},
    },
    "scoring": {
        "positive_signals": {
            "product_ownership": 20,
            "monetization_responsibility": 25,
            "PnL_ownership": 25,
            "B2C_platform": 20,
            "mobile_product": 15,
            "subscriptions": 10,
            "fintech_or_telecom": 15,
            "org_transformation": 20,
            "scaling_product_org": 15,
            "international_team": 10,
            "remote_friendly": 5,
            "executive_visibility": 15,
            "AI_or_modern_tech": 10,
            "target_company": 18,
            "career_page_signal": 10,
            "growth_signal": 8,
        },
        "negative_signals": {
            "outsourcing_company": -40,
            "delivery_only": -30,
            "no_strategy": -25,
            "low_autonomy": -20,
            "pure_project_management": -30,
            "enterprise_bureaucracy": -15,
            "weak_product_culture": -20,
            "generic_remote_noise": -25,
            "unclear_ownership": -15,
        },
        "thresholds": {"exceptional_fit": 90, "strong_fit": 75, "possible_fit": 60, "weak_fit": 40, "reject_below": 40},
    },
    "deduplication": {
        "secondary_similarity": {"description_similarity_threshold": 0.82},
        "repost_detection": {"enabled": True, "repost_window_days": 45},
    },
    "runtime": {
        "scheduler": {"search_frequency": "0 9 * * *", "timezone": "Asia/Almaty", "enrichment_review_days": 14, "market_report_frequency": "0 11 * * 1"},
        "slack": {
            "channel": "executive_search_report",
            "alerts_channel": "executive_search_report",
            "batch_size": 5,
            "market_channel": "executive_search_report",
            "search_report_channel": "executive_search_report",
        },
    },
    "target_companies": {},
    "company_red_flags": {},
}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config_bundle() -> dict[str, Any]:
    bundle = {
        "candidate": {},
        "search_criteria": {},
        "scoring": {},
        "deduplication": {},
        "runtime": {},
        "target_companies": {},
        "company_red_flags": {},
    }
    for key in bundle:
        path = SEED_DIR / f"{key}.yaml"
        loaded = load_yaml(path)
        bundle[key] = loaded.get(key, loaded) or DEFAULT_CONFIG[key]
    return bundle
