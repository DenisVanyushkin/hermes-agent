from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from job_intel import cli, sources


def test_headhunter_query_plan_uses_bilingual_roles_and_structured_geography() -> None:
    build_plan = getattr(sources, "rotating_headhunter_query_plan", None)
    assert callable(build_plan), "HeadHunter needs a structured normal-query planner"

    plan = build_plan(limit=8, as_of=date(2026, 9, 30), rotation_slot=0)
    repeated = build_plan(limit=8, as_of=date(2026, 9, 30), rotation_slot=0)

    assert plan == repeated
    assert len(plan) == 8
    assert {item.role_family for item in plan} == {
        "executive_product",
        "digital_business",
        "customer_growth_commercial_hybrid",
        "product_business_unit",
        "general_management",
        "growth_monetization",
        "transformation_builder",
        "hybrid_executive_exploration",
    }

    expected_areas = {
        "remote_europe": ("21", "27"),
        "eu_gcc": ("65", "74", "208"),
        "apac_core": ("2108", "238", "233", "300"),
        "apac_plus": ("6", "40"),
    }
    expected_unknowns = {
        "remote_europe": ("Europe",),
        "eu_gcc": ("Saudi Arabia", "GCC"),
        "apac_core": ("APAC",),
        "apac_plus": (),
    }
    geography_words = (
        "remote",
        "europe",
        "germany",
        "uk",
        "netherlands",
        "poland",
        "uae",
        "saudi arabia",
        "gcc",
        "singapore",
        "indonesia",
        "malaysia",
        "thailand",
        "apac",
        "australia",
        "kazakhstan",
    )
    for item in plan:
        assert item.area_ids == expected_areas[item.geo_family]
        assert item.unsupported_geographies == expected_unknowns[item.geo_family]
        assert item.work_format == (
            "REMOTE" if item.geo_family == "remote_europe" else None
        )
        assert all(word not in item.query.casefold() for word in geography_words)
        assert any("\u0400" <= char <= "\u04ff" for char in item.query)
        assert any(char.isascii() and char.isalpha() for char in item.query)
        assert "coo-adjacent digital role" not in item.query.casefold()
        assert "chief commercial/product hybrid" not in item.query.casefold()


def test_headhunter_fetch_forwards_area_ids_and_remote_format(monkeypatch) -> None:
    item = next(
        item
        for item in sources.rotating_headhunter_query_plan(
            limit=4, as_of=date(2026, 9, 30), rotation_slot=0
        )
        if item.work_format == "REMOTE"
    )
    seen: dict[str, object] = {}

    def collect_search_results(**params):
        seen.update(params)
        return sources.hh_api.SearchResult(
            items=[], found=0, truncated=False, pages_requested=1
        )

    monkeypatch.setattr(
        sources.hh_api, "collect_search_results", collect_search_results
    )

    assert sources.fetch_headhunter_vacancies(item, per_page=10) == []
    assert seen["text"] == item.query
    assert seen["area"] == list(item.area_ids)
    assert seen["work_format"] == "REMOTE"
    assert "schedule" not in seen


def test_default_daily_collection_passes_structured_query_items(monkeypatch) -> None:
    class Store:
        def bootstrap(self) -> None:
            return None

    seen: list[object] = []

    def fetch(query, *, per_page):
        seen.append(query)
        return []

    monkeypatch.setattr(cli, "load_config_bundle", lambda: cli.DEFAULT_CONFIG)
    monkeypatch.setattr(cli, "_enabled_sources", lambda: {"headhunter"})
    monkeypatch.setattr(cli, "query_experiment_from_env", lambda: None)
    monkeypatch.setattr(cli, "fetch_headhunter_vacancies", fetch)

    collected = cli._collect_vacancies(store=Store())

    assert len(seen) == 6
    assert all(isinstance(item, sources.HeadHunterQueryPlanItem) for item in seen)
    assert collected.source_statuses["headhunter"]["status"] == "empty"
    planned = collected.source_statuses["headhunter"]["api_trace"][
        "planned_query_cells"
    ]
    assert len(planned) == 6
    assert all(item["area_ids"] for item in planned)
    assert all("work_format" in item for item in planned)
    assert all("unsupported_geographies" in item for item in planned)
    assert any(item["unsupported_geographies"] for item in planned)
    coverage = collected.source_statuses["headhunter"]["api_trace"][
        "query_coverage"
    ]
    assert coverage["resolved_geographies"]["eu_gcc"] == ["65", "74", "208"]
    assert "official HH area IDs" in coverage["geography_resolution"]


def test_query_trace_identity_includes_work_format_filter() -> None:
    item = next(
        item
        for item in sources.rotating_headhunter_query_plan(
            limit=4, as_of=date(2026, 9, 30), rotation_slot=0
        )
        if item.work_format == "REMOTE"
    )
    without_remote_filter = replace(item, work_format=None)

    with_filter = cli._query_attempt_event(
        "headhunter", item, outcome="planned", found_count=0
    )
    without_filter = cli._query_attempt_event(
        "headhunter", without_remote_filter, outcome="planned", found_count=0
    )

    assert with_filter["query_id"] != without_filter["query_id"]


def test_query_trace_identity_stays_stable_for_linkedin_and_legacy_hh() -> None:
    linkedin_item = sources.LinkedInQueryPlanItem(
        query="q",
        cell_id="singapore",
        location=None,
        geo_id=None,
        role_family="executive_product",
        context_family=None,
        query_mode="role_only",
        requested_geography="Singapore",
        resolved_geography="geo:x",
    )
    legacy_hh_item = sources.SourceQueryPlanItem(
        query="q",
        role_family="executive_product",
        context_family=None,
        geo_family="remote_europe",
        query_mode="role_only",
        requested_geography="remote_europe",
    )

    linkedin_event = cli._query_attempt_event(
        "linkedin", linkedin_item, outcome="planned", found_count=0
    )
    legacy_hh_event = cli._query_attempt_event(
        "headhunter", legacy_hh_item, outcome="planned", found_count=0
    )

    assert linkedin_event["query_id"] == "6916d84a7e875313"
    assert legacy_hh_event["query_id"] == "fca0ab11c6442878"


def test_linkedin_recency_experiment_keeps_structured_headhunter_plan() -> None:
    experiment = SimpleNamespace(
        name="linkedin_recency_ab",
        as_of=date(2026, 9, 30),
        rotation_slot=0,
    )

    plan = cli._headhunter_plan_for_collection(limit=6, experiment=experiment)

    assert all(isinstance(item, sources.HeadHunterQueryPlanItem) for item in plan)
    assert plan == sources.rotating_headhunter_query_plan(
        limit=6,
        as_of=date(2026, 9, 30),
        rotation_slot=0,
    )


def test_nonremote_area_groups_do_not_force_remote_format(monkeypatch) -> None:
    item = next(
        item
        for item in sources.rotating_headhunter_query_plan(
            limit=4, as_of=date(2026, 9, 30), rotation_slot=0
        )
        if item.work_format is None
    )
    seen: dict[str, object] = {}

    def collect_search_results(**params):
        seen.update(params)
        return sources.hh_api.SearchResult(
            items=[], found=0, truncated=False, pages_requested=1
        )

    monkeypatch.setattr(
        sources.hh_api, "collect_search_results", collect_search_results
    )

    sources.fetch_headhunter_vacancies(item)

    assert seen["area"] == list(item.area_ids)
    assert "work_format" not in seen
