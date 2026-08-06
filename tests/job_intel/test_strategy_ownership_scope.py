"""§7.2 round 2 — owner decision 2026-08-06 on strategy_ownership scope.

A FUNCTIONAL strategy (design, marketing, risk, benefits, events, sales,
talent) is not an executive product mandate; the existing negative fixture
"Senior Director, Enterprise Risk Strategy" already said so. A product or
business strategy is.

Implemented as a denylist on the qualifier rather than an allowlist on the
object, because the flagship GPNI vacancy phrases it bare — "You'll drive
strategy, assess and integrate new financial partners, and deliver
infrastructure products" — and an allowlist would have dropped a role the
acceptance criterion requires in the top band.
"""
from __future__ import annotations

from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
    _extract_mandate_facts,
)

FACT = "mandate.strategy_ownership"


def _row(text, title="Head of Product"):
    return {"vacancy_key": "k", "title": title, "text": text,
            "company": "Acme", "location": "Remote", "source": "s"}


# --- functional strategies do NOT count --------------------------------------

def test_design_strategy_is_not_strategy_ownership():
    assert FACT not in _extract_mandate_facts(_row(
        "You will own the design strategy and execution for our end-to-end "
        "developer experience, spanning our APIs, SDKs and developer tools."))


def test_product_marketing_strategy_is_not_strategy_ownership():
    assert FACT not in _extract_mandate_facts(_row(
        "As Director of Product Marketing, you will define and drive the "
        "product marketing strategy at Brex, positioning our financial products."))


def test_risk_strategy_is_not_strategy_ownership():
    assert FACT not in _extract_mandate_facts(_row(
        "You will define the commercial risk strategy, own the supportability "
        "frameworks that govern which customers and use cases we back."))


def test_events_strategy_is_not_strategy_ownership():
    assert FACT not in _extract_mandate_facts(_row(
        "Drive regional events strategy and lead end-to-end execution of "
        "virtual and in-person programs, including logistics and vendors."))


def test_talent_acquisition_strategy_is_not_strategy_ownership():
    """The round-1 withdrawal case, on a Senior Recruitment Manager."""
    assert FACT not in _extract_mandate_facts(_row(
        "You will drive a Talent Acquisition strategy that scales with the "
        "business and delivers a great candidate experience.",
        title="Senior Recruitment Manager"))


# --- product / business strategies DO count ----------------------------------

def test_bare_strategy_on_a_duty_sentence_still_counts():
    """The GPNI flagship phrases it with no qualifier at all."""
    assert FACT in _extract_mandate_facts(_row(
        "You'll drive strategy, assess and integrate new financial partners, "
        "and deliver infrastructure products that support scale and speed."))


def test_product_strategy_still_counts():
    assert FACT in _extract_mandate_facts(_row(
        "Lead and own product strategy and roadmap for accountable security "
        "product lines, fully aligned to revenue and business goals."))


def test_vision_and_strategy_still_counts():
    assert FACT in _extract_mandate_facts(_row(
        "Own the vision and strategy for Infrastructure Monitoring products, "
        "ensuring alignment with overall company goals and customer needs."))


# --- team prose is not the candidate's duty ----------------------------------

def test_we_set_strategy_is_team_prose_not_a_duty():
    """'We set strategy and drive products' — subject is the team. The
    company-description gate only caught "we're"/"we are"."""
    assert FACT not in _extract_mandate_facts(_row(
        "We set strategy and drive products from inception to launch, "
        "enabling Brex to grow rapidly and reach full potential."))


def test_we_sentence_addressed_to_the_candidate_is_still_a_duty():
    assert FACT in _extract_mandate_facts(_row(
        "We expect you to own the product strategy for the payments area "
        "and to make the hard prioritisation calls."))
