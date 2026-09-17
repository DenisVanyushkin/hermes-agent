from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import types

import pytest

from job_intel import browser_worker, cli, sources
from job_intel import browser_sourcing
from job_intel.browser_sourcing import BrowserFetchResult, BrowserSourceClient
from job_intel.product_search.acquisition_probe import LinkedInExecutionPlan


BASE_ENV = {
    "JOB_INTEL_QUERY_EXPERIMENT": "linkedin_recency_ab",
    "JOB_INTEL_QUERY_EXPERIMENT_DATE": "2026-09-17",
    "JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT": "1",
}


def _load_probe_module():
    path = Path(__file__).parents[2] / "scripts/job_intel_linkedin_recency_probe.py"
    if not path.exists():
        pytest.fail("feature missing: LinkedIn recency probe script does not exist")
    spec = importlib.util.spec_from_file_location("job_intel_linkedin_recency_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_query_experiment_accepts_recency_name_with_fixed_axes() -> None:
    settings = sources.query_experiment_from_env(BASE_ENV)

    assert settings is not None
    assert settings.name == "linkedin_recency_ab"
    assert settings.as_of == date(2026, 9, 17)
    assert settings.rotation_slot == 1


def test_recency_plan_interleaves_identical_control_treatment_pairs() -> None:
    settings = sources.query_experiment_from_env(BASE_ENV)
    assert settings is not None

    plan = sources.linkedin_query_plan_for_run(limit=18, experiment=settings)

    assert len(plan) == 18
    assert [item.experiment_branch for item in plan] == [
        branch for _ in range(9) for branch in ("control", "treatment")
    ]
    for control, treatment in zip(plan[::2], plan[1::2]):
        assert (
            control.cell_id,
            control.role_family,
            control.location,
            control.geo_id,
            control.requested_geography,
            control.resolved_geography,
            control.query,
        ) == (
            treatment.cell_id,
            treatment.role_family,
            treatment.location,
            treatment.geo_id,
            treatment.requested_geography,
            treatment.resolved_geography,
            treatment.query,
        )
        assert control.url_variant == "default"
        assert treatment.url_variant == "recency_24h"


def test_recency_experiment_leaves_headhunter_on_normal_plan() -> None:
    settings = sources.query_experiment_from_env(BASE_ENV)
    assert settings is not None

    actual = sources.source_query_plan_for_run(
        "headhunter", limit=6, experiment=settings
    )
    expected = sources.rotating_source_query_plan(
        "headhunter",
        limit=6,
        as_of=settings.as_of,
        rotation_slot=settings.rotation_slot,
    )

    assert actual == expected
    assert all(item.experiment_branch == "default" for item in actual)


def test_cli_routes_recency_only_to_linkedin_and_keeps_branch_trace_metadata() -> None:
    settings = sources.query_experiment_from_env(BASE_ENV)
    assert settings is not None

    linkedin_plan = cli._linkedin_plan_for_collection(settings)
    headhunter_plan = cli._headhunter_plan_for_collection(limit=6, experiment=settings)
    event = cli._query_attempt_event(
        "linkedin", linkedin_plan[1], outcome="planned", found_count=0
    )
    span_metadata = cli._query_experiment_trace_metadata(settings, linkedin_plan)

    assert linkedin_plan[1].experiment_branch == "treatment"
    assert linkedin_plan[1].url_variant == "recency_24h"
    assert all(item.experiment_branch == "default" for item in headhunter_plan)
    assert event["experiment_branch"] == "treatment"
    assert span_metadata["name"] == "linkedin_recency_ab"
    assert span_metadata["branch_sequence"][:4] == [
        "control", "treatment", "control", "treatment"
    ]


def test_role_context_experiment_keeps_existing_two_arms() -> None:
    settings = sources.query_experiment_from_env(
        {
            "JOB_INTEL_QUERY_EXPERIMENT": "role_context_ab",
            "JOB_INTEL_QUERY_EXPERIMENT_DATE": "2026-09-17",
            "JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT": "0",
        }
    )
    assert settings is not None

    plan = sources.linkedin_query_plan_for_run(limit=18, experiment=settings)

    assert [item.experiment_branch for item in plan] == [
        branch for _ in range(9) for branch in ("role_only", "role_context")
    ]
    assert all(
        control.query != treatment.query
        for control, treatment in zip(plan[::2], plan[1::2])
    )


def test_control_url_is_byte_identical_and_treatment_adds_only_recency_filter() -> None:
    control = browser_sourcing.build_linkedin_search_url(
        keywords="VP Product",
        location="United Kingdom",
        start=25,
        url_variant="default",
    )
    treatment = browser_sourcing.build_linkedin_search_url(
        keywords="VP Product",
        location="United Kingdom",
        start=25,
        url_variant="recency_24h",
    )

    assert control == (
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product"
        "&location=United+Kingdom&start=25"
    )
    assert treatment == control + "&f_TPR=r86400"
    assert "sortBy" not in treatment
    assert "r86400" not in "VP Product"


def test_treatment_variant_is_used_for_base_and_nonzero_offset_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BrowserSourceClient(
        browser_sourcing.BrowserAcquisitionConfig(
            source_name="linkedin", min_delay_ms=0, max_delay_ms=0
        )
    )
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: "with_session")
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    requested: list[str] = []

    def fake_fetch_page(url: str, **kwargs: object) -> BrowserFetchResult:
        requested.append(url)
        return BrowserFetchResult(
            requested_url=url,
            final_url=url,
            html="<html><body></body></html>",
            html_sha256="a" * 64,
            page_offset=int(kwargs["page_offset"]),
            planned_scroll_steps=0,
            completed_scroll_steps=0,
            scroll_trace=(),
            dom_unique_job_ids=frozenset(),
            artifact_ref=None,
        )

    monkeypatch.setattr(client, "fetch_page", fake_fetch_page)
    client.search_linkedin(
        "VP Product",
        geography_location="United Kingdom",
        execution_plan=LinkedInExecutionPlan(
            page_offsets=(0, 25), max_scroll_checkpoints=1
        ),
        url_variant="recency_24h",
    )

    assert requested == [
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product"
        "&location=United+Kingdom&f_TPR=r86400",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product"
        "&location=United+Kingdom&start=25&f_TPR=r86400",
    ]


def test_wrapper_passes_url_variant_to_real_worker_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sources, "browser_native_available", lambda: True)
    monkeypatch.setattr(sources, "_ensure_required_browser_profile", lambda *_args: None)
    monkeypatch.setattr(sources, "_browser_config", lambda _source: types.SimpleNamespace())
    captured: list[str] = []

    def fake_worker(*args: str, **_kwargs: object) -> dict[str, object]:
        captured.extend(args)
        return {"vacancies": [], "session_health": {}, "search_trace": {}}

    monkeypatch.setattr(sources, "_browser_worker_payload", fake_worker)

    sources.fetch_linkedin_vacancies(
        "VP Product",
        location="United Kingdom",
        url_variant="recency_24h",
    )

    assert captured[captured.index("--url-variant") + 1] == "recency_24h"


def test_worker_passes_url_variant_to_browser_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        def search_linkedin(self, _query: str, **kwargs: object) -> list[object]:
            calls.append(kwargs)
            return []

        def session_health_snapshot(self) -> dict[str, object]:
            return {}

    retry_options: list[object] = []

    def fake_with_browser_source(_source: str, fn, **kwargs: object):
        retry_options.append(kwargs["retry_on_attach"])
        return fn(FakeClient())

    monkeypatch.setattr(
        browser_worker,
        "_with_browser_source",
        fake_with_browser_source,
    )

    browser_worker._run_linkedin(
        "VP Product",
        max_pages=1,
        location="United Kingdom",
        url_variant="recency_24h",
        no_retries=True,
    )

    assert calls[0]["url_variant"] == "recency_24h"
    assert retry_options == [False]


def test_search_trace_records_experiment_branch_next_to_requested_and_final_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BrowserSourceClient(
        browser_sourcing.BrowserAcquisitionConfig(
            source_name="linkedin", min_delay_ms=0, max_delay_ms=0
        )
    )
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: "with_session")
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    monkeypatch.setattr(
        client,
        "fetch_page",
        lambda url, **kwargs: BrowserFetchResult(
            requested_url=url,
            final_url=url + "&position=1",
            html="<html><body></body></html>",
            html_sha256="b" * 64,
            page_offset=int(kwargs["page_offset"]),
            planned_scroll_steps=0,
            completed_scroll_steps=0,
            scroll_trace=(),
            dom_unique_job_ids=frozenset(),
            artifact_ref=None,
        ),
    )

    client.search_linkedin(
        "VP Product",
        geography_location="United Kingdom",
        execution_plan=LinkedInExecutionPlan(page_offsets=(0,), max_scroll_checkpoints=1),
        experiment_branch="treatment",
        url_variant="recency_24h",
    )

    page = client._last_search_trace["pages"][0]
    assert page["experiment_branch"] == "treatment"
    assert page["requested_url"].endswith("f_TPR=r86400")
    assert page["final_url"].endswith("position=1")


def test_probe_dry_run_does_not_dispatch_browser_and_prints_six_urls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = _load_probe_module()
    monkeypatch.setattr(
        probe,
        "fetch_linkedin_vacancies",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not invoke worker"),
    )

    assert probe.main(["--date", "2026-09-17", "--rotation-slot", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["live"] is False
    assert len(payload["queries"]) == 6
    assert [item["order"] for item in payload["queries"]] == list(range(1, 7))
    assert [item["branch"] for item in payload["queries"]] == [
        "control", "treatment", "treatment", "control", "control", "treatment"
    ]
    assert all(item["requested_url"].startswith("https://www.linkedin.com/jobs/search/?") for item in payload["queries"])


def test_probe_live_uses_real_source_wrapper_and_serializable_execution_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _load_probe_module()
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(probe, "fetch_linkedin_vacancies", sources.fetch_linkedin_vacancies)
    monkeypatch.setattr(probe, "_preflight_services", lambda: None)
    monkeypatch.setattr(sources, "browser_native_available", lambda: True)
    monkeypatch.setattr(sources, "_browser_config", lambda *_args: types.SimpleNamespace())
    monkeypatch.setattr(sources, "_ensure_required_browser_profile", lambda *_args: None)

    def fake_worker_payload(command: str, *args: str, **_kwargs: object) -> dict[str, object]:
        worker_args = (command, *args)
        calls.append(worker_args)
        assert command == "linkedin"
        plan_index = args.index("--execution-plan-json")
        execution_plan = json.loads(args[plan_index + 1])
        assert execution_plan["page_offsets"] == [0]
        assert args[args.index("--detail-page-budget") + 1] == "0"
        assert "--no-retries" in args
        variant = (
            args[args.index("--url-variant") + 1]
            if "--url-variant" in args
            else "default"
        )
        branch = (
            args[args.index("--experiment-branch") + 1]
            if "--experiment-branch" in args
            else "control"
        )
        call_number = len(calls)
        job_ids = {
            1: ["1001", "1002"],
            2: ["1002", "1003"],
            3: ["2001"],
            4: ["2001", "2002"],
            5: ["3001", "3002"],
            6: ["3002", "3003"],
        }[call_number]
        requested_url = f"https://www.linkedin.com/jobs/search/?call={call_number}"
        if variant == "recency_24h":
            requested_url += "&f_TPR=r86400"
        return {
            "ok": True,
            "vacancies": [],
            "session_health": {},
            "search_trace": {
                "stop_reason": "max_steps",
                "login_wall_hits": 0,
                "auth_redirects": 0,
                "anti_bot_events": 0,
                "pages": [{
                    "requested_url": requested_url,
                    "final_url": requested_url,
                    "page_classification": "usable_result_surface",
                    "safety_reason": None,
                    "dom_unique_job_ids": job_ids,
                    "experiment_branch": branch,
                }],
            },
        }

    monkeypatch.setattr(sources, "_browser_worker_payload", fake_worker_payload)

    report = probe.run_probe(
        cells=list(probe.DEFAULT_CELLS),
        as_of=date(2026, 9, 17),
        rotation_slot=1,
        output_dir=tmp_path,
        live=True,
    )

    assert len(calls) == 6, report
    assert report["stop_reason"] is None
    assert [item["branch"] for item in report["queries"]] == [
        "control", "treatment", "treatment", "control", "control", "treatment"
    ]
    assert report["pairs"][0] == {
        "pair_index": 1,
        "cell_id": "dach",
        "treatment_only": ["1003"],
        "control_only": ["1001"],
        "overlap": ["1002"],
        "stop_reason": None,
    }
    assert "--url-variant" not in calls[0]
    assert calls[1][calls[1].index("--url-variant") + 1] == "recency_24h"


def test_probe_live_stops_after_safety_signal_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _load_probe_module()
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(probe, "fetch_linkedin_vacancies", sources.fetch_linkedin_vacancies)
    monkeypatch.setattr(probe, "_preflight_services", lambda: None)
    monkeypatch.setattr(sources, "browser_native_available", lambda: True)
    monkeypatch.setattr(sources, "_browser_config", lambda *_args: types.SimpleNamespace())
    monkeypatch.setattr(sources, "_ensure_required_browser_profile", lambda *_args: None)

    def fake_worker_payload(command: str, *args: str, **_kwargs: object) -> dict[str, object]:
        calls.append((command, *args))
        requested_url = f"https://www.linkedin.com/jobs/search/?call={len(calls)}"
        page: dict[str, object] = {
            "requested_url": requested_url,
            "final_url": requested_url,
            "page_classification": "usable_result_surface",
            "safety_reason": "login_wall" if len(calls) == 2 else None,
            "dom_unique_job_ids": [str(len(calls))],
        }
        return {
            "ok": True,
            "vacancies": [],
            "session_health": {},
            "search_trace": {
                "stop_reason": "max_steps",
                "login_wall_hits": 1 if len(calls) == 2 else 0,
                "auth_redirects": 0,
                "anti_bot_events": 0,
                "pages": [page],
            },
        }

    monkeypatch.setattr(sources, "_browser_worker_payload", fake_worker_payload)

    report = probe.run_probe(
        cells=list(probe.DEFAULT_CELLS),
        as_of=date(2026, 9, 17),
        rotation_slot=1,
        output_dir=tmp_path,
        live=True,
    )

    assert len(calls) == 2, report
    assert report["stop_reason"] == "login_wall"
    assert report["pairs"][0]["stop_reason"] == "login_wall"
    assert report["pairs"][1]["stop_reason"] == "not_attempted_after: login_wall"
    assert report["queries"][1]["safety"]["login_wall_hits"] == 1


def test_probe_stops_on_first_error_without_retry_or_detail_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _load_probe_module()
    calls: list[dict[str, object]] = []

    def fake_fetch(query: str, **kwargs: object) -> list[object]:
        calls.append({"query": query, **kwargs})
        if len(calls) == 2:
            raise RuntimeError("login wall")
        probe.fetch_linkedin_vacancies.last_trace = {
            "pages": [
                {
                    "requested_url": "https://www.linkedin.com/jobs/search/?keywords=x",
                    "final_url": "https://www.linkedin.com/jobs/search/?keywords=x",
                    "page_classification": "public_results",
                    "safety_reason": None,
                    "dom_unique_job_ids": ["101", "202"],
                }
            ]
        }
        return []

    monkeypatch.setattr(probe, "fetch_linkedin_vacancies", fake_fetch)
    monkeypatch.setattr(probe, "_preflight_services", lambda: None)

    report = probe.run_probe(
        cells=list(probe.DEFAULT_CELLS),
        as_of=date(2026, 9, 17),
        rotation_slot=1,
        output_dir=tmp_path,
        live=True,
    )

    assert len(calls) == 2
    assert calls[0]["max_pages"] == 1
    assert calls[0]["detail_page_budget"] == 0
    assert calls[0]["no_retries"] is True
    assert report["stop_reason"] == "login wall"
    assert report["pairs"][0]["treatment_only"] == []
    assert report["pairs"][0]["control_only"] == ["101", "202"]
    assert report["pairs"][0]["overlap"] == []
    assert report["pairs"][0]["stop_reason"] == "login wall"
    assert report["pairs"][1]["stop_reason"] == "not_attempted_after: login wall"
    assert report["queries"][1]["job_ids"] == []
    assert report["queries"][1]["error"] == "login wall"
    assert not list(tmp_path.glob("*.db"))


def _patch_systemctl_show(
    probe: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, str],
    *,
    error: Exception | None = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> types.SimpleNamespace:
        calls.append(command)
        if error is not None:
            raise error
        if command[1] != "show":
            return types.SimpleNamespace(returncode=0, stdout="inactive\n", stderr="")
        unit = command[-1]
        return types.SimpleNamespace(
            returncode=0,
            stdout=responses[unit],
            stderr="",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    return calls


def _next_elapse_after(minutes: int) -> str:
    next_elapse = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return next_elapse.strftime("%a %Y-%m-%d %H:%M:%S UTC")


def test_preflight_rejects_nonexistent_shadow_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _load_probe_module()
    calls = _patch_systemctl_show(
        probe,
        monkeypatch,
        {
            "job-intel-shadow-collection.service": "LoadState=not-found\nActiveState=inactive\n",
            "job-intel-daily.service": "LoadState=masked\nActiveState=inactive\n",
            "job-intel-shadow-collection.timer": "NextElapseUSecRealtime=" + _next_elapse_after(120) + "\n",
        },
    )

    with pytest.raises(probe.ProbeSafetyError, match="shadow-collection.service.*LoadState"):
        probe._preflight_services()
    assert calls == [[
        "systemctl", "show", "-p", "LoadState", "-p", "ActiveState",
        "job-intel-shadow-collection.service",
    ]]


def test_preflight_rejects_active_shadow_collection_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _load_probe_module()
    calls = _patch_systemctl_show(
        probe,
        monkeypatch,
        {
            "job-intel-shadow-collection.service": "LoadState=loaded\nActiveState=active\n",
            "job-intel-daily.service": "LoadState=masked\nActiveState=inactive\n",
            "job-intel-shadow-collection.timer": "NextElapseUSecRealtime=" + _next_elapse_after(120) + "\n",
        },
    )

    with pytest.raises(probe.ProbeSafetyError, match="shadow-collection.service is active"):
        probe._preflight_services()
    assert len(calls) == 1


def test_preflight_rejects_shadow_timer_with_less_than_twenty_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _load_probe_module()
    calls = _patch_systemctl_show(
        probe,
        monkeypatch,
        {
            "job-intel-shadow-collection.service": "LoadState=loaded\nActiveState=inactive\n",
            "job-intel-daily.service": "LoadState=masked\nActiveState=inactive\n",
            "job-intel-shadow-collection.timer": "NextElapseUSecRealtime=" + _next_elapse_after(5) + "\n",
        },
    )

    with pytest.raises(probe.ProbeSafetyError, match="timer.*20 minutes"):
        probe._preflight_services()
    assert len(calls) == 3


def test_preflight_allows_masked_daily_and_distant_shadow_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _load_probe_module()
    calls = _patch_systemctl_show(
        probe,
        monkeypatch,
        {
            "job-intel-shadow-collection.service": "LoadState=loaded\nActiveState=inactive\n",
            "job-intel-daily.service": "LoadState=masked\nActiveState=inactive\n",
            "job-intel-shadow-collection.timer": "NextElapseUSecRealtime=" + _next_elapse_after(120) + "\n",
        },
    )

    probe._preflight_services()
    assert [call[-1] for call in calls] == [
        "job-intel-shadow-collection.service",
        "job-intel-daily.service",
        "job-intel-shadow-collection.timer",
    ]


def test_preflight_rejects_systemctl_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _load_probe_module()
    _patch_systemctl_show(
        probe, monkeypatch, {}, error=RuntimeError("systemctl unavailable")
    )

    with pytest.raises(probe.ProbeSafetyError, match="systemctl"):
        probe._preflight_services()


def test_probe_live_preflight_rejects_active_service_before_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _load_probe_module()
    called = False

    def fake_fetch(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(probe, "fetch_linkedin_vacancies", fake_fetch)
    _patch_systemctl_show(
        probe,
        monkeypatch,
        {
            "job-intel-shadow-collection.service": "LoadState=loaded\nActiveState=inactive\n",
            "job-intel-daily.service": "LoadState=loaded\nActiveState=active\n",
            "job-intel-shadow-collection.timer": "NextElapseUSecRealtime=" + _next_elapse_after(120) + "\n",
        },
    )

    with pytest.raises(probe.ProbeSafetyError, match="job-intel-daily.service"):
        probe.run_probe(
            cells=list(probe.DEFAULT_CELLS),
            as_of=date(2026, 9, 17),
            rotation_slot=1,
            live=True,
        )
    assert called is False
