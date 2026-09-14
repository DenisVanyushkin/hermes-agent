from __future__ import annotations

from datetime import date
import json

from job_intel import cli, sources
from job_intel.product_search.acquisition_probe import load_linkedin_geography_mapping
from job_intel.performance import RunPerformanceRecorder


EXPECTED_ROLE_FAMILIES = (
    "executive_product",
    "digital_business",
    "customer_growth_commercial_hybrid",
    "product_business_unit",
    "general_management",
    "growth_monetization",
    "transformation_builder",
    "hybrid_executive_exploration",
)


def test_product_sot_role_families_are_all_queryable() -> None:
    assert tuple(name for name, _terms in sources.ROLE_FAMILIES) == EXPECTED_ROLE_FAMILIES
    assert len({name for name, _terms in sources.ROLE_FAMILIES}) == 8


def test_linkedin_role_only_rotation_covers_all_eight_families() -> None:
    plan = sources.rotating_linkedin_queries(
        limit=18, as_of=date(2026, 9, 15), rotation_slot=0
    )

    assert len(plan) == 18
    assert {item.role_family for item in plan} == set(EXPECTED_ROLE_FAMILIES)
    assert all(item.query_mode == "role_only" for item in plan)
    assert all(item.context_family is None for item in plan)
    assert all(item.requested_geography == item.cell_id for item in plan)
    assert all(item.resolved_geography for item in plan)


def test_linkedin_context_is_an_explicit_variant_and_skips_measured_empty_terms() -> None:
    plan = sources.rotating_linkedin_queries(
        limit=18,
        as_of=date(2026, 9, 15),
        rotation_slot=0,
        query_mode="role_plus_context",
    )

    assert all(item.query_mode == "role_plus_context" for item in plan)
    assert all(item.context_family for item in plan)
    assert all(
        sources.linkedin_query_carries_measured_empty_term(item.query) is None
        for item in plan
    )


def test_same_date_rotation_slots_do_not_repeat_linkedin_pairs() -> None:
    morning = sources.rotating_linkedin_queries(
        limit=18, as_of=date(2026, 9, 15), rotation_slot=0
    )
    evening = sources.rotating_linkedin_queries(
        limit=18, as_of=date(2026, 9, 15), rotation_slot=1
    )

    assert {(item.query, item.cell_id) for item in morning}.isdisjoint(
        (item.query, item.cell_id) for item in evening
    )


def test_linkedin_geography_metadata_separates_resolved_and_unsupported_cells() -> None:
    mapping = load_linkedin_geography_mapping()
    coverage = sources.linkedin_geography_coverage()

    expected_supported = sorted(
        cell
        for cell, target in mapping.items()
        if target.status == "verified" and (target.location or target.geo_id)
    )
    expected_unsupported = sorted(set(mapping) - set(expected_supported))
    assert coverage["requested_cells"] == expected_supported
    assert coverage["unsupported_cells"] == expected_unsupported
    assert set(coverage["resolved_geographies"]) == set(expected_supported)


def test_headhunter_defaults_to_role_only_but_can_make_context_variant() -> None:
    plan = sources.rotating_source_query_plan(
        "headhunter", limit=6, as_of=date(2026, 9, 15), rotation_slot=0
    )
    context_plan = sources.rotating_source_query_plan(
        "headhunter",
        limit=6,
        as_of=date(2026, 9, 15),
        rotation_slot=0,
        query_mode="role_plus_context",
    )

    assert len({item.role_family for item in plan}) == 6
    assert all(item.query_mode == "role_only" for item in plan)
    assert all(item.context_family is None for item in plan)
    assert all(item.query_mode == "role_plus_context" for item in context_plan)
    assert all(item.context_family for item in context_plan)
    assert all(item.requested_geography for item in plan)
    assert all(item.resolved_geography is None for item in plan)


def test_query_experiment_is_disabled_without_flag_and_keeps_default_plan() -> None:
    settings = sources.query_experiment_from_env({})
    assert settings is None

    baseline = sources.rotating_linkedin_queries(
        limit=18, as_of=date(2026, 9, 15), rotation_slot=0
    )
    disabled = sources.linkedin_query_plan_for_run(
        limit=18, as_of=date(2026, 9, 15), rotation_slot=0, experiment=settings
    )
    assert disabled == baseline
    assert [item.experiment_branch for item in disabled] == ["default"] * 18


def test_query_experiment_alternates_both_arms_on_identical_pairs() -> None:
    settings = sources.query_experiment_from_env(
        {
            "JOB_INTEL_QUERY_EXPERIMENT": "role_context_ab",
            "JOB_INTEL_QUERY_EXPERIMENT_DATE": "2026-09-15",
            "JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT": "1",
        }
    )
    assert settings is not None
    assert settings.as_of == date(2026, 9, 15)
    assert settings.rotation_slot == 1

    linkedin = sources.linkedin_query_plan_for_run(
        limit=18,
        as_of=settings.as_of,
        rotation_slot=settings.rotation_slot,
        experiment=settings,
    )
    assert len(linkedin) == 18
    assert [item.experiment_branch for item in linkedin] == [
        branch for _pair in range(9) for branch in ("role_only", "role_context")
    ]
    for role_only, role_context in zip(linkedin[::2], linkedin[1::2]):
        assert (
            role_only.cell_id,
            role_only.role_family,
            role_only.location,
            role_only.geo_id,
            role_only.requested_geography,
            role_only.resolved_geography,
        ) == (
            role_context.cell_id,
            role_context.role_family,
            role_context.location,
            role_context.geo_id,
            role_context.requested_geography,
            role_context.resolved_geography,
        )
        assert role_only.query_mode == "role_only"
        assert role_context.query_mode == "role_plus_context"

    headhunter = sources.source_query_plan_for_run(
        "headhunter",
        limit=6,
        as_of=settings.as_of,
        rotation_slot=settings.rotation_slot,
        experiment=settings,
    )
    assert [item.experiment_branch for item in headhunter] == [
        branch for _pair in range(3) for branch in ("role_only", "role_context")
    ]
    for role_only, role_context in zip(headhunter[::2], headhunter[1::2]):
        assert (
            role_only.role_family,
            role_only.geo_family,
            role_only.requested_geography,
            role_only.resolved_geography,
        ) == (
            role_context.role_family,
            role_context.geo_family,
            role_context.requested_geography,
            role_context.resolved_geography,
        )


def test_query_experiment_rejects_missing_fixed_axes() -> None:
    import pytest

    with pytest.raises(ValueError, match="requires"):
        sources.query_experiment_from_env(
            {"JOB_INTEL_QUERY_EXPERIMENT": "role_context_ab"}
        )


def test_enabled_query_experiment_labels_each_linkedin_trace_event(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("JOB_INTEL_ENABLED_SOURCES", "linkedin")
    monkeypatch.setenv("JOB_INTEL_QUERY_EXPERIMENT", "role_context_ab")
    monkeypatch.setenv("JOB_INTEL_QUERY_EXPERIMENT_DATE", "2026-09-15")
    monkeypatch.setenv("JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT", "0")
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(Head of Product)",
            cell_id="uk",
            location="United Kingdom",
            geo_id=None,
            role_family="executive_product",
            query_mode="role_only",
            requested_geography="uk",
            resolved_geography="United Kingdom",
            experiment_branch="role_only",
        ),
        sources.LinkedInQueryPlanItem(
            query="(Head of Product) (payments)",
            cell_id="uk",
            location="United Kingdom",
            geo_id=None,
            role_family="executive_product",
            context_family="payments_fintech",
            query_mode="role_plus_context",
            requested_geography="uk",
            resolved_geography="United Kingdom",
            experiment_branch="role_context",
        ),
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_experiment_queries", lambda **_kw: plan)

    def fake_fetch(query, **_kwargs):
        cli.fetch_linkedin_vacancies.last_trace = {
            "pages_fetched": 1,
            "vacancies_extracted": 0,
        }
        return []

    monkeypatch.setattr(cli, "fetch_linkedin_vacancies", fake_fetch)
    result = cli._collect_vacancies(store=_store(tmp_path))

    trace = result.source_statuses["linkedin"]["search_trace"]
    assert [
        item["experiment_branch"] for item in trace["planned_query_cells"]
    ] == ["role_only", "role_context"]
    assert [
        item["experiment_branch"] for item in trace["executed_query_cells"]
    ] == ["role_only", "role_context"]
    assert trace["query_coverage"]["experiment"] == {
        "enabled": True,
        "name": "role_context_ab",
        "date": "2026-09-15",
        "rotation_slot": 0,
        "order": "alternating",
        "branch_sequence": ["role_only", "role_context"],
    }


def _store(tmp_path):
    from job_intel.store import JobIntelStore

    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    return store


def test_linkedin_composition_records_query_cell_family_and_outcome(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("JOB_INTEL_ENABLED_SOURCES", "linkedin")
    plan = [
        sources.LinkedInQueryPlanItem(
            query="(Head of Product)",
            cell_id="uk",
            location="United Kingdom",
            geo_id=None,
            role_family="executive_product",
            query_mode="role_only",
            requested_geography="uk",
            resolved_geography="United Kingdom",
        )
    ]
    monkeypatch.setattr(cli, "rotating_linkedin_queries", lambda **_kw: plan)

    def fake_fetch(query, **_kwargs):
        cli.fetch_linkedin_vacancies.last_trace = {
            "pages_fetched": 1,
            "vacancies_extracted": 1,
        }
        cli.fetch_linkedin_vacancies.last_health = {"pages_fetched": 1}
        return []

    monkeypatch.setattr(cli, "fetch_linkedin_vacancies", fake_fetch)
    performance = RunPerformanceRecorder(1)
    result = cli._collect_vacancies(store=_store(tmp_path), performance=performance)

    trace = result.source_statuses["linkedin"]["search_trace"]
    assert trace["planned_query_cells"][0]["outcome"] == "planned"
    event = trace["executed_query_cells"][0]
    assert event["role_family"] == "executive_product"
    assert event["cell_id"] == "uk"
    assert event["outcome"] == "empty"
    assert event["found_count"] == 0
    assert event["experiment_branch"] == "default"
    span = next(item for item in performance.spans() if item.span_name == "linkedin.search_pages")
    metadata = json.loads(span.metadata_json or "{}")
    assert metadata["executed_query_cells"][0]["query_id"] == event["query_id"]


def test_headhunter_composition_records_query_family_and_outcome(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("JOB_INTEL_ENABLED_SOURCES", "headhunter")
    plan = [
        sources.SourceQueryPlanItem(
            query="(Head of Product) (remote Europe)",
            role_family="executive_product",
            context_family=None,
            geo_family="remote_europe",
            query_mode="role_only",
            requested_geography="remote_europe",
            resolved_geography=None,
        )
    ]
    monkeypatch.setattr(cli, "rotating_source_query_plan", lambda *_args, **_kw: plan)
    monkeypatch.setattr(cli, "fetch_headhunter_vacancies", lambda query, **_kw: [])

    result = cli._collect_vacancies(store=_store(tmp_path))
    trace = result.source_statuses["headhunter"]["api_trace"]
    assert trace["planned_query_cells"][0]["outcome"] == "planned"
    event = trace["executed_query_cells"][0]
    assert event["role_family"] == "executive_product"
    assert event["requested_geography"] == "remote_europe"
    assert event["outcome"] == "empty"
    assert event["experiment_branch"] == "default"
