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
import types

import pytest

from job_intel.browser_sourcing import (
    BrowserAcquisitionConfig,
    BrowserSourceClient,
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


DETAIL_URL = "https://www.linkedin.com/jobs/view/4441999185/"


def test_the_execution_plan_path_also_yields_no_rows(
    matched_nothing: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through `search_linkedin` with a plan, which is a different branch.

    With an execution plan the page loop qualifies its own pre-filter rows and
    never calls `extract_linkedin_vacancies_from_html`; that is the shape the
    browser worker and the Gate A probe use. A guard placed only in the
    extractor would leave this path ingesting the recommendation block while
    the trace recorded the page as empty -- the exact contradiction the slice
    exists to remove, reintroduced one branch over.
    """

    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(tmp_path / "diag"))
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(source_name="linkedin", max_scrolls=0, noise_probability=0.0)
    )
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)

    class _Locator:
        """Enough of a results container for the plan to run its checkpoints.

        The plan branch scrolls and re-reads; a locator that cannot be
        evaluated fails the run before extraction, which would have made this
        test pass for the wrong reason.
        """

        def evaluate_all(self, _script: str) -> list[str]:
            return []

        def evaluate(self, _script: str) -> bool:
            return True

        def count(self) -> int:
            return 1

        @property
        def first(self) -> "_Locator":
            return self

    class _Page:
        url = ""
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, url: str, **_kwargs) -> None:
            self.url = url

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return matched_nothing

        def locator(self, selector: str) -> _Locator:
            return _Locator()

        def screenshot(self, *, path: str, full_page: bool) -> None:
            pathlib.Path(path).write_bytes(b"png")

        def title(self) -> str:
            return "Search"

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]

    vacancies = client.search_linkedin(
        "product",
        geography_location="United Kingdom",
        cell_id="uk",
        execution_plan={"page_offsets": [0]},
    )
    page_trace = client.last_search_trace_snapshot()["pages"][0]

    assert vacancies == []
    assert page_trace["page_classification"] == "terminal_empty_surface"


def test_the_statement_inside_a_script_does_not_empty_a_page(
    matched_something: str,
) -> None:
    """A page that merely mentions the sentence has not made the statement.

    Built by injecting the phrase into a script body of the results fixture,
    so the difference from that fixture is exactly the injection and nothing
    else. Before the reading was narrowed to rendered paragraphs, this page
    lost its rows.
    """

    # A paragraph *inside* a script body, which is how a single-page app ships
    # a template it has not rendered. A bare sentence in a script would not
    # have exercised anything: the reading already requires a paragraph, so
    # only a paragraph the browser never displayed can tell the two apart.
    poisoned = matched_something.replace(
        "<!--",
        '<script type="text/x-template">'
        "<p class=\"t-24\">No matching jobs found.</p>"
        "</script>\n<!--",
        1,
    )

    assert poisoned != matched_something
    assert len(extract_linkedin_vacancies_from_html(poisoned, page_url=SEARCH_URL)) == 2


def test_a_job_detail_page_is_not_a_search_that_matched_nothing(
    matched_nothing: str,
) -> None:
    """The statement is about a search, so it is only read on a search URL.

    A detail page carrying the same words says nothing about any query, and
    treating it as an empty search would discard rows from a surface the
    reading was never about.
    """

    assert extract_linkedin_vacancies_from_html(matched_nothing, page_url=DETAIL_URL) != []
