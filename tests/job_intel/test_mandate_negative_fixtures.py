"""§7.2 T4 — the owner's rejections become a permanent precision guard.

During the 5B-5 review Denis marked 22 mandate/organization observations as
stretches: company boilerplate, brand/scale language, and job titles read as
if they described the candidate's remit. The LLM made exactly that error, and
new deterministic rules mined from real prose are at risk of repeating it.

Each rejected excerpt is now a test: no rule may extract that mandate fact
from that text. This runs against the CURRENT rule set too, so a regression
introduced later is caught immediately.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
    _extract_mandate_facts,
)

FIXTURES = (Path(__file__).resolve().parents[1]
            / "fixtures/vacancy_understanding/mandate_negative_fixtures.json")


def _cases():
    data = json.loads(FIXTURES.read_text())
    return [(c["fact"], c["excerpt"]) for c in data["cases"]]


def test_fixture_file_is_present_and_populated():
    data = json.loads(FIXTURES.read_text())
    assert len(data["cases"]) >= 20
    assert "owner manual review" in data["source"]


@pytest.mark.parametrize("fact,excerpt", _cases())
def test_owner_rejected_excerpt_does_not_yield_the_mandate_fact(fact, excerpt):
    """The excerpt alone must not produce the fact the owner rejected."""
    row = {"vacancy_key": "neg", "title": "Head of Product",
           "text": excerpt, "company": "Acme", "location": "Remote",
           "source": "fixture"}
    extracted = _extract_mandate_facts(row)
    assert fact not in extracted, (
        f"rule fired on an excerpt the owner judged insufficient for {fact}: "
        f"{excerpt[:80]!r}")
