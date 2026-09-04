"""A LinkedIn search that matched nothing must not yield rows.

When an authenticated LinkedIn jobs search matches nothing, the page says so --
`No matching jobs found.` -- and then shows a block headed `Jobs you may be
interested in`. Those cards carry the same markup as results and are not
results: on 2026-09-04 twenty of twenty-three captured pages were this shape,
and the sixty rows they produced were counted as acquisition.

Both ends are asserted here on purpose. The classification is a record and
changing it alone would only make the trace honest: `classify_linkedin_page`
is called once, to write `trace["pages"][...]["page_classification"]`, and no
decision reads it. The row count is the behaviour.

The fixtures are cut from frozen captures of that run and were accepted only
after the production extractor returned the same rows from the fixture as from
the full page it came from.
"""

from __future__ import annotations

import pathlib

import pytest

from job_intel.browser_sourcing import (
    classify_linkedin_page,
    extract_linkedin_vacancies_from_html,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords=x&location=Australia"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def matched_nothing() -> str:
    return _fixture("linkedin-authenticated-empty-with-recommendations.html")


@pytest.fixture
def matched_something() -> str:
    return _fixture("linkedin-authenticated-results.html")


def test_the_page_that_matched_nothing_yields_no_rows(matched_nothing: str) -> None:
    """The behaviour, and the whole point of the slice.

    The fixture carries three cards the parser can read; a page stating that
    nothing matched cannot be a page whose cards are results.
    """

    assert extract_linkedin_vacancies_from_html(
        matched_nothing, page_url=SEARCH_URL
    ) == []


def test_the_page_that_matched_nothing_is_recorded_as_empty(matched_nothing: str) -> None:
    """The record, asserted separately because it is a separate fact.

    Card-first precedence answers a different question -- a logged-out page
    showing results beside a sign-in prompt is usable -- and it must not
    outrank the source's own statement that the search matched nothing.
    """

    assert (
        classify_linkedin_page(final_url=SEARCH_URL, html=matched_nothing, status=200)
        == "terminal_empty_surface"
    )


def test_a_page_that_matched_still_yields_its_rows(matched_something: str) -> None:
    """The over-correction guard, without which the fix could pass by breaking
    every LinkedIn page."""

    rows = extract_linkedin_vacancies_from_html(matched_something, page_url=SEARCH_URL)

    assert len(rows) == 2
    assert {row.location for row in rows} == {
        "Singapore, Singapore (Hybrid)",
        "Singapore, Singapore (On-site)",
    }


def test_a_page_that_matched_is_still_recorded_as_usable(matched_something: str) -> None:
    assert (
        classify_linkedin_page(final_url=SEARCH_URL, html=matched_something, status=200)
        == "usable_result_surface"
    )
