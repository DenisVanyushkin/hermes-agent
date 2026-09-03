"""The page label follows the evidence, not the markup dialect it arrived in.

The logged-out LinkedIn results page carries real vacancies and its own sign-in
call to action. The detector knew only the authenticated markup, so it saw no
results, the substring wall check caught the page's own CTA, and a page with
sixty vacancies was recorded as a wall.

Measured on the frozen capture of 2026-09-02
(artifacts/uk-fb910d029b4f6c00bb2d-0/page.html): none of the three
authenticated class names appear, the card parser extracts 60 vacancies before
the role filter and 54 after it, _looks_like_linkedin_results_page returns
False and _looks_like_login_wall returns True. The fixture below reproduces
that structure with synthetic identifiers, and the first test asserts the
reproduction rather than assuming it.
"""

import pathlib

import pytest

from job_intel import browser_sourcing as bs
from job_intel.browser_sourcing import (
    BrowserAcquisitionConfig,
    BrowserFetchResult,
    BrowserSourceClient,
)
from job_intel.product_search.acquisition_probe import LinkedInExecutionPlan

PUBLIC_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search?keywords=Chief%20Product%20Officer"
    "&location=United%20Kingdom"
)

# The three class names the detector knows. They belong to the authenticated
# page and appear nowhere in the public one.
AUTHENTICATED_CLASS_NAMES = (
    "job-card-container__link",
    "job-card-list__title--link",
    "artdeco-entity-lockup__subtitle",
)


FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "linkedin-public-search-results.html"
)
# sha256 of the frozen capture the fixture was derived from
SOURCE_CAPTURE_SHA256 = (
    "ca42d58e2404fc16a76a914b4ec29539f5fa8ddd870df13f98ac4067b62e62fb"
)


def public_results_page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_fixture_is_derived_from_the_frozen_capture_and_carries_no_identifiers() -> None:
    """The fixture is a sanitized derivative, not an invention.

    Element structure and every class attribute come from
    uk-fb910d029b4f6c00bb2d-0/page.html verbatim; job ids, tracking ids,
    hrefs, images, titles and company names were replaced. Both halves are
    asserted here, because a fixture that quietly drifted from the captured
    structure would let every test below pass against something LinkedIn never
    served, and a fixture that kept real identifiers would put them in the
    repository.
    """

    html = public_results_page()

    assert SOURCE_CAPTURE_SHA256 in html
    for class_name in AUTHENTICATED_CLASS_NAMES:
        assert class_name not in html
    for public_class in (
        "jobs-search__results-list",
        "base-search-card",
        "base-card__full-link",
        "job-search-card",
    ):
        assert public_class in html
    for identifier in ("burns", "sheehan", "navigator", "aeir", "licdn"):
        assert identifier not in html.lower()

    parsed = bs._linkedin_card_vacancies_from_html(
        html, page_url=PUBLIC_SEARCH_URL, apply_role_filter=False
    )
    assert [vacancy.title for vacancy in parsed] == [
        "Synthetic Role 0",
        "Synthetic Role 1",
        "Synthetic Role 2",
    ]
    assert bs._looks_like_login_wall(PUBLIC_SEARCH_URL, html) is True


def test_public_results_page_is_a_usable_result_surface() -> None:
    """A page carrying vacancies is a result surface, whichever dialect it uses.

    Fails on baseline: the detector matches three authenticated class names,
    and the public page has none of them.
    """

    html = public_results_page()

    assert bs._page_has_source_results("linkedin", PUBLIC_SEARCH_URL, html) is True


def test_public_results_page_is_not_counted_as_a_login_wall() -> None:
    """The page's own sign-in CTA is not evidence that the market was hidden.

    Fails on baseline: the substring check catches "sign in" from the CTA, and
    the guard that would suppress it never fires because the results detector
    said there were no results.
    """

    health = bs.BrowserSessionHealth(source="linkedin")
    html = public_results_page()

    health.update(url=PUBLIC_SEARCH_URL, html=html, vacancies_found=3)

    assert health.login_walls == 0


def test_a_page_with_no_cards_and_a_sign_in_wall_still_counts_as_a_wall() -> None:
    """The repair must not blind the wall counter to an actual wall.

    Degenerate-predicate control: an implementation that simply stopped
    counting walls would pass the two tests above and fail this one.
    """

    health = bs.BrowserSessionHealth(source="linkedin")
    wall = (
        "<!DOCTYPE html><html><body><h1>Sign in to view more jobs</h1>"
        "<p>Continue to LinkedIn</p></body></html>"
    )

    health.update(
        url="https://www.linkedin.com/authwall?trk=jobs", html=wall, vacancies_found=0
    )

    assert health.login_walls == 1


def test_the_role_filter_does_not_decide_whether_the_page_existed() -> None:
    """Existence is read before the product filter, never after it.

    vacancies_found is counted after the role filter, so a page of real
    vacancies that the filter happens to reject entirely would otherwise be
    recorded as a wall -- the same defect, one step further along.
    """

    health = bs.BrowserSessionHealth(source="linkedin")
    html = public_results_page()

    health.update(url=PUBLIC_SEARCH_URL, html=html, vacancies_found=0)

    assert health.login_walls == 0


def test_the_abort_decision_follows_the_safety_axis_not_the_wall_counter() -> None:
    """An auth wall describes a page. A challenge refuses the caller.

    An earlier revision of this test asserted the opposite -- that a wall
    required the walk to stop. That was the intermediate design, in which the
    decision had moved off the cumulative counters but not yet off the wall
    signal, and the composition test showed why it was not enough: a benign
    wall still cancelled a declared plan. The assertion is corrected here
    rather than worked around.
    """

    health = bs.BrowserSessionHealth(source="linkedin")
    wall = "<html><body><h1>Sign in to view more jobs</h1></body></html>"

    health.update(url="https://www.linkedin.com/authwall", html=wall, vacancies_found=0)
    assert health.last_page_login_wall is True
    assert health.page_requires_abort() is False

    health.update(
        url="https://www.linkedin.com/checkpoint/challenge/verify",
        html="<html><body>verify</body></html>",
        vacancies_found=0,
    )
    assert health.last_page_safety_reason == "challenge_redirect"
    assert health.page_requires_abort() is True

    health.update(
        url=PUBLIC_SEARCH_URL, html=public_results_page(), vacancies_found=3
    )
    assert health.page_requires_abort() is False


def test_the_counters_survive_as_telemetry() -> None:
    """Removing them from the control flow does not remove them from the record.

    The docstring on apply_linkedin_verdict forbids drawing a verdict from
    these counters. It does not forbid collecting them, and a page-health
    report without them would be poorer for no gain.
    """

    health = bs.BrowserSessionHealth(source="linkedin")
    wall = "<html><body><h1>Sign in to view more jobs</h1></body></html>"

    health.update(url="https://www.linkedin.com/authwall", html=wall, vacancies_found=0)
    health.update(
        url=PUBLIC_SEARCH_URL, html=public_results_page(), vacancies_found=3
    )

    assert health.login_walls == 1
    assert health.pages_fetched == 2


AUTHWALL_HTML = (
    "<!DOCTYPE html><html><body><h1>Sign in to view more jobs</h1>"
    "<p>Continue to LinkedIn</p></body></html>"
)


def _client_over_two_offsets(monkeypatch, pages: dict[int, tuple[str, str]]):
    """Drive the real LinkedIn page loop over a declared two-offset plan.

    Only the fetch is replaced. The plan walk, the page observation, the abort
    decision and the trace are the production ones, because the claim being
    tested is about how those are wired together -- and a test that also stood
    in for the wiring would prove nothing about it.
    """

    client = BrowserSourceClient(
        BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0)
    )
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    fetched: list[int] = []

    def fake_fetch(url: str, *, page_offset: int, **_kwargs: object) -> BrowserFetchResult:
        fetched.append(page_offset)
        final_url, html = pages[page_offset]
        parsed = bs._linkedin_card_vacancies_from_html(
            html, page_url=url, apply_role_filter=False
        )
        job_ids = frozenset(
            str(vacancy.source_id) for vacancy in parsed if vacancy.source_id
        )
        return BrowserFetchResult(
            requested_url=url,
            final_url=final_url,
            html=html,
            html_sha256=f"{page_offset:064d}",
            page_offset=page_offset,
            planned_scroll_steps=1,
            completed_scroll_steps=1,
            scroll_trace=(),
            dom_unique_job_ids=job_ids,
            artifact_ref=None,
            scroll_checkpoints=(),
            scroll_stop_reason="max_steps",
        )

    monkeypatch.setattr(client, "fetch_page", fake_fetch)
    client.search_linkedin(
        "Chief Product Officer",
        geography_location="United Kingdom",
        execution_plan=LinkedInExecutionPlan(
            page_offsets=(0, 25), max_scroll_checkpoints=1
        ),
    )
    return client, fetched


def test_an_auth_wall_on_the_first_offset_does_not_cancel_the_declared_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The call site, not the predicate. This is the wiring, and it is the claim.

    A unit test of the abort predicate passes just as happily against a loop
    that never calls it, so the loop is driven here for real. An auth wall is a
    statement about one page; the declared plan is (0, 25), and the second
    offset was never asked whether it was reachable.
    """

    client, fetched = _client_over_two_offsets(
        monkeypatch,
        {
            0: ("https://www.linkedin.com/authwall?trk=jobs", AUTHWALL_HTML),
            25: (
                "https://www.linkedin.com/jobs/search?start=25",
                public_results_page(),
            ),
        },
    )

    assert fetched == [0, 25]
    assert client._last_search_trace["completed_page_offsets"] == [0, 25]


def test_the_plan_still_completes_when_both_offsets_are_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: the repair must not be a plan that completes come what may."""

    client, fetched = _client_over_two_offsets(
        monkeypatch,
        {
            offset: (
                f"https://www.linkedin.com/jobs/search?start={offset}",
                public_results_page(),
            )
            for offset in (0, 25)
        },
    )

    assert fetched == [0, 25]
    assert client._last_search_trace["stop_reason"] != "critical_degradation"


def test_a_checkpoint_challenge_does_cancel_the_declared_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety axis still stops the walk, and it is a different axis.

    /checkpoint is a challenge -- positive evidence that the source is pushing
    back -- and the frozen driver records it as one. /authwall is a statement
    about what the page showed. Collapsing them back into one predicate is what
    made a benign wall cancel a plan.
    """

    client, fetched = _client_over_two_offsets(
        monkeypatch,
        {
            0: (
                "https://www.linkedin.com/checkpoint/challenge/verify",
                "<html><body>verify</body></html>",
            ),
            25: (
                "https://www.linkedin.com/jobs/search?start=25",
                public_results_page(),
            ),
        },
    )

    assert fetched == [0]
    assert client._last_search_trace["stop_reason"] == "critical_degradation"


# ---------------------------------------------------------------------------
# D3: three controls on two axes. One positive sample and one negative
# predicate do not separate them -- a predicate calling everything usable
# passes every earlier test in this file.
# ---------------------------------------------------------------------------

SEARCH_URL = "https://www.linkedin.com/jobs/search"
# The heading as captured, with the typographic apostrophe and the padding.
EMPTY_STATE_HTML = (
    "<html><body><h1>  We couldn\u2019t   find a match for "
    "Chief Product Officer jobs  </h1></body></html>"
)


def test_control_one_the_safety_axis_needs_positive_evidence() -> None:
    """A challenge, a rendered challenge, a rate limit -- each on its own sign.

    Absence of results is a sign of none of them. Nor is the word "captcha":
    it is present on 288 of 288 captured pages in both modes, so a predicate
    resting on it would have stopped every run ever made.
    """

    assert (
        bs.linkedin_safety_reason(
            final_url="https://www.linkedin.com/checkpoint/challenge/verify",
            html="<html><body>verify</body></html>",
        )
        == "challenge_redirect"
    )
    assert (
        bs.linkedin_safety_reason(
            final_url=SEARCH_URL,
            html='<html><body><form action="/checkpoint/challenge"></form></body></html>',
        )
        == "rendered_challenge"
    )
    assert (
        bs.linkedin_safety_reason(
            final_url=SEARCH_URL,
            html="<html><body><h1>Too many requests</h1></body></html>",
        )
        == "rendered_rate_limit"
    )
    assert (
        bs.linkedin_safety_reason(final_url=SEARCH_URL, html=public_results_page())
        is None
    )
    # An empty page is not a pushback, and the word alone is not evidence.
    assert bs.linkedin_safety_reason(final_url=SEARCH_URL, html=EMPTY_STATE_HTML) is None
    assert (
        bs.linkedin_safety_reason(
            final_url=SEARCH_URL,
            html="<html><body><p>captcha</p></body></html>",
        )
        is None
    )


def test_control_one_fails_if_the_predicate_is_made_degenerate(monkeypatch) -> None:
    """Degenerate towards safety: everything a pushback."""

    monkeypatch.setattr(
        bs, "linkedin_safety_reason", lambda **_kwargs: "rendered_challenge"
    )

    assert (
        bs.linkedin_safety_reason(final_url=SEARCH_URL, html=public_results_page())
        is not None
    )


def test_control_two_the_auth_wall_is_read_from_the_final_url_path() -> None:
    """The classification axis, and a different axis from control one.

    Control two cannot stand in for control one: they read different values,
    and /checkpoint deliberately is not an auth wall here.
    """

    for path in ("/authwall", "/login", "/uas/login"):
        assert (
            bs.classify_linkedin_page(
                final_url=f"https://www.linkedin.com{path}", html="<html></html>"
            )
            == "auth_wall_surface"
        )
    assert (
        bs.classify_linkedin_page(
            final_url="https://www.linkedin.com/checkpoint/challenge",
            html="<html></html>",
        )
        != "auth_wall_surface"
    )


def test_a_page_can_carry_a_class_and_a_safety_reason_at_once() -> None:
    """Two measurements of one page, not a contradiction.

    The earlier revision put them in one enum and so could not be disjoint
    under any wording. Separating the axes is what makes them disjoint.
    """

    url = "https://www.linkedin.com/checkpoint/challenge/verify"
    html = "<html><body>verify</body></html>"

    assert bs.classify_linkedin_page(final_url=url, html=html) == (
        "unknown_non_usable_surface"
    )
    assert bs.linkedin_safety_reason(final_url=url, html=html) == "challenge_redirect"


def test_control_three_an_honestly_empty_page_is_told_by_its_heading() -> None:
    """Search URL, no cards, and the rendered heading. All three.

    The heading carries a typographic apostrophe, so a comparison against the
    ASCII form matches nothing. The no-results class name is not used: it
    appears on non-empty authenticated pages as well.
    """

    assert (
        bs.classify_linkedin_page(final_url=SEARCH_URL, html=EMPTY_STATE_HTML)
        == "terminal_empty_surface"
    )
    # Same heading, wrong surface: not a terminal empty page.
    assert (
        bs.classify_linkedin_page(
            final_url="https://www.linkedin.com/feed", html=EMPTY_STATE_HTML
        )
        != "terminal_empty_surface"
    )
    # Cards present wins, whatever the heading says.
    assert (
        bs.classify_linkedin_page(
            final_url=SEARCH_URL, html=public_results_page() + EMPTY_STATE_HTML
        )
        == "usable_result_surface"
    )


def test_control_three_rejects_the_ascii_apostrophe_shortcut() -> None:
    """The exact bytes matter, and this is the trap that was paid for once.

    A page whose heading uses the ASCII apostrophe is still an empty state
    once normalised. A predicate that skipped normalisation would pass the
    control above and fail here.
    """

    ascii_variant = (
        "<html><body><h1>We couldn't find a match for VP Product jobs</h1></body></html>"
    )

    assert (
        bs.classify_linkedin_page(final_url=SEARCH_URL, html=ascii_variant)
        == "terminal_empty_surface"
    )


def test_a_page_with_no_positive_sign_is_unknown_and_never_a_wall() -> None:
    """The fourth outcome is mandatory. An invented wall is a market claim."""

    assert (
        bs.classify_linkedin_page(
            final_url=SEARCH_URL, html="<html><body><div></div></body></html>"
        )
        == "unknown_non_usable_surface"
    )


def test_every_classification_is_one_of_the_five_declared_literals() -> None:
    """No abbreviated forms anywhere, and no sixth outcome."""

    cases = (
        (SEARCH_URL, public_results_page(), None),
        (SEARCH_URL, EMPTY_STATE_HTML, None),
        ("https://www.linkedin.com/authwall", "<html></html>", None),
        (SEARCH_URL, "<html></html>", 503),
        (SEARCH_URL, "<html></html>", None),
    )
    observed = {
        bs.classify_linkedin_page(final_url=url, html=html, status=status)
        for url, html, status in cases
    }

    assert observed <= set(bs.LINKEDIN_PAGE_CLASSIFICATIONS)
    assert observed == {
        "usable_result_surface",
        "terminal_empty_surface",
        "auth_wall_surface",
        "http_error_surface",
        "unknown_non_usable_surface",
    }


def test_the_status_branches_are_implemented_but_unreachable_from_the_page_loop() -> None:
    """Named gap, asserted so it cannot become a silent claim.

    BrowserFetchResult carries no HTTP status, so http_429_rate_limit,
    http_401_antibot_or_auth, http_403_antibot_or_auth and
    http_error_surface cannot arise from the page loop today. The logic is
    complete and covered; carrying the status is a separate change.
    """

    assert (
        bs.linkedin_safety_reason(final_url=SEARCH_URL, html="<html></html>", status=429)
        == "http_429_rate_limit"
    )
    assert (
        bs.linkedin_safety_reason(final_url=SEARCH_URL, html="<html></html>", status=403)
        == "http_403_antibot_or_auth"
    )
    # A 401 on an auth wall is benign: the wall is the page, not a pushback.
    assert (
        bs.linkedin_safety_reason(
            final_url="https://www.linkedin.com/authwall", html="<html></html>", status=401
        )
        is None
    )
    assert "status" not in {
        field for field in bs.BrowserFetchResult.__dataclass_fields__
    }


def test_the_page_loop_records_both_axes_for_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The call site, again. A classifier nobody calls classifies nothing.

    Two offsets, one usable and one an auth wall, driven through the real
    plan walk. Both pages must be labelled, and the wall must be labelled on
    the classification axis while leaving the safety axis empty -- it is a
    page, not a pushback.
    """

    client, fetched = _client_over_two_offsets(
        monkeypatch,
        {
            0: (
                "https://www.linkedin.com/jobs/search?start=0",
                public_results_page(),
            ),
            25: ("https://www.linkedin.com/authwall?trk=jobs", AUTHWALL_HTML),
        },
    )

    pages = client._last_search_trace["pages"]
    assert fetched == [0, 25]
    assert [page["page_classification"] for page in pages] == [
        "usable_result_surface",
        "auth_wall_surface",
    ]
    assert [page["safety_reason"] for page in pages] == [None, None]


def test_the_page_loop_records_a_challenge_on_the_safety_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the same page carries a classification of its own, not a wall."""

    client, fetched = _client_over_two_offsets(
        monkeypatch,
        {
            0: (
                "https://www.linkedin.com/checkpoint/challenge/verify",
                "<html><body>verify</body></html>",
            ),
            25: (
                "https://www.linkedin.com/jobs/search?start=25",
                public_results_page(),
            ),
        },
    )

    page = client._last_search_trace["pages"][0]
    assert fetched == [0]
    assert page["safety_reason"] == "challenge_redirect"
    assert page["page_classification"] == "unknown_non_usable_surface"
