from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import random
import re
from pathlib import Path
import shlex
import socket
import signal
import subprocess
import sys
import threading
import time
import types

import pytest

from job_intel import browser_worker
from job_intel import linkedin_session
from job_intel.product_search.acquisition_probe import (
    ProbeQuery,
    LinkedInExecutionPlan,
    RuntimeCapabilityResult,
    SourceIsolation,
    build_isolated_probe_environment,
    run_probe,
    validate_experiment_manifest,
)

from job_intel.browser_sourcing import (
    AcquisitionMetrics,
    BrowserAcquisitionConfig,
    BrowserFetchResult,
    BrowserNativeUnavailable,
    BrowserSourceClient,
    EXCLUSION_REASON_CATALOG,
    classify_linkedin_dom_job_ids,
    browser_native_available,
    extract_company_career_vacancies_from_html,
    extract_linkedin_vacancies_from_html,
    metrics_from_counts,
    resolve_browser_config,
)
from job_intel.models import Vacancy


def _load_browser_supervisor():
    script = Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"
    spec = importlib.util.spec_from_file_location("job_intel_browser_supervisor", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_with_source_isolation(root: Path, settings: dict[str, object]) -> dict[str, object]:
    return {
        "gate": "gate-a",
        "environment_id": "product-search-gate-a",
        "root": str(root),
        "exclusion_reason_codes": {
            "version": EXCLUSION_REASON_CATALOG.version,
            "sha256": EXCLUSION_REASON_CATALOG.sha256,
        },
        "paths": {
            name: str(root / name)
            for name in (
                "runtime",
                "experiment.sqlite3",
                "raw-evidence",
                "logs",
                "locks",
                "browser-profile",
                "cache",
                "tmp",
            )
        },
        "python": {
            "executable_path": str(root / "python-runtime/bin/python"),
            "executable_sha256": "a" * 64,
            "stdlib_tree_sha256": "b" * 64,
        },
        "environment": {
            "import_root": str(root / "runtime"),
            "dependency_lock_sha256": "c" * 64,
            "installed_distributions_sha256": "d" * 64,
            "sys_path_sha256": "e" * 64,
        },
        "source_isolation": {"linkedin": settings},
    }


def test_browser_probe_reports_actual_dispatch_counters(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        browser_worker,
        "_probe",
        lambda _source: ([], {"status": "ok"}, {"browser_start_ms": 3}),
    )

    assert browser_worker.main(["probe", "linkedin"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["market_query_dispatch_count"] == 0
    assert payload["sudo_dispatch_count"] == 0


def test_extract_linkedin_vacancies_from_html_uses_jobposting_data() -> None:
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "VP Product, Monetization",
          "description": "Lead monetization and growth",
          "datePosted": "2026-05-16",
          "hiringOrganization": {"name": "Spark Mobility"},
          "jobLocation": {"address": {"addressLocality": "Dubai", "addressCountry": "AE"}},
          "url": "https://www.linkedin.com/jobs/view/123"
        }
        </script>
      </head>
      <body>
        <a href="/jobs/view/123">VP Product, Monetization</a>
      </body>
    </html>
    """

    vacancies = extract_linkedin_vacancies_from_html(html, page_url="https://www.linkedin.com/jobs/search")

    assert len(vacancies) == 1
    vacancy = vacancies[0]
    assert vacancy.source == "linkedin"
    assert vacancy.company == "Spark Mobility"
    assert vacancy.title == "VP Product, Monetization"
    assert vacancy.location == "Dubai, AE"
    assert vacancy.url == "https://www.linkedin.com/jobs/view/123"
    assert "monetization" in vacancy.description.lower()


def test_extract_company_career_vacancies_from_html_parses_common_ats_links() -> None:
    html = """
    <html>
      <body>
        <a href="https://boards.greenhouse.io/acme/jobs/789">Director of Product</a>
        <a href="https://jobs.lever.co/acme/abc">VP Growth</a>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Chief Product Officer",
          "description": "Lead ecosystem and monetization",
          "hiringOrganization": {"name": "Acme"},
          "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "GB"}},
          "url": "https://acme.com/careers/cpo"
        }
        </script>
      </body>
    </html>
    """

    vacancies = extract_company_career_vacancies_from_html(html, page_url="https://acme.com/careers")

    assert [v.title for v in vacancies] == ["Chief Product Officer", "Director of Product", "VP Growth"]
    assert vacancies[0].source == "company_career"
    assert vacancies[0].company == "Acme"
    assert vacancies[0].location == "London, GB"


def test_extract_company_career_vacancies_from_html_handles_custom_ats_domains() -> None:
    html = """
    <html>
      <body>
        <a href="https://careers.adapty.io/roles/vp-product">VP Product</a>
      </body>
    </html>
    """

    vacancies = extract_company_career_vacancies_from_html(html, page_url="https://careers.adapty.io")

    assert len(vacancies) == 1
    vacancy = vacancies[0]
    assert vacancy.company == "Adapty"
    assert vacancy.title == "VP Product"
    assert vacancy.url == "https://careers.adapty.io/roles/vp-product"


def test_extract_linkedin_vacancies_from_html_skips_link_without_company() -> None:
    html = """
    <html>
      <body>
        <a href="/jobs/view/123">VP Product</a>
      </body>
    </html>
    """

    vacancies = extract_linkedin_vacancies_from_html(html, page_url="https://www.linkedin.com/jobs/search")

    assert vacancies == []


def test_metrics_from_counts_calculates_quality_ratios() -> None:
    metrics = metrics_from_counts(
        source="linkedin",
        found=20,
        executive_matches=8,
        accepted=5,
        rejected=15,
        extraction_successes=18,
        extraction_attempts=20,
        anti_bot_failures=2,
    )

    assert isinstance(metrics, AcquisitionMetrics)
    assert metrics.source == "linkedin"
    assert metrics.vacancies_found == 20
    assert metrics.executive_fit_ratio == 0.4
    assert metrics.accepted_rejected_ratio == (5 / 15)
    assert metrics.extraction_success_rate == 0.9
    assert metrics.anti_bot_failure_rate == 0.1
    assert metrics.source_reliability > 0.0
    assert metrics.status in {"operational", "degraded"}


def test_resolve_browser_config_uses_source_specific_defaults(monkeypatch) -> None:
    monkeypatch.delenv("JOB_INTEL_BROWSER_PROFILE_DIR", raising=False)
    monkeypatch.delenv("JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN", raising=False)

    linkedin_config = resolve_browser_config("linkedin")
    assert linkedin_config.user_data_dir.as_posix() == "/var/lib/browser-desktop/profiles/linkedin"


def test_resolve_browser_config_respects_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR", "/tmp/browser-profile")
    monkeypatch.setenv("JOB_INTEL_BROWSER_HEADLESS", "0")
    monkeypatch.setenv("JOB_INTEL_BROWSER_SLOW_MO_MS", "321")
    monkeypatch.setenv("JOB_INTEL_BROWSER_MIN_DELAY_MS", "11")
    monkeypatch.setenv("JOB_INTEL_BROWSER_MAX_DELAY_MS", "22")
    monkeypatch.setenv("JOB_INTEL_BROWSER_SCROLL_PAUSE_MS", "33")
    monkeypatch.setenv("JOB_INTEL_BROWSER_NAV_TIMEOUT_MS", "4444")
    monkeypatch.setenv("JOB_INTEL_BROWSER_MAX_SCROLLS", "5")

    config = resolve_browser_config()

    assert config.user_data_dir.as_posix() == "/tmp/browser-profile"
    assert config.headless is False
    assert config.slow_mo_ms == 321
    assert config.min_delay_ms == 11
    assert config.max_delay_ms == 22
    assert config.scroll_pause_ms == 33
    assert config.navigation_timeout_ms == 4444
    assert config.max_scrolls == 5


def test_browser_client_refuses_empty_required_profile_before_launch(monkeypatch, tmp_path) -> None:
    profile_dir = tmp_path / "custom-profile"
    profile_dir.mkdir()
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", user_data_dir=profile_dir))

    fake_context = types.SimpleNamespace(
        chromium=types.SimpleNamespace(
            launch_persistent_context=lambda **kwargs: pytest.fail("launch_persistent_context should not be reached")
        )
    )
    fake_playwright = types.SimpleNamespace(start=lambda: fake_context)
    fake_sync_api = types.SimpleNamespace(sync_playwright=lambda: fake_playwright)
    monkeypatch.setattr("job_intel.browser_sourcing.find_spec", lambda _name: object())
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=fake_sync_api))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: pytest.fail("mkdir should not be called for an empty required profile"))

    with pytest.raises(BrowserNativeUnavailable):
        client.__enter__()


def test_browser_client_fetch_html_wraps_runtime_failures() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig())

    class _BrokenContext:
        def new_page(self):
            raise RuntimeError("boom")

    client._context = _BrokenContext()  # type: ignore[attr-defined]

    try:
        client.fetch_html("https://example.com")
    except BrowserNativeUnavailable as exc:
        assert "Playwright browser fetch failed" in str(exc)
    else:
        raise AssertionError("expected BrowserNativeUnavailable")


def test_linkedin_fetch_result_keeps_redirect_and_protected_diagnostic_artifact(monkeypatch, tmp_path: Path) -> None:
    html = """
    <html><body>
      <a class="changed-card-class" href="/jobs/view/101">VP Product</a>
      <a class="another-changed-class" href="/jobs/view/202">Director Product</a>
    </body></html>
    """
    diagnostics_dir = tmp_path / "diagnostics"
    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(diagnostics_dir))
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", max_scrolls=1))

    class _Locator:
        def evaluate_all(self, _script: str) -> list[str]:
            return [
                "https://www.linkedin.com/jobs/view/101",
                "https://www.linkedin.com/jobs/view/202",
            ]

    class _Page:
        url = "https://www.linkedin.com/login"
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, _url: str, **_kwargs) -> None:
            self.url = "https://www.linkedin.com/login"

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return html

        def locator(self, selector: str) -> _Locator:
            assert selector == "a[href*='/jobs/view/']"
            return _Locator()

        def screenshot(self, *, path: str, full_page: bool) -> None:
            assert full_page
            Path(path).write_bytes(b"png")

        def title(self) -> str:
            return "Sign in"

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]
    result = client.fetch_page(
        "https://www.linkedin.com/jobs/search/?keywords=product",
        scrolls=1,
        page_offset=1,
        capture_label="run-1-query-2-cell-uk-page-1",
    )

    assert result.requested_url == "https://www.linkedin.com/jobs/search/?keywords=product"
    assert result.final_url == "https://www.linkedin.com/login"
    assert result.html == html
    assert len(result.html_sha256) == 64
    assert result.page_offset == 1
    assert result.completed_scroll_steps == 1
    assert result.dom_unique_job_ids == frozenset({"101", "202"})
    assert result.artifact_ref and "run-1-query-2-cell-uk-page-1" in result.artifact_ref
    artifacts = list(diagnostics_dir.iterdir())
    page_artifacts = [path for path in artifacts if "run-1-query-2-cell-uk-page-1." in path.name]
    assert {path.suffix for path in page_artifacts} == {".html", ".json", ".png"}
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in page_artifacts)
    summary = json.loads(next(path for path in page_artifacts if path.suffix == ".json").read_text())
    assert "html_excerpt" not in summary
    assert summary["html_sha256"] == result.html_sha256


def test_linkedin_trace_reconciles_dom_ids_independently_from_card_parser(monkeypatch, tmp_path: Path) -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"VP Product",
       "description":"Own monetization","url":"https://www.linkedin.com/jobs/view/101",
       "hiringOrganization":{"name":"Spark"}}
      </script>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"Director Product",
       "description":"Lead growth","url":"https://www.linkedin.com/jobs/view/202",
       "hiringOrganization":{"name":"Spark"}}
      </script>
    </head><body>
      <a class="class-name-changed-again" href="/jobs/view/101">one</a>
      <a class="class-name-changed-again" href="/jobs/view/202">two</a>
      <a class="class-name-changed-again" href="/jobs/view/303">unparsed</a>
    </body></html>
    """
    diagnostics_dir = tmp_path / "diagnostics"
    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(diagnostics_dir))
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", max_scrolls=0, noise_probability=0.0))
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)

    class _Locator:
        def evaluate_all(self, _script: str) -> list[str]:
            return [
                "https://www.linkedin.com/jobs/view/101",
                "https://www.linkedin.com/jobs/view/202",
                "https://www.linkedin.com/jobs/view/303",
            ]

    class _Page:
        url = ""
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, url: str, **_kwargs) -> None:
            self.url = url

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return html

        def locator(self, selector: str) -> _Locator:
            assert selector == "a[href*='/jobs/view/']"
            return _Locator()

        def screenshot(self, *, path: str, full_page: bool) -> None:
            assert full_page
            Path(path).write_bytes(b"png")

        def title(self) -> str:
            return "Search"

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]
    vacancies = client.search_linkedin(
        "product",
        run_id="run-1",
        query_id="query-2",
        cell_id="uk",
        geography_location="United Kingdom",
    )
    page_trace = client.last_search_trace_snapshot()["pages"][0]

    assert {vacancy.url.rsplit("/", 1)[-1] for vacancy in vacancies} == {"101", "202"}
    assert page_trace["dom_unique_job_ids"] == ["101", "202", "303"]
    assert page_trace["parsed_unique_job_ids_before_role_filter"] == ["101", "202"]
    assert page_trace["returned_unique_job_ids"] == ["101", "202"]
    assert page_trace["excluded_job_ids_by_reason"] == {}
    assert page_trace["unexplained_dom_job_ids"] == ["303"]
    assert page_trace["job_id_outcomes"] == {
        "101": "parsed",
        "202": "parsed",
        "303": "unexplained",
    }
    assert page_trace["extraction_counts"] == {
        "dom": 3,
            "parsed_before_filter": 2,
            "returned": 2,
            "returned_outside_dom": 0,
            "returned_outside_dom_rows": 0,
            "returned_without_canonical_job_id": 0,
        "duplicate_canonical": 0,
        "duplicate_canonical_returned": 0,
        "excluded": 0,
        "unexplained": 1,
        "vacancies_extracted": 2,
    }
    assert set(page_trace["dom_unique_job_ids"]) == (
        set(page_trace["parsed_unique_job_ids_before_role_filter"])
        | set(page_trace["excluded_job_ids_by_reason"])
        | set(page_trace["unexplained_dom_job_ids"])
    )
    assert "run-1-query-2-cell-uk-page-0" in page_trace["artifact_ref"]


def test_linkedin_auth_opt_in_allows_missing_session_but_default_stays_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(source_name="linkedin", user_data_dir=tmp_path)
    )
    verdict = types.SimpleNamespace(
        state=linkedin_session.SESSION_MISSING,
        cookie_mismatch=False,
        page_unrecognised=False,
    )
    monkeypatch.setattr(client, "fetch_html", lambda *_args, **_kwargs: "<html>guest</html>")
    monkeypatch.setattr(client, "_write_attach_diagnostics", lambda **_kwargs: None)
    monkeypatch.setattr(linkedin_session, "classify_auth_page", lambda *_args: "guest")
    monkeypatch.setattr(linkedin_session, "resolve_profile_dir", lambda _path: tmp_path)
    monkeypatch.setattr(linkedin_session, "read_cookie_inventory", lambda _path: [])
    monkeypatch.setattr(
        linkedin_session,
        "session_state_from_cookies",
        lambda *_args, **_kwargs: linkedin_session.SESSION_MISSING,
    )
    monkeypatch.setattr(linkedin_session, "resolve_session_state", lambda **_kwargs: verdict)

    with pytest.raises(BrowserNativeUnavailable, match="session_missing_cookie"):
        client._validate_linkedin_auth()

    assert client._validate_linkedin_auth(allow_unauthenticated=True) == "without_session"


def test_unauthenticated_search_trace_keeps_auth_state_and_a1_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            source_name="linkedin",
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            noise_probability=0.0,
        )
    )
    html = """
    <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"VP Product",
       "description":"Own monetization","url":"https://www.linkedin.com/jobs/view/101",
       "hiringOrganization":{"name":"Spark"}}
    </script>
    <a href="/jobs/view/101">VP Product</a>
    """
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda **_kwargs: "without_session")
    monkeypatch.setattr(
        client,
        "fetch_page",
        lambda *_args, **_kwargs: BrowserFetchResult(
            requested_url="https://www.linkedin.com/jobs/search/?keywords=product&location=United+Kingdom",
            final_url="https://www.linkedin.com/jobs/search/?keywords=product&location=United+Kingdom",
            html=html,
            html_sha256="a" * 64,
            page_offset=0,
            planned_scroll_steps=0,
            completed_scroll_steps=0,
            scroll_trace=(),
            dom_unique_job_ids=frozenset({"101"}),
            artifact_ref="raw-a1.json",
        ),
    )

    vacancies = client.search_linkedin(
        "product",
        geography_location="United Kingdom",
        execution_plan=LinkedInExecutionPlan(page_offsets=(0,)),
    )

    assert len(vacancies) == 1
    trace = client.last_search_trace_snapshot()
    assert trace["session_observation"] == "without_session"
    assert trace["extraction_counts"] == {
        "dom": 1,
            "parsed_before_filter": 1,
            "returned": 1,
            "returned_outside_dom": 0,
            "returned_outside_dom_rows": 0,
            "returned_without_canonical_job_id": 0,
        "duplicate_canonical": 0,
        "duplicate_canonical_returned": 0,
        "excluded": 0,
        "unexplained": 0,
        "vacancies_extracted": 1,
    }




def test_public_linkedin_fixture_parses_named_lost_job_ids() -> None:
    public_html = """
    <section class="two-pane-serp-page__results-list">
      <ul class="jobs-search__results-list">
        <li><div class="base-card base-search-card job-search-card"
            data-entity-urn="urn:li:jobPosting:4459675813">
          <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/chief-product-officer-at-burns-sheehan-4459675813?position=1&amp;refId=redacted">open</a>
          <h3 class="base-search-card__title">Chief Product Officer</h3>
          <h4 class="base-search-card__subtitle"><a class="hidden-nested-link">Burns Sheehan</a></h4>
          <div class="base-search-card__metadata"><span class="job-search-card__location">London Area, United Kingdom</span></div>
        </div></li>
        <li><div class="base-card base-search-card job-search-card"
            data-entity-urn="urn:li:jobPosting:4452385279">
          <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/chief-product-officer-ministry-of-justice-scs2-at-manchester-digital-4452385279?position=2&amp;trackingId=redacted">open</a>
          <h3 class="base-search-card__title">Chief Product Officer - Ministry of Justice - SCS2</h3>
          <h4 class="base-search-card__subtitle"><a class="hidden-nested-link">Manchester Digital</a></h4>
          <div class="base-search-card__metadata"><span class="job-search-card__location">Manchester, England, United Kingdom</span></div>
        </div></li>
        <li><div class="base-card base-search-card job-search-card"
            data-entity-urn="urn:li:jobPosting:4459855617">
          <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/chief-product-officer-at-navigator-4459855617?position=3&amp;refId=redacted">open</a>
          <h3 class="base-search-card__title">Chief Product Officer</h3>
          <h4 class="base-search-card__subtitle"><a class="hidden-nested-link">Navigator</a></h4>
          <div class="base-search-card__metadata"><span class="job-search-card__location">United Kingdom</span></div>
        </div></li>
        <li><div class="base-card base-search-card job-search-card"
            data-entity-urn="urn:li:jobPosting:4450096913">
          <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/chief-product-officer-cpo-at-aeir-4450096913?position=4&amp;trackingId=redacted">open</a>
          <h3 class="base-search-card__title">Chief Product Officer CPO</h3>
          <h4 class="base-search-card__subtitle"><a class="hidden-nested-link">Aeir</a></h4>
          <div class="base-search-card__metadata"><span class="job-search-card__location">London, United Kingdom</span></div>
        </div></li>
      </ul>
    </section>
    """

    vacancies = extract_linkedin_vacancies_from_html(
        public_html, page_url="https://www.linkedin.com/jobs/search/"
    )

    assert {vacancy.url.rsplit("-", 1)[-1].split("?", 1)[0] for vacancy in vacancies} == {
        "4459675813",
        "4452385279",
        "4459855617",
        "4450096913",
    }
    assert {vacancy.company for vacancy in vacancies} == {
        "Burns Sheehan",
        "Manchester Digital",
        "Navigator",
        "Aeir",
    }




def test_public_markup_is_parsed_even_when_authentication_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            source_name="linkedin",
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            noise_probability=0.0,
        )
    )
    public_html = """
    <div class="base-card base-search-card job-search-card"
         data-entity-urn="urn:li:jobPosting:4459675813">
      <a class="base-card__full-link"
         href="https://uk.linkedin.com/jobs/view/chief-product-officer-at-burns-sheehan-4459675813">open</a>
      <h3 class="base-search-card__title">Chief Product Officer</h3>
      <h4 class="base-search-card__subtitle"><a class="hidden-nested-link">Burns Sheehan</a></h4>
      <span class="job-search-card__location">United Kingdom</span>
    </div>
    """
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda **_kwargs: "with_session")
    monkeypatch.setattr(client, "fetch_html", lambda *_args, **_kwargs: public_html)

    vacancies = client.search_linkedin(
        "product",
        max_pages=1,
        geography_location="United Kingdom",
    )

    assert [vacancy.source_id for vacancy in vacancies]
    assert vacancies[0].company == "Burns Sheehan"

def test_authorized_linkedin_fixture_still_uses_authorized_card_markup() -> None:
    authorized_html = """
    <div class="job-card-container">
      <a href="/jobs/view/9001" class="job-card-container__link">
        <strong><!---->Chief Product Officer<!----></strong>
      </a>
      <div class="artdeco-entity-lockup__subtitle"><span><!---->Acme<!----></span></div>
      <div class="job-card-container__metadata-wrapper"><li><span><!---->United Kingdom<!----></span></li></div>
      <div class="job-card-list__footer-wrapper"></div>
    </div>
    """

    vacancies = extract_linkedin_vacancies_from_html(
        authorized_html, page_url="https://www.linkedin.com/jobs/search/"
    )

    assert [vacancy.url for vacancy in vacancies] == [
        "https://www.linkedin.com/jobs/view/9001"
    ]
    assert vacancies[0].company == "Acme"


def test_linkedin_public_parser_branch_is_selected_from_document_evidence() -> None:
    public_html = """
    <div class="base-card base-search-card job-search-card"
         data-entity-urn="urn:li:jobPosting:4459675813">
      <a class="base-card__full-link"
         href="https://uk.linkedin.com/jobs/view/chief-product-officer-at-burns-sheehan-4459675813">open</a>
      <h3 class="base-search-card__title">Chief Product Officer</h3>
      <h4 class="base-search-card__subtitle"><a class="hidden-nested-link">Burns Sheehan</a></h4>
      <span class="job-search-card__location">United Kingdom</span>
    </div>
    """

    vacancies = extract_linkedin_vacancies_from_html(
        public_html, page_url="https://www.linkedin.com/jobs/search/"
    )

    assert len(vacancies) == 1
    assert vacancies[0].source == "linkedin"

def test_linkedin_search_fails_closed_when_diagnostic_artifact_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    diagnostics_path = tmp_path / "diagnostics-not-a-directory"
    diagnostics_path.write_text("not a directory")
    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(diagnostics_path))
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)

    class _Page:
        url = "https://www.linkedin.com/jobs/search"
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, _url: str, **_kwargs) -> None:
            return None

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return "<html><body>results</body></html>"

        def locator(self, _selector: str):
            return types.SimpleNamespace(evaluate_all=lambda _script: [])

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]
    with pytest.raises(BrowserNativeUnavailable, match="diagnostic"):
        client.search_linkedin("product", geography_location="United Kingdom")
    health = client.session_health_snapshot()
    assert health["critical_degradation"] is True
    assert health["status"] == "blocked"


def test_acquisition_warm_and_cold_paths_never_dispatch_sudo(monkeypatch) -> None:
    calls: list[list[str]] = []

    def exploding_sudo_run(argv, **kwargs):
        command = list(argv)
        calls.append(command)
        if command[:1] == ["sudo"]:
            raise AssertionError(f"acquisition dispatched sudo: {command!r}")
        return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

    monkeypatch.setattr(browser_worker.subprocess, "run", exploding_sudo_run)
    monkeypatch.setattr(browser_worker, "_close_foreign_pages", lambda *_args: {"remaining_foreign": 0})
    monkeypatch.setattr(browser_worker, "_endpoint_dirty", lambda *_args: False)
    monkeypatch.setattr(browser_worker, "_cdp_ready", lambda *_args, **_kwargs: True)

    assert browser_worker._ensure_browser_desktop("linkedin") == "http://169.254.77.2:19222"

    monkeypatch.setattr(browser_worker, "_cdp_ready", lambda *_args, **_kwargs: False)
    with pytest.raises(BrowserNativeUnavailable, match="bootstrap|endpoint"):
        browser_worker._ensure_browser_desktop("linkedin")

    assert all(command[:1] != ["sudo"] for command in calls)


def test_cloned_profile_manifest_requires_cdp_url(tmp_path: Path) -> None:
    manifest = _manifest_with_source_isolation(
        tmp_path / "experiment",
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": str(tmp_path / "experiment" / "clone"),
        },
    )

    with pytest.raises(ValueError, match="cdp_url"):
        validate_experiment_manifest(manifest)


def test_cloned_profile_manifest_rejects_invalid_cdp_url(tmp_path: Path) -> None:
    manifest = _manifest_with_source_isolation(
        tmp_path / "experiment",
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": str(tmp_path / "experiment" / "clone"),
            "cdp_url": "not-a-url",
        },
    )

    with pytest.raises(ValueError, match="invalid cloned profile cdp_url"):
        validate_experiment_manifest(manifest)


def test_cloned_profile_cannot_point_at_the_shared_linkedin_profile(tmp_path: Path) -> None:
    manifest = _manifest_with_source_isolation(
        tmp_path / "experiment",
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": "/var/lib/browser-desktop/profiles/linkedin",
            "cdp_url": "http://169.254.77.2:19222",
        },
    )

    with pytest.raises(ValueError, match="must differ from the shared LinkedIn profile"):
        validate_experiment_manifest(manifest)


def test_cloned_profile_without_cdp_fails_before_market_dispatch(tmp_path: Path) -> None:
    calls: list[str] = []
    result = run_probe(
        run_id="clone-without-cdp",
        queries=[
            ProbeQuery(
                query_id="query-1",
                cell_id="cell-1",
                source_family="linkedin",
                query="VP Product Almaty",
            )
        ],
        sources={"linkedin": lambda query: calls.append(query) or []},
        output_dir=tmp_path / "evidence",
        runtime_capability_checks={
            "linkedin": lambda: RuntimeCapabilityResult(state="ready")
        },
        isolation={
            "linkedin": SourceIsolation(
                mode="cloned_profile",
                path=tmp_path / "clone",
                collection_method="browser",
                cdp_url="",
            )
        },
        max_attempts=1,
    )

    assert calls == []
    assert result.family_attempts[0]["market_query_dispatch_count"] == 0
    assert result.source_states["linkedin"] == "runtime_capability_blocked"


def test_browser_worker_uses_manifest_cdp_override_without_changing_exclusive_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/tmp/experiment")
    manifest = _manifest_with_source_isolation(
        root,
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": str(root / "clone"),
            "cdp_url": "http://127.0.0.1:19223",
        },
    )
    environment = build_isolated_probe_environment(
        manifest, ambient={"JOB_INTEL_BROWSER_CDP_URL": "http://127.0.0.1:1"}
    )
    assert environment["JOB_INTEL_BROWSER_CDP_URL"] == "http://127.0.0.1:19223"
    assert environment["JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN"] == str(root / "clone")

    seen: list[str] = []

    def fake_ready(url: str, **_kwargs: object) -> bool:
        seen.append(url)
        return False

    monkeypatch.setattr(browser_worker, "_cdp_ready", fake_ready)
    monkeypatch.setenv("JOB_INTEL_BROWSER_CDP_URL", "http://127.0.0.1:19223")
    with pytest.raises(browser_worker.BrowserNativeUnavailable):
        browser_worker._ensure_browser_desktop("linkedin")
    assert seen == ["http://127.0.0.1:19223"]

    seen.clear()
    monkeypatch.delenv("JOB_INTEL_BROWSER_CDP_URL")
    with pytest.raises(browser_worker.BrowserNativeUnavailable):
        browser_worker._ensure_browser_desktop("linkedin")
    assert seen == ["http://169.254.77.2:19222"]


def test_browser_process_age_check_stays_unprivileged_and_semantic(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_ps(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "  42 /usr/bin/chromium --user-data-dir=/var/lib/browser-desktop/"
                "profiles/linkedin --remote-debugging-port=19222\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(browser_worker.subprocess, "run", fake_ps)

    assert browser_worker._browser_process_age_seconds("linkedin") == 42
    assert calls == [["ps", "-eo", "etimes=,args="]]


def test_supervisor_rejects_profile_override_without_explicit_cdp(tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"),
        "--source",
        "linkedin",
        "--profile",
        str(tmp_path / "a0probe"),
        "--bootstrap-script",
        str(tmp_path / "unused-bootstrap.sh"),
        "--lock-path",
        str(tmp_path / "profile.lock"),
    ]

    result = subprocess.run(command, capture_output=True, text=True, timeout=5)

    assert result.returncode != 0
    assert "--cdp-url" in result.stderr


def test_supervisor_stop_releases_the_profile_lock_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _load_browser_supervisor()
    lock_script = Path(__file__).parents[2] / "scripts/job_intel_profile_lock.sh"
    lock_path = tmp_path / "profile.lock"
    holder = subprocess.Popen(
        [str(lock_script), "--path", str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert holder.stdout is not None
        assert b"profile lock acquired:" in holder.stdout.readline()
        holder_process_group = os.getpgid(holder.pid)
        monkeypatch.setattr(supervisor, "_stop_profile", lambda _profile: 0)

        result = supervisor.main(
            [
                "--stop",
                "--source",
                "linkedin",
                "--profile",
                str(tmp_path / "linkedin"),
                "--lock-path",
                str(lock_path),
            ]
        )

        assert result == 0
        assert holder.wait(timeout=3) is not None
        assert subprocess.run(
            ["flock", "-n", str(lock_path), "true"], check=False
        ).returncode == 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            group_pids = []
            for process_dir in Path("/proc").iterdir():
                if not process_dir.name.isdigit():
                    continue
                try:
                    stat = (process_dir / "stat").read_text(encoding="ascii")
                    process_group = int(stat.rsplit(")", 1)[1].split()[2])
                except (OSError, ValueError, IndexError):
                    continue
                if process_group == holder_process_group:
                    group_pids.append(int(process_dir.name))
            if not group_pids:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"profile lock process group survived stop: {group_pids}")
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=3)


def test_supervisor_sigterm_releases_the_profile_lock_holder(tmp_path: Path) -> None:
    supervisor = _load_browser_supervisor()
    lock_path = tmp_path / "profile.lock"
    bootstrap_script = tmp_path / "hanging-bootstrap.sh"
    bootstrap_started = tmp_path / "bootstrap-started"
    bootstrap_script.write_text(
        "#!/bin/sh\n"
        f"touch {str(bootstrap_started)!r}\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    bootstrap_script.chmod(0o755)
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"),
        "--source",
        "linkedin",
        "--profile",
        str(tmp_path / "linkedin"),
        "--cdp-url",
        "http://127.0.0.1:1",
        "--lock-path",
        str(lock_path),
        "--bootstrap-script",
        str(bootstrap_script),
        "--bootstrap-timeout",
        "30",
        "--startup-timeout",
        "30",
    ]
    process = subprocess.Popen(
        command,
        env={**os.environ, "JOB_INTEL_BROWSER_PROFILE_ROOT": str(tmp_path)},
        start_new_session=True,
    )
    holder_process_group: int | None = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if bootstrap_started.exists():
                break
            time.sleep(0.02)
        else:
            pytest.fail("supervisor never started the bootstrap after acquiring the profile lock")

        holder_pids = supervisor._lock_holder_pids(lock_path)
        assert len(holder_pids) == 1
        holder_process_group = os.getpgid(holder_pids[0])

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert subprocess.run(
            ["flock", "-n", str(lock_path), "true"], check=False
        ).returncode == 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            group_pids = []
            for process_dir in Path("/proc").iterdir():
                if not process_dir.name.isdigit():
                    continue
                try:
                    stat = (process_dir / "stat").read_text(encoding="ascii")
                    process_group = int(stat.rsplit(")", 1)[1].split()[2])
                except (OSError, ValueError, IndexError):
                    continue
                if process_group == holder_process_group:
                    group_pids.append(int(process_dir.name))
            if not group_pids:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"lock-holder process group survived SIGTERM: {group_pids}")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        for pid in supervisor._lock_holder_pids(lock_path):
            try:
                process_group = os.getpgid(pid)
            except ProcessLookupError:
                continue
            subprocess.run(["kill", "-TERM", f"-{process_group}"], check=False)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and supervisor._pid_is_running(pid):
                time.sleep(0.02)
            if supervisor._pid_is_running(pid):
                subprocess.run(["kill", "-KILL", f"-{process_group}"], check=False)


def test_supervisor_stop_signals_main_before_endpoint_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _load_browser_supervisor()
    events: list[object] = []

    monkeypatch.setattr(
        supervisor.os,
        "kill",
        lambda pid, signum: events.append(("signal", pid, signum)),
    )
    monkeypatch.setattr(supervisor, "_pid_is_running", lambda _pid: False)
    monkeypatch.setattr(
        supervisor,
        "_stop_profile",
        lambda _profile: events.append("endpoint") or 0,
    )
    monkeypatch.setattr(
        supervisor,
        "_release_profile_lock",
        lambda _lock_path: events.append("lock"),
    )

    result = supervisor.main(
        [
            "--stop",
            "--source",
            "linkedin",
            "--profile",
            str(tmp_path / "linkedin"),
            "--lock-path",
            str(tmp_path / "profile.lock"),
            "--supervisor-pid",
            "1234",
        ]
    )

    assert result == 0
    assert events == [
        ("signal", 1234, signal.SIGTERM),
        "endpoint",
        "lock",
    ]


def _notify_socket(path: Path) -> socket.socket:
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(path))
    receiver.settimeout(3)
    return receiver


def _supervisor_command(
    *,
    profile: Path,
    cdp_url: str,
    lock_path: Path,
    notify_path: Path,
    timeout: str,
    network_namespace: str | None = None,
    monitor_interval: str = "30",
) -> tuple[list[str], dict[str, str], Path]:
    bootstrap_script = profile.parent / "fake-browser-desktop-bootstrap.sh"
    bootstrap_log = profile.parent / "bootstrap-argv.log"
    bootstrap_script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {str(bootstrap_log)!r}\n",
        encoding="utf-8",
    )
    bootstrap_script.chmod(0o755)
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"),
        "--source",
        "linkedin",
        "--profile",
        str(profile),
        "--cdp-url",
        cdp_url,
        "--lock-path",
        str(lock_path),
        "--startup-timeout",
        timeout,
        "--poll-interval",
        "0.02",
        "--monitor-interval",
        monitor_interval,
        "--bootstrap-script",
        str(bootstrap_script),
    ]
    if network_namespace:
        command.extend(["--network-namespace", network_namespace])
    return command, {
        **os.environ,
        "NOTIFY_SOCKET": str(notify_path),
        "JOB_INTEL_BROWSER_PROFILE_ROOT": str(profile.parent),
    }, bootstrap_log


def test_supervisor_notifies_ready_only_after_cdp_version_responds(tmp_path: Path) -> None:
    events: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/json/version":
                self.send_response(404)
                self.end_headers()
                return
            events.append("json/version")
            body = b'{"Browser":"fake"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    notify_path = tmp_path / "notify.sock"
    receiver = _notify_socket(notify_path)
    profile = tmp_path / "linkedin"
    profile.mkdir()
    command, environment, bootstrap_log = _supervisor_command(
        profile=profile,
        cdp_url=f"http://127.0.0.1:{server.server_port}",
        lock_path=tmp_path / "profile.lock",
        notify_path=notify_path,
        timeout="30",
        network_namespace="ln-eg",
    )
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        try:
            message, _ = receiver.recvfrom(128)
        except socket.timeout:
            stdout, stderr = process.communicate(timeout=3)
            pytest.fail(
                "supervisor did not notify READY: "
                f"returncode={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        assert message == b"READY=1\n"
        assert events == ["json/version"]
        assert bootstrap_log.read_text(encoding="utf-8").splitlines() == [
            "--profile",
            "linkedin",
            "--url",
            "https://www.linkedin.com/",
            "--network-namespace",
            "ln-eg",
        ]
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
        receiver.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_supervisor_times_out_without_cdp_ready_and_never_notifies(tmp_path: Path) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    notify_path = tmp_path / "notify.sock"
    receiver = _notify_socket(notify_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    command, environment, _bootstrap_log = _supervisor_command(
        profile=profile,
        cdp_url=f"http://127.0.0.1:{port}",
        lock_path=tmp_path / "profile.lock",
        notify_path=notify_path,
        timeout="0.2",
    )

    result = subprocess.run(command, env=environment, capture_output=True, timeout=5)

    assert result.returncode != 0
    with pytest.raises(socket.timeout):
        receiver.recvfrom(128)
    receiver.close()


def test_bootstrap_unit_declares_foreground_notify_and_explicit_teardown() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-browser-bootstrap.service").read_text()

    assert "Type=notify" in unit
    assert "KillMode=control-group" in unit
    assert "KillMode=process" not in unit
    assert "KillMode=none" not in unit
    assert "StopWhenUnneeded=yes" in unit
    assert "ExecStop=" in unit
    assert "job_intel_browser_supervisor.py" in unit
    assert "browser-desktop-bootstrap.sh" in unit
    assert "--source linkedin" in unit
    assert "--cdp-url ${PRODUCT_SEARCH_BROWSER_CDP_URL}" in unit
    assert "User=root" in unit


def test_bootstrap_unit_uses_one_interpreter_for_start_and_stop() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-browser-bootstrap.service").read_text()
    commands = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in unit.splitlines()
        if line.startswith(("ExecStart=", "ExecStop="))
    }

    start_tokens = commands["ExecStart"].split()
    stop_tokens = commands["ExecStop"].split()

    assert start_tokens[0] == stop_tokens[0] == "/usr/bin/env"
    assert start_tokens[1] == stop_tokens[1] == "${PRODUCT_SEARCH_PYTHON}"


def _bootstrap_runtime_config(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).parents[2] / "scripts/browser-desktop-bootstrap.sh"
    return subprocess.run(
        ["bash", str(script), "--print-runtime-config", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


def _runtime_config(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _capture_start_as_browser_environment(tmp_path: Path, namespace: str) -> dict[str, str]:
    script = Path(__file__).parents[2] / "scripts/browser-desktop-bootstrap.sh"
    body = script.read_text(encoding="utf-8")
    start = body.index("start_as_browser() {")
    end = body.index("\n}", start) + 2

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "nohup").write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    (fake_bin / "runuser").write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-u\" ]; then shift 2; fi\n"
        "if [ \"$1\" = \"--\" ]; then shift; fi\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    prefix = fake_bin / "namespace-prefix"
    prefix.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    for executable in (fake_bin / "nohup", fake_bin / "runuser", prefix):
        executable.chmod(0o755)

    capture = tmp_path / "browser.env"
    log_file = tmp_path / "browser.log"
    prefix_value = f"({shlex.quote(str(prefix))})" if namespace else "()"
    harness = (
        "set -eu\n"
        f"PATH={shlex.quote(str(fake_bin))}:$PATH\n"
        f"NETNS_PREFIX={prefix_value}\n"
        f"NETWORK_NAMESPACE={shlex.quote(namespace)}\n"
        "BROWSER_TIMEZONE=Asia/Almaty\n"
        "USER_NAME=browser\n"
        "DISPLAY_NUM=99\n"
        "USER_HOME=/tmp/browser\n"
        "RUNTIME_DIR=/tmp/browser-runtime\n"
        "BASE_DIR=/tmp/browser\n"
        f"{body[start:end]}\n"
        f"start_as_browser {shlex.quote(str(log_file))} sh -c 'env > {shlex.quote(str(capture))}'\n"
        "wait\n"
    )
    environment = {key: value for key, value in os.environ.items() if key != "TZ"}
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=environment,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return dict(
        line.split("=", 1)
        for line in capture.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_bootstrap_namespace_browser_gets_configured_timezone(tmp_path: Path) -> None:
    environment = _capture_start_as_browser_environment(tmp_path, "ln-eg")

    assert environment["TZ"] == "Asia/Almaty"


def test_bootstrap_host_browser_environment_does_not_get_timezone(tmp_path: Path) -> None:
    environment = _capture_start_as_browser_environment(tmp_path, "")

    assert "TZ" not in environment


def test_bootstrap_runtime_config_preserves_linkedin_defaults() -> None:
    result = _bootstrap_runtime_config("--profile", "linkedin")

    assert result.returncode == 0, result.stderr
    assert _runtime_config(result.stdout) == {
        "profile": "linkedin",
        "namespace": "ln-eg",
        "cdp_port": "9222",
        "novnc_bind": "169.254.77.2",
        "cdp_endpoint": "http://169.254.77.2:19222",
        "cdp_tunnel_host": "169.254.77.2",
        "cdp_tunnel_port": "19222",
    }


def test_bootstrap_runtime_config_preserves_non_linkedin_defaults() -> None:
    result = _bootstrap_runtime_config("--profile", "hh")

    assert result.returncode == 0, result.stderr
    config = _runtime_config(result.stdout)
    assert config["profile"] == "hh"
    assert config["namespace"] == ""
    assert config["novnc_bind"] == "127.0.0.1"
    assert config["cdp_endpoint"] == "http://127.0.0.1:9223"
    assert config["cdp_tunnel_host"] == "127.0.0.1"
    assert config["cdp_tunnel_port"] == "9223"


def test_bootstrap_runtime_config_allows_an_arbitrary_profile_in_a_namespace(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ip = fake_bin / "ip"
    fake_ip.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = netns ] && [ \"$2\" = exec ]; then shift 3; exec \"$@\"; fi\n"
        "printf '%s\\n' ln-eg\n",
        encoding="utf-8",
    )
    fake_ip.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = _bootstrap_runtime_config(
        "--profile", "a0probe", "--network-namespace", "ln-eg", env=environment
    )

    assert result.returncode == 0, result.stderr
    config = _runtime_config(result.stdout)
    assert config["profile"] == "a0probe"
    assert config["namespace"] == "ln-eg"
    assert config["novnc_bind"] == "169.254.77.2"
    assert config["cdp_endpoint"] == "http://169.254.77.2:19254"
    assert config["cdp_tunnel_host"] == "169.254.77.2"
    assert config["cdp_tunnel_port"] == "19254"


def test_bootstrap_runtime_config_rejects_a_missing_namespace(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ip = fake_bin / "ip"
    fake_ip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_ip.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = _bootstrap_runtime_config(
        "--profile", "a0probe", "--network-namespace", "typo", env=environment
    )

    assert result.returncode != 0
    assert "network namespace" in result.stderr.lower()


def test_bootstrap_runtime_config_derives_a_distinct_relay_port_from_profile(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ip = fake_bin / "ip"
    fake_ip.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = netns ] && [ \"$2\" = exec ]; then shift 3; exec \"$@\"; fi\n"
        "printf '%s\\n' ln-eg\n",
        encoding="utf-8",
    )
    fake_ip.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = _bootstrap_runtime_config(
        "--profile", "a0probe", "--network-namespace", "ln-eg", env=environment
    )

    assert result.returncode == 0, result.stderr
    config = _runtime_config(result.stdout)
    assert config["cdp_port"] == "9254"
    assert config["cdp_tunnel_port"] == "19254"
    assert config["cdp_tunnel_port"] != "19222"


def test_bootstrap_rejects_an_occupied_relay_address_before_side_effects(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ip = fake_bin / "ip"
    fake_ip.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = netns ] && [ \"$2\" = exec ]; then shift 3; exec \"$@\"; fi\n"
        "printf '%s\\n' ln-eg\n",
        encoding="utf-8",
    )
    fake_ip.chmod(0o755)
    fake_ss = fake_bin / "ss"
    fake_ss.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'LISTEN 0 128 169.254.77.2:19254 0.0.0.0:*'\n",
        encoding="utf-8",
    )
    fake_ss.chmod(0o755)
    fake_pgrep = fake_bin / "pgrep"
    fake_pgrep.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_pgrep.chmod(0o755)
    fake_apt = fake_bin / "apt-get"
    fake_apt.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_apt.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    script = Path(__file__).parents[2] / "scripts/browser-desktop-bootstrap.sh"
    result = subprocess.run(
        ["bash", str(script), "--profile", "a0probe", "--network-namespace", "ln-eg"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=5,
    )

    assert result.returncode != 0
    assert "CDP relay address 169.254.77.2:19254 is already occupied" in result.stderr


def test_bootstrap_unit_start_timeout_exceeds_supervisor_startup_timeout() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-browser-bootstrap.service").read_text()
    match = re.search(r"(?m)^TimeoutStartSec=([0-9]+(?:\\.[0-9]+)?)$", unit)
    assert match is not None

    supervisor = _load_browser_supervisor()
    assert float(match.group(1)) > supervisor.DEFAULT_STARTUP_TIMEOUT_SECONDS


def test_acquisition_unit_binds_to_bootstrap_without_privilege_escalation() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-product-search-probe-experiment.service").read_text()

    assert "BindsTo=job-intel-browser-bootstrap.service" in unit
    assert "After=job-intel-browser-bootstrap.service" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "sudo" not in unit.lower()


def test_search_linkedin_stops_when_login_wall_appears(monkeypatch) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            max_scrolls=0,
            noise_probability=0.0,
            linkedin_followup_page_probability=1.0,
            max_linkedin_pages=2,
        )
    )

    page_urls: list[str] = []
    feed_page = '<html><body><main data-testid="mainfeed">Feed</main></body></html>'
    first_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "VP Product", "description": "Own monetization", "url": "https://www.linkedin.com/jobs/view/123", "hiringOrganization": {"name": "Spark"}}
        </script>
      </head>
      <body>
        <a href="/jobs/view/123">VP Product</a>
      </body>
    </html>
    """
    login_wall = "<html><body>Sign in to view more jobs</body></html>"

    def fake_fetch(url: str, *, scrolls=None, capture_label=None):
        page_urls.append(url)
        if url.endswith("/feed/"):
            return feed_page
        return first_page if "start=25" not in url else login_wall

    monkeypatch.setattr(client, "fetch_html", fake_fetch)

    vacancies = client.search_linkedin(
        "VP Product", max_pages=5, geography_location="United Kingdom"
    )

    assert len(vacancies) == 1
    assert page_urls[0] == "https://www.linkedin.com/feed/"
    assert page_urls[1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom"
    assert page_urls[-1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom&start=25"
    health = client.session_health_snapshot()
    assert health["pages_fetched"] >= 2
    assert health["login_walls"] == 1
    assert health["auth_redirects"] == 1
    assert health["status"] in {"degraded", "blocked"}


def test_search_linkedin_occasionally_opens_a_detail_page(monkeypatch) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            max_scrolls=0,
            noise_probability=1.0,
            linkedin_followup_page_probability=0.0,
            max_linkedin_pages=1,
        )
    )

    page_urls: list[str] = []
    feed_page = '<html><body><main data-testid="mainfeed">Feed</main></body></html>'
    search_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "VP Product", "description": "Own monetization", "url": "https://www.linkedin.com/jobs/view/123", "hiringOrganization": {"name": "Spark"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "Director of Product", "description": "Lead ecosystem", "url": "https://www.linkedin.com/jobs/view/456", "hiringOrganization": {"name": "Spark"}}
        </script>
      </head>
      <body>
        <a href="/jobs/view/123">VP Product</a>
        <a href="/jobs/view/456">Director of Product</a>
      </body>
    </html>
    """
    detail_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "Director of Product", "description": "Lead ecosystem", "url": "https://www.linkedin.com/jobs/view/456", "hiringOrganization": {"name": "Spark"}}
        </script>
      </head>
      <body>Director of Product</body>
    </html>
    """

    def fake_fetch(url: str, *, scrolls=None, capture_label=None):
        page_urls.append(url)
        if url.endswith("/feed/"):
            return feed_page
        return detail_page if url.endswith("/456") else search_page

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr("job_intel.browser_sourcing.random.choice", lambda items: items[1])

    vacancies = client.search_linkedin(
        "VP Product", max_pages=1, geography_location="United Kingdom"
    )

    assert any(url.endswith("/456") for url in page_urls)
    assert client.session_health_snapshot()["detail_pages_opened"] == 2
    assert len(vacancies) >= 1


def test_metrics_from_counts_calculates_quality_score() -> None:
    metrics = metrics_from_counts(
        source="linkedin",
        found=20,
        executive_matches=8,
        accepted=5,
        rejected=15,
        extraction_successes=18,
        extraction_attempts=20,
        anti_bot_failures=2,
        normalization_quality=0.9,
    )

    assert isinstance(metrics, AcquisitionMetrics)
    assert metrics.executive_density == 0.4
    assert metrics.signal_noise_ratio == (5 / 15)
    assert metrics.normalization_quality == 0.9
    assert 0.0 <= metrics.acquisition_quality_score <= 1.0
    assert metrics.source_reliability > 0.0
    assert metrics.status in {"operational", "degraded"}

def test_linkedin_url_uses_independent_structured_geography_target() -> None:
    from job_intel.browser_sourcing import build_linkedin_search_url

    urls = [
        build_linkedin_search_url(keywords="VP Product", location="United Kingdom"),
        build_linkedin_search_url(keywords="VP Product", location="Singapore"),
        build_linkedin_search_url(keywords="VP Product", location="Kazakhstan"),
        build_linkedin_search_url(keywords="VP Product", geo_id="103644278"),
    ]

    assert urls == [
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=Singapore",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=Kazakhstan",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&geoId=103644278",
    ]
    assert len(set(urls)) == 4


def test_linkedin_without_confirmed_geography_is_named_block_not_keywords_fallback() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))

    with pytest.raises(BrowserNativeUnavailable, match="blocked_unsupported_geography"):
        client.search_linkedin("VP Product")


def test_gate_a_page_plan_is_deterministic_without_random_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    plan = LinkedInExecutionPlan(page_offsets=(0, 25, 50), max_scroll_checkpoints=3)
    selection_calls: list[str] = []

    monkeypatch.setattr(random, "random", lambda: selection_calls.append("random") or 0.0)
    monkeypatch.setattr(random, "shuffle", lambda _items: selection_calls.append("shuffle"))
    monkeypatch.setattr(random, "choice", lambda _items: selection_calls.append("choice"))

    assert client._linkedin_page_plan(3, execution_plan=plan) == [0, 25, 50]
    assert client._linkedin_page_plan(3, execution_plan=plan) == [0, 25, 50]
    assert selection_calls == []


def test_gate_a_search_plan_does_not_traverse_random_detail_or_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    plan = LinkedInExecutionPlan(page_offsets=(0,), max_scroll_checkpoints=1)
    selection_calls: list[str] = []
    monkeypatch.setattr(random, "random", lambda: selection_calls.append("random") or 0.0)
    monkeypatch.setattr(random, "shuffle", lambda _items: selection_calls.append("shuffle"))
    monkeypatch.setattr(random, "choice", lambda _items: selection_calls.append("choice"))
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    monkeypatch.setattr(
        client,
        "_maybe_open_detail_vacancy",
        lambda **_kwargs: pytest.fail("Gate A must not traverse detail pages"),
    )
    monkeypatch.setattr(
        client,
        "_maybe_open_noise_page",
        lambda **_kwargs: pytest.fail("Gate A must not traverse noise pages"),
    )
    monkeypatch.setattr(
        client,
        "fetch_page",
        lambda *_args, **_kwargs: BrowserFetchResult(
            requested_url="https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
            final_url="https://www.linkedin.com/jobs/search/",
            html="<html><body>results</body></html>",
            html_sha256="a" * 64,
            page_offset=0,
            planned_scroll_steps=1,
            completed_scroll_steps=1,
            scroll_trace=(),
            dom_unique_job_ids=frozenset(),
            artifact_ref=None,
            scroll_checkpoints=(),
            scroll_stop_reason="max_steps",
        ),
    )

    client.search_linkedin(
        "VP Product",
        geography_location="United Kingdom",
        execution_plan=plan,
    )

    assert selection_calls == []


def test_gate_a_scroll_records_checkpoint_growth_and_saturation() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0))
    plan = LinkedInExecutionPlan(
        page_offsets=(0,),
        max_scroll_checkpoints=3,
        saturation_checkpoints=2,
        settle_timeout_ms=0,
    )

    class _Results:
        def __init__(self, page: object) -> None:
            self.page = page

        def evaluate(self, _script: str) -> bool:
            self.page.scroll_calls += 1  # type: ignore[attr-defined]
            return True

    class _Jobs:
        def __init__(self, page: object) -> None:
            self.page = page

        def evaluate_all(self, _script: str) -> list[str]:
            ids = {"1"}
            if self.page.scroll_calls >= 1:  # type: ignore[attr-defined]
                ids.add("2")
            return [f"https://www.linkedin.com/jobs/view/{job_id}" for job_id in ids]

    class _Page:
        url = "https://www.linkedin.com/jobs/search/"
        scroll_calls = 0
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, _url: str, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return "<html><body>results</body></html>"

        def locator(self, selector: str):
            if selector == plan.results_selector:
                return _Results(self)
            return _Jobs(self)

        def close(self) -> None:
            return None

    page = _Page()
    client._context = types.SimpleNamespace(pages=[], new_page=lambda: page)  # type: ignore[attr-defined]
    result = client.fetch_page(
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
        page_offset=0,
        execution_plan=plan,
    )

    assert result.planned_scroll_steps == 3
    assert result.completed_scroll_steps == 3
    assert result.scroll_stop_reason == "saturation"
    assert [item["page_offset"] for item in result.scroll_checkpoints] == [0, 0, 0]
    assert [item["after_unique_dom_id_count"] for item in result.scroll_checkpoints] == [2, 2, 2]
    assert result.scroll_checkpoints[0]["new_unique_dom_ids"] == ["2"]
    assert result.scroll_checkpoints[1]["new_unique_dom_ids"] == []


def test_gate_a_auth_page_uses_first_observed_scrollable_results_container() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0))
    plan = LinkedInExecutionPlan(
        page_offsets=(25,),
        max_scroll_checkpoints=1,
        saturation_checkpoints=2,
        settle_timeout_ms=0,
    )

    class _Container:
        def __init__(self, page: object, index: int) -> None:
            self.page = page
            self.index = index

        def evaluate(self, _script: str) -> bool:
            self.page.container_scrolls.append(self.index)  # type: ignore[attr-defined]
            return True

    class _Candidates:
        def __init__(self, page: object) -> None:
            self.page = page

        def evaluate_all(self, _script: str) -> list[dict[str, int]]:
            return [
                {"clientHeight": 180, "scrollHeight": 1200},
                {"clientHeight": 587, "scrollHeight": 3807},
                {"clientHeight": 700, "scrollHeight": 5000},
            ]

        def nth(self, index: int) -> _Container:
            return _Container(self.page, index)

    class _ConfiguredSelector:
        def count(self) -> int:
            return 0

    class _Jobs:
        def evaluate_all(self, _script: str) -> list[str]:
            return ["https://www.linkedin.com/jobs/view/1"]

    class _Page:
        url = "https://www.linkedin.com/jobs/search/"

        def __init__(self) -> None:
            self.container_scrolls: list[int] = []
            self.mouse = types.SimpleNamespace(wheel=lambda *_args: pytest.fail("must not use page fallback"))

        def goto(self, _url: str, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return "<html><body>results</body></html>"

        def locator(self, selector: str):
            if selector == plan.results_selector:
                return _ConfiguredSelector()
            if selector == "div, ul":
                return _Candidates(self)
            return _Jobs()

        def close(self) -> None:
            return None

    page = _Page()
    client._context = types.SimpleNamespace(pages=[], new_page=lambda: page)  # type: ignore[attr-defined]
    result = client.fetch_page(
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
        page_offset=25,
        execution_plan=plan,
    )

    assert page.container_scrolls == [1]
    assert result.scroll_checkpoints[0]["mode"] == "results_container"
    assert result.scroll_checkpoints[0]["page_offset"] == 25


def test_gate_a_scroll_trace_distinguishes_growth_from_saturation() -> None:
    def run_sequence(ids_after_scroll: tuple[frozenset[str], ...]) -> object:
        client = BrowserSourceClient(
            BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0)
        )
        plan = LinkedInExecutionPlan(
            page_offsets=(0,),
            max_scroll_checkpoints=len(ids_after_scroll),
            saturation_checkpoints=2,
            settle_timeout_ms=0,
        )

        class _Results:
            def __init__(self, page: object) -> None:
                self.page = page

            def evaluate(self, _script: str) -> bool:
                self.page.scroll_calls += 1  # type: ignore[attr-defined]
                return True

        class _Jobs:
            def __init__(self, page: object) -> None:
                self.page = page

            def evaluate_all(self, _script: str) -> list[str]:
                if self.page.scroll_calls == 0:  # type: ignore[attr-defined]
                    ids = frozenset({"1"})
                else:
                    ids = ids_after_scroll[self.page.scroll_calls - 1]  # type: ignore[attr-defined]
                return [f"https://www.linkedin.com/jobs/view/{job_id}" for job_id in ids]

        class _Page:
            url = "https://www.linkedin.com/jobs/search/"
            scroll_calls = 0

            def goto(self, _url: str, **_kwargs: object) -> None:
                return None

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

            def content(self) -> str:
                return "<html><body>results</body></html>"

            def locator(self, selector: str):
                return _Results(self) if selector == plan.results_selector else _Jobs(self)

            def close(self) -> None:
                return None

        page = _Page()
        client._context = types.SimpleNamespace(pages=[], new_page=lambda: page)  # type: ignore[attr-defined]
        return client.fetch_page(
            "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
            page_offset=0,
            execution_plan=plan,
        )

    growing = run_sequence(
        (frozenset({"1", "2"}), frozenset({"1", "2", "3"}), frozenset({"1", "2", "3", "4"}))
    )
    saturated = run_sequence(
        (frozenset({"1", "2"}), frozenset({"1", "2"}), frozenset({"1", "2"}))
    )

    assert [item["cumulative_unique_dom_id_count"] for item in growing.scroll_checkpoints] == [2, 3, 4]  # type: ignore[union-attr]
    assert growing.scroll_stop_reason == "max_steps"  # type: ignore[union-attr]
    assert [item["cumulative_unique_dom_id_count"] for item in saturated.scroll_checkpoints] == [2, 2, 2]  # type: ignore[union-attr]
    assert saturated.scroll_stop_reason == "saturation"  # type: ignore[union-attr]


def test_gate_a_incomplete_scroll_is_critical_degradation() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0))
    plan = LinkedInExecutionPlan(page_offsets=(0,), max_scroll_checkpoints=1, settle_timeout_ms=0)

    class _Page:
        url = "https://www.linkedin.com/jobs/search/"

        def goto(self, _url: str, **_kwargs: object) -> None:
            return None

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return "<html></html>"

        def locator(self, selector: str):
            if selector == plan.results_selector:
                return types.SimpleNamespace(
                    evaluate=lambda _script: False
                )
            return types.SimpleNamespace(evaluate_all=lambda _script: [])

        def close(self) -> None:
            return None

    page = _Page()
    client._context = types.SimpleNamespace(pages=[], new_page=lambda: page)  # type: ignore[attr-defined]
    with pytest.raises(BrowserNativeUnavailable, match="did not execute scroll"):
        client.fetch_page(
            "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
            page_offset=0,
            execution_plan=plan,
        )

    health = client.session_health_snapshot()
    assert health["critical_degradation"] is True
    assert health["status"] == "blocked"


def _a4_vacancy(job_id: str, title: str = "VP Product") -> Vacancy:
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    return Vacancy(
        source="linkedin",
        source_id=job_id,
        company="Spark",
        title=title,
        location="United Kingdom",
        url=url,
        description=title,
    )


def test_a4_dom_id_accounting_is_exhaustive_and_arithmetic_closes() -> None:
    accounting = classify_linkedin_dom_job_ids(
        dom_job_ids=frozenset({"101", "202", "303", "404"}),
        parser_vacancies=[
            _a4_vacancy("101"),
            _a4_vacancy("202"),
            _a4_vacancy("202", title="Director Product"),
            _a4_vacancy("303", title="Software Engineer"),
        ],
        returned_vacancies=[_a4_vacancy("101"), _a4_vacancy("202")],
        excluded_by_reason={"303": "role_filter"},
    )

    assert accounting.outcome_by_id == {
        "101": "parsed",
        "202": "duplicate-canonical",
        "303": "excluded",
        "404": "unexplained",
    }
    assert accounting.excluded_by_reason == {"303": "role_filter"}
    assert accounting.dom_count == (
        accounting.parsed_count
        + accounting.duplicate_canonical_count
        + accounting.excluded_count
        + accounting.unexplained_count
    )
    assert accounting.parser_before_filter_count == (
        accounting.parsed_count
        + accounting.duplicate_canonical_count
        + accounting.excluded_count
    )
    assert accounting.returned_count == (
        accounting.parsed_count + accounting.duplicate_canonical_returned_count
    )
    assert accounting.vacancies_extracted == accounting.returned_count


def test_a4_unknown_exclusion_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown LinkedIn exclusion reason"):
        classify_linkedin_dom_job_ids(
            dom_job_ids=frozenset({"303"}),
            parser_vacancies=[_a4_vacancy("303", title="Software Engineer")],
            returned_vacancies=[],
            excluded_by_reason={"303": "new_reason_not_reviewed"},
        )


def test_a4_qualification_is_a_named_post_extraction_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0))
    plan = LinkedInExecutionPlan(page_offsets=(0,), max_scroll_checkpoints=1)
    parsed = [_a4_vacancy("101"), _a4_vacancy("202", title="Software Engineer")]

    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    monkeypatch.setattr(
        "job_intel.browser_sourcing._linkedin_card_vacancies_from_html",
        lambda *_args, **_kwargs: parsed,
    )
    monkeypatch.setattr(
        "job_intel.browser_sourcing._jobposting_objects",
        lambda _html: [],
    )
    monkeypatch.setattr(
        client,
        "fetch_page",
        lambda url, **_kwargs: BrowserFetchResult(
            requested_url=url,
            final_url=url,
            html="<html><body>results</body></html>",
            html_sha256="a" * 64,
            page_offset=0,
            planned_scroll_steps=1,
            completed_scroll_steps=1,
            scroll_trace=(),
            dom_unique_job_ids=frozenset({"101", "202"}),
            artifact_ref=None,
        ),
    )

    vacancies = client.search_linkedin(
        "VP Product",
        geography_location="United Kingdom",
        execution_plan=plan,
    )

    assert [vacancy.source_id for vacancy in vacancies] == ["101"]


def test_gate_a_incomplete_page_plan_is_critical_and_not_a_clean_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(source_name="linkedin", min_delay_ms=0, max_delay_ms=0)
    )
    plan = LinkedInExecutionPlan(page_offsets=(0, 25, 50), max_scroll_checkpoints=1)
    fetched_offsets: list[int] = []
    critical_reasons: list[str] = []
    original_mark_critical = client._mark_critical_degradation

    def record_critical(reason: str) -> None:
        critical_reasons.append(reason)
        original_mark_critical(reason)

    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    monkeypatch.setattr(client, "_mark_critical_degradation", record_critical)

    def fake_fetch(
        _url: str,
        *,
        page_offset: int,
        **_kwargs: object,
    ) -> BrowserFetchResult:
        fetched_offsets.append(page_offset)
        client._health.login_walls = 1
        return BrowserFetchResult(
            requested_url=_url,
            final_url=_url,
            html=(
                '<html><head><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"JobPosting",'
                '"title":"VP Product","url":"https://www.linkedin.com/jobs/view/123",'
                '"hiringOrganization":{"name":"Spark"}}'
                '</script></head><body>'
                '<a href="/jobs/view/123">VP Product</a></body></html>'
            ),
            html_sha256="a" * 64,
            page_offset=page_offset,
            planned_scroll_steps=1,
            completed_scroll_steps=1,
            scroll_trace=(),
            dom_unique_job_ids=frozenset({"123"}),
            artifact_ref=None,
            scroll_checkpoints=(),
            scroll_stop_reason="max_steps",
        )

    monkeypatch.setattr(client, "fetch_page", fake_fetch)

    client.search_linkedin(
        "VP Product",
        geography_location="United Kingdom",
        execution_plan=plan,
    )

    trace = client._last_search_trace
    assert fetched_offsets == [0]
    assert trace["planned_page_offsets"] == [0, 25, 50]
    assert trace["completed_page_offsets"] == [0]
    assert trace["stop_reason"] == "critical_degradation"
    assert "planned page offsets were not all completed" in critical_reasons
    assert trace["zero_result_reason"] != "searched_no_qualified_results"
    health = client.session_health_snapshot()
    assert health["critical_degradation"] is True
    assert health["status"] == "blocked"


def test_supervisor_keeps_a_slow_cdp_endpoint_alive(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/json/version":
                self.send_response(404)
                self.end_headers()
                return
            time.sleep(1.2)
            body = b'{"Browser":"slow-fake"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    notify_path = tmp_path / "notify.sock"
    receiver = _notify_socket(notify_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    command, environment, _bootstrap_log = _supervisor_command(
        profile=profile,
        cdp_url=f"http://127.0.0.1:{server.server_port}",
        lock_path=tmp_path / "profile.lock",
        notify_path=notify_path,
        timeout="8",
        monitor_interval="0.02",
    )
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        message, _ = receiver.recvfrom(128)
        assert message == b"READY=1\n"
        time.sleep(1.4)
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=8)
        receiver.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_supervisor_requires_three_consecutive_cdp_failures(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/json/version":
                body = b'{"Browser":"fake"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    notify_path = tmp_path / "notify.sock"
    receiver = _notify_socket(notify_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    command, environment, _bootstrap_log = _supervisor_command(
        profile=profile,
        cdp_url=f"http://127.0.0.1:{server.server_port}",
        lock_path=tmp_path / "profile.lock",
        notify_path=notify_path,
        timeout="8",
        monitor_interval="0.02",
    )
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        message, _ = receiver.recvfrom(128)
        assert message == b"READY=1\n"
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
        _stdout, stderr = process.communicate(timeout=8)
        assert process.returncode != 0
        stderr_text = stderr.decode()
        assert "consecutive=1/3" in stderr_text
        assert "consecutive=2/3" in stderr_text
        assert "consecutive=3/3" in stderr_text
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=8)
        receiver.close()


def test_browser_process_age_accepts_trailing_slash_in_cdp_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_process(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [],
            0,
            stdout="42  /usr/bin/chromium --remote-debugging-port=19222 "
            "--user-data-dir=/tmp/linkedin\n",
            stderr="",
        )

    monkeypatch.setattr(browser_worker, "_run_process", fake_process)

    assert (
        browser_worker._browser_process_age_seconds(
            "linkedin",
            cdp_url="http://169.254.77.2:19222/",
            profile=Path("/tmp/linkedin"),
        )
        == 42
    )


def test_dom_accounting_accepts_returned_duplicate_canonical_rows() -> None:
    accounting = classify_linkedin_dom_job_ids(
        dom_job_ids=frozenset({"202"}),
        parser_vacancies=[
            _a4_vacancy("202"),
            _a4_vacancy("202", title="Director Product"),
        ],
        returned_vacancies=[
            _a4_vacancy("202"),
            _a4_vacancy("202", title="Director Product"),
        ],
    )

    assert accounting.outcome_by_id == {"202": "duplicate-canonical"}
    assert accounting.returned_count == 1
    assert accounting.vacancies_extracted == 2


def test_dom_accounting_reports_returned_rows_outside_observed_dom() -> None:
    accounting = classify_linkedin_dom_job_ids(
        dom_job_ids=frozenset({"101"}),
        parser_vacancies=[_a4_vacancy("101")],
        returned_vacancies=[_a4_vacancy("101"), _a4_vacancy("999")],
    )

    assert accounting.returned_count == 1
    assert accounting.vacancies_extracted == 2
    assert accounting.returned_outside_dom_job_ids == frozenset({"999"})


def test_dom_accounting_names_returned_rows_without_canonical_job_id() -> None:
    accounting = classify_linkedin_dom_job_ids(
        dom_job_ids=frozenset(),
        parser_vacancies=[],
        returned_vacancies=[_a4_vacancy("")],
    )

    assert accounting.vacancies_extracted == 1
    assert accounting.returned_without_canonical_job_id_count == 1


def test_public_parser_ignores_unclosed_void_tags_inside_a_card() -> None:
    html = """
    <div class="base-card base-search-card job-search-card"
         data-entity-urn="urn:li:jobPosting:4459675813">
      <img class="company-logo">
      <input type="hidden">
      <a class="base-card__full-link"
         href="https://uk.linkedin.com/jobs/view/chief-product-officer-at-burns-sheehan-4459675813">open</a>
      <h3 class="base-search-card__title">Chief Product Officer</h3>
      <a class="hidden-nested-link">Burns Sheehan</a>
      <span class="job-search-card__location">United Kingdom</span>
    </div>
    """

    vacancies = extract_linkedin_vacancies_from_html(
        html, page_url="https://www.linkedin.com/jobs/search/"
    )

    assert len(vacancies) == 1
    assert vacancies[0].url.endswith("/jobs/view/chief-product-officer-at-burns-sheehan-4459675813")


def test_supervisor_rejects_profile_path_outside_bootstrap_profile_root(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    supervisor = _load_browser_supervisor()
    profile = tmp_path / "linkedin"
    bootstrap_script = tmp_path / "bootstrap.sh"
    bootstrap_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    assert (
        supervisor.main(
            [
                "--source",
                "linkedin",
                "--profile",
                str(profile),
                "--cdp-url",
                "http://127.0.0.1:19222",
                "--lock-path",
                str(tmp_path / "profile.lock"),
                "--bootstrap-script",
                str(bootstrap_script),
                "--startup-timeout",
                "0.01",
            ]
        )
        == 1
    )
    assert "profile path" in capsys.readouterr().err
