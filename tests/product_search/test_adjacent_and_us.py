"""Rules for the two classes run 509 let through: adjacent functions and US work authorisation.

Every title here is copied from run 509's accepted set, so a regression in these
rules is a regression against real collected data rather than an invented case.
"""

from __future__ import annotations

import pytest

from job_intel.product_search.role_fit import evaluate_role_fit

SOFTWARE_TEXT = (
    "We are a B2B SaaS platform for payments. You will lead a team, own the roadmap "
    "and partner with engineering across the product organisation."
)


@pytest.mark.parametrize(
    "title",
    [
        "Director of Product Design - Payments",
        "Director of Product Design",
        "Director, Product Design",
        "Director, Product Marketing (Payment, Platform)",
        "Product Marketing Lead, Billing",
        "Product Marketing Lead",
        "Strategic Product Partnerships Lead",
        "Product Localisation Lead - India",
    ],
)
def test_adjacent_product_functions_are_rejected(title: str) -> None:
    decision = evaluate_role_fit(title, "Acme", "London, United Kingdom", SOFTWARE_TEXT)
    assert decision.verdict == "reject"
    assert "adjacent_product_function" in decision.rule_ids


@pytest.mark.parametrize(
    "title",
    [
        "VP Product",
        "Head of Product",
        "Chief Product Officer",
        "Head of Product, Connected Experience",
        "Product Director, Localisation & AI Evals",
        "Director of Product Management, Agentic Software Delivery",
    ],
)
def test_product_mandates_survive_the_adjacent_function_rule(title: str) -> None:
    """The rule must fire on a function that replaces product, not on a product domain."""
    decision = evaluate_role_fit(title, "Acme", "London, United Kingdom", SOFTWARE_TEXT)
    assert "adjacent_product_function" not in decision.rule_ids
    assert decision.verdict == "accept"


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, California, United States",
        "New York, New York, United States",
        "Seattle, Washington, United States",
        "US - San Francisco",
        "Hybrid - San Francisco",
    ],
)
def test_us_onsite_without_sponsorship_is_rejected(location: str) -> None:
    decision = evaluate_role_fit("VP Product", "Acme", location, SOFTWARE_TEXT)
    assert decision.verdict == "reject"
    assert "us_onsite_without_sponsorship" in decision.rule_ids


def test_us_onsite_with_explicit_sponsorship_is_not_rejected() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "Acme",
        "San Francisco, California, United States",
        SOFTWARE_TEXT + " We sponsor work visas for this role, including H-1B transfers.",
    )
    assert "us_onsite_without_sponsorship" not in decision.rule_ids
    assert decision.verdict == "accept"


@pytest.mark.parametrize(
    "location",
    ["Remote - USA", "Remote, US", "Remote, United States", "Remote US"],
)
def test_us_remote_without_stated_eligibility_is_blocked_not_rejected(location: str) -> None:
    """Remote US is an open question about eligibility, not a disqualification."""
    decision = evaluate_role_fit("VP Product", "Acme", location, SOFTWARE_TEXT)
    assert decision.verdict == "blocked"
    assert "us_remote_eligibility_unknown" in decision.rule_ids


@pytest.mark.parametrize(
    "location",
    ["London, United Kingdom", "Almaty, Kazakhstan", "Amsterdam", "Singapore", "Remote, EMEA"],
)
def test_non_us_locations_are_untouched_by_the_work_authorisation_rules(location: str) -> None:
    decision = evaluate_role_fit("VP Product", "Acme", location, SOFTWARE_TEXT)
    assert "us_onsite_without_sponsorship" not in decision.rule_ids
    assert "us_remote_eligibility_unknown" not in decision.rule_ids
    assert decision.verdict == "accept"
