"""§7.2 round 2 — rules for the facts that never fired, authored from real text.

Every string below is a real duty sentence from the DEV slice of the live
corpus (target roles only; holdout was not read). The negatives are the
near-misses found in the same reading — the constructions that mention the
same vocabulary without assigning the mandate.
"""
from __future__ import annotations

from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
    _extract_mandate_facts,
)


def _row(text, title="Head of Product"):
    return {"vacancy_key": "k", "title": title, "text": text,
            "company": "Acme", "location": "Remote", "source": "s"}


# --- pricing_core -----------------------------------------------------------

def test_owning_the_pricing_structure_yields_pricing_core():
    assert "mandate.pricing_core" in _extract_mandate_facts(_row(
        "You will own the pricing and incentive structure for the segment."))


def test_pricing_in_an_ownership_list_yields_pricing_core():
    assert "mandate.pricing_core" in _extract_mandate_facts(_row(
        "You own acquisition, product strategy, onboarding, pricing, "
        "incentives, positioning, lifecycle, and the pipeline number."))


def test_contributing_to_pricing_projects_is_not_pricing_core():
    """Real near-miss: contribution is not ownership."""
    assert "mandate.pricing_core" not in _extract_mandate_facts(_row(
        "Actively contribute to projects related to pricing and bundling, "
        "craft value-based narratives, and drive adoption of premium tiers."))


# --- acquiring_core ---------------------------------------------------------

def test_acquiring_capabilities_yields_acquiring_core():
    assert "mandate.acquiring_core" in _extract_mandate_facts(_row(
        "Formulate and lead a strategic vision that balances technical "
        "acquiring capabilities and locally preferred payment methods."))


def test_card_issuing_is_not_acquiring_core():
    """Issuing is the other side of the card business, not acquiring."""
    assert "mandate.acquiring_core" not in _extract_mandate_facts(_row(
        "Collaborate closely with engineering, design and commercial teams to "
        "build and iterate on embedded finance products — including payments, "
        "card issuing, banking-as-a-service, and FX."))


def test_acquiring_talent_is_not_acquiring_core():
    """'Acquiring' is also an ordinary verb."""
    assert "mandate.acquiring_core" not in _extract_mandate_facts(_row(
        "You will lead the effort of acquiring new enterprise customers "
        "across the region and growing the installed base."))


# --- expansion_mandate ------------------------------------------------------

def test_new_markets_yields_expansion_mandate():
    """The GPNI flagship sentence."""
    assert "mandate.expansion_mandate" in _extract_mandate_facts(_row(
        "You'll also drive the construction of new rails in new markets, "
        "manage cards and e-wallet payouts, and align with our mission."))


def test_market_expansion_label_yields_expansion_mandate():
    assert "mandate.expansion_mandate" in _extract_mandate_facts(_row(
        "Market Expansion: Drive end-to-end growth especially in the European "
        "and American markets, leveraging your experience in launching."))


def test_penetrating_new_markets_yields_expansion_mandate():
    assert "mandate.expansion_mandate" in _extract_mandate_facts(_row(
        "Develop tailored go-to-market strategies to penetrate new markets "
        "and establish strong product-market fit."))


def test_channel_expansion_is_not_market_expansion():
    assert "mandate.expansion_mandate" not in _extract_mandate_facts(_row(
        "Own the product roadmap: procedure design, action integrations, "
        "escalation logic, knowledge-base quality, and channel expansion."))


def test_revenue_expansion_is_not_market_expansion():
    assert "mandate.expansion_mandate" not in _extract_mandate_facts(_row(
        "Define success metrics tied to company outcomes such as attach rate, "
        "balance growth, revenue expansion, margin improvement and retention."))


# --- org_design_mandate -----------------------------------------------------

def test_explicit_org_design_yields_the_mandate():
    assert "mandate.org_design_mandate" in _extract_mandate_facts(_row(
        "Build, mentor, and develop a multi-layer organization of Product "
        "Managers, including org design, talent development and succession."))


def test_designing_team_structures_yields_the_mandate():
    assert "mandate.org_design_mandate" in _extract_mandate_facts(_row(
        "Architect and scale an efficient lending organization, design "
        "high-performing team structures, and establish clear governance."))


def test_organisational_impact_boilerplate_is_not_org_design():
    assert "mandate.org_design_mandate" not in _extract_mandate_facts(_row(
        "Leadership and Organisational Impact: be a self-starter with a "
        "strong bias for action, willing to lead from the front when needed."))


# --- board_exposure ---------------------------------------------------------

def test_board_updates_yield_board_exposure():
    assert "mandate.board_exposure" in _extract_mandate_facts(_row(
        "You will own the quarterly board reporting for the payments line "
        "and represent the product organisation in those reviews."))


def test_onboarding_is_not_board_exposure():
    """'board' is a substring of 'onboarding' and 'leaderboards'."""
    assert "mandate.board_exposure" not in _extract_mandate_facts(_row(
        "You will be responsible for the entire developer journey, from "
        "onboarding to the first API call to scaling globally."))


def test_leaderboards_are_not_board_exposure():
    assert "mandate.board_exposure" not in _extract_mandate_facts(_row(
        "Build quality scorecards and leaderboards for production systems "
        "across the company, ensuring all systems meet defined standards."))
