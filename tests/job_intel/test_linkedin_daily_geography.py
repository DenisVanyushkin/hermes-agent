"""The daily pipeline must give LinkedIn a geography target, not geography words.

Since 2026-08-27 a LinkedIn search carrying neither a location nor a geoId is
refused outright: the commit that introduced the guard moved the Gate A path
onto that convention and left the daily path on the old one, where geography
was words inside one query string and no target was passed at all. Every
LinkedIn call in runs 470 and 471 raised `blocked_unsupported_geography`.

The geography axis here is the verified mapping, not `GEO_FAMILIES`: those
families are a mixed keyword vocabulary with no verified target contract behind
them, so nothing in them establishes that an entry names somewhere LinkedIn can
be pointed at.

Half of this file drives `cli._collect_vacancies` rather than the builder. That
is deliberate and was learned the hard way: an earlier version of these tests
monkeypatched `fetch_linkedin_vacancies` and then called it itself, so deleting
`location=` and `geo_id=` from the production call left all six green.
"""

from __future__ import annotations

from datetime import date
import json

import pytest

from job_intel import cli, sources
from job_intel.product_search.acquisition_probe import load_linkedin_geography_mapping


# --------------------------------------------------------------------------
# the builder
# --------------------------------------------------------------------------


def test_every_query_carries_a_geography_target() -> None:
    """A location *or* a geoId -- the source accepts either.

    Demanding both would drop cells the mapping deliberately expresses the
    other way round.
    """

    plan = sources.rotating_linkedin_queries(limit=6)

    assert len(plan) == 6
    for item in plan:
        assert item.query
        assert item.cell_id
        assert item.location or item.geo_id


def test_geography_words_leave_the_query_text() -> None:
    """Otherwise the geography target is searched twice: as a filter and as a
    keyword. Target rather than country, because five of the eligible cells --
    ``dach`` and ``benelux`` among them -- name groups.
    """

    geo_words = {word for _name, words in sources.GEO_FAMILIES for word in words}
    plan = sources.rotating_linkedin_queries(limit=12)

    for item in plan:
        for word in geo_words:
            assert word.lower() not in item.query.lower(), (word, item.query)


def test_only_verified_cells_are_targeted() -> None:
    """An `unsupported` cell is not a place to point a search at."""

    mapping = load_linkedin_geography_mapping()
    plan = sources.rotating_linkedin_queries(limit=24)

    assert plan
    for item in plan:
        assert mapping[item.cell_id].status == "verified"


def test_no_place_repeats_until_every_place_has_been_asked() -> None:
    """The exhaustiveness claim, stated as the property it is.

    A shuffle satisfies "covers more than one geography" while revisiting a
    cell twice before others are reached once -- which is what the previous
    implementation did, because its seed mixed in fresh `SystemRandom` entropy
    on every call. This asserts the ordering directly.
    """

    eligible = [
        cell
        for cell, target in load_linkedin_geography_mapping().items()
        if target.status == "verified" and (target.location or target.geo_id)
    ]
    plan = sources.rotating_linkedin_queries(limit=len(eligible))

    cells = [item.cell_id for item in plan]
    assert len(cells) == len(eligible)
    assert sorted(cells) == sorted(eligible)


def test_a_second_pass_reuses_places_but_not_pairs() -> None:
    """Past the first full pass the text advances instead of the place."""

    eligible = sum(
        1
        for _cell, target in load_linkedin_geography_mapping().items()
        if target.status == "verified" and (target.location or target.geo_id)
    )
    plan = sources.rotating_linkedin_queries(limit=eligible + 3)

    pairs = [(item.query, item.cell_id) for item in plan]
    assert len(pairs) == len(set(pairs))
    assert len({item.query for item in plan}) > 1


def test_the_order_does_not_change_within_a_day() -> None:
    """Determinism is the whole basis of the exhaustiveness claim above.

    The day is passed in rather than read from the clock: taking it from the
    clock makes this test fail once a year at midnight for a reason that has
    nothing to do with the property being asserted.
    """

    first = sources.rotating_linkedin_queries(limit=9, as_of=date(2026, 9, 4))
    second = sources.rotating_linkedin_queries(limit=9, as_of=date(2026, 9, 4))

    assert [(i.query, i.cell_id) for i in first] == [
        (i.query, i.cell_id) for i in second
    ]


def test_consecutive_days_open_on_different_places() -> None:
    """The claim the offset exists to support, on the day that breaks it.

    A decimal `YYYYMMDD` offset jumps 72 from 2028-02-29 to 2028-03-01, which
    is exactly three cycles of the twenty-four eligible cells, so both days
    would open on the same place and the first place would go two days without
    being asked. Counting days instead of reading the decimal makes the jump
    1, as it is everywhere else.
    """

    leap_day = sources.rotating_linkedin_queries(limit=1, as_of=date(2028, 2, 29))
    next_day = sources.rotating_linkedin_queries(limit=1, as_of=date(2028, 3, 1))

    assert leap_day[0].cell_id != next_day[0].cell_id


def test_a_month_of_days_opens_on_a_month_of_places() -> None:
    """Not just one boundary: every step of a run of days moves by one.

    A single transition can pass by luck. This walks twenty-four consecutive
    days across the same February boundary and asserts each opens somewhere
    new, which is the same thing as saying the whole cycle is covered.
    """

    days = [date(2028, 2, 20).toordinal() + n for n in range(24)]
    openings = [
        sources.rotating_linkedin_queries(limit=1, as_of=date.fromordinal(d))[0].cell_id
        for d in days
    ]

    assert len(set(openings)) == 24


# --------------------------------------------------------------------------
# the production call site
# --------------------------------------------------------------------------


def _only_linkedin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_INTEL_ENABLED_SOURCES", "linkedin")


def _store(tmp_path):
    from job_intel.store import JobIntelStore

    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    return store


def test_the_collector_passes_the_location_to_the_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Through `_collect_vacancies`, so deleting the argument fails here."""

    _only_linkedin(monkeypatch)
    seen: list[dict[str, object]] = []

    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="uk_gm",
            location="United Kingdom",
            geo_id=None,
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)
    monkeypatch.setattr(
        cli,
        "fetch_linkedin_vacancies",
        lambda query, **kwargs: seen.append({"query": query, **kwargs}) or [],
    )

    cli._collect_vacancies(store=_store(tmp_path))

    assert len(seen) == 1
    assert seen[0]["location"] == "United Kingdom"
    # Without the cell the search still runs and the coverage trace loses the
    # place it was run for, which is a silent hole rather than a failure.
    assert seen[0]["cell_id"] == "uk_gm"


def test_the_collector_passes_a_geoid_when_that_is_the_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A geoId-only cell must survive the call site just as a location does.

    Kept separate from the location test on purpose: one production argument
    each, so dropping either one is caught by its own failure.
    """

    _only_linkedin(monkeypatch)
    seen: list[dict[str, object]] = []

    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="kz_gm",
            location=None,
            geo_id="106049128",
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)
    monkeypatch.setattr(
        cli,
        "fetch_linkedin_vacancies",
        lambda query, **kwargs: seen.append({"query": query, **kwargs}) or [],
    )

    cli._collect_vacancies(store=_store(tmp_path))

    assert len(seen) == 1
    assert seen[0]["geo_id"] == "106049128"
    assert seen[0]["cell_id"] == "kz_gm"


def test_no_eligible_geography_is_not_an_empty_market(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A configuration gap must not be reported as a source that found nothing.

    `empty` sits alongside `ok` in every downstream non-failure set in this
    module, so an empty plan falling through would be counted as a clean run
    against a market that had nothing -- the same laundering the geography
    guard exists to prevent.
    """

    _only_linkedin(monkeypatch)
    called: list[str] = []

    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: [])
    monkeypatch.setattr(
        cli,
        "fetch_linkedin_vacancies",
        lambda query, **kwargs: called.append(query) or [],
    )

    result = cli._collect_vacancies(store=_store(tmp_path))
    status = result.source_statuses["linkedin"]

    assert not called
    assert status["status"] not in {"ok", "empty", "skipped"}
    assert any("blocked_no_eligible_geography" in err for err in status["errors"])


def test_daily_linkedin_public_mode_is_explicitly_env_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Keep the old authenticated default and opt into public mode explicitly."""

    _only_linkedin(monkeypatch)
    seen: list[dict[str, object]] = []
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="uk_gm",
            location="United Kingdom",
            geo_id=None,
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)
    monkeypatch.setattr(
        cli,
        "fetch_linkedin_vacancies",
        lambda query, **kwargs: seen.append({"query": query, **kwargs}) or [],
    )

    monkeypatch.delenv("JOB_INTEL_LINKEDIN_ALLOW_UNAUTHENTICATED", raising=False)
    cli._collect_vacancies(store=_store(tmp_path))
    assert seen[-1]["allow_unauthenticated"] is False

    monkeypatch.setenv("JOB_INTEL_LINKEDIN_ALLOW_UNAUTHENTICATED", "1")
    cli._collect_vacancies(store=_store(tmp_path))
    assert seen[-1]["allow_unauthenticated"] is True


def test_daily_linkedin_detail_budget_is_round_robin_across_queries(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _only_linkedin(monkeypatch)
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_BUDGET", "2")
    seen: list[dict[str, object]] = []
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="uk_gm",
            location="United Kingdom",
            geo_id=None,
        ),
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="ca_gm",
            location="Canada",
            geo_id=None,
        ),
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="kz_gm",
            location="Kazakhstan",
            geo_id=None,
        ),
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)

    def fake_fetch(query, **kwargs):
        seen.append({"query": query, **kwargs})
        budget = int(kwargs["detail_page_budget"])
        cli.fetch_linkedin_vacancies.last_trace = {
            "detail_pages_budget_opened": min(1, budget),
            "detail_pages_opened": min(1, budget),
            "detail_description_lengths": [4_000] if budget else [],
        }
        return []

    monkeypatch.setattr(cli, "fetch_linkedin_vacancies", fake_fetch)

    cli._collect_vacancies(store=_store(tmp_path))

    assert [call["detail_page_budget"] for call in seen] == [1, 1, 0]
    assert sum(int(call["detail_page_budget"]) for call in seen) <= 2


def test_daily_linkedin_detail_budget_carries_unused_quota_forward(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _only_linkedin(monkeypatch)
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_BUDGET", "6")
    seen: list[dict[str, object]] = []
    opened_counts: list[int] = []
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="early",
            location="United Kingdom",
            geo_id=None,
        ),
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="late",
            location="Canada",
            geo_id=None,
        ),
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)

    def fake_fetch(query, **kwargs):
        budget = int(kwargs["detail_page_budget"])
        eligible = 1 if kwargs["cell_id"] == "early" else 20
        opened = min(eligible, budget)
        seen.append({"query": query, **kwargs})
        opened_counts.append(opened)
        cli.fetch_linkedin_vacancies.last_trace = {
            "detail_pages_budget_opened": opened,
            "detail_pages_opened": opened,
        }
        return []

    monkeypatch.setattr(cli, "fetch_linkedin_vacancies", fake_fetch)

    cli._collect_vacancies(store=_store(tmp_path))

    assert [call["detail_page_budget"] for call in seen] == [3, 5]
    assert opened_counts == [1, 5]
    assert sum(opened_counts) <= 6


def test_daily_linkedin_passes_persisted_enrichment_urls_to_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _only_linkedin(monkeypatch)
    seen: list[dict[str, object]] = []
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="uk_gm",
            location="United Kingdom",
            geo_id=None,
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)
    monkeypatch.setattr(
        cli.JobIntelStore,
        "fetch_linkedin_enriched_urls",
        lambda _store: {"https://www.linkedin.com/jobs/view/42"},
    )
    monkeypatch.setattr(
        cli,
        "fetch_linkedin_vacancies",
        lambda query, **kwargs: seen.append({"query": query, **kwargs}) or [],
    )

    cli._collect_vacancies(store=_store(tmp_path))

    assert seen[0]["detail_skip_urls"] == {
        "https://www.linkedin.com/jobs/view/42"
    }


def test_daily_linkedin_passes_persisted_first_seen_times_to_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _only_linkedin(monkeypatch)
    seen: list[dict[str, object]] = []
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="uk_gm",
            location="United Kingdom",
            geo_id=None,
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)
    monkeypatch.setattr(
        cli.JobIntelStore,
        "fetch_linkedin_first_seen_at",
        lambda _store: {
            "https://www.linkedin.com/jobs/view/42": "2026-09-13T10:00:00+00:00"
        },
    )
    monkeypatch.setattr(
        cli,
        "fetch_linkedin_vacancies",
        lambda query, **kwargs: seen.append({"query": query, **kwargs}) or [],
    )

    cli._collect_vacancies(store=_store(tmp_path))

    assert seen[0]["detail_first_seen_at"] == {
        "https://www.linkedin.com/jobs/view/42": "2026-09-13T10:00:00+00:00"
    }


def test_linkedin_detail_trace_reaches_kpi_health_and_performance_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _only_linkedin(monkeypatch)
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(head of product) (fintech)",
            cell_id="uk_gm",
            location="United Kingdom",
            geo_id=None,
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)

    def fake_fetch(query, **_kwargs):
        cli.fetch_linkedin_vacancies.last_trace = {
            "detail_pages_planned": 20,
            "detail_pages_opened": 20,
            "detail_pages_filled": 19,
            "detail_pages_blocked": 0,
            "detail_pages_errors": 1,
            "detail_description_lengths": [6_000, 6_500],
            "pages_fetched": 1,
            "login_wall_hits": 4,
            "auth_redirects": 3,
            "anti_bot_events": 4,
            "extraction_failures": 2,
            "vacancies_extracted": 2,
        }
        cli.fetch_linkedin_vacancies.last_health = {
            "pages_fetched": 1,
            "login_walls": 1,
            "auth_redirects": 1,
            "anti_bot_events": 1,
            "extraction_failures": 1,
            "detail_pages_opened": 20,
        }
        return []

    monkeypatch.setattr(cli, "fetch_linkedin_vacancies", fake_fetch)
    from job_intel.performance import RunPerformanceRecorder

    performance = RunPerformanceRecorder(1)
    result = cli._collect_vacancies(store=_store(tmp_path), performance=performance)
    status = result.source_statuses["linkedin"]
    health = status["session_health"]
    assert {
        key: health[key]
        for key in (
            "detail_pages_planned",
            "detail_pages_opened",
            "detail_pages_filled",
            "detail_pages_blocked",
            "detail_pages_errors",
            "detail_description_median_chars",
            "login_walls",
            "auth_redirects",
            "anti_bot_events",
            "extraction_failures",
        )
    } == {
        "detail_pages_planned": 20,
        "detail_pages_opened": 20,
        "detail_pages_filled": 19,
        "detail_pages_blocked": 0,
        "detail_pages_errors": 1,
        "detail_description_median_chars": 6_250,
        "login_walls": 4,
        "auth_redirects": 3,
        "anti_bot_events": 4,
        "extraction_failures": 2,
    }

    span = next(
        item for item in performance.spans() if item.span_name == "linkedin.search_pages"
    )
    metadata = json.loads(span.metadata_json or "{}")
    assert metadata["detail_pages_planned"] == 20
    assert metadata["detail_pages_opened"] == 20
    assert metadata["detail_pages_filled"] == 19
    assert metadata["detail_pages_blocked"] == 0
    assert metadata["detail_pages_errors"] == 1
    assert metadata["detail_description_median_chars"] == 6_250
    assert metadata["login_wall_hits"] == 4
    assert metadata["auth_redirects"] == 3
    assert metadata["anti_bot_events"] == 4
    assert metadata["extraction_failures"] == 2
