from __future__ import annotations

import importlib
from typing import Any

import pytest


def evaluate_role_fit(*args: Any, **kwargs: Any) -> Any:
    try:
        module = importlib.import_module("job_intel.product_search.role_fit")
    except ModuleNotFoundError:
        pytest.fail("role_fit module is not implemented yet")
    return module.evaluate_role_fit(*args, **kwargs)


def test_accepts_product_leadership_in_software_without_scope_requirement() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "CloudWorks",
        "Remote",
        "CloudWorks is a B2B SaaS software company. Own the product strategy "
        "with the co-founder and lead product management across the business.",
    )

    assert decision.verdict == "accept"
    assert "software_product_leadership" in decision.rule_ids
    match = decision.match_for("software_product_leadership")
    assert "SaaS" in match.fragments or "software" in match.fragments
    assert "co-founder" in " ".join(match.fragments)


def test_rejects_advertising_platform_from_description() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "Aster",
        "London",
        "Lead product for an advertising technology platform using programmatic "
        "advertising and demand-side platform capabilities.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("advertising_platform")
    assert "advertising technology" in " ".join(match.fragments).lower()


def test_rejects_industry_banking_software_portfolio() -> None:
    decision = evaluate_role_fit(
        "Product Director",
        "FinCo",
        "Frankfurt",
        "Lead a portfolio of core banking software products and banking platforms "
        "for financial institutions.",
    )

    assert decision.verdict == "reject"
    assert decision.match_for("banking_software_portfolio").fragments


def test_rejects_banking_credit_product_p_and_l() -> None:
    decision = evaluate_role_fit(
        "Product General Manager",
        "A bank",
        "Singapore",
        "Own the P&L and commercial performance for the bank's consumer credit "
        "and lending product.",
    )

    assert decision.verdict == "reject"
    assert decision.match_for("banking_credit_p_and_l").fragments


def test_rejects_commercial_ecommerce_assortment_leadership() -> None:
    decision = evaluate_role_fit(
        "Chief Commercial Officer",
        "MarketCo",
        "Berlin",
        "Lead commercial strategy, category management and assortment for our "
        "e-commerce marketplace.",
    )

    assert decision.verdict == "reject"
    assert decision.match_for("ecommerce_commercial_leadership").fragments


def test_blocks_required_language_outside_owner_languages() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership role. Fluent French is required; English is the "
        "company language.",
    )

    assert decision.verdict == "blocked"
    match = decision.match_for("required_language_unavailable")
    assert "French" in " ".join(match.fragments)
    assert "fr" in match.details["languages"]


def test_owner_language_list_is_configurable() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership role. Fluent French is required; English is the "
        "company language.",
        owner_languages=("en", "ru", "fr"),
    )

    assert decision.verdict == "accept"
    assert "required_language_unavailable" not in decision.rule_ids


def test_rejects_interim_role() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "SaaSCo",
        "Remote",
        "Lead product strategy in an interim capacity for a software company.",
    )

    assert decision.verdict == "reject"
    assert "interim_or_cover" in decision.rule_ids


def test_rejects_short_fixed_term_contract() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "SaaSCo",
        "Remote",
        "Software company seeking a product leader for a 12-month fixed-term "
        "contract.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("short_contract")
    assert "12-month" in " ".join(match.fragments)


def test_language_blocked_is_distinct_from_hard_reject() -> None:
    blocked = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership. French is required for the working language.",
    )
    rejected = evaluate_role_fit(
        "VP Product",
        "AdTechCo",
        "Remote",
        "Product leadership for an advertising technology platform.",
    )

    assert blocked.verdict == "blocked"
    assert rejected.verdict == "reject"


def test_rule_matches_include_exact_trigger_fragments() -> None:
    decision = evaluate_role_fit(
        "Product Director",
        "FinCo",
        "Remote",
        "Core banking software portfolio.",
    )

    match = decision.match_for("banking_software_portfolio")
    assert all(fragment in decision.normalized_text for fragment in match.fragments)
    assert match.explanation


def test_does_not_block_language_marked_as_preferred() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "SaaS product leadership. French is preferred, while English is required.",
    )

    assert decision.verdict == "accept"
    assert "required_language_unavailable" not in decision.rule_ids


def test_does_not_reject_an_advertising_experience_preference() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "SaaS product leadership. Experience with advertising technology is a plus; "
        "this is not an advertising platform.",
    )

    assert decision.verdict == "accept"
    assert "advertising_platform" not in decision.rule_ids


def test_does_not_reject_product_leadership_in_ecommerce_without_commercial_scope() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "MarketCo",
        "Remote",
        "Lead the product for an e-commerce SaaS marketplace and improve the "
        "customer experience across the product.",
    )

    assert decision.verdict == "accept"
    assert "ecommerce_commercial_leadership" not in decision.rule_ids


def test_does_not_accept_a_non_product_role_that_mentions_product_management() -> None:
    decision = evaluate_role_fit(
        "Chief Operating Officer",
        "SaaSCo",
        "Remote",
        "Partner with product management while owning operations for a SaaS company.",
    )

    assert decision.verdict == "reject"
    assert "software_product_leadership" not in decision.rule_ids


def test_detects_required_language_proficiency_without_fluent_keyword() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership. French language proficiency is required for this role.",
    )

    assert decision.verdict == "blocked"
    assert "required_language_unavailable" in decision.rule_ids


def test_accepts_product_leadership_in_a_technology_platform_company() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "TechCo",
        "Remote",
        "Lead the product strategy for a technology platform used by businesses.",
    )

    assert decision.verdict == "accept"
    assert "software_product_leadership" in decision.rule_ids


def test_rejects_commercial_title_with_ecommerce_signal_in_another_sentence() -> None:
    decision = evaluate_role_fit(
        "Chief Commercial Officer",
        "MarketCo",
        "Remote",
        "The company operates an e-commerce marketplace. Own commercial growth.",
    )

    assert decision.verdict == "reject"
    assert "ecommerce_commercial_leadership" in decision.rule_ids


def test_does_not_reject_adtech_platform_experience_as_a_preference() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "SaaS product leadership. Experience with an advertising technology platform "
        "is a plus.",
    )

    assert decision.verdict == "accept"
    assert "advertising_platform" not in decision.rule_ids
