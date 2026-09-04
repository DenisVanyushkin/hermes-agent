"""What the source searched, as the source states it -- not as we typed it.

The page title says `Jobs in United Kingdom` because `United Kingdom` is the
`location` parameter we sent; a request carrying only a `geoId` produces a
title with no place in it at all. Reading correspondence off the title
therefore compares our own words with themselves. On 2026-09-04 that reading
would have called every one of eighteen cells confirmed, including the one
whose results contradicted its own exclusion.

The source does state a geography, once, in the embedded `JobSearchMetadata`,
as `urn:li:fsd_geo:<id>`. That identifier is what these tests read.
"""

from __future__ import annotations

import pathlib
import types

import pytest

from job_intel.browser_sourcing import (
    BrowserAcquisitionConfig,
    BrowserSourceClient,
    classify_linkedin_geography_correspondence,
    extract_linkedin_vacancies_from_html,
    linkedin_resolved_geography,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords=x&location=Singapore"


@pytest.fixture
def resolved_geography_page() -> str:
    return (FIXTURES / "linkedin-search-resolved-geography.html").read_text(encoding="utf-8")


def test_the_resolved_geography_is_read_from_the_metadata(
    resolved_geography_page: str,
) -> None:
    assert linkedin_resolved_geography(resolved_geography_page) == "91000014"


def test_the_title_is_not_the_source_speaking(resolved_geography_page: str) -> None:
    """The fixture disagrees with itself, exactly as the real page does.

    Its title names `Southeast Asia excluding Singapore` because those are the
    words that were sent. Nothing in the page says what entity `91000014`
    stands for, and the reading must not invent one.
    """

    assert "Southeast Asia excluding Singapore" in resolved_geography_page
    assert linkedin_resolved_geography(resolved_geography_page) == "91000014"


def test_a_page_without_the_metadata_resolves_to_nothing() -> None:
    assert linkedin_resolved_geography("<title>Jobs in Kazakhstan | LinkedIn</title>") is None


@pytest.mark.parametrize(
    ("requested", "resolved", "expected"),
    [
        ("91000014", "91000014", "confirmed_geo_id"),
        ("102454443", "91000014", "divergent_geo_id"),
        (None, "91000014", "resolved_from_free_text"),
        ("91000014", None, "unresolved"),
        (None, None, "unresolved"),
    ],
)
def test_correspondence_says_only_what_can_be_compared(
    requested: str | None, resolved: str | None, expected: str
) -> None:
    """An identifier can be compared with an identifier; a place name cannot.

    `resolved_from_free_text` is the honest state for the whole daily plan as
    it stands: every cell asks by name, so no cell's correspondence is
    established either way. Calling that `confirmed` is the mistake this
    literal exists to make impossible.
    """

    assert (
        classify_linkedin_geography_correspondence(
            requested_geo_id=requested, resolved_geo_id=resolved
        )
        == expected
    )


def test_the_raw_location_survives_normalisation() -> None:
    """Normalisation is lossy in a way that erases the question.

    `Jakarta, Jakarta, Indonesia (Remote)` normalises to `Remote`; a page of
    Indonesian results then reads as a page of remote ones, and no target
    correspondence can be asked of it afterwards. Both are kept.
    """

    rows = extract_linkedin_vacancies_from_html(
        (FIXTURES / "linkedin-authenticated-results.html").read_text(encoding="utf-8"),
        page_url=SEARCH_URL,
    )

    assert rows
    for row in rows:
        raw = row.metadata.get("raw_location")
        assert raw, row.title
        assert "Singapore" in raw


def test_the_page_trace_records_both_geographies(
    resolved_geography_page: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the loop, because a reader nobody calls records nothing.

    The helpers above prove the reading; this proves the run performs it. The
    two were separated after a slice where the classification was correct and
    nothing consulted it.
    """

    page_html = (
        FIXTURES / "linkedin-authenticated-results.html"
    ).read_text(encoding="utf-8") + resolved_geography_page

    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(tmp_path / "diag"))
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(source_name="linkedin", max_scrolls=0, noise_probability=0.0)
    )
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)

    class _Locator:
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
            return page_html

        def locator(self, selector: str) -> _Locator:
            return _Locator()

        def screenshot(self, *, path: str, full_page: bool) -> None:
            pathlib.Path(path).write_bytes(b"png")

        def title(self) -> str:
            return "Search"

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]

    client.search_linkedin(
        "product",
        geography_location="Southeast Asia excluding Singapore",
        cell_id="southeast_asia_other",
        execution_plan={"page_offsets": [0]},
    )
    page_trace = client.last_search_trace_snapshot()["pages"][0]

    assert page_trace["requested_geography_location"] == "Southeast Asia excluding Singapore"
    assert page_trace["requested_geography_geo_id"] is None
    assert page_trace["source_resolved_geo_id"] == "91000014"
    assert page_trace["geography_correspondence"] == "resolved_from_free_text"
