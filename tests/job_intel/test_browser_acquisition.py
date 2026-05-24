from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from job_intel.browser_sourcing import (
    AcquisitionMetrics,
    BrowserAcquisitionConfig,
    BrowserNativeUnavailable,
    BrowserSourceClient,
    browser_native_available,
    extract_company_career_vacancies_from_html,
    extract_headhunter_vacancies_from_html,
    extract_linkedin_vacancies_from_html,
    metrics_from_counts,
    resolve_browser_config,
)


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


def test_extract_headhunter_vacancies_from_html_supports_exec_roles_without_tokens() -> None:
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Head of Product",
          "description": "Own monetization and product strategy",
          "hiringOrganization": {"name": "Fintech Group"},
          "jobLocation": {"address": {"addressLocality": "Almaty", "addressCountry": "KZ"}},
          "url": "https://hh.ru/vacancy/456"
        }
        </script>
      </head>
      <body>
        <a href="/vacancy/456">Head of Product</a>
      </body>
    </html>
    """

    vacancies = extract_headhunter_vacancies_from_html(html, page_url="https://hh.ru/search/vacancy")

    assert len(vacancies) == 1
    vacancy = vacancies[0]
    assert vacancy.source == "headhunter"
    assert vacancy.company == "Fintech Group"
    assert vacancy.title == "Head of Product"
    assert vacancy.location == "Almaty, KZ"
    assert vacancy.url == "https://hh.ru/vacancy/456"


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


def test_extract_linkedin_vacancies_from_html_does_not_infer_wrong_company_from_view_path() -> None:
    html = """
    <html>
      <body>
        <a href="/jobs/view/123">VP Product</a>
      </body>
    </html>
    """

    vacancies = extract_linkedin_vacancies_from_html(html, page_url="https://www.linkedin.com/jobs/search")

    assert len(vacancies) == 1
    assert vacancies[0].company == "Unknown"


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
    monkeypatch.delenv("JOB_INTEL_BROWSER_PROFILE_DIR_HH", raising=False)

    linkedin_config = resolve_browser_config("linkedin")
    hh_config = resolve_browser_config("headhunter")

    assert linkedin_config.user_data_dir.as_posix() == "/var/lib/browser-desktop/profiles/linkedin"
    assert hh_config.user_data_dir.as_posix() == "/var/lib/browser-desktop/profiles/hh"


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
    feed_page = "<html><body><div class='feed-identity-module'>Feed</div></body></html>"
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

    def fake_fetch(url: str, *, scrolls=None):
        page_urls.append(url)
        if url.endswith("/feed/"):
            return feed_page
        return first_page if "start=25" not in url else login_wall

    monkeypatch.setattr(client, "fetch_html", fake_fetch)

    vacancies = client.search_linkedin("VP Product", max_pages=5)

    assert len(vacancies) == 1
    assert page_urls[0] == "https://www.linkedin.com/feed/"
    assert page_urls[1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product"
    assert page_urls[-1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product&start=25"
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
    feed_page = "<html><body><div class='feed-identity-module'>Feed</div></body></html>"
    search_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "VP Product", "description": "Own monetization", "url": "https://www.linkedin.com/jobs/view/123", "hiringOrganization": {"name": "Spark"}}
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

    def fake_fetch(url: str, *, scrolls=None):
        page_urls.append(url)
        if url.endswith("/feed/"):
            return feed_page
        return detail_page if url.endswith("/456") else search_page

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr("job_intel.browser_sourcing.random.choice", lambda items: items[1])

    vacancies = client.search_linkedin("VP Product", max_pages=1)

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
