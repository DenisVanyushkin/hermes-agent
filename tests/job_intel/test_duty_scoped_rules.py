"""§7.2 round 2 — the two structural defects found by self-inspection.

1. The provider regexed raw text, so company-culture boilerplate matched:
   "scale and solve them as a team" produced team_build_mandate on a
   Recruiter, a Data Engineer and an AI Engineer.
2. Coverage was measured over the whole eligible corpus, most of which is
   non-target roles, so the number described the wrong population.
"""
from __future__ import annotations

from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
    _extract_mandate_facts,
    coverage_report,
)


def _row(text, title="Head of Product"):
    return {"vacancy_key": "k", "title": title, "text": text,
            "company": "Acme", "location": "Remote", "source": "s"}


# --- defect 1: duty-scoped matching -----------------------------------------

def test_culture_boilerplate_no_longer_yields_team_mandate():
    boiler = ("We believe the hardest problems are worth solving. "
              "We scale and solve them as a team, with curiosity and care.")
    assert "mandate.team_build_mandate" not in _extract_mandate_facts(_row(boiler))


def test_company_growth_prose_no_longer_yields_team_mandate():
    assert "mandate.team_build_mandate" not in _extract_mandate_facts(
        _row("Join our fast growing team of 500 people in 20 countries."))


def test_genuine_duty_sentence_still_yields_the_fact():
    duty = "You will build and lead a team of product managers for the region."
    assert "mandate.team_build_mandate" in _extract_mandate_facts(_row(duty))


def test_genuine_pnl_duty_still_yields_the_fact():
    assert "mandate.pnl_ownership" in _extract_mandate_facts(
        _row("You will own the full P&L for the payments business line."))


def test_third_party_strategy_does_not_yield_strategy_ownership():
    """'Own the CaptivateIQ strategy' on a Finance Manager was a false hit."""
    text = "The marketing team sets the strategy for paid media campaigns."
    assert "mandate.strategy_ownership" not in _extract_mandate_facts(_row(text))


# --- defect 2: coverage measures the right population ------------------------

def test_coverage_can_scope_to_target_roles():
    rows = [_row("You will own the full P&L.", title="Head of Product"),
            _row("You will own the full P&L.", title="Senior Technical Recruiter")]
    all_roles = coverage_report(rows)
    target = coverage_report(rows, target_only=True)
    assert all_roles["roles_total"] == 2
    assert target["roles_total"] == 1
    assert target["population"] == "target_roles"


def test_coverage_reports_population_label_by_default():
    rep = coverage_report([_row("You will own the full P&L.")])
    assert rep["population"] == "all_eligible"
