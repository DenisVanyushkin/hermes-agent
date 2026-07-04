"""Reason taxonomy and attribution model for the negative feedback loop.

Implements the taxonomy defined in the negative-feedback-loop PRD:
top-level reason categories, detail reason codes, numeric Slack prompt
options, keyword rules (RU/EN) for rule-based classification, attribution
targets, and the reason -> scoring feature mapping used by calibration.
"""

from __future__ import annotations

FEEDBACK_POLARITY_NEGATIVE = "negative"
CLASSIFIER_VERSION = "rules-v1"

# --- Top-level categories -------------------------------------------------

CATEGORY_ROLE_MISMATCH = "role_mismatch"
CATEGORY_SENIORITY_SCOPE = "seniority_scope_mismatch"
CATEGORY_LOCATION_WORK_FORMAT = "location_or_work_format_blocker"
CATEGORY_COMPANY_QUALITY = "company_quality_issue"
CATEGORY_INDUSTRY_THESIS = "industry_or_thesis_mismatch"
CATEGORY_COMPENSATION = "compensation_or_upside_issue"
CATEGORY_DATA_QUALITY = "data_quality_issue"
CATEGORY_OTHER = "other"

REASON_CATEGORIES: list[str] = [
    CATEGORY_ROLE_MISMATCH,
    CATEGORY_SENIORITY_SCOPE,
    CATEGORY_LOCATION_WORK_FORMAT,
    CATEGORY_COMPANY_QUALITY,
    CATEGORY_INDUSTRY_THESIS,
    CATEGORY_COMPENSATION,
    CATEGORY_DATA_QUALITY,
    CATEGORY_OTHER,
]

# Numeric options shown in the Slack prompt (1-based).
NUMERIC_CATEGORY_MAP: dict[int, str] = {
    1: CATEGORY_ROLE_MISMATCH,
    2: CATEGORY_SENIORITY_SCOPE,
    3: CATEGORY_LOCATION_WORK_FORMAT,
    4: CATEGORY_COMPANY_QUALITY,
    5: CATEGORY_INDUSTRY_THESIS,
    6: CATEGORY_COMPENSATION,
    7: CATEGORY_DATA_QUALITY,
    8: CATEGORY_OTHER,
}

# --- Detail reason codes ---------------------------------------------------

DETAIL_CODES_BY_CATEGORY: dict[str, list[str]] = {
    CATEGORY_ROLE_MISMATCH: [
        "wrong_function_marketing",
        "wrong_function_sales",
        "wrong_function_ops",
        "wrong_function_project_delivery",
        "wrong_function_support",
        "too_narrow_product_scope",
        "too_technical_platform",
        "too_growth_hacky",
        "not_product_or_business_ownership",
    ],
    CATEGORY_SENIORITY_SCOPE: [
        "too_junior_title",
        "too_small_team",
        "no_pnl_ownership",
        "no_strategy_ownership",
        "weak_reporting_line",
        "regional_scope_too_small",
        "ic_or_low_manager_role",
        "not_exec_level",
        "scope_not_worth_move",
    ],
    CATEGORY_LOCATION_WORK_FORMAT: [
        "no_remote",
        "onsite_required",
        "bad_relocation_required",
        "bad_timezone",
        "visa_or_work_auth_blocker",
        "travel_too_heavy",
        "location_unclear",
    ],
    CATEGORY_COMPANY_QUALITY: [
        "company_too_small",
        "company_too_big_bureaucratic",
        "company_low_growth",
        "company_reputation_risk",
        "company_sanctions_risk",
        "company_unclear_business_model",
        "company_weak_funding_or_runway",
        "company_not_strategic_enough",
        "company_stage_mismatch",
    ],
    CATEGORY_INDUSTRY_THESIS: [
        "industry_not_interesting",
        "outside_fintech_telco_digital_ecosystem",
        "crypto_too_risky",
        "gambling_or_gray_market",
        "market_too_local",
        "b2b_saas_not_exciting",
        "consumer_scope_weak",
        "not_platform_or_ecosystem",
    ],
    CATEGORY_COMPENSATION: [
        "comp_too_low",
        "equity_weak_or_irrelevant",
        "no_exec_level_package_signal",
        "package_unclear",
        "risk_reward_bad",
    ],
    CATEGORY_DATA_QUALITY: [
        "duplicate",
        "stale_job",
        "bad_parse",
        "wrong_location_detected",
        "wrong_seniority_detected",
        "wrong_company_detected",
        "ats_noise",
        "not_enough_info",
        "low_confidence_enrichment",
    ],
    CATEGORY_OTHER: [
        "other",
        "not_now",
        "maybe_later",
        "user_free_text_only",
    ],
}

ALL_DETAIL_CODES: set[str] = {
    code for codes in DETAIL_CODES_BY_CATEGORY.values() for code in codes
}

CATEGORY_BY_DETAIL_CODE: dict[str, str] = {
    code: category
    for category, codes in DETAIL_CODES_BY_CATEGORY.items()
    for code in codes
}

# --- Attribution targets ---------------------------------------------------

ATTRIBUTION_TARGETS: set[str] = {
    "vacancy",
    "company",
    "role_title",
    "role_function",
    "seniority_scope",
    "location",
    "work_format",
    "industry",
    "compensation",
    "source",
    "parser",
    "enrichment",
    "dedup",
    "scoring_rule",
    "unknown",
}

ATTRIBUTION_BY_CATEGORY: dict[str, list[str]] = {
    CATEGORY_ROLE_MISMATCH: ["role_function", "scoring_rule"],
    CATEGORY_SENIORITY_SCOPE: ["seniority_scope", "scoring_rule"],
    CATEGORY_LOCATION_WORK_FORMAT: ["work_format", "location"],
    CATEGORY_COMPANY_QUALITY: ["company"],
    CATEGORY_INDUSTRY_THESIS: ["industry"],
    CATEGORY_COMPENSATION: ["compensation"],
    CATEGORY_DATA_QUALITY: ["parser", "enrichment"],
    CATEGORY_OTHER: ["unknown"],
}

ATTRIBUTION_BY_DETAIL_CODE: dict[str, list[str]] = {
    "duplicate": ["source", "parser", "dedup"],
    "stale_job": ["source"],
    "ats_noise": ["source", "parser"],
    "bad_parse": ["parser"],
    "wrong_location_detected": ["parser", "enrichment"],
    "wrong_seniority_detected": ["parser", "enrichment"],
    "wrong_company_detected": ["parser", "enrichment"],
    "not_enough_info": ["enrichment"],
    "low_confidence_enrichment": ["enrichment"],
    "location_unclear": ["location", "enrichment"],
}

# --- Hard blockers / preference-neutral codes (PRD recommended defaults) ---

DEFAULT_HARD_BLOCKER_CODES: set[str] = {
    "onsite_required",
    "no_remote",
    "too_junior_title",
    "no_pnl_ownership",
    "wrong_function_project_delivery",
    "company_sanctions_risk",
    "company_reputation_risk",
}

# Data-quality signals must never be read as candidate preference.
NO_PREFERENCE_PENALTY_CODES: set[str] = set(
    DETAIL_CODES_BY_CATEGORY[CATEGORY_DATA_QUALITY]
)

SOFT_PREFERENCE_CODES: set[str] = {"not_now", "maybe_later"}

# --- Keyword rules for rule-based classification (RU/EN) -------------------
# Each entry: substring (casefolded) -> detail code. Checked against the
# casefolded free text; multi-word entries match as plain substrings.

KEYWORD_DETAIL_RULES: list[tuple[str, str]] = [
    # location / work format
    ("нет удаленки", "no_remote"),
    ("нет удалёнки", "no_remote"),
    ("без удаленки", "no_remote"),
    ("no remote", "no_remote"),
    ("не удаленка", "no_remote"),
    ("only office", "onsite_required"),
    ("onsite", "onsite_required"),
    ("on-site", "onsite_required"),
    ("офис", "onsite_required"),
    ("in office", "onsite_required"),
    ("релокация", "bad_relocation_required"),
    ("relocation", "bad_relocation_required"),
    ("таймзона", "bad_timezone"),
    ("timezone", "bad_timezone"),
    ("виза", "visa_or_work_auth_blocker"),
    ("visa", "visa_or_work_auth_blocker"),
    ("локация", "location_unclear"),
    # seniority / scope
    ("junior", "too_junior_title"),
    ("джуниор", "too_junior_title"),
    ("слишком джун", "too_junior_title"),
    ("не тот уровень", "not_exec_level"),
    ("низкий уровень", "not_exec_level"),
    ("not exec", "not_exec_level"),
    ("нет p&l", "no_pnl_ownership"),
    ("нет pnl", "no_pnl_ownership"),
    ("no p&l", "no_pnl_ownership"),
    ("no pnl", "no_pnl_ownership"),
    ("без p&l", "no_pnl_ownership"),
    ("маленькая команда", "too_small_team"),
    ("small team", "too_small_team"),
    ("нет стратегии", "no_strategy_ownership"),
    ("no strategy", "no_strategy_ownership"),
    ("scope", "scope_not_worth_move"),
    ("скоуп", "scope_not_worth_move"),
    # role / function
    ("маркетинг", "wrong_function_marketing"),
    ("marketing", "wrong_function_marketing"),
    ("сейлз", "wrong_function_sales"),
    ("sales", "wrong_function_sales"),
    ("продажи", "wrong_function_sales"),
    ("delivery", "wrong_function_project_delivery"),
    ("проектный менедж", "wrong_function_project_delivery"),
    ("project manage", "wrong_function_project_delivery"),
    ("саппорт", "wrong_function_support"),
    ("support", "wrong_function_support"),
    ("не продукт", "not_product_or_business_ownership"),
    ("not product", "not_product_or_business_ownership"),
    ("слишком технич", "too_technical_platform"),
    ("too technical", "too_technical_platform"),
    # company quality
    ("компания мутная", "company_reputation_risk"),
    ("мутная компания", "company_reputation_risk"),
    ("мутная", "company_reputation_risk"),
    ("red flag", "company_reputation_risk"),
    ("репутация", "company_reputation_risk"),
    ("санкци", "company_sanctions_risk"),
    ("sanction", "company_sanctions_risk"),
    ("маленькая компания", "company_too_small"),
    ("company too small", "company_too_small"),
    ("бюрократ", "company_too_big_bureaucratic"),
    ("bureaucra", "company_too_big_bureaucratic"),
    ("непонятная бизнес-модель", "company_unclear_business_model"),
    ("unclear business model", "company_unclear_business_model"),
    # industry / thesis
    ("индустрия", "industry_not_interesting"),
    ("industry", "industry_not_interesting"),
    ("крипта", "crypto_too_risky"),
    ("crypto", "crypto_too_risky"),
    ("гемблинг", "gambling_or_gray_market"),
    ("gambling", "gambling_or_gray_market"),
    ("казино", "gambling_or_gray_market"),
    ("не интересный рынок", "industry_not_interesting"),
    # compensation
    ("мало денег", "comp_too_low"),
    ("зарплата", "comp_too_low"),
    ("comp too low", "comp_too_low"),
    ("низкая вилка", "comp_too_low"),
    ("деньги не те", "comp_too_low"),
    ("equity", "equity_weak_or_irrelevant"),
    # data quality
    ("дубль", "duplicate"),
    ("дубликат", "duplicate"),
    ("duplicate", "duplicate"),
    ("уже видел", "duplicate"),
    ("плохой парсинг", "bad_parse"),
    ("bad parse", "bad_parse"),
    ("парсинг", "bad_parse"),
    ("мусор", "bad_parse"),
    ("stale", "stale_job"),
    ("старая вакансия", "stale_job"),
    ("протухла", "stale_job"),
    ("мало информации", "not_enough_info"),
    ("not enough info", "not_enough_info"),
    # other
    ("не сейчас", "not_now"),
    ("not now", "not_now"),
    ("позже", "maybe_later"),
    ("maybe later", "maybe_later"),
]

# --- Reason -> scoring feature mapping (PRD section 21) --------------------

REASON_TO_SCORING_FEATURE: dict[str, dict[str, str]] = {
    "no_pnl_ownership": {
        "feature": "PnL_ownership",
        "default_action": "increase_penalty_for_absence",
        "attribution": "seniority_scope",
    },
    "too_junior_title": {
        "feature": "seniority_title_fit",
        "default_action": "increase_junior_penalty",
        "attribution": "seniority_scope",
    },
    "onsite_required": {
        "feature": "remote_friendly",
        "default_action": "increase_onsite_penalty",
        "attribution": "work_format",
    },
    "no_remote": {
        "feature": "remote_friendly",
        "default_action": "increase_onsite_penalty",
        "attribution": "work_format",
    },
    "wrong_function_marketing": {
        "feature": "function_fit",
        "default_action": "increase_marketing_only_penalty",
        "attribution": "role_function",
    },
    "wrong_function_project_delivery": {
        "feature": "pure_project_management",
        "default_action": "increase_penalty",
        "attribution": "role_function",
    },
    "company_reputation_risk": {
        "feature": "company_risk",
        "default_action": "increase_company_risk_penalty",
        "attribution": "company",
    },
    "no_strategy_ownership": {
        "feature": "no_strategy",
        "default_action": "increase_penalty",
        "attribution": "seniority_scope",
    },
    "bad_parse": {
        "feature": "parser_quality",
        "default_action": "no_preference_change",
        "attribution": "parser",
    },
    "duplicate": {
        "feature": "dedup_quality",
        "default_action": "no_preference_change",
        "attribution": "dedup",
    },
}


def category_for_detail(detail_code: str) -> str | None:
    return CATEGORY_BY_DETAIL_CODE.get(detail_code)


def attribution_for(category_codes: list[str], detail_codes: list[str]) -> list[str]:
    """Attribution targets for a set of category and detail codes.

    Detail-level mappings take precedence (e.g. `duplicate` attributes to
    source/parser/dedup rather than the generic data-quality mapping).
    """
    targets: list[str] = []
    seen: set[str] = set()
    for code in detail_codes:
        for target in ATTRIBUTION_BY_DETAIL_CODE.get(code, []):
            if target not in seen:
                seen.add(target)
                targets.append(target)
        category = CATEGORY_BY_DETAIL_CODE.get(code)
        if category:
            for target in ATTRIBUTION_BY_CATEGORY.get(category, []):
                if target not in seen:
                    seen.add(target)
                    targets.append(target)
    for category in category_codes:
        for target in ATTRIBUTION_BY_CATEGORY.get(category, []):
            if target not in seen:
                seen.add(target)
                targets.append(target)
    if not targets:
        targets = ["unknown"]
    return targets


def scoring_features_for(detail_codes: list[str]) -> list[str]:
    features: list[str] = []
    seen: set[str] = set()
    for code in detail_codes:
        mapping = REASON_TO_SCORING_FEATURE.get(code)
        if not mapping:
            continue
        if mapping["default_action"] == "no_preference_change":
            continue
        feature = mapping["feature"]
        if feature not in seen:
            seen.add(feature)
            features.append(feature)
    return features
